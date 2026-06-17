"""### TRPG AI 客戶端共用模組

提供統一的 Deepseek AI 客戶端初始化、API 呼叫、JSON 回應解析。
消除 events/trpg_world_gen.py、events/trpg_action.py、events/trpg_listener.py 三份重複程式碼。
"""

import json
import os
import re
from typing import Any

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
DEEPSEEK_MODEL = "deepseek-v4-flash"

_ai_client: Any | None = None


def get_ai_client() -> Any | None:
    """### 延遲初始化 AI 客戶端，全專案共用單一實例

    Returns:
        AsyncOpenAI 客戶端實例，或 None（若 SDK 未安裝或 API Key 未設定）
    """
    global _ai_client
    if _ai_client is None and DEEPSEEK_API_KEY:
        try:
            from openai import AsyncOpenAI
            _ai_client = AsyncOpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
        except ImportError:
            pass
    return _ai_client


def parse_json_response(raw: str) -> dict[str, Any] | None:
    """### 從 LLM 回應字串中解析 JSON

    嘗試三種策略：
    1. 直接 json.loads（純 JSON）
    2. 從 ```json ... ``` code block 中提取
    3. 從第一個 { 到最後一個 } 截取

    Args:
        raw: LLM 回傳的原始文字

    Returns:
        解析成功的 dict，或 None
    """
    if not raw:
        return None

    # 策略 1：純 JSON
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # 策略 2：code block
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # 策略 3：花括號截取
    brace_start = raw.find("{")
    brace_end = raw.rfind("}")
    if brace_start != -1 and brace_end != -1:
        try:
            return json.loads(raw[brace_start:brace_end + 1])
        except json.JSONDecodeError:
            pass

    return None


async def call_ai(
    messages: list[dict[str, str]],
    temperature: float = 0.8,
    max_tokens: int = 1200,
    timeout: int = 60,
) -> str | None:
    """### 呼叫 Deepseek AI 並回傳原始回應文字

    Args:
        messages: OpenAI 格式的訊息列表
        temperature: 創造性（預設 0.8）
        max_tokens: 最大 token 數
        timeout: 超時秒數

    Returns:
        回傳文字內容，或 None（呼叫失敗）
    """
    client = get_ai_client()
    if not client:
        return None

    response = await client.chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
    )
    return response.choices[0].message.content.strip()


__all__ = ["get_ai_client", "parse_json_response", "call_ai", "DEEPSEEK_MODEL"]
