"""
### AI 客戶端模組

封裝 New-API（OpenAI 相容）API 呼叫，供各功能（如每日知識生成）重複使用。

- AI_PROFILES 配置池（config.py）定義各 profile 的 model / base_url / 思考模式。
- NewApiClient 依 profile 呼叫，含自動重試、漸進式降級與多層 JSON 容錯解析。
"""
import asyncio
import json
from datetime import datetime
from typing import Dict, Optional, Tuple, Any

import openai
from json_repair import repair_json

from config import (
    AI_PROFILES, DAILY_AI_MAX_RETRIES,
    AI_DAILY_MAX_TOKENS, AI_DAILY_MAX_CALLS, TZ,
)
from database.ai_usage_db import aiUsageDB


class AIQuotaExceededError(Exception):
    """### 當每日 AI Token 或呼叫次數達到上限時拋出的基底例外"""
    def __init__(self, message: str, policy: str = "drop"):
        super().__init__(message)
        self.message = message
        self.policy = policy


class AIQuotaPostponedError(AIQuotaExceededError):
    """### 當每日 AI 用量超額且任務策略為 postpone 時拋出"""
    def __init__(self, message: str):
        super().__init__(message, policy="postpone")



def build_request_kwargs(
    model: str,
    reasoning_effort: str | None,
    messages: list,
    max_tokens: int,
    use_json_mode: bool,
    temperature: float,
) -> dict:
    """建立 New API OpenAI 相容 Chat Completions 請求參數。"""

    kwargs: dict = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
    }

    if reasoning_effort:
        kwargs["reasoning_effort"] = reasoning_effort
    else:
        kwargs["temperature"] = temperature

    if use_json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    return kwargs


def extract_json_block(text: str) -> Optional[str]:
    """### 掃描字串中第一個合法的 JSON 物件（大括號深度計數）

    Args:
        text: 可能含 JSON 的原始文字

    Returns:
        提取出的 JSON 字串或 None
    """
    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]

    return None


def parse_ai_response(response_text: str) -> Optional[Dict[str, str]]:
    """### 解析 AI 回傳的 JSON（通用，含多層容錯 + json-repair 修復）

    容錯策略（依序嘗試）：
    1. 清除 Markdown ``` 程式碼塊包裹 → json.loads 直接解析
    2. 大括號深度掃描提取 JSON 區塊 → json.loads 解析
    3. json-repair 修復後解析（處理換行、尾逗號、截斷等常見 AI 錯誤）

    Args:
        response_text: AI 回傳文字

    Returns:
        dict 或 None（解析失敗）
    """
    text = response_text.strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline + 1:]
        if text.endswith("```"):
            text = text[:-3].strip()

    # 嘗試直接解析
    try:
        data = json.loads(text, strict=False)
        return {k: str(v).strip() if v is not None else "" for k, v in data.items()}
    except (json.JSONDecodeError, TypeError):
        pass

    # 後備：用大括號深度掃描提取 JSON 區塊
    extracted = extract_json_block(text)
    if extracted:
        try:
            data = json.loads(extracted, strict=False)
            return {k: str(v).strip() if v is not None else "" for k, v in data.items()}
        except (json.JSONDecodeError, TypeError):
            pass

        # 第三層：json-repair 修復後解析
        try:
            repaired = repair_json(extracted)
            data = json.loads(repaired)
            return {k: str(v).strip() if v is not None else "" for k, v in data.items()}
        except Exception:
            pass

    # 最後手段：對整個原始文字嘗試 json-repair
    try:
        repaired = repair_json(text)
        data = json.loads(repaired)
        return {k: str(v).strip() if v is not None else "" for k, v in data.items()}
    except Exception:
        pass

    # 全部失敗
    print("[AIClient] 解析 AI 回傳 JSON 失敗（所有容錯層均無效）")
    print(f"[AIClient] 原始回傳長度: {len(response_text)} 字元")
    print(f"[AIClient] 原始回傳內容:\n{response_text[:800]}")
    if len(response_text) > 800:
        print(f"[AIClient] ...（後續 {len(response_text) - 800} 字元已截斷）")
    return None


