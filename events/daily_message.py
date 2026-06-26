import asyncio
import json
import openai
from datetime import datetime
from typing import Any, Dict, List, Optional

from discord import Embed, Color
from discord.ext import commands, tasks

from config import TZ, DAILY_CHANNEL, DEEPSEEK_API_KEY, DAILY_MESSAGE_TIME, DAILY_AI_MAX_RETRIES, DAILY_AI_RETRY_BASE_DELAY
from database.daily_content_db import dailyContentDB


def build_daily_embed(content: dict) -> Embed:
    """### 建置每日知識 Embed（供 event 與 command 共用）

    Args:
        content: dict，需含 date, quote_text, quote_source, quote_author,
                 quote_translation, idiom_text, idiom_explanation, idiom_usage, idiom_origin

    Returns:
        Embed
    """
    embed = Embed(
        title=f"每日學習 — {content['date']}",
        color=Color.blue(),
    )

    # 佳句區塊
    quote_content = f"{content['quote_text']}"
    if content['quote_translation']:
        quote_content += f"\n> {content['quote_translation']}"
    quote_content += f"\n—— {content['quote_author']}，《{content['quote_source']}》"
    embed.add_field(name="# 佳句", value=quote_content, inline=False)

    # 成語區塊
    idiom_content = (
        f"{content['idiom_text']}\n"
        f"解釋：{content['idiom_explanation']}\n"
        f"用法：{content['idiom_usage']}\n"
        f"出處：{content['idiom_origin']}"
    )
    embed.add_field(name="# 成語", value=idiom_content, inline=False)

    # 從 DAILY_MESSAGE_TIME 動態生成 footer 時間
    hour_str = f"{DAILY_MESSAGE_TIME.hour:02d}:{DAILY_MESSAGE_TIME.minute:02d}"
    embed.set_footer(text=f"每日 {hour_str} 更新 | 內容由 AI 生成，請自行查證出處")
    return embed


