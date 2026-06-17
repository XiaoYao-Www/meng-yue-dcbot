"""### AI 世界生成引擎

呼叫 Deepseek API 根據故事大綱與目標類型，生成完整的 TRPG 世界設定與規則集。
無 fallback — 失敗時拋出例外，由呼叫端處理。
"""

import json

from database.trpg_db import trpgDB
from utils.trpg_ai_client import get_ai_client, parse_json_response, call_ai, DEEPSEEK_MODEL
from utils.trpg_dice import normalize_dice_rules


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
    "world_setting": "世界觀完整描述（300-800字繁體中文，包含世界背景、文明狀態、當前危機等，完全根據故事大綱生成，**不得使用通用奇幻模板**）",

    "final_goal": "遊戲最終目標（100-200字繁體中文，若為隱藏目標請描述揭示方式但不要洩漏真相）",

    "world_rules": {{
        "setting_name": "世界名稱",

        "character_creation": {{
            "description": "角色創建規則說明（100-200字，說明如何創建角色）",
            "steps": [
                {{
                    "step": 1,
                    "field": "第一步的名稱（完全由故事決定，如：出身背景、陣營、種族、職業方向、血統等）",
                    "prompt": "引導玩家選擇的文字（20-40字繁體中文）",
                    "options": [
                        {{"name": "選項1（必須貼合故事設定）", "description": "選項說明"}},
                        {{"name": "選項2", "description": "..."}},
                        {{"name": "選項3", "description": "..."}}
                    ]
                }},
                {{
                    "step": 2,
                    "field": "第二步名稱",
                    "prompt": "引導文字",
                    "options": [
                        {{"name": "...", "description": "..."}}
                    ]
                }},
                {{
                    "step": 3,
                    "field": "第三步名稱（完全由故事決定，如：出身背景、職業方向、血統、技能專長等）",
                    "prompt": "引導玩家選擇的文字（20-40字繁體中文）",
                    "options": [
                        {{"name": "選項1（必須貼合故事設定）", "description": "選項說明"}},
                        {{"name": "選項2", "description": "..."}},
                        {{"name": "選項3", "description": "..."}}
                    ]
                }}
            ],
            "base_attributes": ["屬性1", "屬性2"],
            "free_attributes": ["自訂屬性1", "自訂屬性2"],
            "free_points": 5,
            "skills": [
                {{"name": "技能1名稱（完全由故事決定）", "description": "技能說明"}},
                {{"name": "技能2名稱", "description": "技能說明"}},
                {{"name": "技能3名稱", "description": "技能說明"}}
            ]
        }},

        "rules_summary": "規則說明（100-300字繁體中文）",

        "validity_rules": "技能、行為與道具合理性準則（100-200字繁體中文，說明什麼行為合理、道具強度限制等）",

        "dice_rules": {{
            "description": "整體骰子系統說明（如：D20檢定為主，特定情況使用2D6）",
            "default": "D20",
            "rules": [
                {{
                    "description": "此骰子用途說明",
                    "dices": [
                        {{"range": "1-20", "count": 1}},
                        {{"range": "1-6", "count": 2}}
                    ],
                    "rule": "點數讀取規則（如：大成功/大失敗條件、加值規則）"
                }}
            ]
        }},

    }}
}},
"end_conditions": ["結束條件1", "結束條件2"]
}}

⚠️ 關鍵規則（請嚴格遵守）：
- **所有內容必須嚴格根據故事大綱生成，不得使用通用奇幻模板**
- **若故事大綱是現代校園/都市，不該出現任何奇幻/科幻元素（精靈、矮人、魔法、法術等）**
- **角色創建步驟的 field 名稱必須完全呼應故事背景，禁止使用通用詞（如「種族」除非故事確實有不同種族）**
- **世界觀描述不得使用「在一個遙遠的...」「這是一個...的世界」等通用開頭**
- **現代故事不該出現精靈/矮人/魔法，科幻不該出現中世紀職業**
- character_creation.steps 至少 2 步、最多 4 步，每步 options 至少 3 個選項，step 數字從 1 開始遞增
- character_creation.free_attributes 由故事決定（如克系有「理智」、科幻有「科技」）
- **base_attributes 由故事背景決定**：奇幻→["生命","魔力"]、現代→["體能","意志"]、科幻→["能量","護盾"]、克系→["生命","理智"]。**不要預設 hp/san**，非克系故事不該有 san
- character_creation.skills 至少 3 個技能，技能名稱必須符合世界設定
- 所有文字繁體中文"""


class WorldGenError(Exception):
    """世界生成失敗時拋出"""
    pass


async def generate_world(game_id: int, game_name: str, story_outline: str, goal_type: str) -> tuple[str, str, str, str]:
    """### 呼叫 Deepseek API 生成世界設定與規則

    回傳 (world_setting, world_rules_json_string, final_goal, end_conditions_json_string)

    Raises:
        WorldGenError: 當 AI 不可用或生成失敗時
    """
    client = get_ai_client()
    if not client:
        raise WorldGenError(
            "AI 用戶端不可用（openai SDK 未安裝或 DEEPSEEK_API_KEY 未設定）"
        )

    prompt = WORLD_GEN_PROMPT.format(
        game_name=game_name,
        story_outline=story_outline,
        goal_type=goal_type,
    )

    # retry 一次
    last_error = None
    for attempt in range(2):
        try:
            raw = await call_ai(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.9,
                max_tokens=4000,
                timeout=60,
            )
            if raw is None:
                raise WorldGenError("AI 呼叫失敗（回傳空值）")

            parsed = parse_json_response(raw)

            if parsed and "world_setting" in parsed and "world_rules" in parsed:
                world_setting = parsed["world_setting"]
                world_rules = parsed["world_rules"]
                final_goal = parsed.get("final_goal", "")
                end_conditions = parsed.get("end_conditions", [])
                # 向後相容：若頂層沒有 end_conditions，嘗試從 world_rules 內部提取並移除
                if not end_conditions and isinstance(world_rules, dict):
                    end_conditions = world_rules.pop("end_conditions", [])

                if isinstance(world_rules, dict):
                    # 正規化 dice_rules
                    dr = world_rules.get("dice_rules")
                    if dr is not None:
                        world_rules["dice_rules"] = normalize_dice_rules(dr)
                    world_rules_str = json.dumps(world_rules, ensure_ascii=False)
                else:
                    world_rules_str = str(world_rules)

                end_conditions_str = json.dumps(end_conditions, ensure_ascii=False)

                await trpgDB.update_game_world(
                    game_id, world_setting, world_rules_str, final_goal, end_conditions_str
                )
                print(f"[WorldGen] ✅ 世界生成成功: {game_name}")
                return world_setting, world_rules_str, final_goal, end_conditions_str

            last_error = "AI 回傳格式錯誤，缺少必要欄位"
            print(f"[WorldGen] ⚠️ 嘗試 {attempt+1}/2 失敗: {last_error}")
            if raw:
                print(f"[WorldGen] ⚠️ Raw 前 300 字: {raw[:300]}")
            if attempt == 0:
                continue

        except Exception as e:
            last_error = str(e)
            print(f"[WorldGen] ❌ 嘗試 {attempt+1}/2 失敗: {e}")
            if attempt == 0:
                continue

    raise WorldGenError(f"世界生成失敗（已重試 2 次）：{last_error}")
