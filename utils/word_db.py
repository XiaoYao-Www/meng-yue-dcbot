import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional, Set

from discord import Color, Embed
from utils.word_list import HIGH_FREQUENCY_WORDS

# 預設資料庫路徑：指向 meng_yue 專案下的 assets 資料庫
WORD_DB_PATH = Path(__file__).resolve().parent.parent / "assets" / "en_word_release_v0.db"

# 詞性代號中英對照
POS_MAP = {
    "n": "名詞 (n.)",
    "v": "動詞 (v.)",
    "a": "形容詞 (adj.)",
    "s": "形容詞 (adj.)",
    "r": "副詞 (adv.)",
}


def _get_connection() -> sqlite3.Connection:
    """取得資料庫連線，並設定 utf-8 解碼以支援繁體中文"""
    conn = sqlite3.connect(str(WORD_DB_PATH))
    conn.text_factory = lambda b: b.decode("utf-8", errors="ignore")
    return conn


def query_word(word: str) -> Optional[Dict[str, Any]]:
    """### 依照 lookup.py 模式精確查詢單字詳細資料
    
    包含詞性、翻譯、中英定義、以及例句。
    
    Args:
        word: 欲查詢的英文單字（大小寫不拘）
        
    Returns:
        結構化的單字資料 dict，若無此單字則回傳 None
    """
    if not WORD_DB_PATH.exists():
        return None

    clean_word = word.strip().lower()
    conn = _get_connection()

    try:
        # 1. 查詢單字、語義 ID、翻譯、詞性、定義
        rows = conn.execute(
            """
            SELECT
                s.id AS semantics_id,
                w.word,
                w.translate,
                p.value AS pos,
                d.en AS def_en,
                d.zh AS def_zh
            FROM word w
            JOIN semantics_id s
                ON s.id = w.semantics_id
            LEFT JOIN part_of_speech p
                ON p.semantics_id = s.id
            LEFT JOIN definition d
                ON d.semantics_id = s.id
            WHERE w.word = ?
            ORDER BY s.id, d.id
            """,
            (clean_word,),
        ).fetchall()

        if not rows:
            return None

        # 整理 semantics 結構
        semantics_map: Dict[int, Dict[str, Any]] = {}
        for sem_id, w_val, translate, pos, def_en, def_zh in rows:
            if sem_id not in semantics_map:
                semantics_map[sem_id] = {
                    "semantics_id": sem_id,
                    "word": w_val,
                    "translate": translate or "",
                    "pos": POS_MAP.get(pos, pos or "其他"),
                    "definitions": [],
                    "examples": [],
                }
            if def_en or def_zh:
                semantics_map[sem_id]["definitions"].append({
                    "en": def_en or "",
                    "zh": def_zh or "",
                })

        # 2. 補充例句
        sem_ids = list(semantics_map.keys())
        if sem_ids:
            placeholders = ",".join("?" for _ in sem_ids)
            eg_rows = conn.execute(
                f"""
                SELECT semantics_id, en, zh
                FROM example
                WHERE semantics_id IN ({placeholders})
                ORDER BY id
                """,
                sem_ids,
            ).fetchall()

            for sem_id, eg_en, eg_zh in eg_rows:
                if sem_id in semantics_map:
                    semantics_map[sem_id]["examples"].append({
                        "en": eg_en or "",
                        "zh": eg_zh or "",
                    })

        senses = list(semantics_map.values())
        return {
            "word": clean_word,
            "senses": senses,
        }

    finally:
        conn.close()


