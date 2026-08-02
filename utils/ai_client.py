"""
### AI 客戶端模組

封裝 DeepSeek（OpenAI 相容）API 呼叫，供各功能（如每日知識生成）重複使用。

- AI_PROFILES 配置池（config.py）定義各 profile 的 model / base_url / 思考模式。
- DeepSeekClient 依 profile 呼叫，含自動重試、漸進式降級與多層 JSON 容錯解析。
"""
import asyncio
import json
from typing import Dict, Optional

import openai
from json_repair import repair_json

from config import AI_PROFILES, DAILY_AI_MAX_RETRIES


def build_request_kwargs(
    model: str,
    thinking_enabled: bool,
    reasoning_effort: str,
    messages: list,
    max_tokens: int,
    use_json_mode: bool,
    temperature: float,
) -> dict:
    """### 依配置構建 DeepSeek Chat Completion 請求參數

    依 DeepSeek 思考模式文檔：
    - 思考啟用：傳 reasoning_effort + extra_body={"thinking": {"type": "enabled"}}，
      且思考模式不支援 temperature（傳入不會報錯但不生效，故不傳）。
    - 思考禁用：extra_body={"thinking": {"type": "disabled"}} + 既有 temperature 控制。

    Args:
        model: 模型名稱
        thinking_enabled: 思考模式是否啟用
        reasoning_effort: 思考強度（low / high / max）
        messages: 對話訊息
        max_tokens: 最大 token 數
        use_json_mode: 是否使用 JSON Mode
        temperature: 溫度（僅思考禁用時使用）

    Returns:
        可直接傳給 client.chat.completions.create 的 kwargs
    """
    kwargs: dict = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
    }
    if thinking_enabled:
        kwargs["reasoning_effort"] = reasoning_effort
        kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
    else:
        kwargs["temperature"] = temperature
        kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
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


class DeepSeekClient:
    """### DeepSeek（OpenAI 相容）API 客戶端

    依 AI_PROFILES 配置呼叫，並按 base_url 快取底層 client。
    呼叫含自動重試 + 漸進式降級（重試降低 temperature、JSON Mode 僅首次嘗試）。
    """

    def __init__(self, api_key: str):
        """### 初始化

        Args:
            api_key: DeepSeek API Key
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
            self._clients[base_url] = openai.OpenAI(api_key=self._api_key, base_url=base_url)
        return self._clients[base_url]

    async def call(
        self,
        prompt: str,
        profile_name: str,
        max_tokens: int = 2048,
        use_json_mode: bool = True,
        temperature: float = 0.7,
    ) -> Optional[Dict[str, str]]:
        """### 依指定配置呼叫 AI 生成內容（含自動重試 + 漸進式降級）

        策略：
        1. 依 AI_PROFILES 配置決定 model / base_url / 思考模式與強度
        2. 優先嘗試 JSON Mode（response_format），若返回空 content 立即回退一般模式
        3. 思考禁用時每次重試降低 temperature，提高輸出確定性

        Args:
            prompt: 提示詞
            profile_name: AI_PROFILES 中的配置名稱
            max_tokens: 最大 token 數
            use_json_mode: 是否優先使用 JSON Mode
            temperature: 初始 temperature（僅思考禁用時生效，重試時逐步降低）

        Returns:
            解析後的 dict 或 None
        """
        profile = AI_PROFILES.get(profile_name)
        if profile is None:
            print(f"[AIClient] AI 配置「{profile_name}」不存在於 AI_PROFILES")
            return None

        client = self._get_client(profile["base_url"])
        if client is None:
            print("[AIClient] API Key 未設定，無法呼叫 AI")
            return None

        model = profile["model"]
        thinking_enabled = profile["thinking_enabled"]
        # reasoning_effort 僅思考啟用時需要；禁用時可缺省（DeepSeek 思考禁用不傳 effort）
        reasoning_effort = profile.get("reasoning_effort", "high")
        max_retries = DAILY_AI_MAX_RETRIES

        for attempt in range(1, max_retries + 1):
            # 漸進式降級：每次重試降低 temperature
            current_temp = max(0.1, temperature * (0.7 ** (attempt - 1)))
            # JSON Mode 僅首次嘗試
            try_json = use_json_mode and attempt == 1

            try:
                def _sync_call() -> str:
                    kwargs = build_request_kwargs(
                        model=model,
                        thinking_enabled=thinking_enabled,
                        reasoning_effort=reasoning_effort,
                        messages=[{"role": "user", "content": prompt}],
                        max_tokens=max_tokens,
                        use_json_mode=try_json,
                        temperature=current_temp,
                    )
                    if thinking_enabled:
                        print(f"[AIClient] {profile_name} 思考模式 model={model} effort={reasoning_effort}")
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

                    return content

                text = await asyncio.to_thread(_sync_call)
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

            except Exception as e:
                print(f"[AIClient] 第 {attempt}/{max_retries} 次嘗試異常: {type(e).__name__}: {e}")

        print(f"[AIClient] API 呼叫失敗（已重試 {max_retries} 次）")
        return None

    def close(self) -> None:
        """### 關閉所有底層 client"""
        for client in self._clients.values():
            client.close()
        self._clients.clear()
