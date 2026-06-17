"""### TRPG 骰子系統共用模組

完全由 world_rules.dice_rules 驅動，無任何 D20 硬編碼。
提供標準化：骰子規則正規化、解析、擲骰、比例判定、規則格式化。
"""

import random
import re as _re
from typing import Any


def normalize_dice_rules(dr: Any) -> dict[str, Any]:
    """### 正規化 dice_rules 為標準 dict 格式

    AI 可能回傳 list（舊格式）或 dict（新格式），統一輸出：
    {"description": "...", "default": "D20", "rules": [...]}

    Args:
        dr: AI 回傳的 dice_rules（可能是 dict 或 list）

    Returns:
        標準化的 dict 格式
    """
    if isinstance(dr, dict):
        return {
            "description": dr.get("description", ""),
            "default": dr.get("default", "D20"),
            "rules": dr.get("rules", []),
        }
    if isinstance(dr, list) and dr:
        return {
            "description": "自訂骰子系統",
            "default": "D20",
            "rules": dr,
        }
    return {"description": "自訂骰子系統", "default": "D20", "rules": []}


def get_default_dice(dice_rules: dict[str, Any]) -> str:
    """### 從標準 dict 取得預設骰子字串

    Args:
        dice_rules: normalize_dice_rules 輸出

    Returns:
        骰子字串，如 "D20"、"3D6"、"D100"
    """
    return dice_rules.get("default", "D20")


def parse_dice_string(dice_str: str) -> tuple[int, int] | None:
    """### 解析骰子字串 → (count, faces)

    "D20" → (1, 20)
    "3D6" → (3, 6)
    "D100" → (1, 100)
    無效格式 → None

    Args:
        dice_str: 骰子字串，如 "D20"、"2D6"

    Returns:
        (骰子數量, 面數) 或 None
    """
    match = _re.match(r"^(\d*)D(\d+)$", dice_str.strip(), _re.IGNORECASE)
    if not match:
        return None
    count = int(match.group(1)) if match.group(1) else 1
    faces = int(match.group(2))
    return (count, faces)


def roll_dice(dice_str: str) -> tuple[int, int, int]:
    """### 擲骰並回傳 (roll_value, dice_count, dice_faces)

    若 dice_str 格式無效，回傳 (random 1-20, 1, 20) 作為最終 fallback。

    Args:
        dice_str: 骰子字串，如 "D20"、"3D6"

    Returns:
        (擲骰結果, 骰子數量, 面數)
    """
    parsed = parse_dice_string(dice_str)
    if parsed is None:
        return (random.randint(1, 20), 1, 20)

    count, faces = parsed
    if count <= 1:
        return (random.randint(1, faces), count, faces)

    rolls = [random.randint(1, faces) for _ in range(count)]
    return (sum(rolls), count, faces)


def calc_dice_range(count: int, faces: int) -> tuple[int, int]:
    """### 計算骰子點數範圍

    Args:
        count: 骰子數量
        faces: 骰子面數

    Returns:
        (最小值, 最大值)
    """
    return (count * 1, count * faces)


def judge_result(roll: int, dice_count: int, dice_faces: int) -> str:
    """### 根據骰子範圍比例計算成敗

    通用於任何骰子類型 — 完全無 D20 硬編碼：
    - roll == dice_max → 大成功
    - roll >= min + range * 75% → 成功
    - roll >= min + range * 50% → 普通
    - roll > dice_min → 失敗
    - roll == dice_min → 大失敗

    Args:
        roll: 實際擲骰結果
        dice_count: 骰子數量
        dice_faces: 骰子面數

    Returns:
        "大成功" | "成功" | "普通" | "失敗" | "大失敗"
    """
    dice_min = dice_count * 1
    dice_max = dice_count * dice_faces
    dice_range = dice_max - dice_min

    if roll == dice_max:
        return "大成功"
    if roll >= dice_min + dice_range * 0.75:
        return "成功"
    if roll >= dice_min + dice_range * 0.5:
        return "普通"
    if roll > dice_min:
        return "失敗"
    return "大失敗"


def format_dice_rules_for_prompt(dice_rules: dict[str, Any]) -> str:
    """### 把骰子規則格式化成 AI GM prompt 用的可讀文字

    輸出範例：
    D20 檢定為主，特定情況使用 2D6
    - 戰鬥檢定（骰子: 1D20、2D6，規則: D20 判定成敗，2D6 判定傷害）
    - 說服交涉（骰子: 1D20，規則: 大成功/大失敗條件）

    Args:
        dice_rules: normalize_dice_rules 輸出

    Returns:
        格式化的規則文字
    """
    desc_parts = [dice_rules.get("description", "")]
    for rule in dice_rules.get("rules", []):
        dices_str = "、".join(
            f"{d.get('count', 1)}D{d.get('range', '1-20').split('-')[1]}"
            for d in rule.get("dices", []) if isinstance(d, dict)
        )
        rule_desc = rule.get("description", "")
        rule_text = rule.get("rule", "")
        items = [f"骰子: {dices_str}"] if dices_str else []
        if rule_text:
            items.append(f"規則: {rule_text}")
        desc_parts.append(f"- {rule_desc}（{'，'.join(items)}）")

    result = "\n".join(desc_parts)
    return result if any(p for p in desc_parts if p) else "自訂骰子系統"


__all__ = [
    "normalize_dice_rules", "get_default_dice",
    "parse_dice_string", "roll_dice",
    "calc_dice_range", "judge_result",
    "format_dice_rules_for_prompt",
]
