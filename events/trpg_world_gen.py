"""### AI 世界生成引擎

呼叫 Deepseek API 根據故事大綱與目標類型，生成完整的 TRPG 世界設定與規則集。
供 `/rpg_start` 在創建論壇頻道後呼叫。
"""

import json
import os
import re
from typing import Optional

from database.trpg_db import trpgDB

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
DEEPSEEK_MODEL = "deepseek-chat"

try:
    from openai import AsyncOpenAI
    _ai_client = AsyncOpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL) if DEEPSEEK_API_KEY else None
except ImportError:
    _ai_client = None


WORLD_GEN_PROMPT = """你是一個 TRPG 世界創造者。請根據以下資訊，設計一個完整的 TRPG 遊戲世界。

## 遊戲名稱
{game_name}

## 故事大綱
{story_outline}

## 目標類型
{goal_type}

========================================
請嚴格按照以下 JSON 格式回傳，不要包含任何其他文字：

{{
    "world_setting": "世界觀完整描述（300-800字，包含世界背景、地理、文明狀態、當前危機等，繁體中文）",

    "final_goal": "遊戲最終目標描述（100-200字，明確說明玩家需要達成的終極目標。若為隱藏目標類型，請描述目標如何被逐步揭示，但不要洩漏最終真相）",

    "world_rules": {{
        "setting_name": "世界名稱",

        "races": [
            {{
                "name": "種族名稱",
                "description": "種族描述與特性（50-150字）",
                "traits": {{}}
            }}
        ],

        "classes": [
            {{
                "name": "職業名稱",
                "description": "職業描述與玩法說明（50-150字）",
                "base_stats": {{"hp": 100, "san": 100}}
            }}
        ],

        "skills": [
            {{
                "name": "技能名稱",
                "description": "技能說明"
            }}
        ],

        "rules_summary": "簡短的規則說明（戰鬥機制、判定方式等，100-300字）",

        "validity_rules": "行為與道具合理性準則（100-200字，說明什麼樣的玩家行為合理、什麼樣的道具可以存在、能力上限等。嚴禁突然變成神明，道具強度需與世界設定一致，傳說級物品必須透過劇情獲得）"
    }}
}}

注意：
- world_setting 必須與故事大綱緊密相關
- final_goal 必須與故事大綱和目標類型一致，這是整場遊戲不可變更的硬編碼
- races 和 classes 至少各 3 個選項，但不要超過 6 個
- skills 至少 5 個
- validity_rules 是硬編碼準則，用於後續 AI GM 判定玩家行為是否合理
- 所有文字使用繁體中文"""


FALLBACK_WORLD = {
    "world_setting": (
        "這是一個充滿未知與冒險的廣闊世界。古老的遺蹟散落在大地各處，"
        "傳說中的寶藏與危險並存。不同種族與勢力在這片大陸上交織出複雜的歷史，"
        "而冒險者們將在這混沌的時代中寫下屬於自己的傳奇。"
    ),
    "final_goal": (
        "冒險者們需要探索未知大陸，尋找傳說中的古代遺跡，"
        "解開隱藏在歷史中的真相，最終阻止黑暗勢力的復甦。"
    ),
    "world_rules": json.dumps({
        "setting_name": "未知大陸",
        "races": [
            {"name": "人類", "description": "適應力強、均衡發展的種族。", "traits": {"適應力": 1}},
            {"name": "精靈", "description": "敏捷優雅、與自然共鳴的長壽種族。", "traits": {"敏捷": 1}},
            {"name": "矮人", "description": "堅韌頑強、擅長工藝的戰鬥民族。", "traits": {"體質": 1}},
        ],
        "classes": [
            {"name": "戰士", "description": "專精近身戰鬥的勇猛鬥士。", "base_stats": {"hp": 120, "san": 80}},
            {"name": "法師", "description": "操縱元素與奧術的智者。", "base_stats": {"hp": 70, "san": 150}},
            {"name": "遊俠", "description": "穿梭荒野的敏捷獵手。", "base_stats": {"hp": 90, "san": 110}},
        ],
        "skills": [
            {"name": "偵查", "description": "探索周圍環境，發現隱藏線索。"},
            {"name": "說服", "description": "用言語影響他人的判斷。"},
            {"name": "潛行", "description": "無聲移動，避開敵人的注意。"},
            {"name": "知識", "description": "回憶與理解古老知識。"},
            {"name": "生存", "description": "在野外尋找食物、水源與庇護。"},
        ],
        "rules_summary": (
            "所有行動透過 D20 擲骰判定（1-20）。1 為大失敗，20 為大成功。"
            "技能檢定時，將對應屬性加值加入骰點。"
            "戰鬥中先攻順序由敏捷決定，每次攻擊進行命中檢定與傷害計算。"
            "SAN 值歸零時角色陷入瘋狂狀態。"
        ),
        "validity_rules": (
            "玩家行為需符合角色能力與世界邏輯。禁止憑空獲得神力、"
            "一擊殺死 Boss 級敵人、創造現代科技產物。道具強度需與世界設定一致，"
            "傳說級道具必須透過劇情推進獲得。"
        ),
    }, ensure_ascii=False),
}


