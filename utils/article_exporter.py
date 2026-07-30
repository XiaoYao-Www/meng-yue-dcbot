"""
### 文章匯出模組

自動將每日知識內容匯出為 Markdown 文件，儲存至 data/article_md/{YYYYMMDD}/ 下。
每個知識單元獨立成一個 .md 檔案。
"""

import os
import re
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from config import DB_PATH

if TYPE_CHECKING:
    from database.daily_content_db import dailyContentDB as _DailyContentDB, DailyContentRow


# ── 路徑常數 ──

ARTICLES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "article_md")


# ── 輔助函數 ──

def _sanitize_filename(text: str, max_len: int = 60) -> str:
    """### 清理字串以用作檔案名稱

    移除不允許在 Windows 檔案名稱中出現的字元，並截斷過長片段。

    Args:
        text: 原始字串
        max_len: 最大長度

    Returns:
        安全的檔案名稱片段
    """
    # 移除或替換不合法字元
    sanitized = re.sub(r'[\\/:*?"<>|]', "·", text)
    # 移除前後空白與控制字元
    sanitized = sanitized.strip()
    sanitized = re.sub(r'[\x00-\x1f]', "", sanitized)
    # 壓縮連續空白
    sanitized = re.sub(r"\s+", " ", sanitized)
    # 截斷
    if len(sanitized) > max_len:
        sanitized = sanitized[:max_len].rstrip()
    return sanitized if sanitized else "untitled"


def _date_to_folder(date_str: str) -> str:
    """### 將日期字串轉為資料夾名稱 (YYYY-MM-DD → YYYYMMDD)

    Args:
        date_str: 日期字串 (YYYY-MM-DD 或 YYYYMMDD)

    Returns:
        YYYYMMDD 格式
    """
    return date_str.replace("-", "")


# ── Markdown 建構函數 ──

def build_article_md(
    section_data: Dict[str, Any],
    date_str: str,
    section_number: int,
) -> str:
    """### 建構單篇知識文章的 Markdown 內容

    格式設計：YAML frontmatter + 正文結構，包含所有原始資料不刪減。

    Args:
        section_data: 單篇文章的資料 dict（含 type, title, summary, detail, sources, credibility）
        date_str: 日期 YYYY-MM-DD
        section_number: 第幾篇 (1 或 2)

    Returns:
        Markdown 字串
    """
    sec_type = section_data.get("section_type", section_data.get(f"section{section_number}_type", ""))
    sec_title = section_data.get("section_title", section_data.get(f"section{section_number}_title", ""))
    sec_summary = section_data.get("section_summary", section_data.get(f"section{section_number}_summary", ""))
    sec_detail = section_data.get("section_detail", section_data.get(f"section{section_number}_detail", ""))
    sec_sources = section_data.get("section_sources", section_data.get(f"section{section_number}_sources", ""))
    sec_credibility = section_data.get("section_credibility", section_data.get(f"section{section_number}_credibility", ""))

    generated_at = section_data.get("generated_at", "")
    verified_at = section_data.get("verified_at", "")
    verification_notes = section_data.get("verification_notes", "")

    # ── 解析 detail 中的「知識價值」區塊 ──
    knowledge_value = ""
    detail_body = sec_detail
    kv_marker = "【知識價值】"
    if kv_marker in sec_detail:
        parts = sec_detail.split(kv_marker, 1)
        detail_body = parts[0].strip()
        knowledge_value = kv_marker + parts[1]

    lines: List[str] = []

    # ── YAML Frontmatter ──
    # lines.append("---")
    # lines.append(f'title: "{sec_title}"')
    # lines.append(f"date: {date_str}")
    # lines.append(f"category: {sec_type}")
    # lines.append(f"credibility: {sec_credibility}")
    # if generated_at:
    #     lines.append(f"generated_at: {generated_at}")
    # if verified_at:
    #     lines.append(f"verified_at: {verified_at}")
    # if verification_notes:
    #     # 避免 YAML 多行值中出現特殊字元問題
    #     notes_clean = verification_notes.replace('"', "'")
    #     lines.append(f'verification_notes: "{notes_clean}"')
    # lines.append("---")
    # lines.append("")

    # ── 標題 ──
    lines.append(f"# {sec_title}")
    lines.append("")

    # ── 摘要 ──
    if sec_summary:
        lines.append("> **摘要**")
        for para in sec_summary.split("\n"):
            lines.append(f"> {para}")
        lines.append("")

    # ── 詳細內容 ──
    lines.append("## 詳細內容")
    lines.append("")
    for para in detail_body.split("\n"):
        para = para.strip()
        if not para:
            continue
        # 小標題行（以「【」或粗體開頭或結尾為冒號）
        if para.startswith("【") or para.startswith("**") and para.endswith("**"):
            lines.append(para)
            lines.append("")
        else:
            lines.append(para)
            lines.append("")
    lines.append("")

    # ── 知識價值 ──
    if knowledge_value:
        lines.append("## 知識價值")
        lines.append("")
        for line in knowledge_value.split("\n"):
            line = line.strip()
            if line:
                lines.append(line)
                lines.append("")

    # ── 參考資料 ──
    if sec_sources:
        lines.append("## 參考資料")
        lines.append("")
        for src in sec_sources.split("\n"):
            src = src.strip()
            if src:
                # 若尚未是列表格式則加上 - 前綴
                if not src.startswith("-") and not src.startswith("*"):
                    lines.append(f"- {src}")
                else:
                    lines.append(src)
        lines.append("")

    # ── 可信度評級 ──
    lines.append(f"**📊 可信度評級：** {sec_credibility}")
    lines.append("")

    # ── 驗證資訊 ──
    if verified_at:
        lines.append("---")
        lines.append("")
        lines.append("## 驗證資訊")
        lines.append("")
        lines.append(f"**驗證時間：** {verified_at}")
        if verification_notes:
            lines.append("")
            lines.append(f"{verification_notes}")
        lines.append("")

    # ── 免責聲明 ──
    lines.append("")
    lines.append("> ⚠️ 內容由 AI 生成並經自動驗證，請自行斟酌參考。")

    return "\n".join(lines)


