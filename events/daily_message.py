import asyncio
from datetime import datetime
import json
from typing import Any, Dict, List, Optional, Tuple

from discord import Embed, Color
from discord.ext import commands, tasks

from config import (
    TZ, DAILY_CHANNEL, NEW_API_KEY, DAILY_MESSAGE_TIME,
    DAILY_AI_MAX_RETRIES, DAILY_AI_RETRY_BASE_DELAY,
    DAILY_GENERATION_PROFILE, DAILY_VERIFICATION_PROFILES,
    DAILY_GENERATION_MAX_TOKENS, DAILY_VERIFICATION_MAX_TOKENS,
    DAILY_VERIFY_MAX_RETRIES, DAILY_VERIFY_RETRY_BASE_DELAY,
    DAILY_SINGLE_SECTION_GENERATION_PROMPT_TEMPLATE,
    DAILY_SINGLE_SECTION_VERIFICATION_PROMPT_TEMPLATE,
    DAILY_ARTICLES_PER_DAY, STOCK_MIN_LIMIT, STOCK_MAX_LIMIT,
)
from database.daily_content_db import dailyContentDB
from utils.ai_client import NewApiClient, AIQuotaExceededError, AIQuotaPostponedError
from utils.article_exporter import save_article_md
from utils.word_db import (
    pick_daily_word,
    build_word_embed,
    build_word_detail_content,
)