async def generate_world(game_id: int, game_name: str, story_outline: str, goal_type: str) -> tuple[str, str, str]:
    """### 呼叫 Deepseek API 生成世界設定與規則

    回傳 (world_setting, world_rules_json_string, final_goal)
    """
    if not _ai_client:
        print("[WorldGen] AI 用戶端不可用，使用 fallback 世界")
        await _save_fallback(game_id)
        return FALLBACK_WORLD["world_setting"], FALLBACK_WORLD["world_rules"], FALLBACK_WORLD["final_goal"]

    prompt = WORLD_GEN_PROMPT.format(
        game_name=game_name,
        story_outline=story_outline,
        goal_type=goal_type,
    )

    try:
        response = await _ai_client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.9,
            max_tokens=3500,
            timeout=60,
        )

        raw = response.choices[0].message.content.strip()
        parsed = _parse_world_response(raw)

        if parsed and "world_setting" in parsed and "world_rules" in parsed:
            world_setting = parsed["world_setting"]
            world_rules = parsed["world_rules"]
            final_goal = parsed.get("final_goal", "")

            if isinstance(world_rules, dict):
                world_rules_str = json.dumps(world_rules, ensure_ascii=False)
            else:
                world_rules_str = str(world_rules)

            await trpgDB.update_game_world(game_id, world_setting, world_rules_str, final_goal)
            print(f"[WorldGen] ✅ 世界生成成功: {game_name}")
            return world_setting, world_rules_str, final_goal
        else:
            print(f"[WorldGen] ⚠️ AI 回傳格式錯誤，使用 fallback")
            await _save_fallback(game_id)
            return FALLBACK_WORLD["world_setting"], FALLBACK_WORLD["world_rules"], FALLBACK_WORLD["final_goal"]

    except Exception as e:
        print(f"[WorldGen] ❌ API 錯誤: {e}，使用 fallback")
        await _save_fallback(game_id)
        return FALLBACK_WORLD["world_setting"], FALLBACK_WORLD["world_rules"], FALLBACK_WORLD["final_goal"]


async def _save_fallback(game_id: int) -> None:
    """儲存 fallback 世界資料到 DB"""
    await trpgDB.update_game_world(
        game_id,
        FALLBACK_WORLD["world_setting"],
        FALLBACK_WORLD["world_rules"],
        FALLBACK_WORLD["final_goal"],
    )


def _parse_world_response(raw: str) -> Optional[dict]:
    """從 LLM 回應中解析 JSON"""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    brace_start = raw.find("{")
    brace_end = raw.rfind("}")
    if brace_start != -1 and brace_end != -1:
        try:
            return json.loads(raw[brace_start:brace_end + 1])
        except json.JSONDecodeError:
            pass

    return None
