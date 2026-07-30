"""
驗證文章匯出模組功能：測試 build_article_md 與 save_article_md。
"""
import os
import sys
import tempfile

# 測試前先設定環境變數（避免 config.py 檢查失敗）
os.environ.setdefault("DB_PATH", "data/db")
os.environ.setdefault("DISCORD_TOKEN", "test_discord_token")
os.environ.setdefault("GUILD_ID", "0")
os.environ.setdefault("DAILY_CHANNEL", "0")

# 確保專案根目錄在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.article_exporter import build_article_md, save_article_md, _sanitize_filename, _date_to_folder

# ── 測試資料 ──
MOCK_SECTION = {
    "section_type": "認知心理學",
    "section_title": "認知失調理論：當行為與信念衝突時",
    "section_summary": "認知失調理論（Cognitive Dissonance Theory）由 Leon Festinger 於 1957 年提出，\n說明當個人同時持有兩種相互矛盾的心理認知（想法、信念、態度或行為）時，\n會產生一種心理上的不適感，驅使個體改變其中一項認知以減輕衝突。",
    "section_detail": "【背景】\\n認知失調理論是社會心理學中最重要的動機理論之一。\\n\\n【核心內容】\\nFestinger 認為人類有追求內在一致性的基本需求。\\n\\n【知識價值】\\n小說創作：★★★★★\\n心理分析：★★★★★",
    "section_sources": "Festinger, L. (1957). A Theory of Cognitive Dissonance. Stanford University Press.\\nTavris, C., & Aronson, E. (2007). Mistakes Were Made (But Not by Me). Harcourt.",
    "section_credibility": "高",
    "generated_at": "2026-07-31 08:00:00",
    "verified_at": "2026-07-31 08:05:00",
    "verification_notes": "第一則: 通過(可信度高); 第二則: 通過(可信度高)",
}

MOCK_DATE = "2026-07-31"


def test_sanitize_filename():
    """測試檔案名稱清理"""
    assert _sanitize_filename("正常標題") == "正常標題"
    assert "?" not in _sanitize_filename("不允許的?字元*:測試")
    assert "·" in _sanitize_filename("a/b/c")  # / 被替換為 ·
    assert len(_sanitize_filename("A" * 100)) <= 60
    print("✅ _sanitize_filename 測試通過")


def test_date_to_folder():
    """測試日期轉資料夾名稱"""
    assert _date_to_folder("2026-07-31") == "20260731"
    assert _date_to_folder("20260801") == "20260801"
    print("✅ _date_to_folder 測試通過")


def test_build_article_md():
    """測試 Markdown 建構"""
    md = build_article_md(MOCK_SECTION, MOCK_DATE, 1)

    # 檢查必備段落
    assert "cognitive" in md.lower() or "認知" in md, "應包含標題內容"
    assert "---" in md, "應包含 YAML frontmatter"
    assert "title:" in md, "frontmatter 應有 title"
    assert "date:" in md, "frontmatter 應有 date"
    assert "category:" in md, "frontmatter 應有 category"
    assert "credibility:" in md, "frontmatter 應有 credibility"
    assert "## 詳細內容" in md, "應有詳細內容小節"
    assert "## 參考資料" in md, "應有參考資料小節"
    assert "Festinger" in md, "應包含參考資料內容"
    assert "## 知識價值" in md, "應有知識價值小節"
    assert "可信度評級" in md, "應有可信度資訊"
    assert "## 驗證資訊" in md, "應有驗證資訊小節"
    assert "AI 生成" in md, "應有免責聲明"

    # 摘要應為引言區塊
    assert "> **摘要**" in md, "摘要應為 blockquote 格式"

    print("✅ build_article_md 測試通過")


def test_save_article_md():
    """測試檔案寫入"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        from utils.article_exporter import ARTICLES_DIR

        # 用暫存目錄覆蓋 ARTICLES_DIR
        original = os.path.join(tmp_dir, "article_md")
        result = save_article_md(MOCK_SECTION, MOCK_DATE, 1, articles_dir=original)

        assert result is not None, "應成功回傳檔案路徑"
        assert os.path.exists(result), "檔案應存在於磁碟"
        assert "認知心理學" in result, "路徑應包含分類"
        assert "20260731" in result, "路徑應包含日期資料夾"

        # 檢查內容完整
        with open(result, "r", encoding="utf-8") as f:
            content = f.read()
        assert "Festinger" in content
        assert "---" in content

        print(f"✅ save_article_md 測試通過 ({result})")


if __name__ == "__main__":
    test_sanitize_filename()
    test_date_to_folder()
    test_build_article_md()
    test_save_article_md()
    print("🎉 所有測試通過！")
