"""
驗證 AI 模組功能：AI_PROFILES 配置池、請求參數構建、兩段式驗證短路邏輯。
"""
import asyncio
import os
import sys

os.environ.setdefault("DB_PATH", "data/db")
os.environ.setdefault("DISCORD_TOKEN", "test_discord_token")
os.environ.setdefault("GUILD_ID", "0")
os.environ.setdefault("DAILY_CHANNEL", "0")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import AI_PROFILES, DAILY_GENERATION_PROFILE, DAILY_VERIFICATION_PROFILES
from utils.ai_client import build_request_kwargs
from events.daily_message import DailyMessageEvent


def test_ai_profiles_structure():
    """驗證 AI_PROFILES 配置池結構完整"""
    assert isinstance(AI_PROFILES, dict) and len(AI_PROFILES) >= 2
    for name, p in AI_PROFILES.items():
        assert all(k in p for k in ("model", "base_url", "thinking_enabled")), f"{name} 缺欄位"
        assert isinstance(p["model"], str) and p["model"], f"{name} model 非法"
        assert isinstance(p["base_url"], str) and p["base_url"].startswith("http"), f"{name} base_url 非法"
        assert isinstance(p["thinking_enabled"], bool), f"{name} thinking_enabled 非法"
        # reasoning_effort 僅思考啟用時需要（思考禁用可不填）
        if p["thinking_enabled"]:
            assert p.get("reasoning_effort") in ("low", "high", "max"), f"{name} effort 非法"
    print("OK: AI_PROFILES 結構測試通過")


def test_daily_profiles_valid():
    """驗證生成/兩段式驗證配置名皆存在於 AI_PROFILES"""
    assert DAILY_GENERATION_PROFILE in AI_PROFILES
    assert isinstance(DAILY_VERIFICATION_PROFILES, tuple) and len(DAILY_VERIFICATION_PROFILES) >= 2
    for p in DAILY_VERIFICATION_PROFILES:
        assert p in AI_PROFILES
    print("OK: 每日配置名有效測試通過")


def test_build_request_kwargs_thinking_enabled():
    """思考啟用：effort + thinking enabled，不傳 temperature"""
    k = build_request_kwargs(
        model="deepseek-v4-pro", thinking_enabled=True, reasoning_effort="high",
        messages=[{"role": "user", "content": "x"}], max_tokens=100,
        use_json_mode=True, temperature=0.7)
    assert k["reasoning_effort"] == "high"
    assert k["extra_body"] == {"thinking": {"type": "enabled"}}
    assert "temperature" not in k, "思考模式不應傳 temperature"
    assert k["response_format"] == {"type": "json_object"}
    print("OK: 思考啟用參數測試通過")


def test_build_request_kwargs_thinking_disabled():
    """思考禁用：thinking disabled + temperature"""
    k = build_request_kwargs(
        model="deepseek-v4-flash", thinking_enabled=False, reasoning_effort="low",
        messages=[{"role": "user", "content": "x"}], max_tokens=100,
        use_json_mode=False, temperature=0.5)
    assert k["extra_body"] == {"thinking": {"type": "disabled"}}
    assert k["temperature"] == 0.5
    assert "reasoning_effort" not in k
    print("OK: 思考禁用參數測試通過")


# ── 兩段式驗證短路邏輯 ──

async def _run_verify(seq):
    """以指定的 _verify_with_profile 回傳序列執行 _verify_section，記錄呼叫的配置"""
    cog = DailyMessageEvent.__new__(DailyMessageEvent)
    calls = []

    async def fake_verify(content, profile_name):
        calls.append(profile_name)
        return seq.pop(0)

    cog._verify_with_profile = fake_verify
    return await cog._verify_section({"x": 1}), calls


def test_verify_short_circuit_accepted():
    """配置1 判定通過 → 直接 accepted，配置2 不被呼叫（短路）"""
    r, calls = asyncio.run(_run_verify([
        {"verification": "通過", "credibility": "高", "evidence": "e"}]))
    assert r == ({"verification": "通過", "credibility": "高", "evidence": "e"}, "accepted")
    assert calls == ["deepseek_flash"], f"配置2 不應被呼叫: {calls}"
    print("OK: 配置1 通過即短路測試通過")


def test_verify_second_profile_on_reject():
    """配置1 判定不通過 → 才嘗試配置2；配置2 通過 → accepted"""
    r, calls = asyncio.run(_run_verify([
        {"verification": "不通過", "credibility": "低", "evidence": "e"},
        {"verification": "通過", "credibility": "高", "evidence": "e2"}]))
    assert r[1] == "accepted" and r[0]["credibility"] == "高"
    assert calls == ["deepseek_flash", "deepseek_pro"]
    print("OK: 配置1 不通過→配置2 通過測試通過")


def test_verify_double_reject():
    """配置1、配置2 皆判定不通過 → rejected"""
    r, calls = asyncio.run(_run_verify([
        {"verification": "不通過", "credibility": "低", "evidence": "e"},
        {"verification": "不通過", "credibility": "低", "evidence": "e2"}]))
    assert r == (None, "rejected")
    assert calls == ["deepseek_flash", "deepseek_pro"]
    print("OK: 雙不通過→rejected 測試通過")


def test_verify_api_error_fallback():
    """配置1 API 失敗 → 嘗試配置2；配置2 通過 → accepted"""
    r, calls = asyncio.run(_run_verify([
        None,
        {"verification": "有疑慮", "credibility": "中", "evidence": "e"}]))
    assert r[1] == "accepted"
    assert calls == ["deepseek_flash", "deepseek_pro"]
    print("OK: 配置1 API 失敗→配置2 通過測試通過")


def test_verify_all_api_error():
    """配置1、配置2 皆 API 失敗 → error"""
    r, calls = asyncio.run(_run_verify([None, None]))
    assert r == (None, "error")
    assert calls == ["deepseek_flash", "deepseek_pro"]
    print("OK: 雙 API 失敗→error 測試通過")


if __name__ == "__main__":
    test_ai_profiles_structure()
    test_daily_profiles_valid()
    test_build_request_kwargs_thinking_enabled()
    test_build_request_kwargs_thinking_disabled()
    test_verify_short_circuit_accepted()
    test_verify_second_profile_on_reject()
    test_verify_double_reject()
    test_verify_api_error_fallback()
    test_verify_all_api_error()
    print("所有測試通過！")
