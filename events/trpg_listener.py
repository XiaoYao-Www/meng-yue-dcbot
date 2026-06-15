import json
import os
import re

import discord
from discord import Message
from discord.ext import commands

from database.trpg_db import trpgDB

# AI 客戶端
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
DEEPSEEK_MODEL = "deepseek-flash"

try:
    from openai import AsyncOpenAI
    _ai_client = AsyncOpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL) if DEEPSEEK_API_KEY else None
except ImportError:
    _ai_client = None


# 多輪創角 AI 提示模板
STEP_PROMPTS = {
    "race_intro": """你是這個 TRPG 世界的創角引導者。根據以下世界設定，引導玩家選擇**種族**。

## 世界背景
{world_setting}

## 可用種族
{races_text}

## 玩家名稱
{player_name}

請用繁體中文撰寫一段引導文字，介紹這個世界的種族（不要列職業，只需種族）。
列出選項讓玩家輸入種族名稱選擇。
格式範例：
---
（世界背景簡述...）
你可以選擇以下種族：
🌿 精靈 — 優雅而長壽，與自然共鳴
⚔️ 人類 — 適應力強，充滿潛力
⛰️ 矮人 — 堅韌頑強，大地之子

請直接輸入你選擇的種族名稱（如：精靈）""",

    "class_intro": """根據玩家選擇的種族「{race_choice}」，引導選擇**職業**。

## 世界背景
{world_setting}

## 該種族可選職業
{classes_text}

## 玩家名稱
{player_name}

請用繁體中文撰寫引導文字，推薦適合 {race_choice} 的職業選項。
列出選項讓玩家輸入職業名稱選擇。

請直接輸入你選擇的職業名稱（如：戰士）""",

    "stats_generate": """根據以下資訊生成角色完整數值。

## 世界規則
{world_rules}

## 玩家選擇
種族：{race_choice}
職業：{class_choice}

## 玩家名稱
{player_name}

請嚴格按照以下 JSON 格式回傳，不要包含任何其他文字：

{{
    "name": "角色名稱（可自定義或使用「{player_name}」）",
    "stats": {{
        "hp": 100,
        "san": 100,
        "種族": "{race_choice}",
        "職業": "{class_choice}",
        ...（根據 world_rules 的 base_stats 與種族特性分配合理數值）
    }},
    "description": "簡短的角色描述（30-80字）"
}}

注意：
- stats 中的數值根據 world_rules 的 base_stats 與種族特性合理分配
- 可以加入自定義屬性，不需要固定為力量/敏捷/智力/體質
- 必須使用繁體中文
- 所有數值是該角色的初始值""",
}


