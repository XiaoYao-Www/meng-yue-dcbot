import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from discord import Embed, Color
from discord.ext import commands, tasks

from config import (
    TZ, DAILY_CHANNEL, DEEPSEEK_API_KEY, DAILY_MESSAGE_TIME,
    DAILY_AI_MAX_RETRIES, DAILY_AI_RETRY_BASE_DELAY,
    DAILY_GENERATION_PROFILE, DAILY_VERIFICATION_PROFILES,
    DAILY_GENERATION_MAX_TOKENS, DAILY_VERIFICATION_MAX_TOKENS,
    DAILY_VERIFY_MAX_RETRIES, DAILY_VERIFY_RETRY_BASE_DELAY,
    DAILY_SINGLE_SECTION_GENERATION_PROMPT_TEMPLATE,
    DAILY_SINGLE_SECTION_VERIFICATION_PROMPT_TEMPLATE,
    DAILY_ARTICLES_PER_DAY,
)
from database.daily_content_db import dailyContentDB
from utils.ai_client import DeepSeekClient
from utils.article_exporter import save_article_md


def _strip_embed_unsafe_markdown(text: str) -> str:
    """### 剝離 Embed 不支援的 Markdown 語法

    保留粗體（**）等 Embed 可渲染格式；移除標題行（#）、分隔線（---）、程式碼塊，
    引用（>）移除前綴。防範 AI 未遵守「summary/quick_learn 僅限粗體」規範時造成排版錯亂。

    Args:
        text: 原始文字

    Returns:
        剝離後的文字
    """
    lines: List[str] = []
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#") or stripped.startswith("---"):
            continue  # 標題與分隔線：整行移除
        if stripped.startswith(">"):
            stripped = stripped.lstrip(">").strip()  # 引用：移除 > 前綴
        lines.append(stripped)
    return "\n".join(lines)


def build_daily_embed(contents: List[Dict[str, Any]]) -> Embed:
    """### 建置每日知識 Embed（頻道簡述版本，供 event 與 command 共用）

    一篇一個 field，同一天多篇依序顯示。

    Args:
        contents: list[dict]，該日全部文章（每篇含 date/section_type/section_title/
                  section_summary/section_quick_learn/section_credibility/verified_at...）

    Returns:
        Embed
    """
    date_str = contents[0]["date"] if contents else ""
    embed = Embed(
        title=f"每日知識 — {date_str}",
        color=Color.blue(),
    )

    for content in contents:
        field = f"**{content['section_title']}**\n"
        field += f"{_strip_embed_unsafe_markdown(content['section_summary'])}\n"
        if content.get("section_quick_learn"):
            field += f"快速學習：{_strip_embed_unsafe_markdown(content['section_quick_learn'])}\n"
        field += f"可信度：{content['section_credibility']}"
        if content.get("verified_at"):
            field += f"\n驗證時間：{content['verified_at']}"
        embed.add_field(
            name=f"# {content['section_type']}",
            value=field,
            inline=False,
        )

    # 時間 footer
    hour_str = f"{DAILY_MESSAGE_TIME.hour:02d}:{DAILY_MESSAGE_TIME.minute:02d}"
    embed.set_footer(text=f"每日 {hour_str} 更新 | 詳細資料請見下方討論串")
    return embed


def build_detail_content(content: Dict[str, Any]) -> str:
    """### 建置單篇文章的詳細資料文字（討論串用）

    Args:
        content: dict，單篇文章內容

    Returns:
        str: 格式化詳細文字
    """
    lines = [
        f"# {content['section_type']}：{content['section_title']}",
        "",
        "## 快速學習",
        "",
        content.get("section_quick_learn", ""),
        "",
        "## 詳細內容",
        "",
        content["section_detail"],
        "",
        "**參考資料／出處**",
        content["section_sources"],
        "",
        f"**可信度評級：** {content['section_credibility']}",
        "",
    ]

    if content.get("verified_at"):
        lines.extend([
            "## 驗證資訊",
            f"驗證時間：{content['verified_at']}",
            content.get("verification_notes", ""),
            "",
        ])

    lines.append("> 內容由 AI 生成並經自動驗證，請自行斟酌參考。")

    return "\n".join(lines)


