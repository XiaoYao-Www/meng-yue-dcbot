import asyncio
import json
import os
import random
import re

import discord
from discord import Message
from discord.ext import commands

from database.trpg_db import trpgDB

# AI 客戶端
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
DEEPSEEK_MODEL = "deepseek-chat"

try:
    from openai import AsyncOpenAI
    _ai_client = AsyncOpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL) if DEEPSEEK_API_KEY else None
except ImportError:
    _ai_client = None


GM_PROMPT_TEMPLATE = """你是一個 TRPG 遊戲主持人 (Game Master)。請根據以下完整遊戲狀態，對玩家的行動進行判定與回應。

## 世界設定
{world_setting}

## 規則摘要
{rules_summary}

## 近期劇情紀錄（最新在前）
{recent_narratives}

## 近期對話
{recent_dialogues}

## 當前玩家角色
名稱: {character_name}
數值: {character_stats}
狀態: {character_status}
持有道具: {inventory}

## 玩家行動
{action}

## D20 擲骰結果
{d20_roll} / 20 → {result}

========================================
請嚴格按照以下 JSON 格式回傳，不要包含任何其他文字：

{{
    "narrative": "劇情敘述文字（100-300字，繁體中文，生動描寫行動結果）",

    "stat_changes": {{
        "hp": 0,
        "san": 0
    }},

    "new_items": [],

    "new_npcs": [],

    "dialogues": [],

    "action_log": "此行動的簡短摘要（10-30字）"
}}

注意：
- stat_changes 中的數值：正數=增加，負數=減少，0=不變
- new_items 格式：{{"name": "道具名", "description": "描述", "properties": {{}}, "is_known": true}}
- new_npcs 格式：{{"name": "NPC/怪物名", "description": "描述", "stats": {{"hp": 50}}, "is_hostile": false, "is_known": true}}
- dialogues 格式：{{"speaker": "NPC名稱", "content": "對話內容"}}
- 沒有新項目時，new_items / new_npcs / dialogues 保持空列表
- 所有文字使用繁體中文"""