class DailyMessageEvent(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._ai_client: openai.OpenAI | None = (
            openai.OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
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
    def _build_prompt(existing_contents: List[Dict[str, Any]]) -> str:
        """### 建構 AI Prompt

        Args:
            existing_contents: 歷史內容清單（供去重）

        Returns:
            str: prompt
        """
        # 建立歷史內容摘要字串（只含佳句原文與成語原文，供去重比對）
        history_lines: List[str] = []
        for row in existing_contents:
            history_lines.append(f'- 佳句：「{row["quote_text"]}」| 成語：「{row["idiom_text"]}」')

        history_block = "\n".join(history_lines) if history_lines else "（尚無歷史內容）"

        return f"""你是一個每日學習助手。請產生今天的一組「每日佳句」與「每日成語」。

【嚴格要求】
1. 佳句必須是真實存在的名言，附帶具體出處（書名/演講/作品/動漫名稱）與作者。
2. 若非中文佳句，須附繁體中文翻譯。
3. 成語須含解釋、用法例句、出處（如「出自《戰國策·秦策》」）。
4. 以下為已使用過的內容，**佳句與成語皆不可與下列任一筆重複**：

{history_block}

【回傳格式】
請僅回傳純 JSON（無 Markdown 標記、無備註），格式如下：
{{
    "quote_text": "佳句原文",
    "quote_source": "出處名稱",
    "quote_author": "作者",
    "quote_translation": "繁體中文翻譯（若原文已是中文則留空）",
    "idiom_text": "成語或諺語",
    "idiom_explanation": "解釋",
    "idiom_usage": "用法例句",
    "idiom_origin": "出處"
}}"""

    @staticmethod
    def _parse_ai_response(response_text: str) -> Optional[Dict[str, str]]:
        """### 解析 AI 回傳的 JSON

        Args:
            response_text: AI 回傳文字

        Returns:
            dict 或 None（解析失敗）
        """
        # 去除可能的 Markdown 程式碼塊包裹
        text = response_text.strip()
        if text.startswith("```"):
            # 移除 ```json 或 ``` 前綴
            first_newline = text.find("\n")
            if first_newline != -1:
                text = text[first_newline + 1:]
            # 移除結尾的 ```
            if text.endswith("```"):
                text = text[:-3].strip()

        try:
            data = json.loads(text)
            required_keys = [
                "quote_text", "quote_source", "quote_author", "quote_translation",
                "idiom_text", "idiom_explanation", "idiom_usage", "idiom_origin",
            ]
            for key in required_keys:
                if key not in data or not isinstance(data[key], str) or not data[key].strip():
                    print(f"[DailyMessage] AI 回傳缺少必要欄位: {key}")
                    return None
            return {
                "quote_text": data["quote_text"].strip(),
                "quote_source": data["quote_source"].strip(),
                "quote_author": data["quote_author"].strip(),
                "quote_translation": data["quote_translation"].strip(),
                "idiom_text": data["idiom_text"].strip(),
                "idiom_explanation": data["idiom_explanation"].strip(),
                "idiom_usage": data["idiom_usage"].strip(),
                "idiom_origin": data["idiom_origin"].strip(),
            }
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            print(f"[DailyMessage] 解析 AI 回傳 JSON 失敗: {e}")
            return None

    async def _call_deepseek(self, prompt: str) -> Optional[Dict[str, str]]:
        """### 呼叫 DeepSeek API 生成內容（含自動重試）

        Args:
            prompt: 提示詞

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
                        model="deepseek-chat",
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.7,
                        max_tokens=1024,
                    )
                    return response.choices[0].message.content or ""

                text = await asyncio.to_thread(_sync_call)
                parsed = self._parse_ai_response(text)
                if parsed is not None:
                    return parsed

                # JSON 解析失敗或欄位缺失（_parse_ai_response 回傳 None）
                print(f"[DailyMessage] ⚠️ 第 {attempt}/{max_retries} 次嘗試失敗（解析錯誤）")

            except Exception as e:
                print(f"[DailyMessage] ⚠️ 第 {attempt}/{max_retries} 次嘗試失敗: {e}")

            # 若非最後一次，指數退避後重試
            if attempt < max_retries:
                delay = base_delay * (2 ** (attempt - 1))
                print(f"[DailyMessage] ⏳ 等待 {delay} 秒後重試...")
                await asyncio.sleep(delay)

        print(f"[DailyMessage] ❌ DeepSeek API 呼叫失敗（已重試 {max_retries} 次）")
        return None

    @tasks.loop(time=DAILY_MESSAGE_TIME)
    async def daily_message_task(self):
        """### 每日訊息任務

        檢查資料庫 → 若已有今日內容則跳過 → 否則呼叫 AI 生成 → 存入 DB → 發送 Embed
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
            existing_dicts = [dict(row) for row in all_contents]  # type: ignore[arg-type]

            # 3. 呼叫 AI
            prompt = self._build_prompt(existing_dicts)
            result = await self._call_deepseek(prompt)
            if result is None:
                print("[DailyMessage] ❌ AI 生成失敗，今日不發送")
                return

            # 4. 存入資料庫
            now_str = now.strftime("%Y-%m-%d %H:%M:%S")
            await dailyContentDB.set_daily_content(
                date=date_str,
                quote_text=result["quote_text"],
                quote_source=result["quote_source"],
                quote_author=result["quote_author"],
                quote_translation=result["quote_translation"],
                idiom_text=result["idiom_text"],
                idiom_explanation=result["idiom_explanation"],
                idiom_usage=result["idiom_usage"],
                idiom_origin=result["idiom_origin"],
                generated_at=now_str,
            )
            print(f"[DailyMessage] ✅ 已儲存 {date_str} 的每日內容")

            # 5. 發送到頻道
            if not DAILY_CHANNEL:
                print("[DailyMessage] ❌ DAILY_CHANNEL 未設定，無法發送")
                return

            channel = self.bot.get_channel(DAILY_CHANNEL)
            if channel is None:
                print(f"[DailyMessage] ❌ 無法取得頻道 ID {DAILY_CHANNEL}")
                return

            embed = build_daily_embed({"date": date_str, **result})
            await channel.send(embed=embed)
            print(f"[DailyMessage] ✅ 已發送每日訊息到頻道 {DAILY_CHANNEL}")

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
        # 等待 before_loop 完成（若已透過定時任務啟動）
        await asyncio.sleep(1)
        await self.daily_message_task()


async def setup(bot: commands.Bot):
    cog = DailyMessageEvent(bot)
    await bot.add_cog(cog)
    # 啟動後立即檢查今天是否需發送
    bot.loop.create_task(cog._startup_check())
