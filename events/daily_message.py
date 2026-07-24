import asyncio
import json
import openai
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from discord import Embed, Color
from discord.ext import commands, tasks

from config import TZ, DAILY_CHANNEL, DEEPSEEK_API_KEY, DAILY_MESSAGE_TIME, DAILY_AI_MAX_RETRIES, DAILY_AI_RETRY_BASE_DELAY, DAILY_AI_MODEL, DAILY_AI_BASE_URL, DAILY_GENERATION_PROMPT_TEMPLATE, DAILY_VERIFICATION_PROMPT_TEMPLATE
from database.daily_content_db import dailyContentDB


def build_daily_embed(content: dict) -> Embed:
    """### 建置每日知識 Embed（頻道簡述版本，供 event 與 command 共用）

    Args:
        content: dict，需含 date 及 section1 / section2 的相關欄位

    Returns:
        Embed
    """
    embed = Embed(
        title=f"每日知識 — {content['date']}",
        color=Color.blue(),
    )

    # 第一則：心理學／社會學
    s1 = f"**{content['section1_title']}**\n"
    s1 += f"{content['section1_summary']}\n"
    s1 += f"📊 可信度：{content['section1_credibility']}"
    embed.add_field(
        name=f"# {content['section1_type']}",
        value=s1,
        inline=False,
    )

    # 第二則：哲學／神話／神祕學
    s2 = f"**{content['section2_title']}**\n"
    s2 += f"{content['section2_summary']}\n"
    s2 += f"📊 可信度：{content['section2_credibility']}"
    embed.add_field(
        name=f"# {content['section2_type']}",
        value=s2,
        inline=False,
    )

    # 驗證資訊
    if content.get('verified_at'):
        embed.add_field(
            name="✅ 二次驗證",
            value=f"驗證時間：{content['verified_at']}\n{content.get('verification_notes', '')}",
            inline=False,
        )

    # 時間 footer
    hour_str = f"{DAILY_MESSAGE_TIME.hour:02d}:{DAILY_MESSAGE_TIME.minute:02d}"
    embed.set_footer(text=f"每日 {hour_str} 更新 | 詳細資料請見下方討論串")
    return embed


def build_detail_content(content: dict) -> str:
    """### 建置詳細資料文字（討論串用）

    Args:
        content: dict，每日內容

    Returns:
        str: 格式化詳細文字
    """
    lines = [
        f"# 每日知識詳細資料 — {content['date']}",
        "",
        f"## 📖 {content['section1_type']}：{content['section1_title']}",
        "",
        content['section1_detail'],
        "",
        "**📚 參考資料／出處**",
        content['section1_sources'],
        "",
        f"**📊 可信度評級：** {content['section1_credibility']}",
        "",
        "---",
        "",
        f"## 🔮 {content['section2_type']}：{content['section2_title']}",
        "",
        content['section2_detail'],
        "",
        "**📚 參考資料／出處**",
        content['section2_sources'],
        "",
        f"**📊 可信度評級：** {content['section2_credibility']}",
        "",
        "---",
        "",
    ]

    if content.get('verified_at'):
        lines.extend([
            "## ✅ 二次驗證結果",
            f"驗證時間：{content['verified_at']}",
            content.get('verification_notes', ''),
            "",
        ])

    lines.append("> ⚠️ 內容由 AI 生成並經自動驗證，請自行斟酌參考。")

    return "\n".join(lines)