class TRPGD20Event(commands.Cog):
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
        if game is None or game["current_stage"] == "創角":
            return

        content = message.content.strip()
        if not content.startswith("D20") and not content.startswith("d20"):
            return

        action = content[3:].strip() if len(content) > 3 else "執行行動"
        await self._process_action(message, thread, game, action)

    async def _process_action(
        self, message: Message, thread: discord.Thread,
        game: dict, action: str
    ) -> None:
        character = await trpgDB.get_character_by_discord(game["id"], message.author.id)
        if character is None:
            await message.reply("❌ 你沒有參與此遊戲，無法行動。", mention_author=False)
            return

        # D20 擲骰
        d20_roll = random.randint(1, 20)
        if d20_roll == 20:
            result = "大成功"
        elif d20_roll >= 15:
            result = "成功"
        elif d20_roll >= 10:
            result = "普通"
        elif d20_roll >= 2:
            result = "失敗"
        else:
            result = "大失敗"

        # 解析世界規則
        world_rules = {}
        try:
            world_rules = json.loads(game.get("world_rules", "{}"))
        except (json.JSONDecodeError, TypeError):
            pass

        # 解析角色數值
        char_stats = {}
        try:
            char_stats = json.loads(character.get("stats", "{}"))
        except (json.JSONDecodeError, TypeError):
            pass
        stats_str = ", ".join(f"{k}: {v}" for k, v in char_stats.items()) if char_stats else "（無特殊數值）"

        # 取得持有道具
        items = await trpgDB.get_items_by_owner(character["id"])
        inv_str = "、".join(it["name"] for it in items) if items else "無"

        # 取得近期劇情紀錄
        narratives = await trpgDB.get_recent_narratives(game["id"], limit=5)
        recent_narratives = "\n".join(
            f"[{n['created_at'][:19]}] {n['action']} → {n['result']}: {n['narrative'][:100]}"
            for n in narratives
        ) if narratives else "（尚無紀錄）"

        # 取得近期對話
        dialogues_list = await trpgDB.get_recent_dialogues(game["id"], limit=5)
        recent_dialogues = "\n".join(
            f"[{d['created_at'][:19]}] {d['content'][:100]}"
            for d in dialogues_list
        ) if dialogues_list else "（尚無對話）"

        # 構建 prompt
        rules_summary = world_rules.get("rules_summary", "無特殊規則，D20 判定。")
        prompt = GM_PROMPT_TEMPLATE.format(
            world_setting=game.get("world_setting", "未知的世界"),
            rules_summary=rules_summary,
            recent_narratives=recent_narratives,
            recent_dialogues=recent_dialogues,
            character_name=character.get("name", message.author.display_name),
            character_stats=stats_str,
            character_status=character.get("status", "存活"),
            inventory=inv_str,
            action=action,
            d20_roll=d20_roll,
            result=result,
        )

        thinking_msg = await message.reply("🎲 擲骰中...", mention_author=False)

        # 呼叫 AI
        response_data = await self._call_ai(prompt, action, d20_roll, result, game["name"])

        narrative = response_data.get("narrative", "")
        stat_changes = response_data.get("stat_changes", {})
        new_items = response_data.get("new_items", [])
        new_npcs = response_data.get("new_npcs", [])
        dialogues = response_data.get("dialogues", [])
        action_log = response_data.get("action_log", f"{action} → {result}")

        # 更新角色數值
        if stat_changes:
            for stat_key, change in stat_changes.items():
                if change != 0 and stat_key in char_stats:
                    old_val = char_stats[stat_key]
                    char_stats[stat_key] = max(0, old_val + change)
            await trpgDB.update_character_stats(character["id"], json.dumps(char_stats, ensure_ascii=False))

        # 建立新道具
        for item_data in new_items:
            try:
                item_id = await trpgDB.create_item(
                    game_id=game["id"],
                    name=item_data.get("name", "未知道具"),
                    description=item_data.get("description", ""),
                    properties=json.dumps(item_data.get("properties", {}), ensure_ascii=False),
                    is_known=item_data.get("is_known", True),
                )
                # 自動放入角色背包
                await trpgDB.add_item_to_inventory(character["id"], item_id)

                # 建立 Discord 子貼文
                if forum := thread.parent:
                    try:
                        item_thread, _ = await forum.create_thread(
                            name=f"📦 {item_data['name']}",
                            content=f"**{item_data['name']}**\n\n{item_data.get('description', '')}",
                            reason=f"{game['name']} 新道具",
                        )
                        await trpgDB.update_item_thread(item_id, item_thread.id)
                    except discord.HTTPException:
                        pass
            except Exception as e:
                print(f"[AI GM] 建立道具失敗: {e}")

        # 建立新 NPC/怪物
        for npc_data in new_npcs:
            try:
                npc_id = await trpgDB.create_npc(
                    game_id=game["id"],
                    name=npc_data.get("name", "未知"),
                    description=npc_data.get("description", ""),
                    stats=json.dumps(npc_data.get("stats", {}), ensure_ascii=False),
                    is_hostile=npc_data.get("is_hostile", False),
                    is_known=npc_data.get("is_known", True),
                )
                if forum := thread.parent:
                    try:
                        icon = "🦄" if npc_data.get("is_hostile") else "👤"
                        npc_thread, _ = await forum.create_thread(
                            name=f"{icon} {npc_data['name']}",
                            content=f"**{npc_data['name']}**\n\n{npc_data.get('description', '')}",
                            reason=f"{game['name']} 新NPC",
                        )
                        await trpgDB.update_npc_thread(npc_id, npc_thread.id)
                    except discord.HTTPException:
                        pass
            except Exception as e:
                print(f"[AI GM] 建立NPC失敗: {e}")

        # 記錄對話（NPC 發言）
        for d in dialogues:
            # 先找或建立 NPC character（簡化：直接記錄到 dialogue 表）
            await trpgDB.add_dialogue(
                game_id=game["id"],
                character_id=character["id"],
                content=f"{d.get('speaker', '???')}: {d.get('content', '')}",
            )

        # 記錄劇情
        stat_changes_str = json.dumps(stat_changes, ensure_ascii=False) if stat_changes else "{}"
        await trpgDB.add_narrative_log(
            game_id=game["id"],
            character_id=character["id"],
            action=action,
            narrative=narrative,
            stat_changes=stat_changes_str,
            result=result,
        )

        # 記錄行動日誌
        await trpgDB.add_action_log(
            game_id=game["id"],
            actor_id=character["id"],
            action_type="D20",
            content=action,
            result_state=result,
        )

        # 回覆結果
        result_embed = discord.Embed(
            title=f"🎲 {message.author.display_name} 的行動判定",
            color=discord.Color.gold(),
            timestamp=discord.utils.utcnow(),
        )
        result_embed.add_field(name="🎯 行動", value=action, inline=False)
        result_embed.add_field(name="🎲 擲骰", value=f"**{d20_roll}** / 20", inline=True)
        result_embed.add_field(name="📊 判定", value=result, inline=True)
        result_embed.add_field(name="📖 結果", value=narrative[:1024], inline=False)

        # 狀態變更
        changed = {k: v for k, v in stat_changes.items() if v != 0}
        if changed:
            status_lines = []
            for k, v in changed.items():
                icon = "❤️" if k == "hp" else "🧠" if k == "san" else "📊"
                status_lines.append(f"{icon} {k}: {v:+d}")
            result_embed.add_field(name="📊 狀態變更", value="\n".join(status_lines), inline=False)

        # 新道具/NPC
        if new_items:
            result_embed.add_field(
                name="📦 獲得道具",
                value="\n".join(f"• {it['name']}" for it in new_items),
                inline=False,
            )

        await thinking_msg.edit(content=None, embed=result_embed)

    async def _call_ai(self, prompt: str, action: str, d20_roll: int, result: str, game_name: str) -> dict:
        """### 呼叫 AI 並解析回應"""
        if not _ai_client:
            await asyncio.sleep(0.3)
            return self._fallback_response(action, d20_roll, result)

        try:
            response = await _ai_client.chat.completions.create(
                model=DEEPSEEK_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.8,
                max_tokens=1200,
                timeout=60,
            )
            raw = response.choices[0].message.content.strip()
            parsed = self._parse_json(raw)
            if parsed and "narrative" in parsed:
                return parsed
            return self._fallback_response(action, d20_roll, result)
        except Exception as e:
            print(f"[AI GM] API 錯誤: {e}")
            return self._fallback_response(action, d20_roll, result)

    def _fallback_response(self, action: str, roll: int, result: str) -> dict:
        """### AI 不可用時的 fallback"""
        templates = {
            "大成功": [
                "你以驚人的氣勢完成了行動！結果遠超預期，周圍的人都為之讚嘆。",
                "命運之輪轉向了最有利的方向！你的行動完美無瑕。",
            ],
            "成功": [
                "你穩健地完成了行動。雖然有些小波折，但整體結果令人滿意。",
                "你的經驗與技巧發揮了作用，行動順利達成。",
            ],
            "普通": [
                "行動結果中規中矩，沒有特別好也沒有特別壞。",
                "你完成了行動，但結果平平，還有改進空間。",
            ],
            "失敗": [
                "事情沒有按照計劃發展，你的行動未能達到預期效果。",
                "運氣不在你這邊，行動失敗了，但至少你學到了經驗。",
            ],
            "大失敗": [
                "一切朝著最糟的方向發展！不僅失敗，還帶來了意想不到的麻煩！",
                "這是最壞的情況！行動徹底失敗，還造成了嚴重的後果！",
            ],
        }
        tpls = templates.get(result, ["你行動了。"])
        return {
            "narrative": random.choice(tpls),
            "stat_changes": {},
            "new_items": [],
            "new_npcs": [],
            "dialogues": [],
            "action_log": f"{action} → {result}",
        }

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
    await bot.add_cog(TRPGD20Event(bot))