class DailyMessageEvent(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._ai = DeepSeekClient(DEEPSEEK_API_KEY) if DEEPSEEK_API_KEY else None
        self.daily_message_task.start()

    def cog_unload(self):
        """### 卸載插件
        """
        self.daily_message_task.cancel()
        if self._ai:
            self._ai.close()

    @staticmethod
    def _build_single_section_prompt(
        existing_history: List[Dict[str, Any]],
        forbidden_topics: str = "",
    ) -> str:
        """### 建構單篇生成 Prompt

        單篇生成：每次只生成一篇，透過注入「當日禁止主題」避免同天主題類似。

        Args:
            existing_history: 更早歷史內容清單（不含當日，避免與歷史重複）
            forbidden_topics: 當日已生成主題清單（注入為禁止主題）

        Returns:
            str: prompt
        """
        # 建立歷史標題摘要
        history_lines: List[str] = []
        for row in existing_history:
            history_lines.append(
                f'- [{row["section_type"]}]「{row["section_title"]}」'
            )

        history_block = "\n".join(history_lines) if history_lines else "（尚無歷史內容）"
        forbidden_block = forbidden_topics if forbidden_topics else "（本日尚無已生成主題）"

        return DAILY_SINGLE_SECTION_GENERATION_PROMPT_TEMPLATE.format(
            history_block=history_block,
            forbidden_topics=forbidden_block,
        )

    @staticmethod
    def _build_single_verification_prompt(
        section_type: str,
        section_title: str,
        section_summary: str,
        section_detail: str,
        section_sources: str,
    ) -> str:
        """### 建構單篇驗證 Prompt

        Args:
            section_type: 文章領域
            section_title: 標題
            section_summary: 摘要
            section_detail: 詳細內容
            section_sources: 參考資料

        Returns:
            str: 驗證 prompt
        """
        return DAILY_SINGLE_SECTION_VERIFICATION_PROMPT_TEMPLATE.format(
            section_type=section_type,
            section_title=section_title,
            section_summary=section_summary,
            section_detail=section_detail,
            section_sources=section_sources,
        )

    async def _generate_section(
        self,
        existing_history: List[Dict[str, Any]],
        forbidden_topics: str = "",
    ) -> Optional[Dict[str, str]]:
        """### 生成單篇知識內容

        Args:
            existing_history: 歷史內容清單（不含當日）
            forbidden_topics: 當日已生成主題（注入為禁止主題）

        Returns:
            單篇內容 dict（含 section_quick_learn）或 None
        """
        if self._ai is None:
            print("[DailyMessage] DEEPSEEK_API_KEY 未設定，無法呼叫 AI")
            return None

        prompt = self._build_single_section_prompt(existing_history, forbidden_topics)
        result = await self._ai.call(
            prompt,
            profile_name=DAILY_GENERATION_PROFILE,
            max_tokens=DAILY_GENERATION_MAX_TOKENS,
            use_json_mode=False,
        )

        if result is None:
            print("[DailyMessage] 單篇生成失敗（API/解析錯誤）")
            return None

        # 驗證必要欄位（含快速學習）
        required = ["section_type", "section_title", "section_summary", "section_detail", "section_quick_learn", "section_sources"]
        for key in required:
            if key not in result or not result[key]:
                print(f"[DailyMessage] 生成缺少必要欄位: {key}")
                return None

        merged = {
            "section_type": result["section_type"],
            "section_title": result["section_title"],
            "section_summary": result["section_summary"],
            "section_detail": result["section_detail"],
            "section_quick_learn": result["section_quick_learn"],
            "section_sources": result["section_sources"],
        }

        print(f"[DailyMessage] 單篇生成成功 ({merged['section_type']})")
        return merged

    async def _verify_with_profile(
        self,
        content: Dict[str, Any],
        profile_name: str,
    ) -> Optional[Dict[str, str]]:
        """### 以指定配置驗證單篇文章

        Args:
            content: 已生成的單篇內容 dict
            profile_name: AI_PROFILES 中的配置名稱

        Returns:
            驗證結果 dict（verification / credibility / evidence）或 None（API/解析失敗）
        """
        if self._ai is None:
            print("[DailyMessage] DEEPSEEK_API_KEY 未設定，無法呼叫 AI")
            return None

        prompt = self._build_single_verification_prompt(
            content["section_type"],
            content["section_title"],
            content["section_summary"],
            content["section_detail"],
            content["section_sources"],
        )
        v = await self._ai.call(
            prompt,
            profile_name=profile_name,
            max_tokens=DAILY_VERIFICATION_MAX_TOKENS,
            use_json_mode=False,
        )

        if v is None:
            print(f"[DailyMessage] 驗證失敗（API/解析錯誤，配置={profile_name}）")
            return None

        v_required = ["verification", "credibility", "evidence"]
        for key in v_required:
            if key not in v or not v[key]:
                print(f"[DailyMessage] 驗證缺少必要欄位: {key}（配置={profile_name}）")
                return None

        print(f"[DailyMessage] 驗證完成（配置={profile_name}）: {v['verification']}(可信度{v['credibility']})")
        return v

    async def _verify_section(self, content: Dict[str, Any]) -> Tuple[Optional[Dict[str, str]], str]:
        """### 兩段式驗證單篇文章（依 DAILY_VERIFICATION_PROFILES 依序嘗試、命中即短路）

        規則：
        - 依序嘗試每個驗證配置；任一配置判定「通過/有疑慮」即接受並返回 (結果, "accepted")，
          不再嘗試後續配置（不是每篇都跑兩次驗證）。
        - 僅當前一配置判定「不通過」時，才嘗試下一個配置。
        - 所有配置皆判定「不通過」→ (None, "rejected")（宣告文章失敗，觸發重新生成）。
        - 所有配置皆 API 失敗（重試耗盡）→ (None, "error")（重試驗證或降級）。

        Args:
            content: 已生成的單篇內容 dict

        Returns:
            (驗證結果 dict, 狀態)；狀態 ∈ {"accepted", "rejected", "error"}
        """
        last_profile = DAILY_VERIFICATION_PROFILES[-1]

        for profile_name in DAILY_VERIFICATION_PROFILES:
            v = await self._verify_with_profile(content, profile_name)
            if v is None:
                # 該配置 API 失敗：僅最後一個配置失敗時宣告 error，否則換下一個配置
                if profile_name == last_profile:
                    return None, "error"
                continue

            if v["verification"] != "不通過":
                # 通過 / 有疑慮：直接接受，短路（不再呼叫後續配置）
                return v, "accepted"

            # 判定「不通過」→ 嘗試下一個配置
            print(f"[DailyMessage] 配置「{profile_name}」判定不通過，嘗試下一個配置")

        # 所有配置皆判定「不通過」：宣告文章失敗
        return None, "rejected"

    async def _generate_and_verify_article(
        self,
        existing_history: List[Dict[str, Any]],
        forbidden_topics: str = "",
    ) -> Optional[Dict[str, str]]:
        """### 生成 + 兩段式驗證單篇文章（獨立重試循環）

        生成失敗重試（DAILY_AI_MAX_RETRIES）。
        驗證依 DAILY_VERIFICATION_PROFILES 依序嘗試：配置1 通過即接受（短路），
        不通過才試配置2；全部不通過（rejected）宣告文章失敗並回到生成階段重新生成；
        全部 API 失敗（error）重試驗證，耗盡則降級返回未驗證版本。

        Args:
            existing_history: 歷史內容清單（不含當日）
            forbidden_topics: 當日已生成主題（注入為禁止主題）

        Returns:
            完整文章 dict（含 section_credibility / verified_at / verification_notes）或 None
        """
        gen_max = DAILY_AI_MAX_RETRIES
        ver_max = DAILY_VERIFY_MAX_RETRIES

        for gen_attempt in range(1, gen_max + 1):
            generated = await self._generate_section(existing_history, forbidden_topics)
            if generated is None:
                print(f"[DailyMessage] 生成嘗試 {gen_attempt}/{gen_max} 失敗（API/解析錯誤）")
                if gen_attempt < gen_max:
                    await asyncio.sleep(DAILY_AI_RETRY_BASE_DELAY * (2 ** (gen_attempt - 1)))
                continue

            rejected = False
            for ver_attempt in range(1, ver_max + 1):
                verification, status = await self._verify_section(generated)

                if status == "accepted":
                    # 驗證通過（通過/有疑慮均可接受）
                    now_str = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
                    return {
                        **generated,
                        "section_credibility": verification["credibility"],
                        "verified_at": now_str,
                        "verification_notes": f"{verification['verification']}(可信度{verification['credibility']})",
                    }

                if status == "rejected":
                    # 兩段式皆判定不通過：宣告文章失敗，回到生成階段重新生成
                    print(f"[DailyMessage] 兩段式驗證皆判定不通過（第 {gen_attempt}/{gen_max} 次生成），回到生成階段")
                    rejected = True
                    break  # 跳出驗證循環，回到生成循環

                # status == "error"：全部配置 API 失敗，重試驗證（不重新生成）
                print(f"[DailyMessage] 驗證嘗試 {ver_attempt}/{ver_max} 失敗（API/解析錯誤），重試驗證")
                if ver_attempt < ver_max:
                    await asyncio.sleep(DAILY_VERIFY_RETRY_BASE_DELAY * (2 ** (ver_attempt - 1)))

            # rejected → 回到生成循環（重新生成）；error 且重試耗盡 → 降級未驗證
            if rejected:
                continue

            # 驗證全部重試失敗（error）：降級使用未驗證內容
            print("[DailyMessage] 驗證階段全部失敗，使用未驗證內容")
            now_str = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
            return {
                **generated,
                "section_credibility": "未驗證",
                "verified_at": "",
                "verification_notes": "（驗證失敗，請自行查證）",
            }

        return None

    @tasks.loop(time=DAILY_MESSAGE_TIME)
    async def daily_message_task(self):
        """### 每日訊息任務

        檢查當日已入庫篇數 → 未達 DAILY_ARTICLES_PER_DAY 則逐篇「生成→驗證→入庫」，
        並將當日已生成主題注入為禁止主題（避免同天主題類似）→ 齊全後發送 Embed + 討論串。
        重啟／補跑時自動只補缺的篇數。
        """
        now = datetime.now(TZ)
        date_str = now.strftime("%Y-%m-%d")
        print(f"--- 每日訊息任務開始 ({date_str}) ---")

        try:
            # 1. 檢查當日已入庫篇數
            today_articles = await dailyContentDB.get_daily_contents(date_str)
            if len(today_articles) >= DAILY_ARTICLES_PER_DAY:
                print(f"[DailyMessage] 今日 ({date_str}) 已有 {len(today_articles)} 篇，跳過生成")
                return

            # 2. 歷史內容（不含當日），供避免重複
            all_contents = await dailyContentDB.get_all_contents()
            history = [dict(row) for row in all_contents if row["date"] != date_str]

            # 3. 逐篇生成：注入當日已生成主題為禁止主題
            for i in range(len(today_articles), DAILY_ARTICLES_PER_DAY):
                forbidden_topics = "\n".join(
                    f'- [{a["section_type"]}]「{a["section_title"]}」' for a in today_articles
                )
                article = await self._generate_and_verify_article(history, forbidden_topics)
                if article is None:
                    print(f"[DailyMessage] 第 {i + 1} 篇生成失敗（重試耗盡），停止今日生成")
                    break

                now_str = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
                await dailyContentDB.set_daily_content(
                    date=date_str,
                    section_type=article["section_type"],
                    section_title=article["section_title"],
                    section_summary=article["section_summary"],
                    section_detail=article["section_detail"],
                    section_quick_learn=article["section_quick_learn"],
                    section_sources=article["section_sources"],
                    section_credibility=article["section_credibility"],
                    generated_at=now_str,
                    verified_at=article["verified_at"],
                    verification_notes=article["verification_notes"],
                )
                print(f"[DailyMessage] 已儲存第 {i + 1} 篇：{article['section_title']}")

                # 重新讀取當日文章，供下一輪注入禁止主題
                today_articles = await dailyContentDB.get_daily_contents(date_str)

            # 4. 發送當日全部文章
            today_articles = await dailyContentDB.get_daily_contents(date_str)
            if not today_articles:
                print("[DailyMessage] 今日無任何文章，不發送")
                return
            await self._send_daily(date_str, [dict(row) for row in today_articles])

        except Exception as e:
            print(f"[DailyMessage] 每日訊息任務錯誤: {e}")

    async def _send_daily(self, date_str: str, articles: List[Dict[str, Any]]) -> None:
        """### 發送當日文章：Markdown 匯出 ＋ Embed（頻道）＋ 討論串（詳細資料）

        Args:
            date_str: 日期 YYYY-MM-DD
            articles: 當日全部文章 dict 清單
        """
        # 1. 匯出 Markdown 文章（每篇獨立 .md 檔案）
        for idx, article in enumerate(articles, 1):
            save_article_md(article, date_str, idx)

        # 2. 發送到頻道
        if not DAILY_CHANNEL:
            print("[DailyMessage] DAILY_CHANNEL 未設定，無法發送")
            return

        channel = self.bot.get_channel(DAILY_CHANNEL)
        if channel is None:
            print(f"[DailyMessage] 無法取得頻道 ID {DAILY_CHANNEL}")
            return

        embed = build_daily_embed(articles)
        message = await channel.send(embed=embed)
        print(f"[DailyMessage] 已發送每日訊息到頻道 {DAILY_CHANNEL}")

        # 3. 建立討論串並貼上詳細資料
        try:
            thread = await message.create_thread(
                name=f"詳細資料 — {date_str}",
                auto_archive_duration=1440,  # 24 小時後自動封存
            )

            for article in articles:
                detail_text = build_detail_content(article)
                for part in self._split_long_text(detail_text):
                    await thread.send(part)

            print("[DailyMessage] 已建立討論串並張貼詳細資料")
        except Exception as e:
            print(f"[DailyMessage] 建立討論串失敗: {e}")

    @staticmethod
    def _split_long_text(text: str, max_len: int = 1900) -> List[str]:
        """### 將長文字分段（Discord 訊息上限 2000 字元）

        Args:
            text: 原始文字
            max_len: 單段最大長度

        Returns:
            分段後的清單
        """
        if len(text) <= max_len:
            return [text]

        parts: List[str] = []
        current: List[str] = []
        current_len = 0
        for line in text.split("\n"):
            line_len = len(line) + 1  # +1 for newline
            if current_len + line_len > max_len and current:
                parts.append("\n".join(current))
                current = [line]
                current_len = line_len
            else:
                current.append(line)
                current_len += line_len
        if current:
            parts.append("\n".join(current))
        return parts

    @daily_message_task.before_loop
    async def before_daily_message_task(self):
        """巡迴前檢查
        """
        await self.bot.wait_until_ready()

    async def _startup_check(self) -> None:
        """### 啟動後立即檢查：若今天篇數不足則觸發一次

        解決 tasks.loop(time=...) 在啟動時間已過指定時刻時，
        要等到隔天才觸發的問題。
        """
        await self.bot.wait_until_ready()
        await asyncio.sleep(1)
        await self.daily_message_task()


async def setup(bot: commands.Bot):
    cog = DailyMessageEvent(bot)
    await bot.add_cog(cog)
    # 啟動後立即檢查今天是否需發送
    bot.loop.create_task(cog._startup_check())