class NewApiClient:
    """### New-API（OpenAI 相容）API 客戶端

    依 AI_PROFILES 配置呼叫，並按 base_url 快取底層 client。
    呼叫含自動重試 + 漸進式降級（重試降低 temperature、JSON Mode 僅首次嘗試）。
    """

    def __init__(self, api_key: str):
        """### 初始化

        Args:
            api_key: New-API Key
        """
        self._api_key = api_key
        self._clients: dict[str, openai.OpenAI] = {}  # base_url → client 快取

    def _get_client(self, base_url: str) -> openai.OpenAI | None:
        """### 依 base_url 取得（並快取）OpenAI 客戶端

        Args:
            base_url: API 端點

        Returns:
            OpenAI client 或 None（api_key 無效）
        """
        if not self._api_key:
            return None
        if base_url not in self._clients:
            self._clients[base_url] = openai.OpenAI(
                api_key=self._api_key,
                base_url=base_url,
                timeout=60.0,
            )
        return self._clients[base_url]

    async def call(
        self,
        prompt: str,
        profile_name: str,
        max_tokens: int = 2048,
        use_json_mode: bool = True,
        temperature: float = 0.7,
        on_quota_exceeded: str = "drop",
    ) -> Optional[Dict[str, str]]:
        """### 依指定配置呼叫 AI 生成內容（含用量限制 + 自動重試 + 漸進式降級）

        Args:
            prompt: 提示詞
            profile_name: AI_PROFILES 中的配置名稱
            max_tokens: 最大 token 數
            use_json_mode: 是否優先使用 JSON Mode
            temperature: 初始 temperature
            on_quota_exceeded: 每日用量達到上限時的處理策略：
                - "drop" (預設): 直接攔截並丟棄請求，拋出 AIQuotaExceededError
                - "postpone": 暫緩任務直至隔天，拋出 AIQuotaPostponedError

        Returns:
            解析後的 dict 或 None
        """
        # 1. 前置每日用量檢查
        today_str = datetime.now(TZ).strftime("%Y-%m-%d")
        exceeded, reason = await aiUsageDB.check_quota_exceeded(
            today_str, AI_DAILY_MAX_TOKENS, AI_DAILY_MAX_CALLS
        )
        if exceeded:
            if on_quota_exceeded == "postpone":
                print(f"[AIClient] ⏸️ 每日 AI 用量已達上限 ({reason})，任務策略為 postpone（已自動暫緩至隔日）")
                raise AIQuotaPostponedError(reason)
            else:
                print(f"[AIClient] ⛔ 每日 AI 用量已達上限 ({reason})，任務策略為 drop（已直接放棄請求）")
                raise AIQuotaExceededError(reason, policy="drop")

        profile = AI_PROFILES.get(profile_name)
        if profile is None:
            print(f"[AIClient] AI 配置「{profile_name}」不存在於 AI_PROFILES")
            return None

        client = self._get_client(profile["base_url"])
        if client is None:
            print("[AIClient] API Key 未設定，無法呼叫 AI")
            return None

        model = profile["model"]
        reasoning_effort = profile.get("reasoning_effort")
        max_retries = DAILY_AI_MAX_RETRIES

        for attempt in range(1, max_retries + 1):
            # 漸進式降級：每次重試降低 temperature
            current_temp = max(0.1, temperature * (0.7 ** (attempt - 1)))
            # JSON Mode 僅首次嘗試
            try_json = use_json_mode and attempt == 1

            try:
                def _sync_call() -> Tuple[str, Any]:
                    kwargs = build_request_kwargs(
                        model=model,
                        reasoning_effort=reasoning_effort,
                        messages=[{"role": "user", "content": prompt}],
                        max_tokens=max_tokens,
                        use_json_mode=try_json,
                        temperature=current_temp,
                    )
                    if reasoning_effort:
                        print(
                            f"[AIClient] {profile_name} "
                            f"model={model} reasoning_effort={reasoning_effort}"
                        )
                    elif try_json:
                        print(f"[AIClient] {profile_name} JSON 模式 model={model} temp={current_temp:.2f}")
                    else:
                        print(f"[AIClient] {profile_name} 一般模式 model={model} temp={current_temp:.2f}")

                    response = client.chat.completions.create(**kwargs)
                    content = response.choices[0].message.content or ""
                    finish = response.choices[0].finish_reason
                    usage = getattr(response, "usage", None)
                    usage_str = f"prompt={usage.prompt_tokens} completion={usage.completion_tokens}" if usage else "N/A"

                    if not content:
                        print(f"[AIClient] API 回傳空內容！finish_reason={finish} usage=({usage_str})")

                    return content, usage

                text, usage = await asyncio.to_thread(_sync_call)

                # 記錄用量
                if usage:
                    p_tokens = getattr(usage, "prompt_tokens", 0) or 0
                    c_tokens = getattr(usage, "completion_tokens", 0) or 0
                    rec = await aiUsageDB.record_usage(today_str, p_tokens, c_tokens)
                    print(
                        f"[AIClient] 用量紀錄 ({today_str}): 呼叫第 {rec['call_count']} 次 | "
                        f"Tokens: 今日總計 {rec['total_tokens']} (本次 +{p_tokens + c_tokens})"
                    )

                if not text:
                    # 空內容：若為 JSON Mode 則直接視為該模式不支援，下次不再嘗試
                    print(f"[AIClient] 第 {attempt}/{max_retries} 次嘗試回傳空內容")
                    if try_json:
                        print(f"[AIClient] {profile_name} JSON 回傳空內容，後續嘗試將跳過 JSON 模式")
                        use_json_mode = False
                    continue

                parsed = parse_ai_response(text)
                if parsed is not None:
                    return parsed

                print(f"[AIClient] 第 {attempt}/{max_retries} 次嘗試失敗（解析錯誤）")

            except openai.APIConnectionError as e:
                print(f"[AIClient] 第 {attempt}/{max_retries} 次連線異常: {e}")
            except openai.APIStatusError as e:
                print(f"[AIClient] 第 {attempt}/{max_retries} 次 HTTP 狀態錯誤 ({e.status_code}): {e.message}")
            except Exception as e:
                print(f"[AIClient] 第 {attempt}/{max_retries} 次嘗試異常: {type(e).__name__}: {e}")

        print(f"[AIClient] API 呼叫失敗（已重試 {max_retries} 次）")
        return None

    def close(self) -> None:
        """### 關閉所有底層 client"""
        for client in self._clients.values():
            client.close()
        self._clients.clear()


# 向下相容別名
DeepSeekClient = NewApiClient