def pick_daily_word(exclude_words: Optional[Set[str]] = None, date_str: str = "") -> Optional[Dict[str, Any]]:
    """### 從高頻常用單字庫中挑選一個今日單字
    
    使用日期作為種子計算固定索引，確保同一天取得的單字一致，
    且自動排除已發送過的單字。
    
    Args:
        exclude_words: 欲排除（已發布）的單字集合
        date_str: 日期字串（例如 "2026-09-26"）
        
    Returns:
        完整的單字資訊 dict，若皆無可用詞則回傳 None
    """
    exclude = {w.lower() for w in (exclude_words or set())}
    total_words = len(HIGH_FREQUENCY_WORDS)

    # 依日期產生確定性哈希種子
    seed_str = date_str or "default_seed"
    seed_hash = int(hashlib.md5(seed_str.encode("utf-8")).hexdigest(), 16)
    start_index = seed_hash % total_words

    # 直接依種子索引巡迴，避免每次建立複製一份 2500+ 單字的大清單
    for i in range(total_words):
        candidate = HIGH_FREQUENCY_WORDS[(start_index + i) % total_words]
        if candidate.lower() in exclude:
            continue
        details = query_word(candidate)
        if details and details.get("senses"):
            return details

    # 若全數已用過（極長週期後），隨機選取未在當日重複的第一個
    for i in range(total_words):
        candidate = HIGH_FREQUENCY_WORDS[(start_index + i) % total_words]
        details = query_word(candidate)
        if details and details.get("senses"):
            return details

    return None


def build_word_embed(word_data: Dict[str, Any], daily_time_str: str = "08:00") -> Embed:
    """### 建置每日單字簡明 Embed 卡片"""
    word = word_data["word"]
    senses = word_data.get("senses", [])

    embed = Embed(
        title=f"🔤 每日單字 — {word}",
        color=Color.gold(),
    )

    # 匯總常見釋義
    summary_parts = []
    for s in senses[:3]:
        pos = s.get("pos", "")
        trans = s.get("translate", "")
        if trans:
            summary_parts.append(f"{pos} {trans}")

    if summary_parts:
        embed.description = f"**常用釋義：** {' ｜ '.join(summary_parts)}"

    # 主要詞義展示（最多顯示前 2 個 semantics 避免 Embed 過長）
    for idx, s in enumerate(senses[:2], 1):
        field_name = f"詞義 {idx}：{s.get('translate', '')} ({s.get('pos', '')})"
        lines = []
        defs = s.get("definitions", [])
        if defs:
            d = defs[0]
            if d.get("zh"):
                lines.append(f"📖 **定義：** {d['zh']}")
            if d.get("en"):
                lines.append(f"　 *{d['en']}*")

        egs = s.get("examples", [])
        if egs:
            e = egs[0]
            if e.get("en"):
                lines.append(f"💬 **例句：** {e['en']}")
            if e.get("zh"):
                lines.append(f"　 {e['zh']}")

        if lines:
            embed.add_field(name=field_name, value="\n".join(lines), inline=False)

    if len(senses) > 2:
        embed.add_field(
            name="💡 更多內容",
            value=f"本單字共有 {len(senses)} 種詳細詞義與例句，請見下方討論串！",
            inline=False,
        )

    embed.set_footer(text=f"每日 {daily_time_str} 更新 | 詳細詞義與例句請見下方討論串")
    return embed


def build_word_detail_content(word_data: Dict[str, Any]) -> str:
    """### 建置單字詳細討論串內容（Markdown 格式）"""
    word = word_data["word"]
    senses = word_data.get("senses", [])

    lines = [
        f"# 🔤 單字學習詳解：{word}",
        "",
        "> 本單字精選自高頻核心詞庫，詳細資料摘自權威詞典資料庫。",
        "",
    ]

    for idx, s in enumerate(senses, 1):
        pos = s.get("pos", "")
        translate = s.get("translate", "")
        lines.append(f"## 詞義 {idx}：{translate}（{pos}）")

        defs = s.get("definitions", [])
        if defs:
            lines.append("### 【中英定義】")
            for d in defs:
                if d.get("zh"):
                    lines.append(f"- **中文**：{d['zh']}")
                if d.get("en"):
                    lines.append(f"- **英文**：{d['en']}")
            lines.append("")

        egs = s.get("examples", [])
        if egs:
            lines.append("### 【實用例句】")
            for e_idx, e in enumerate(egs, 1):
                if e.get("en"):
                    lines.append(f"{e_idx}. {e['en']}")
                if e.get("zh"):
                    lines.append(f"   ↳ *{e['zh']}*")
            lines.append("")

        lines.append("---")
        lines.append("")

    return "\n".join(lines)

