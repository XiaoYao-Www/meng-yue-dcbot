import asyncio
import json
import openai
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from discord import Embed, Color
from discord.ext import commands, tasks
from json_repair import repair_json

from config import TZ, DAILY_CHANNEL, DEEPSEEK_API_KEY, DAILY_MESSAGE_TIME, DAILY_AI_MAX_RETRIES, DAILY_AI_RETRY_BASE_DELAY, DAILY_AI_MODEL, DAILY_AI_BASE_URL, DAILY_GENERATION_MAX_TOKENS, DAILY_VERIFICATION_MAX_TOKENS, DAILY_VERIFY_MAX_RETRIES, DAILY_VERIFY_RETRY_BASE_DELAY, DAILY_SINGLE_SECTION_GENERATION_PROMPT_TEMPLATE, DAILY_SINGLE_SECTION_VERIFICATION_PROMPT_TEMPLATE
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
    def _get_category_group(section_type: str) -> str:
        """### 從次領域回推大分類（用於避免兩篇撞同大類）

        Args:
            section_type: 次領域名稱（如「犯罪心理學」）

        Returns:
            大分類名稱（如「人類」）或空字串（無法識別）
        """
        category_map: Dict[str, List[str]] = {
            "人類": [
                "心理學", "社會心理學", "人格心理學", "認知心理學",
                "發展心理學", "演化心理學", "神經心理學", "臨床心理學",
                "教育心理學", "犯罪心理學", "司法心理學", "精神病學",
                "精神醫學", "神經科學", "認知科學", "行為科學",
                "行為經濟學",
            ],
            "社會": [
                "社會學", "犯罪學", "政治學", "法理學", "法哲學",
                "公共政策", "組織管理", "文化研究", "人類學", "經濟學",
                "賽局理論", "傳播學",
            ],
            "思想": [
                "哲學", "倫理學", "邏輯學", "認識論", "存在主義",
                "現象學", "分析哲學", "東方哲學", "宗教哲學",
            ],
            "文明": [
                "世界史", "文明史", "思想史", "科技史", "宗教史",
                "軍事史", "法律史", "藝術史", "神話學", "比較神話",
                "宗教學", "民俗學", "神祕學", "象徵學",
            ],
            "思考工具": [
                "統計思維", "認知偏誤", "決策理論", "系統思考",
                "風險分析", "資訊理論", "博弈論",
            ],
        }
        for group, types in category_map.items():
            if section_type in types:
                return group
        return ""

    @staticmethod
    def _build_single_section_prompt(
        section_label: str,
        existing_contents: List[Dict[str, Any]],
        excluded_categories: str = "",
    ) -> str:
        """### 建構單篇生成 Prompt

        Args:
            section_label: "第一則" 或 "第二則"
            existing_contents: 歷史內容清單
            excluded_categories: 要排除的大分類字串（另一篇已使用的大類）

        Returns:
            str: prompt
        """
        # 建立歷史標題摘要
        history_lines: List[str] = []
        for row in existing_contents:
            history_lines.append(
                f'- [{row["section1_type"]}]「{row["section1_title"]}」| '
                f'[{row["section2_type"]}]「{row["section2_title"]}」'
            )

        history_block = "\n".join(history_lines) if history_lines else "（尚無歷史內容）"

        # 若有需要排除的大分類，加入提示
        excluded_block = ""
        if excluded_categories:
            excluded_block = (
                f"⚠️ 另一篇文章已選用「{excluded_categories}」大分類，\n"
                f"本篇文章「不得」再從「{excluded_categories}」中選擇領域。\n"
                "請改選其他大分類下的次領域。"
            )
        else:
            excluded_block = "無特定排除分類。請任意選擇大分類下的次領域。"

        return DAILY_SINGLE_SECTION_GENERATION_PROMPT_TEMPLATE.format(
            section_label=section_label,
            history_block=history_block,
            excluded_categories=excluded_block,
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

    @staticmethod
    def _parse_ai_response(response_text: str) -> Optional[Dict[str, str]]:
        """### 解析 AI 回傳的 JSON（通用，含多層容錯 + json-repair 修復）

        容錯策略（依序嘗試）：
        1. 清除 Markdown ``` 程式碼塊包裹 → json.loads 直接解析
        2. 大括號深度掃描提取 JSON 區塊 → json.loads 解析
        3. json-repair 修復後解析（處理換行、尾逗號、截斷等常見 AI 錯誤）

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

        # 嘗試直接解析
        try:
            data = json.loads(text, strict=False)
            return {k: str(v).strip() if v is not None else "" for k, v in data.items()}
        except (json.JSONDecodeError, TypeError):
            pass

        # 後備：用大括號深度掃描提取 JSON 區塊
        extracted = DailyMessageEvent._extract_json_block(text)
        if extracted:
            try:
                data = json.loads(extracted, strict=False)
                return {k: str(v).strip() if v is not None else "" for k, v in data.items()}
            except (json.JSONDecodeError, TypeError):
                pass

            # 第三層：json-repair 修復後解析
            try:
                repaired = repair_json(extracted)
                data = json.loads(repaired)
                return {k: str(v).strip() if v is not None else "" for k, v in data.items()}
            except Exception:
                pass

        # 最後手段：對整個原始文字嘗試 json-repair
        try:
            repaired = repair_json(text)
            data = json.loads(repaired)
            return {k: str(v).strip() if v is not None else "" for k, v in data.items()}
        except Exception:
            pass

        # 全部失敗，輸出完整原始內容供調試
        print(f"[DailyMessage] ❌ 解析 AI 回傳 JSON 失敗（所有容錯層均無效）")
        print(f"[DailyMessage] 原始回傳長度: {len(response_text)} 字元")
        print(f"[DailyMessage] 原始回傳內容:\n{response_text[:800]}")
        if len(response_text) > 800:
            print(f"[DailyMessage] ...（後續 {len(response_text) - 800} 字元已截斷）")
        return None

    @staticmethod
    def _extract_json_block(text: str) -> Optional[str]:
        """### 掃描字串中第一個合法的 JSON 物件（大括號深度計數）

        Args:
            text: 可能含 JSON 的原始文字

        Returns:
            提取出的 JSON 字串或 None
        """
        start = text.find("{")
        if start == -1:
            return None

        depth = 0
        in_string = False
        escape = False
        for i in range(start, len(text)):
            ch = text[i]
            if escape:
                escape = False
                continue
            if ch == "\\" and in_string:
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]

        return None

    async def _call_deepseek(
        self,
        prompt: str,
        max_tokens: int = 2048,
        use_json_mode: bool = True,
        temperature: float = 0.7,
    ) -> Optional[Dict[str, str]]:
        """### 呼叫 DeepSeek API 生成內容（含自動重試 + 漸進式降級）

        策略：
        1. 優先嘗試 JSON Mode（response_format），若返回空 content 立即回退文字模式
        2. 每次重試降低 temperature，提高輸出確定性
        3. 無重試等待（API 無速率限制）

        Args:
            prompt: 提示詞
            max_tokens: 最大 token 數
            use_json_mode: 是否優先使用 JSON Mode
            temperature: 初始 temperature（重試時會逐步降低）

        Returns:
            解析後的 dict 或 None
        """
        if not self._ai_client:
            print("[DailyMessage] ❌ DEEPSEEK_API_KEY 未設定，無法呼叫 AI")
            return None

        max_retries = DAILY_AI_MAX_RETRIES

        for attempt in range(1, max_retries + 1):
            # 漸進式降級：每次重試降低 temperature
            current_temp = max(0.1, temperature * (0.7 ** (attempt - 1)))
            # JSON Mode 僅首次嘗試，且改用更安全的 prompt 內嵌方式
            try_json = use_json_mode and attempt == 1

            try:
                def _sync_call() -> str:
                    kwargs: dict = {
                        "model": DAILY_AI_MODEL,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": current_temp,
                        "max_tokens": max_tokens,
                    }
                    if try_json:
                        kwargs["response_format"] = {"type": "json_object"}
                        print(f"[DailyMessage] 🔧 嘗試 JSON Mode (temp={current_temp:.2f})")
                    else:
                        print(f"[DailyMessage] 🔧 文字模式 (temp={current_temp:.2f})")

                    response = self._ai_client.chat.completions.create(**kwargs)
                    content = response.choices[0].message.content or ""
                    finish = response.choices[0].finish_reason
                    usage = getattr(response, "usage", None)
                    usage_str = f"prompt={usage.prompt_tokens} completion={usage.completion_tokens}" if usage else "N/A"

                    if not content:
                        print(f"[DailyMessage] ⚠️ API 回傳空內容！finish_reason={finish} usage=({usage_str})")

                    return content

                text = await asyncio.to_thread(_sync_call)
                if not text:
                    # 空內容：若為 JSON Mode 則直接視為該模式不支援，下次不再嘗試
                    print(f"[DailyMessage] ⚠️ 第 {attempt}/{max_retries} 次嘗試回傳空內容")
                    if try_json:
                        print(f"[DailyMessage] 💡 JSON Mode 回傳空內容，後續嘗試將跳過 JSON Mode")
                        use_json_mode = False
                    continue

                parsed = self._parse_ai_response(text)
                if parsed is not None:
                    return parsed

                print(f"[DailyMessage] ⚠️ 第 {attempt}/{max_retries} 次嘗試失敗（解析錯誤）")

            except Exception as e:
                print(f"[DailyMessage] ⚠️ 第 {attempt}/{max_retries} 次嘗試異常: {type(e).__name__}: {e}")

        print(f"[DailyMessage] ❌ DeepSeek API 呼叫失敗（已重試 {max_retries} 次）")
        return None

    async def _generate_sections(self, existing_dicts: List[Dict[str, Any]]) -> Optional[Dict[str, str]]:
        """### 生成每日知識內容（分兩次 API 調用，每次生成一篇）

        拆分策略：
        1. 先生成 section1
        2. 提取 section1 的大分類
        3. 生成 section2（排除 section1 的大分類，防止撞類）

        Args:
            existing_dicts: 歷史內容清單

        Returns:
            合併後的內容 dict（含 section1_ / section2_ 前綴）或 None
        """
        # ── 生成 section1 ──
        prompt1 = self._build_single_section_prompt("第一則", existing_dicts)
        result1 = await self._call_deepseek(prompt1, max_tokens=DAILY_GENERATION_MAX_TOKENS, use_json_mode=False)

        if result1 is None:
            print("[DailyMessage] ❌ section1 生成失敗")
            return None

        # 驗證 section1 的必要欄位
        s1_required = ["section_type", "section_title", "section_summary", "section_detail", "section_sources"]
        for key in s1_required:
            if key not in result1 or not result1[key]:
                print(f"[DailyMessage] ⚠️ section1 生成缺少必要欄位: {key}")
                return None

        # 提取 section1 的大分類，用於 section2 排除
        s1_type = result1["section_type"]
        s1_group = self._get_category_group(s1_type)
        excluded = ""
        if s1_group:
            excluded = s1_group

        # ── 生成 section2（排除 section1 的大分類） ──
        prompt2 = self._build_single_section_prompt("第二則", existing_dicts, excluded_categories=excluded)
        result2 = await self._call_deepseek(prompt2, max_tokens=DAILY_GENERATION_MAX_TOKENS, use_json_mode=False)

        if result2 is None:
            print("[DailyMessage] ❌ section2 生成失敗")
            return None

        s2_required = ["section_type", "section_title", "section_summary", "section_detail", "section_sources"]
        for key in s2_required:
            if key not in result2 or not result2[key]:
                print(f"[DailyMessage] ⚠️ section2 生成缺少必要欄位: {key}")
                return None

        # ── 合併結果，加上 prefix ──
        merged = {
            "section1_type": result1["section_type"],
            "section1_title": result1["section_title"],
            "section1_summary": result1["section_summary"],
            "section1_detail": result1["section_detail"],
            "section1_sources": result1["section_sources"],
            "section2_type": result2["section_type"],
            "section2_title": result2["section_title"],
            "section2_summary": result2["section_summary"],
            "section2_detail": result2["section_detail"],
            "section2_sources": result2["section_sources"],
        }

        print(f"[DailyMessage] ✅ 兩篇內容生成成功 (s1={s1_type}, s2={result2['section_type']})")
        return merged

    async def _verify_sections(self, content: dict) -> Optional[Dict[str, str]]:
        """### 二次驗證內容正確性（分兩次 API 調用，每次驗證一篇 + 本地拼接 overall_notes）

        Args:
            content: 已生成的內容 dict（含 section1_ / section2_ 前綴）

        Returns:
            驗證結果 dict（7 個欄位）或 None
        """
        # ── 驗證 section1 ──
        prompt1 = self._build_single_verification_prompt(
            content["section1_type"],
            content["section1_title"],
            content["section1_summary"],
            content["section1_detail"],
            content["section1_sources"],
        )
        v1 = await self._call_deepseek(prompt1, max_tokens=DAILY_VERIFICATION_MAX_TOKENS, use_json_mode=False)

        if v1 is None:
            print("[DailyMessage] ❌ section1 驗證失敗（API/解析錯誤）")
            return None

        v1_required = ["verification", "credibility", "evidence"]
        for key in v1_required:
            if key not in v1 or not v1[key]:
                print(f"[DailyMessage] ⚠️ section1 驗證缺少必要欄位: {key}")
                return None

        # ── 驗證 section2 ──
        prompt2 = self._build_single_verification_prompt(
            content["section2_type"],
            content["section2_title"],
            content["section2_summary"],
            content["section2_detail"],
            content["section2_sources"],
        )
        v2 = await self._call_deepseek(prompt2, max_tokens=DAILY_VERIFICATION_MAX_TOKENS, use_json_mode=False)

        if v2 is None:
            print("[DailyMessage] ❌ section2 驗證失敗（API/解析錯誤）")
            return None

        v2_required = ["verification", "credibility", "evidence"]
        for key in v2_required:
            if key not in v2 or not v2[key]:
                print(f"[DailyMessage] ⚠️ section2 驗證缺少必要欄位: {key}")
                return None

        # ── 本地拼接 overall_notes ──
        overall_notes = (
            f"第一則: {v1['verification']}(可信度{v1['credibility']}); "
            f"第二則: {v2['verification']}(可信度{v2['credibility']})"
        )

        merged_verification = {
            "section1_verification": v1["verification"],
            "section1_credibility": v1["credibility"],
            "section1_evidence": v1["evidence"],
            "section2_verification": v2["verification"],
            "section2_credibility": v2["credibility"],
            "section2_evidence": v2["evidence"],
            "overall_notes": overall_notes,
        }

        print(f"[DailyMessage] ✅ 兩篇驗證完成 {overall_notes}")
        return merged_verification

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

            # 3. 階段一：生成內容（最多 DAILY_AI_MAX_RETRIES 次）
            generated = None
            verification = None
            gen_max = DAILY_AI_MAX_RETRIES
            ver_max = DAILY_VERIFY_MAX_RETRIES

            for gen_attempt in range(1, gen_max + 1):
                generated = await self._generate_sections(existing_dicts)
                if generated is None:
                    print(f"[DailyMessage] ⚠️ 生成嘗試 {gen_attempt}/{gen_max} 失敗（API/解析錯誤）")
                    if gen_attempt < gen_max:
                        await asyncio.sleep(DAILY_AI_RETRY_BASE_DELAY * (2 ** (gen_attempt - 1)))
                    continue

                # 4. 階段二：二次驗證（獨立重試：API 失敗僅重試驗證，不重新生成）
                for ver_attempt in range(1, ver_max + 1):
                    verification = await self._verify_sections(generated)
                    if verification is None:
                        print(f"[DailyMessage] ⚠️ 驗證嘗試 {ver_attempt}/{ver_max} 失敗（API/解析錯誤），重試驗證")
                        if ver_attempt < ver_max:
                            await asyncio.sleep(DAILY_VERIFY_RETRY_BASE_DELAY * (2 ** (ver_attempt - 1)))
                        continue

                    # 驗證成功，檢查內容是否不合格
                    s1_verdict = verification.get("section1_verification", "")
                    s2_verdict = verification.get("section2_verification", "")
                    if s1_verdict == "不通過" or s2_verdict == "不通過":
                        print(f"[DailyMessage] ⚠️ 驗證判定內容不合格 "
                              f"(s1={s1_verdict}, s2={s2_verdict})，回到生成階段")
                        break  # 跳出驗證循環，回到生成循環

                    # 驗證通過（通過/有疑慮均可接受）
                    print(f"[DailyMessage] ✅ 驗證通過 (s1={s1_verdict}, s2={s2_verdict})")
                    break

                # 如果驗證通過了（verification 不為 None 且非「不通過」），跳出生成循環
                if verification is not None:
                    s1_verdict = verification.get("section1_verification", "")
                    s2_verdict = verification.get("section2_verification", "")
                    if s1_verdict != "不通過" and s2_verdict != "不通過":
                        break
                else:
                    # 驗證全部重試失敗（verification 仍為 None），繼續生成循環
                    print(f"[DailyMessage] ⚠️ 驗證階段全部失敗 ({ver_max} 次)，回到生成階段")
                    verification = None

            # 最終檢查
            if generated is None:
                print("[DailyMessage] ❌ 內容生成失敗，今日不發送")
                return
            if verification is None:
                print("[DailyMessage] ⚠️ 驗證階段最終失敗，使用未驗證內容發送")

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