NUM_EMOJIS: list[str] = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]


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

    for index, content in enumerate(contents, start=1):
        emoji_prefix = NUM_EMOJIS[index - 1] if index <= len(NUM_EMOJIS) else f"{index}."
        field = f"**{content['section_title']}**\n"  # 標題旁加上編號
        field += f"{_strip_embed_unsafe_markdown(content['section_summary'])}\n"
        if content.get("section_quick_learn"):
            field += f"📖 快速學習：{_strip_embed_unsafe_markdown(content['section_quick_learn'])}\n"
        field += f"可信度：{content['section_credibility']}"
        if content.get("verified_at"):
            field += f"\n🔍 驗證時間：{content['verified_at']}"
        
        embed.add_field(
            name=f"{emoji_prefix} {content['section_type']}",
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
        self._ai = NewApiClient(NEW_API_KEY) if NEW_API_KEY else None
        self._replenish_lock = asyncio.Lock()  # 庫存補充專用鎖，防止並發重複執行浪費 Token
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
            print("[DailyMessage] NEW_API_KEY 未設定，無法呼叫 AI")
            return None

        prompt = self._build_single_section_prompt(existing_history, forbidden_topics)
        result = await self._ai.call(
            prompt,
            profile_name=DAILY_GENERATION_PROFILE,
            max_tokens=DAILY_GENERATION_MAX_TOKENS,
            use_json_mode=True,
            on_quota_exceeded="postpone",
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
            print("[DailyMessage] NEW_API_KEY 未設定，無法呼叫 AI")
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
            use_json_mode=True,
            on_quota_exceeded="postpone",
        )

        if v is None:
            print(f"[DailyMessage] 驗證失敗（API/解析錯誤，配置={profile_name}）")
            return None

        v_required = ["verification", "credibility", "evidence"]
        for key in v_required:
            if key not in v or not v[key]:
                print(f"[DailyMessage] 驗證缺少必要欄位: {key}（配置={profile_name}）")
                return None

        print(f"[DailyMessage] 驗證完成（配置={profile_name}）: {v['verification']} - {v['credibility']}")
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
                    credibility_desc = verification["credibility"]
                    evidence_desc = verification.get("evidence", "")
                    ver_notes = (
                        f"**審查結論：** {verification['verification']}\n"
                        f"**評價與依據：** {credibility_desc}\n\n"
                        f"**詳細核實說明：**\n{evidence_desc}"
                    )
                    return {
                        **generated,
                        "section_credibility": credibility_desc,
                        "verified_at": now_str,
                        "verification_notes": ver_notes,
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

    async def replenish_stock(self) -> int:
        """### 背景檢查並補充庫存至 STOCK_MAX_LIMIT

        當庫存小於 STOCK_MIN_LIMIT 時觸發，計算缺口 (STOCK_MAX_LIMIT - 現有庫存)，
        逐篇執行生成 + 兩段式驗證，並存入庫存庫 (status='stock')。
        去重提示詞包含資料庫中所有已發送與庫存文章標題。
        """
        if self._replenish_lock.locked():
            print("[DailyMessage] 庫存補充任務已在執行中，跳過重複觸發")
            return 0

        async with self._replenish_lock:
            try:
                current_stock = await dailyContentDB.get_stock_count()
                if current_stock >= STOCK_MIN_LIMIT:
                    print(f"[DailyMessage] 目前庫存 {current_stock} 篇（≥ 下限 {STOCK_MIN_LIMIT} 篇），無需補充")
                    return 0

                needed = STOCK_MAX_LIMIT - current_stock
                print(f"[DailyMessage] 目前庫存 {current_stock} 篇（< 下限 {STOCK_MIN_LIMIT} 篇），開始補充 {needed} 篇至上限 {STOCK_MAX_LIMIT} 篇...")

                added = 0
                for i in range(needed):
                    # 取得全部歷史 + 庫存內容，進行 Prompt 全量去重
                    all_contents = await dailyContentDB.get_all_contents()
                    history = [dict(row) for row in all_contents]

                    article = await self._generate_and_verify_article(history, forbidden_topics="")
                    if article is None:
                        print(f"[DailyMessage] 庫存補充第 {i + 1}/{needed} 篇生成失敗（重試耗盡），中斷補充")
                        break

                    now_str = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
                    await dailyContentDB.add_stock_content(
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
                    added += 1
                    print(f"[DailyMessage] 已成功加入庫存 ({i + 1}/{needed})：{article['section_title']}")

                print(f"[DailyMessage] 庫存補充完成，成功新增 {added} 篇，現有庫存 {await dailyContentDB.get_stock_count()} 篇")
                return added
            except AIQuotaPostponedError as e:
                print(f"[DailyMessage] ⏸️ 每日 AI 用量已達上限，庫存補充任務已自動暫緩（{e}），將於隔日 00:00 恢復處理")
                return added
            except AIQuotaExceededError as e:
                print(f"[DailyMessage] ⛔ 每日 AI 用量已達上限，庫存補充任務已中斷（{e}）")
                return added
            except Exception as e:
                print(f"[DailyMessage] 庫存補充處理異常: {e}")
                return 0

    @tasks.loop(time=DAILY_MESSAGE_TIME)
    async def daily_message_task(self):
        """### 每日訊息任務（庫存消耗模式）

        1. 檢查當日已發送篇數 → 若未達 DAILY_ARTICLES_PER_DAY 則從庫存庫取出文章劃歸當日。
        2. 若庫存不足所需篇數，先同步執行庫存補充。
        3. 劃歸為當日文章後，匯出 Markdown 檔並發送 Embed 訊息與討論串。
        4. 發送成功後，發起非阻塞背景任務補充庫存至上限。
        """
        now = datetime.now(TZ)
        date_str = now.strftime("%Y-%m-%d")
        print(f"--- 每日訊息任務開始 ({date_str}) ---")

        try:
            today_articles = await dailyContentDB.get_daily_contents(date_str)
            if len(today_articles) >= DAILY_ARTICLES_PER_DAY:
                print(f"[DailyMessage] 今日 ({date_str}) 已有 {len(today_articles)} 篇已發送文章，跳過知識發送")
                # 檢查今日單字是否已發送，未發送則補發
                await self._send_daily_word(date_str)
                # 仍發起背景庫存檢查
                self.bot.loop.create_task(self.replenish_stock())
                return

            needed_count = DAILY_ARTICLES_PER_DAY - len(today_articles)

            # 檢查庫存數量，若不足則同步補充
            stock_count = await dailyContentDB.get_stock_count()
            if stock_count < needed_count:
                print(f"[DailyMessage] 庫存僅有 {stock_count} 篇，不足所需 {needed_count} 篇，先執行同步庫存補充...")
                await self.replenish_stock()

            # 從庫存取出最舊的 needed_count 篇劃歸為今日
            stock_items = await dailyContentDB.get_stock_contents(needed_count)
            if not stock_items:
                print("[DailyMessage] 庫存為空且補充失敗，今日無法發送每日訊息")
                return

            for item in stock_items:
                await dailyContentDB.publish_stock_content(item["id"], date_str)
                print(f"[DailyMessage] 已將庫存文章 [{item['section_title']}] (ID: {item['id']}) 劃歸為今日 ({date_str}) 發送")

            # 重新取得今日劃歸後的文章
            today_articles = await dailyContentDB.get_daily_contents(date_str)
            if not today_articles:
                print("[DailyMessage] 今日無劃歸文章，不發送")
                return

            # 發送當日文章至 Discord
            await self._send_daily(date_str, [dict(row) for row in today_articles])

            # 發送每日單字
            await self._send_daily_word(date_str)

            # 發送成功後，背景發起庫存補充
            self.bot.loop.create_task(self.replenish_stock())

        except Exception as e:
            print(f"[DailyMessage] 每日訊息任務錯誤: {e}")

    async def _send_daily_word(self, date_str: str) -> None:
        """### 發送每日單字：Embed（簡明卡片）＋ 討論串（詳細筆記）

        挑選高頻常用單字，直接由本機詞庫讀取，不調用 AI。
        """
        if not DAILY_CHANNEL:
            print("[DailyWord] DAILY_CHANNEL 未設定，無法發送")
            return

        channel = self.bot.get_channel(DAILY_CHANNEL)
        if channel is None:
            print(f"[DailyWord] 無法取得頻道 ID {DAILY_CHANNEL}")
            return

        # 1. 檢查今日是否已發布每日單字
        existing = await dailyContentDB.get_daily_word(date_str)
        if existing:
            print(f"[DailyWord] 今日 ({date_str}) 已發布每日單字 [{existing['word']}]，跳過發送")
            return

        # 2. 取得所有歷史已發布單字進行去重，並挑選今日高頻單字
        published_words = await dailyContentDB.get_all_published_words()
        word_data = pick_daily_word(exclude_words=published_words, date_str=date_str)
        if not word_data:
            print("[DailyWord] 無法從高頻詞庫選取可用單字")
            return

        word = word_data["word"]
        hour_str = f"{DAILY_MESSAGE_TIME.hour:02d}:{DAILY_MESSAGE_TIME.minute:02d}"

        # 3. 發送簡明 Embed 至頻道
        embed = build_word_embed(word_data, daily_time_str=hour_str)
        message = await channel.send(embed=embed)
        print(f"[DailyWord] 已發送每日單字 [{word}] 到頻道 {DAILY_CHANNEL}")

        # 4. 建立討論串並張貼詳細資料
        try:
            thread = await message.create_thread(
                name=f"單字筆記 — {word}",
                auto_archive_duration=1440,
            )
            detail_text = build_word_detail_content(word_data)
            for part in self._split_long_text(detail_text):
                await thread.send(part)
            print(f"[DailyWord] 已建立單字討論串並張貼詳細資料")
        except Exception as e:
            print(f"[DailyWord] 建立單字討論串失敗: {e}")

        # 5. 持久化至資料庫
        now_str = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
        await dailyContentDB.set_daily_word(
            date=date_str,
            word=word,
            data_json=json.dumps(word_data, ensure_ascii=False),
            published_at=now_str,
        )

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
        """### 啟動後立即檢查：若今天篇數不足則觸發一次發送，並在背景補足庫存"""
        await self.bot.wait_until_ready()
        await asyncio.sleep(1)
        await self.daily_message_task()
        # 背景補充庫存
        self.bot.loop.create_task(self.replenish_stock())


async def setup(bot: commands.Bot):
    cog = DailyMessageEvent(bot)
    await bot.add_cog(cog)
    # 啟動後立即檢查今天是否需發送與補充庫存
    bot.loop.create_task(cog._startup_check())