def save_article_md(
    section_data: Dict[str, Any],
    date_str: str,
    section_number: int,
    articles_dir: str = ARTICLES_DIR,
) -> Optional[str]:
    """### 將單篇知識文章儲存為 .md 檔案

    檔案路徑：data/article_md/{YYYYMMDD}/{分類}-{標題}.md

    Args:
        section_data: 單篇文章的資料 dict
        date_str: 日期 YYYY-MM-DD
        section_number: 第幾篇 (1 或 2)
        articles_dir: 文章根目錄

    Returns:
        成功時回傳檔案路徑，失敗回傳 None
    """
    sec_type = section_data.get("section_type", section_data.get(f"section{section_number}_type", "unknown"))
    sec_title = section_data.get("section_title", section_data.get(f"section{section_number}_title", "untitled"))

    # 建立日期子資料夾
    folder_name = _date_to_folder(date_str)
    folder_path = os.path.join(articles_dir, folder_name)
    os.makedirs(folder_path, exist_ok=True)

    # 安全檔案名稱
    safe_type = _sanitize_filename(sec_type)
    safe_title = _sanitize_filename(sec_title)
    filename = f"{safe_type}-{safe_title}.md"
    filepath = os.path.join(folder_path, filename)

    # 建構並寫入 Markdown
    md_content = build_article_md(section_data, date_str, section_number)
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(md_content)
        print(f"📝 已匯出文章: {filepath}")
        return filepath
    except Exception as e:
        print(f"❌ 匯出文章失敗 {filepath}: {e}")
        return None


# ── 歷史批量匯出 ──

async def export_all_history(
    db: Any = None,
    articles_dir: str = ARTICLES_DIR,
) -> int:
    # 延遲匯入資料庫（避免無 aiosqlite 環境下無法匯入本模組）
    if db is None:
        from database.daily_content_db import dailyContentDB
        db = dailyContentDB
    """### 匯出所有歷史每日內容為 .md 檔案

    遍歷資料庫中所有已儲存的每日內容，將每篇 section1 與 section2
    分別匯出為獨立的 .md 檔案。若檔案已存在則跳過。

    Args:
        db: 每日內容資料庫實例
        articles_dir: 文章根目錄

    Returns:
        成功匯出的檔案數量
    """
    try:
        rows = await db.get_all_contents()
    except Exception as e:
        print(f"❌ 讀取資料庫失敗: {e}")
        return 0

    if not rows:
        print("📭 資料庫中無歷史內容可匯出")
        return 0

    count = 0
    for row in rows:
        date_str = row["date"]

        # Section 1
        s1_data = {
            "section_type": row["section1_type"],
            "section_title": row["section1_title"],
            "section_summary": row["section1_summary"],
            "section_detail": row["section1_detail"],
            "section_sources": row["section1_sources"],
            "section_credibility": row["section1_credibility"],
            "generated_at": row["generated_at"],
            "verified_at": row.get("verified_at", ""),
            "verification_notes": row.get("verification_notes", ""),
        }
        s1_path = save_article_md(s1_data, date_str, 1, articles_dir)
        if s1_path:
            count += 1

        # Section 2
        s2_data = {
            "section_type": row["section2_type"],
            "section_title": row["section2_title"],
            "section_summary": row["section2_summary"],
            "section_detail": row["section2_detail"],
            "section_sources": row["section2_sources"],
            "section_credibility": row["section2_credibility"],
            "generated_at": row["generated_at"],
            "verified_at": row.get("verified_at", ""),
            "verification_notes": row.get("verification_notes", ""),
        }
        s2_path = save_article_md(s2_data, date_str, 2, articles_dir)
        if s2_path:
            count += 1

    print(f"📚 歷史文章匯出完成，共 {count} 篇")
    return count