class TRPGListener(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: Message):
        if message.author.bot:
            return

        thread = message.channel
        if not isinstance(thread, discord.Thread):
            return

        forum_channel = thread.parent
        if forum_channel is None or not isinstance(forum_channel, discord.ForumChannel):
            return

        try:
            game = await trpgDB.get_game_by_forum(forum_channel.id)
        except Exception:
            return
        if game is None:
            return

        content = message.content.strip()

        # 加入指令（僅在主頻道）
        if content.startswith("加入") and thread.id == game["main_thread_id"]:
            await self._handle_join(message, thread, forum_channel, game)
            return

        # 開始遊戲指令（僅在主頻道、創角階段、遊戲擁有者可觸發）
        if content in ("開始遊戲", "開始") and thread.id == game["main_thread_id"]:
            await self._handle_start_game(message, thread, game)
            return

        # 多輪創角流程（在角色子貼文中）
        if game["current_stage"] == "創角":
            await self._handle_creation_multi(message, thread, game)

    async def _handle_start_game(
        self, message: Message, thread: discord.Thread, game: dict
    ) -> None:
        """### 遊戲擁有者手動開始遊戲（不需等滿員）"""
        if message.author.id != game["owner_id"]:
            await message.reply("❌ 只有遊戲建立者可以開始遊戲。", mention_author=False)
            return

        if game["current_stage"] != "創角":
            await message.reply("❌ 遊戲已經開始或已結束。", mention_author=False)
            return

        player_count = await trpgDB.get_player_count(game["id"])
        if player_count == 0:
            await message.reply("❌ 目前沒有任何玩家加入，無法開始遊戲。", mention_author=False)
            return

        # 切換階段
        await trpgDB.update_game_stage(game["id"], "進行中")

        # 公告開始
        embed = discord.Embed(
            title="🎮 遊戲開始！",
            description=(
                f"**{game['name']}** 正式開始！\n"
                f"👥 目前玩家：{player_count} 人\n\n"
                f"使用 `D20 <行動描述>` 來進行遊戲！\n"
                f"例如：`D20 檢查房間角落`"
            ),
            color=discord.Color.green(),
        )
        await thread.send(embed=embed)

    async def _handle_join(
        self, message: Message, thread: discord.Thread,
        forum_channel: discord.ForumChannel, game: dict
    ) -> None:
        if game["current_stage"] != "創角":
            await message.reply("❌ 此遊戲已過了創角階段，無法加入。", mention_author=False)
            return

        player_count = await trpgDB.get_player_count(game["id"])
        if player_count >= game["max_players"]:
            await message.reply(
                embed=discord.Embed(title="❌ 人數已滿",
                                    description=f"最多 {game['max_players']} 人，已達上限。",
                                    color=discord.Color.red()),
                mention_author=False
            )
            return

        existing = await trpgDB.get_character_by_discord(game["id"], message.author.id)
        if existing is not None:
            await message.reply("❌ 你已經加入了此遊戲！", mention_author=False)
            return

        # 建立角色子貼文
        try:
            player_thread, _ = await forum_channel.create_thread(
                name=f"📋 {message.author.display_name} 的角色卡",
                content="⏳ AI 正在生成創角引導，請稍候...",
                reason=f"{message.author} 加入 {game['name']}",
            )
        except discord.HTTPException as e:
            await message.reply(f"❌ 建立角色卡貼文失敗：{e}", mention_author=False)
            return

        character_id = await trpgDB.create_character(
            game_id=game["id"],
            discord_user_id=message.author.id,
            thread_id=player_thread.id,
        )

        await message.reply(
            embed=discord.Embed(
                title="🎉 加入成功！",
                description=(
                    f"{message.author.mention} 已加入 **{game['name']}**\n"
                    f"請到 {player_thread.mention} 完成角色創建！"
                ),
                color=discord.Color.green(),
            ).add_field(name="當前人數", value=f"{player_count + 1}/{game['max_players']}", inline=True),
            mention_author=False
        )

        # 第一步：AI 引導選擇種族（creation_step = 0）
        intro_text = await self._step_race_intro(game, message.author.display_name)
        await player_thread.edit(name=f"📋 {message.author.display_name} 的角色卡")
        await player_thread.send(intro_text)

    async def _handle_creation_multi(self, message: Message, thread: discord.Thread, game: dict) -> None:
        """多輪創角：根據 creation_step 分流"""
        character = await trpgDB.get_character_by_thread(thread.id, game["id"])
        if character is None:
            return
        if character["discord_user_id"] != message.author.id:
            return

        step = character.get("creation_step", 0)
        content = message.content.strip()

        # 已完成創角
        if character["name"]:
            return

        if step == 0:
            # 玩家輸入了種族選擇
            await self._step_race_choice(message, thread, game, character, content)
        elif step == 1:
            # 玩家輸入了職業選擇
            await self._step_class_choice(message, thread, game, character, content)
        elif step == 2:
            # 玩家確認或拒絕角色數值
            await self._step_confirm_or_retry(message, thread, game, character, content)

    async def _step_race_intro(self, game: dict, player_name: str) -> str:
        """AI 生成種族選擇引導"""
        world_rules = self._parse_world_rules(game)

        if not _ai_client:
            return self._fallback_race_intro(player_name, world_rules)

        races_text = self._format_list(world_rules.get("races", []), "name", "description")
        prompt = STEP_PROMPTS["race_intro"].format(
            world_setting=game.get("world_setting", "未知的世界"),
            races_text=races_text or "無特殊種族設定",
            player_name=player_name,
        )

        try:
            response = await _ai_client.chat.completions.create(
                model=DEEPSEEK_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.8, max_tokens=800, timeout=30,
            )
            text = response.choices[0].message.content.strip()
            return text if text else self._fallback_race_intro(player_name, world_rules)
        except Exception as e:
            print(f"[TRPGListener] AI race intro error: {e}")
            return self._fallback_race_intro(player_name, world_rules)

    async def _step_race_choice(
        self, message: Message, thread: discord.Thread,
        game: dict, character: dict, choice: str
    ) -> None:
        """處理種族選擇 → 進入職業選擇"""
        # 儲存種族到 stats
        stats = {"種族": choice}

        # AI 生成職業引導（用已有種族資訊）
        world_rules = self._parse_world_rules(game)
        if _ai_client:
            classes_text = self._format_list(world_rules.get("classes", []), "name", "description")
            prompt = STEP_PROMPTS["class_intro"].format(
                race_choice=choice,
                world_setting=game.get("world_setting", ""),
                classes_text=classes_text or "無特殊職業設定",
                player_name=message.author.display_name,
            )
            try:
                response = await _ai_client.chat.completions.create(
                    model=DEEPSEEK_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.8, max_tokens=800, timeout=30,
                )
                guide_text = response.choices[0].message.content.strip() or self._fallback_class_intro(choice, world_rules)
            except Exception:
                guide_text = self._fallback_class_intro(choice, world_rules)
        else:
            guide_text = self._fallback_class_intro(choice, world_rules)

        await trpgDB.update_character(character["id"], stats=json.dumps(stats, ensure_ascii=False))
        await trpgDB.update_character_creation_step(character["id"], 1)
        await thread.send(guide_text)

    async def _step_class_choice(
        self, message: Message, thread: discord.Thread,
        game: dict, character: dict, choice: str
    ) -> None:
        """處理職業選擇 → AI 生成完整數值"""
        # 合併種族 + 職業
        current_stats = {}
        try:
            current_stats = json.loads(character.get("stats", "{}"))
        except (json.JSONDecodeError, TypeError):
            pass
        current_stats["職業"] = choice

        if _ai_client:
            prompt = STEP_PROMPTS["stats_generate"].format(
                world_rules=game.get("world_rules", "{}"),
                race_choice=current_stats.get("種族", "未知"),
                class_choice=choice,
                player_name=message.author.display_name,
            )
            try:
                response = await _ai_client.chat.completions.create(
                    model=DEEPSEEK_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.7, max_tokens=800, timeout=30,
                )
                raw = response.choices[0].message.content.strip()
                parsed = self._parse_json(raw)
                if parsed and "stats" in parsed:
                    stats_data = parsed
                else:
                    stats_data = self._fallback_stats(message.author.display_name, current_stats)
            except Exception:
                stats_data = self._fallback_stats(message.author.display_name, current_stats)
        else:
            stats_data = self._fallback_stats(message.author.display_name, current_stats)

        name = stats_data.get("name", choice)
        stats = stats_data.get("stats", current_stats)
        description = stats_data.get("description", "")

        await trpgDB.update_character(character["id"], stats=json.dumps(stats, ensure_ascii=False))
        await trpgDB.update_character_creation_step(character["id"], 2)

        # 顯示角色卡讓玩家確認
        embed = self._build_character_card(message.author.display_name, name, stats, description)
        embed.add_field(name="✅ 確認角色？", value="輸入 `確認` 完成創角，或輸入 `重選` 重新選擇", inline=False)
        await thread.send(embed=embed)

    async def _step_confirm_or_retry(
        self, message: Message, thread: discord.Thread,
        game: dict, character: dict, content: str
    ) -> None:
        """處理確認或重選"""
        if content in ("確認", "confirm", "確定"):
            # 完成創角
            await trpgDB.update_character_creation_step(character["id"], 4)
            stats = {}
            try:
                stats = json.loads(character.get("stats", "{}"))
            except (json.JSONDecodeError, TypeError):
                pass
            name = character.get("name", message.author.display_name)

            embed = self._build_character_card(message.author.display_name, name, stats, "")
            embed.set_footer(text="角色創建完成！準備開始你的冒險！")
            await thread.edit(name=f"📋 {message.author.display_name} — {name}")
            await thread.send(embed=embed)

        elif content in ("重選", "retry", "重新"):
            # 回到第一步重新選擇種族
            await trpgDB.update_character(character["id"], stats=json.dumps({"種族": "", "職業": ""}, ensure_ascii=False))
            await trpgDB.update_character_creation_step(character["id"], 0)
            intro = await self._step_race_intro(game, message.author.display_name)
            await thread.send(f"🔄 重新開始創角！\n\n{intro}")
        else:
            await message.reply("請輸入 `確認` 完成創角，或輸入 `重選` 重新選擇種族。", mention_author=False)

    ##### AI 輔助方法 #####
    def _fallback_race_intro(self, player_name: str, world_rules: dict) -> str:
        races = world_rules.get("races", [])
        lines = [f"歡迎 {player_name}！請選擇你的種族：\n"]
        for r in races:
            lines.append(f"• {r.get('name', '?')} — {r.get('description', '')}")
        lines.append("\n請輸入種族名稱（如：" + races[0]["name"] + "）")
        return "\n".join(lines)

    def _fallback_class_intro(self, race: str, world_rules: dict) -> str:
        classes = world_rules.get("classes", [])
        lines = [f"你選擇了 {race}！請選擇你的職業：\n"]
        for c in classes:
            lines.append(f"• {c.get('name', '?')} — {c.get('description', '')}")
        lines.append("\n請輸入職業名稱（如：" + classes[0]["name"] + "）")
        return "\n".join(lines)

    def _fallback_stats(self, player_name: str, current_stats: dict) -> dict:
        import hashlib
        seed = int(hashlib.md5(str(current_stats).encode()).hexdigest()[:8], 16)
        r = lambda lo, hi: (seed % (hi - lo + 1)) + lo
        race = current_stats.get("種族", "未知")
        cls = current_stats.get("職業", "冒險者")
        return {
            "name": f"{race}{cls}",
            "stats": {
                "hp": r(80, 150), "san": r(60, 150),
                "種族": race, "職業": cls,
                "力量": r(2, 8), "敏捷": r(2, 8),
                "智力": r(2, 8), "體質": r(2, 8),
                "技能": ["基礎戰鬥", "觀察"],
            },
            "description": f"{race}{cls}，初始冒險者。",
        }

    def _build_character_card(self, display_name: str, name: str, stats: dict, description: str) -> discord.Embed:
        embed = discord.Embed(
            title=f"📋 {display_name} 的角色卡 — {name}",
            color=discord.Color.blue(),
            timestamp=discord.utils.utcnow(),
        )
        for key, value in stats.items():
            if key == "技能" and isinstance(value, list):
                embed.add_field(name="🎯 技能", value="、".join(value), inline=False)
            else:
                embed.add_field(name=f"📊 {key}", value=str(value), inline=True)
        if description:
            embed.add_field(name="📖 角色描述", value=description, inline=False)
        return embed

    def _parse_world_rules(self, game: dict) -> dict:
        try:
            return json.loads(game.get("world_rules", "{}"))
        except (json.JSONDecodeError, TypeError):
            return {}

    def _format_list(self, items: list, name_key: str, desc_key: str) -> str:
        if not items:
            return ""
        return "\n".join(f"- {it.get(name_key, '?')}: {it.get(desc_key, '')}" for it in items)

    @staticmethod
    def _parse_json(raw: str) -> dict | None:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
        match = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
        brace_start = raw.find("{")
        brace_end = raw.rfind("}")
        if brace_start != -1 and brace_end != -1:
            try:
                return json.loads(raw[brace_start:brace_end + 1])
            except json.JSONDecodeError:
                pass
        return None


async def setup(bot: commands.Bot):
    await bot.add_cog(TRPGListener(bot))
