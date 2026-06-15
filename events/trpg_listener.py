import json
import os
import re

import discord
from discord import Message
from discord.ext import commands

from database.trpg_db import trpgDB

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
DEEPSEEK_MODEL = "deepseek-flash"

try:
    from openai import AsyncOpenAI
    _ai_client = AsyncOpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL) if DEEPSEEK_API_KEY else None
except ImportError:
    _ai_client = None


OPENING_PROMPT = """你是這個 TRPG 世界的遊戲主持人。遊戲即將開始，請生成開場敘事。

## 世界設定
{world_setting}

## 故事大綱
{story_outline}

## 最終目標
{final_goal}

## 目前玩家與角色
{players_info}

## 規則
{rules_summary}

請回傳 JSON：
{{
    "opening": "開場敘事（150-400字繁體中文，描述當前場景、氛圍、玩家們的處境）",
    "first_player": "建議首位行動的玩家角色名稱",
    "hint": "給首位玩家的提示（20-40字，建議他做什麼）"
}}

注意：開場敘事需生動描述當前場景，讓玩家有身歷其境感。所有文字繁體中文。"""


# 動態創角 AI 提示模板（不預設任何世界觀，完全由 character_creation 驅動）
STEP_PROMPTS = {
    "step_intro": """你是這個 TRPG 世界的創角引導者。根據世界設定引導玩家完成角色創建。

## 世界背景
{world_setting}

## 當前步驟：{step_field}
{step_prompt}

## 可選項目
{options_text}

## 玩家名稱
{player_name}

請用繁體中文撰寫引導文字，介紹可選項目並讓玩家從中選擇一個。
列出選項讓玩家直接輸入名稱即可選擇。""",

    "stats_generate": """根據以下資訊生成角色完整數值。

## 世界規則（角色創建規則）
{world_rules}

## 玩家的所有選擇
{all_choices}

## 玩家名稱
{player_name}

請嚴格按照以下 JSON 格式回傳，不要包含任何其他文字：
{{
    "name": "角色名稱",
    "stats": {{
        ...（根據 world_rules.character_creation 的 base_attributes 和 free_attributes 生成，不要使用預設 hp/san）
    }},
    "description": "簡短角色描述（30-80字繁體中文）"
}}

注意：
- stats 中的 key 根據 world_rules.character_creation 的 base_attributes 和 free_attributes 決定
- 數值需合理分配，free_points 為可自由分配點數
- 所有文字繁體中文""",
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

        join_keywords = ("加入", "加入游戏", "join")
        if any(content.startswith(k) for k in join_keywords) and thread.id == game["main_thread_id"]:
            await self._handle_join(message, thread, forum_channel, game)
            return

        start_keywords = ("開始遊戲", "开始游戏", "開始", "开始", "start")
        if any(content in (k,) for k in start_keywords) and thread.id == game["main_thread_id"]:
            await self._handle_start_game(message, thread, game)
            return

        if game["current_stage"] == "創角":
            await self._handle_creation_multi(message, thread, game)

    async def _handle_start_game(self, message: Message, thread: discord.Thread, game: dict) -> None:
        if message.author.id != game["owner_id"]:
            await message.reply("❌ 只有遊戲建立者可以開始遊戲。", mention_author=False)
            return
        if game["current_stage"] != "創角":
            await message.reply("❌ 遊戲已經開始或已結束。", mention_author=False)
            return

        # 收集已創角完成的玩家
        characters = await trpgDB.get_characters_by_game(game["id"])
        ready_players = [c for c in characters if c["name"] and c["character_type"] == "player"]
        if not ready_players:
            await message.reply("❌ 目前沒有完成創角的玩家，無法開始遊戲。", mention_author=False)
            return

        await trpgDB.update_game_stage(game["id"], "進行中")

        # 構建玩家資訊
        guild = message.guild
        players_info = "\n".join(
            f"- {c['name']}（{guild.get_member(c['discord_user_id']).display_name if c['discord_user_id'] and guild else '???'}）"
            for c in ready_players
        )

        wr = self._parse_world_rules(game)
        rules_summary = wr.get("rules_summary", "D20 判定系統")

        # AI 生成開場
        opening_text, first_player, hint = await self._generate_opening(
            game, players_info, rules_summary
        )

        # 發佈開場 embed
        embed = discord.Embed(
            title=f"📜 {game['name']} — 遊戲開始",
            description=(opening_text[:2048] if opening_text else f"**{game['name']}** 正式開始！"),
            color=discord.Color.gold(),
        )
        embed.add_field(name="👥 玩家", value=str(len(ready_players)), inline=True)
        embed.add_field(name="🎯 首位行動", value=f"**{first_player}**" if first_player else "任意玩家", inline=True)
        if hint:
            embed.add_field(name="💡 提示", value=hint, inline=False)
        embed.add_field(
            name="🎲 指令",
            value="`D <行動>` 擲骰判定 | `說 <內容>` 對話 | `/status` 查看狀態",
            inline=False,
        )
        await thread.send(embed=embed)

    async def _generate_opening(self, game: dict, players_info: str, rules_summary: str) -> tuple[str, str, str]:
        """AI 生成開場敘事，回傳 (opening, first_player, hint)"""
        if not _ai_client:
            return (
                f"**{game['name']}** 的冒險正式展開！\n\n{game.get('world_setting', '')[:500]}\n\n準備好開始你的旅程。",
                "", ""
            )

        prompt = OPENING_PROMPT.format(
            world_setting=game.get("world_setting", ""),
            story_outline=game.get("story_outline", ""),
            final_goal=game.get("final_goal", ""),
            players_info=players_info,
            rules_summary=rules_summary,
        )
        try:
            response = await _ai_client.chat.completions.create(
                model=DEEPSEEK_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.9, max_tokens=1000, timeout=45,
            )
            raw = response.choices[0].message.content.strip()
            parsed = self._parse_json(raw)
            if parsed:
                return (
                    parsed.get("opening", ""),
                    parsed.get("first_player", ""),
                    parsed.get("hint", ""),
                )
            return (raw[:2000], "", "")
        except Exception as e:
            print(f"[TRPGListener] opening gen error: {e}")
            return (
                f"**{game['name']}** 的冒險正式展開！\n\n{game.get('world_setting', '')[:500]}",
                "", ""
            )

    async def _handle_join(self, message: Message, thread: discord.Thread,
                           forum_channel: discord.ForumChannel, game: dict) -> None:
        if game["current_stage"] != "創角":
            await message.reply("❌ 此遊戲已過了創角階段。", mention_author=False)
            return
        player_count = await trpgDB.get_player_count(game["id"])
        if player_count >= game["max_players"]:
            await message.reply(embed=discord.Embed(title="❌ 人數已滿", color=discord.Color.red()), mention_author=False)
            return
        existing = await trpgDB.get_character_by_discord(game["id"], message.author.id)
        if existing is not None:
            await message.reply("❌ 你已經加入了此遊戲！", mention_author=False)
            return
        try:
            player_thread, _ = await forum_channel.create_thread(
                name=f"📋 {message.author.display_name} 的角色卡",
                content="⏳ AI 正在生成創角引導...",
                reason=f"{message.author} 加入 {game['name']}",
            )
        except discord.HTTPException as e:
            await message.reply(f"❌ 建立角色卡貼文失敗：{e}", mention_author=False)
            return
        character_id = await trpgDB.create_character(
            game_id=game["id"], discord_user_id=message.author.id, thread_id=player_thread.id,
        )
        await message.reply(
            embed=discord.Embed(
                title="🎉 加入成功！",
                description=f"{message.author.mention} 已加入 **{game['name']}**\n請到 {player_thread.mention} 完成創角！\n💡 創角完成後，建立者可輸入 `開始遊戲` 啟動。",
                color=discord.Color.green(),
            ).add_field(name="人數", value=f"{player_count+1}/{game['max_players']}", inline=True),
            mention_author=False,
        )
        # 第一步引導
        intro = await self._gen_step_intro(game, message.author.display_name, 0)
        await player_thread.edit(name=f"📋 {message.author.display_name} 的角色卡")
        await player_thread.send(intro)

    async def _handle_creation_multi(self, message: Message, thread: discord.Thread, game: dict) -> None:
        """多輪創角：根據 character_creation.steps 動態分流"""
        character = await trpgDB.get_character_by_thread(thread.id, game["id"])
        if character is None:
            return
        if character["discord_user_id"] != message.author.id:
            return
        if character["name"]:
            return

        content = message.content.strip()
        world_rules = self._parse_world_rules(game)
        cc = world_rules.get("character_creation", {})
        steps = cc.get("steps", [])
        step = character.get("creation_step", 0)
        total = len(steps)

        if step < total:
            await self._step_choice(message, thread, game, character, content, step, steps, cc)
        elif step == total:
            await self._step_confirm_or_retry(message, thread, game, character, content)

    async def _gen_step_intro(self, game: dict, player_name: str, step_idx: int) -> str:
        """根據 character_creation.steps[step_idx] 生成引導文字"""
        world_rules = self._parse_world_rules(game)
        cc = world_rules.get("character_creation", {})
        steps = cc.get("steps", [])
        if not steps or step_idx >= len(steps):
            return self._fallback_generic_intro(player_name)

        step_info = steps[step_idx]
        options = step_info.get("options", [])
        options_text = "\n".join(f"• {o['name']} — {o.get('description', '')}" for o in options) if options else "（請描述你想扮演的角色）"

        if not _ai_client:
            return self._fallback_step_intro(player_name, step_info, options_text)

        prompt = STEP_PROMPTS["step_intro"].format(
            world_setting=game.get("world_setting", ""),
            step_field=step_info.get("field", "角色特質"),
            step_prompt=step_info.get("prompt", "請選擇"),
            options_text=options_text,
            player_name=player_name,
        )
        try:
            resp = await _ai_client.chat.completions.create(
                model=DEEPSEEK_MODEL, messages=[{"role": "user", "content": prompt}],
                temperature=0.8, max_tokens=800, timeout=30,
            )
            text = resp.choices[0].message.content.strip()
            return text if text else self._fallback_step_intro(player_name, step_info, options_text)
        except Exception:
            return self._fallback_step_intro(player_name, step_info, options_text)

    async def _step_choice(self, message: Message, thread: discord.Thread,
                           game: dict, character: dict, choice: str,
                           step: int, steps: list, cc: dict) -> None:
        """處理任一步驟的選擇"""
        step_info = steps[step]
        field = step_info.get("field", f"step_{step}")
        current_stats = {}
        try:
            current_stats = json.loads(character.get("stats", "{}"))
        except (json.JSONDecodeError, TypeError):
            pass
        current_stats[field] = choice
        await trpgDB.update_character(character["id"], stats=json.dumps(current_stats, ensure_ascii=False))

        next_step = step + 1
        if next_step < len(steps):
            # 還有下一步
            next_info = steps[next_step]
            next_options = next_info.get("options", [])
            next_options_text = "\n".join(f"• {o['name']} — {o.get('description', '')}" for o in next_options) if next_options else "（請描述）"
            if _ai_client:
                prompt = STEP_PROMPTS["step_intro"].format(
                    world_setting=game.get("world_setting", ""),
                    step_field=next_info.get("field", "下一步"),
                    step_prompt=next_info.get("prompt", "請選擇"),
                    options_text=next_options_text,
                    player_name=message.author.display_name,
                )
                try:
                    resp = await _ai_client.chat.completions.create(
                        model=DEEPSEEK_MODEL, messages=[{"role": "user", "content": prompt}],
                        temperature=0.8, max_tokens=800, timeout=30,
                    )
                    guide = resp.choices[0].message.content.strip() or self._fallback_step_intro(message.author.display_name, next_info, next_options_text)
                except Exception:
                    guide = self._fallback_step_intro(message.author.display_name, next_info, next_options_text)
            else:
                guide = self._fallback_step_intro(message.author.display_name, next_info, next_options_text)
            await trpgDB.update_character_creation_step(character["id"], next_step)
            await thread.send(guide)
        else:
            # 最後一步 → 生成完整數值
            await trpgDB.update_character_creation_step(character["id"], next_step)
            await self._gen_final_stats_and_show(message, thread, game, character, current_stats, cc)

    async def _gen_final_stats_and_show(self, message: Message, thread: discord.Thread,
                                        game: dict, character: dict, choices: dict, cc: dict) -> None:
        """AI 根據所有選擇生成完整數值並顯示角色卡"""
        world_rules_str = game.get("world_rules", "{}")
        all_choices_str = "\n".join(f"{k}: {v}" for k, v in choices.items())

        if _ai_client:
            prompt = STEP_PROMPTS["stats_generate"].format(
                world_rules=world_rules_str,
                all_choices=all_choices_str,
                player_name=message.author.display_name,
            )
            try:
                resp = await _ai_client.chat.completions.create(
                    model=DEEPSEEK_MODEL, messages=[{"role": "user", "content": prompt}],
                    temperature=0.7, max_tokens=800, timeout=30,
                )
                raw = resp.choices[0].message.content.strip()
                parsed = self._parse_json(raw)
                if parsed and "stats" in parsed:
                    stats_data = parsed
                else:
                    stats_data = self._fallback_stats(message.author.display_name, choices)
            except Exception:
                stats_data = self._fallback_stats(message.author.display_name, choices)
        else:
            stats_data = self._fallback_stats(message.author.display_name, choices)

        name = stats_data.get("name", message.author.display_name)
        stats = stats_data.get("stats", choices)
        description = stats_data.get("description", "")

        await trpgDB.update_character(character["id"], name=name, stats=json.dumps(stats, ensure_ascii=False))

        embed = self._build_character_card(message.author.display_name, name, stats, description)
        embed.add_field(name="✅ 確認角色？", value="輸入 `確認` 完成，或輸入 `重選` 重新選擇", inline=False)
        await thread.send(embed=embed)

    async def _step_confirm_or_retry(self, message: Message, thread: discord.Thread,
                                     game: dict, character: dict, content: str) -> None:
        if content in ("確認", "confirm", "確定"):
            await trpgDB.update_character_creation_step(character["id"], 4)
            stats = {}
            try:
                stats = json.loads(character.get("stats", "{}"))
            except (json.JSONDecodeError, TypeError):
                pass
            name = stats.get("角色名稱", "") or character.get("name", "") or message.author.display_name
            # 確保 name 已寫入 DB
            if name and not character.get("name"):
                await trpgDB.update_character(character["id"], name=name)
            embed = self._build_character_card(message.author.display_name, name, stats, "")
            embed.set_footer(text="角色創建完成！準備開始你的冒險！")
            await thread.edit(name=f"📋 {message.author.display_name} — {name}")
            await thread.send(embed=embed)
        elif content in ("重選", "retry", "重新"):
            await trpgDB.update_character(character["id"], stats=json.dumps({}, ensure_ascii=False))
            await trpgDB.update_character_creation_step(character["id"], 0)
            intro = await self._gen_step_intro(game, message.author.display_name, 0)
            await thread.send(f"🔄 重新開始創角！\n\n{intro}")
        else:
            await message.reply("請輸入 `確認` 完成創角，或輸入 `重選` 重新選擇。", mention_author=False)

    ##### 輔助方法 #####

    def _fallback_step_intro(self, player_name: str, step_info: dict, options_text: str) -> str:
        field = step_info.get("field", "角色特質")
        prompt = step_info.get("prompt", f"請選擇你的{field}")
        return f"**{prompt}**\n\n{options_text}\n\n請直接輸入你的選擇。"

    def _fallback_generic_intro(self, player_name: str) -> str:
        return (
            f"歡迎 {player_name}！請描述你想扮演的角色類型、背景與能力，"
            "我會據此為你生成角色卡。"
        )

    def _fallback_stats(self, player_name: str, choices: dict) -> dict:
        import hashlib
        seed = int(hashlib.md5(str(choices).encode()).hexdigest()[:8], 16)
        r = lambda lo, hi: (seed % (hi - lo + 1)) + lo
        tags = "、".join(choices.values()) if choices else "冒險者"
        return {
            "name": player_name,
            "stats": {"生命": r(80, 150), "意志": r(60, 100), "tags": tags},
            "description": f"基於「{tags}」生成的初始角色。",
        }

    def _build_character_card(self, display_name: str, name: str, stats: dict, description: str) -> discord.Embed:
        embed = discord.Embed(
            title=f"📋 {display_name} 的角色卡 — {name}",
            color=discord.Color.blue(), timestamp=discord.utils.utcnow(),
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