class DailyMessageEvent(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._ai_client: openai.OpenAI | None = (
            openai.OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DAILY_AI_BASE_URL)
            if DEEPSEEK_API_KEY else None
        )
        self.daily_message_task.start()

    def cog_unload(self):
        """### 卸載插件
        """
        self.daily_message_task.cancel()
        if self._ai_client:
            self._ai_client.close()

    @staticmethod
    def _build_generation_prompt(existing_contents: List[Dict[str, Any]]) -> str:
        """### 建構 AI 生成 Prompt

        Args:
            existing_contents: 歷史內容清單（供去重）

        Returns:
            str: prompt
        """
        # 建立歷史標題摘要（供去重比對）
        history_lines: List[str] = []
        for row in existing_contents:
            history_lines.append(
                f'- [{row["section1_type"]}]「{row["section1_title"]}」| '
                f'[{row["section2_type"]}]「{row["section2_title"]}」'
            )

        history_block = "\n".join(history_lines) if history_lines else "（尚無歷史內容）"

        return DAILY_GENERATION_PROMPT_TEMPLATE.format(history_block=history_block)

    @staticmethod
    def _build_verification_prompt(content: dict) -> str:
        """### 建構驗證 Prompt

        Args:
            content: 已生成的內容 dict

        Returns:
            str: 驗證 prompt
        """
        return DAILY_VERIFICATION_PROMPT_TEMPLATE.format(
            s1_type=content['section1_type'],
            s1_title=content['section1_title'],
            s1_summary=content['section1_summary'],
            s1_detail=content['section1_detail'],
            s1_sources=content['section1_sources'],
            s2_type=content['section2_type'],
            s2_title=content['section2_title'],
            s2_summary=content['section2_summary'],
            s2_detail=content['section2_detail'],
            s2_sources=content['section2_sources'],
        )

    @staticmethod
    def _parse_ai_response(response_text: str) -> Optional[Dict[str, str]]:
        """### 解析 AI 回傳的 JSON（通用）

        Args:
            response_text: AI 回傳文字

        Returns:
            dict 或 None（解析失敗）
        """
        text = response_text.strip()
        if text.startswith("```"):
            first_newline = text.find("\n")
            if first_newline != -1:
                text = text[first_newline + 1:]
            if text.endswith("```"):
                text = text[:-3].strip()

        try:
            data = json.loads(text)
            return {k: str(v).strip() if v is not None else "" for k, v in data.items()}
        except (json.JSONDecodeError, TypeError) as e:
            print(f"[DailyMessage] 解析 AI 回傳 JSON 失敗: {e}")
            return None

    async def _call_deepseek(self, prompt: str, max_tokens: int = 2048) -> Optional[Dict[str, str]]:
        """### 呼叫 DeepSeek API 生成內容（含自動重試）

        Args:
            prompt: 提示詞
            max_tokens: 最大 token 數

        Returns:
            解析後的 dict 或 None
        """
        if not self._ai_client:
            print("[DailyMessage] ❌ DEEPSEEK_API_KEY 未設定，無法呼叫 AI")
            return None

        max_retries = DAILY_AI_MAX_RETRIES
        base_delay = DAILY_AI_RETRY_BASE_DELAY

        for attempt in range(1, max_retries + 1):
            try:
                def _sync_call() -> str:
                    response = self._ai_client.chat.completions.create(
                        model=DAILY_AI_MODEL,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.7,
                        max_tokens=max_tokens,
                    )
                    return response.choices[0].message.content or ""

                text = await asyncio.to_thread(_sync_call)
                parsed = self._parse_ai_response(text)
                if parsed is not None:
                    return parsed

                print(f"[DailyMessage] ⚠️ 第 {attempt}/{max_retries} 次嘗試失敗（解析錯誤）")

            except Exception as e:
                print(f"[DailyMessage] ⚠️ 第 {attempt}/{max_retries} 次嘗試失敗: {e}")

            if attempt < max_retries:
                delay = base_delay * (2 ** (attempt - 1))
                print(f"[DailyMessage] ⏳ 等待 {delay} 秒後重試...")
                await asyncio.sleep(delay)

        print(f"[DailyMessage] ❌ DeepSeek API 呼叫失敗（已重試 {max_retries} 次）")
        return None

    async def _generate_content(self, existing_dicts: List[Dict[str, Any]]) -> Optional[Dict[str, str]]:
        """### 生成每日知識內容（階段一）

        Args:
            existing_dicts: 歷史內容清單

        Returns:
            生成的內容 dict 或 None
        """
        prompt = self._build_generation_prompt(existing_dicts)
        result = await self._call_deepseek(prompt, max_tokens=2048)

        if result is None:
            return None

        # 驗證必要欄位
        required_keys = [
            "section1_type", "section1_title", "section1_summary", "section1_detail", "section1_sources",
            "section2_type", "section2_title", "section2_summary", "section2_detail", "section2_sources",
        ]
        for key in required_keys:
            if key not in result or not result[key]:
                print(f"[DailyMessage] 生成內容缺少必要欄位: {key}")
                return None

        return result

    async def _verify_content(self, content: dict) -> Optional[Dict[str, str]]:
        """### 二次驗證內容正確性（階段二）

        Args:
            content: 已生成的內容 dict

        Returns:
            驗證結果 dict 或 None
        """
        prompt = self._build_verification_prompt(content)
        result = await self._call_deepseek(prompt, max_tokens=2048)

        if result is None:
            return None

        required_keys = [
            "section1_verification", "section1_credibility",
            "section1_evidence",
            "section2_verification", "section2_credibility",
            "section2_evidence", "overall_notes",
        ]
        for key in required_keys:
            if key not in result or not result[key]:
                print(f"[DailyMessage] 驗證結果缺少必要欄位: {key}")
                return None

        return result

    @tasks.loop(time=DAILY_MESSAGE_TIME)
    async def daily_message_task(self):
        """### 每日訊息任務

        檢查資料庫 → 若已有今日內容則跳過 → 否則呼叫 AI 生成（兩階段含驗證）
        → 存入 DB → 發送 Embed（頻道簡述）→ 建立討論串並貼上詳細資料
        """
        now = datetime.now(TZ)
        date_str = now.strftime("%Y-%m-%d")
        print(f"--- 📅 每日訊息任務開始 ({date_str}) ---")

        try:
            # 1. 檢查是否已有今日內容
            existing = await dailyContentDB.get_daily_content(date_str)
            if existing:
                print(f"[DailyMessage] ✅ 今日 ({date_str}) 已有內容，跳過生成")
                return

            # 2. 取得歷史內容供去重
            all_contents = await dailyContentDB.get_all_contents()
            existing_dicts = [dict(row) for row in all_contents]

            # 3. 階段一：生成內容（最多重試 2 次）
            generated = None
            gen_attempts = 2
            for attempt in range(1, gen_attempts + 1):
                generated = await self._generate_content(existing_dicts)
                if generated:
                    break
                print(f"[DailyMessage] ⚠️ 生成嘗試 {attempt}/{gen_attempts} 失敗")
                if attempt < gen_attempts:
                    await asyncio.sleep(3)

            if generated is None:
                print("[DailyMessage] ❌ 內容生成失敗，今日不發送")
                return

            # 4. 階段二：二次驗證
            verification = await self._verify_content(generated)

            # 5. 決定最終內容與可信度
            now_str = now.strftime("%Y-%m-%d %H:%M:%S")
            verified_at_str = now_str if verification else ""
            verification_notes = verification.get("overall_notes", "") if verification else "（驗證失敗，請自行查證）"

            # 使用驗證結果中的可信度（若驗證成功），否則預設為「未驗證」
            s1_cred = verification.get("section1_credibility", "未驗證") if verification else "未驗證"
            s2_cred = verification.get("section2_credibility", "未驗證") if verification else "未驗證"

            # 6. 存入資料庫
            await dailyContentDB.set_daily_content(
                date=date_str,
                section1_type=generated["section1_type"],
                section1_title=generated["section1_title"],
                section1_summary=generated["section1_summary"],
                section1_detail=generated["section1_detail"],
                section1_sources=generated["section1_sources"],
                section1_credibility=s1_cred,
                section2_type=generated["section2_type"],
                section2_title=generated["section2_title"],
                section2_summary=generated["section2_summary"],
                section2_detail=generated["section2_detail"],
                section2_sources=generated["section2_sources"],
                section2_credibility=s2_cred,
                generated_at=now_str,
                verified_at=verified_at_str,
                verification_notes=verification_notes,
            )
            print(f"[DailyMessage] ✅ 已儲存 {date_str} 的每日內容")

            # 7. 發送到頻道
            if not DAILY_CHANNEL:
                print("[DailyMessage] ❌ DAILY_CHANNEL 未設定，無法發送")
                return

            channel = self.bot.get_channel(DAILY_CHANNEL)
            if channel is None:
                print(f"[DailyMessage] ❌ 無法取得頻道 ID {DAILY_CHANNEL}")
                return

            # 準備顯示用的 content dict（加入驗證資訊）
            display_content = {
                "date": date_str,
                "section1_type": generated["section1_type"],
                "section1_title": generated["section1_title"],
                "section1_summary": generated["section1_summary"],
                "section1_credibility": s1_cred,
                "section2_type": generated["section2_type"],
                "section2_title": generated["section2_title"],
                "section2_summary": generated["section2_summary"],
                "section2_credibility": s2_cred,
                "verified_at": verified_at_str,
                "verification_notes": verification_notes,
            }

            embed = build_daily_embed(display_content)
            message = await channel.send(embed=embed)
            print(f"[DailyMessage] ✅ 已發送每日訊息到頻道 {DAILY_CHANNEL}")

            # 8. 建立討論串並貼上詳細資料
            try:
                thread = await message.create_thread(
                    name=f"📖 詳細資料 — {date_str}",
                    auto_archive_duration=1440,  # 24 小時後自動封存
                )

                # 準備完整的詳細內容 dict
                detail_content = {
                    "date": date_str,
                    "section1_type": generated["section1_type"],
                    "section1_title": generated["section1_title"],
                    "section1_detail": generated["section1_detail"],
                    "section1_sources": generated["section1_sources"],
                    "section1_credibility": s1_cred,
                    "section2_type": generated["section2_type"],
                    "section2_title": generated["section2_title"],
                    "section2_detail": generated["section2_detail"],
                    "section2_sources": generated["section2_sources"],
                    "section2_credibility": s2_cred,
                    "verified_at": verified_at_str,
                    "verification_notes": verification_notes,
                }

                detail_text = build_detail_content(detail_content)

                # 若詳細內容超過 Discord 2000 字限制，分段發送
                max_len = 1900
                if len(detail_text) <= max_len:
                    await thread.send(detail_text)
                else:
                    # 分段：先發標題與第一則，再發第二則與驗證
                    parts = []
                    current = []
                    current_len = 0
                    for line in detail_text.split("\n"):
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

                    for part in parts:
                        await thread.send(part)

                print(f"[DailyMessage] ✅ 已建立討論串並張貼詳細資料")
            except Exception as e:
                print(f"[DailyMessage] ⚠️ 建立討論串失敗: {e}")

        except Exception as e:
            print(f"[DailyMessage] ❌ 每日訊息任務錯誤: {e}")

    @daily_message_task.before_loop
    async def before_daily_message_task(self):
        """巡迴前檢查
        """
        await self.bot.wait_until_ready()

    async def _startup_check(self) -> None:
        """### 啟動後立即檢查：若今天尚未有內容則觸發一次

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
