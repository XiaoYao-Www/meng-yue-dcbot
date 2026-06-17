import json
import random

import discord
from discord import Message
from discord.ext import commands

from database.trpg_db import trpgDB
from utils.trpg_ai_client import get_ai_client, parse_json_response, call_ai
from utils.trpg_dice import (
    normalize_dice_rules,
    get_default_dice,
    roll_dice,
    calc_dice_range,
    judge_result,
    format_dice_rules_for_prompt,
)


GM_PROMPT_TEMPLATE = """你是一個 TRPG 遊戲主持人 (Game Master)。請根據以下完整遊戲狀態，對玩家的行動進行判定與回應。

## 世界設定
{world_setting}

## 規則摘要
{rules_summary}

## 骰子規則
{dice_rules_description}

## 遊戲結束條件
{end_conditions_text}

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

## 擲骰結果
使用骰子: {dice_used}
擲骰結果: {dice_roll}
判定描述: {result}

========================================
請嚴格按照以下 JSON 格式回傳，不要包含任何其他文字：

{{
    "narrative": "劇情敘述文字（100-300字，繁體中文，生動描寫行動結果）",

    "stat_changes": {{}},

    "dice_used": "實際使用的骰子，如 D20、3D6、D100",

    "end_game": false,

    "new_items": [],

    "new_npcs": [],

    "dialogues": [],

    "action_log": "此行動的簡短摘要（10-30字）",

    "next_player_hint": "建議下一位行動的玩家角色名，或空白表示不指定"
}}

注意：
- stat_changes 中的數值：正數=增加，負數=減少，0=不變
- end_game：若本次行動觸發了遊戲結束條件，設為 true，否則 false
- new_items 格式：{{"name": "道具名", "count": 1, "description": "描述", "properties": {{}}, "is_known": true}}
- new_npcs 格式：{{"name": "NPC/怪物名", "description": "描述", "stats": {{"hp": 50}}, "is_hostile": false, "is_known": true}}
- dialogues 格式：{{"speaker": "NPC名稱", "content": "對話內容"}}
- **next_player_hint**：根據劇情發展建議誰該接著行動，若無特別需求設為空字串
- 沒有新項目時，new_items / new_npcs / dialogues 保持空列表
- 所有文字使用繁體中文"""


class TRPGActionEvent(commands.Cog):
    """### TRPG 行動判定事件

    監聽論壇頻道中的 D 指令與對話指令，自動選擇骰子並呼叫 AI GM 判定。
    骰子完全由 world_rules.dice_rules 驅動，無硬編碼。
    """

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
        if game is None or game["current_stage"] in ("創角", "結束"):
            return

        content = message.content.strip()

        # D 指令匹配（僅 D / d 開頭，自動選骰）
        is_dice = (
            content.upper().startswith("D ") or
            content.upper().startswith("D\t") or
            content.upper() == "D"
        )
        is_speech = any(content.startswith(p) for p in ("說", "说", "講", "讲", "大喊", "私語", "私语", "say"))

        if is_dice:
            parts = content.split(None, 1)
            action = parts[1].strip() if len(parts) > 1 else "執行行動"

            # 從 world_rules.dice_rules 自動選擇預設骰子
            wr = {}
            try:
                wr = json.loads(game.get("world_rules", "{}"))
            except (json.JSONDecodeError, TypeError):
                pass
            dice_rules = normalize_dice_rules(wr.get("dice_rules"))
            dice_type = get_default_dice(dice_rules)

            await self._process_action(message, thread, game, action, dice_type)
            return

        if is_speech:
            # 一般對話（不擲骰）
            action = content
            await self._process_action(message, thread, game, action, "對話")
            return

    async def _process_action(
        self, message: Message, thread: discord.Thread,
        game: dict, action: str, dice_type: str = ""
    ) -> None:
        """### 處理玩家行動（擲骰判定 + AI GM 回應）"""
        character = await trpgDB.get_character_by_discord(game["id"], message.author.id)
        if character is None:
            await message.reply("❌ 你沒有參與此遊戲，無法行動。", mention_author=False)
            return

        # 擲骰（完全由 dice_type 驅動，無 D20 硬編碼）
        is_dialogue = dice_type == "對話"
        if is_dialogue:
            dice_roll = 0
            result = "對話"
            dice_used = "-"
            dice_count = 0
            dice_faces = 0
        else:
            dice_roll, dice_count, dice_faces = roll_dice(dice_type)
            dice_used = dice_type.upper()
            result = judge_result(dice_roll, dice_count, dice_faces)

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

        # 骰子規則描述（格式化為 AI GM 可讀）
        dice_rules = normalize_dice_rules(world_rules.get("dice_rules"))
        dice_rules_desc = format_dice_rules_for_prompt(dice_rules)

        # 結束條件
        end_conditions_list = []
        try:
            end_conditions_list = json.loads(game.get("end_conditions", "[]"))
        except (json.JSONDecodeError, TypeError):
            pass
        # 向後相容：若 DB 專用欄位為空，嘗試從 world_rules 內部讀取（舊遊戲）
        if not end_conditions_list:
            ec = world_rules.get("end_conditions", [])
            if isinstance(ec, list):
                end_conditions_list = ec
        end_conditions_text = "\n".join(f"- {c}" for c in end_conditions_list) if end_conditions_list else "（無特殊結束條件）"

        # 構建 prompt
        rules_summary = world_rules.get("rules_summary", "")
        prompt = GM_PROMPT_TEMPLATE.format(
            world_setting=game.get("world_setting", "未知的世界"),
            rules_summary=rules_summary,
            dice_rules_description=dice_rules_desc,
            end_conditions_text=end_conditions_text,
            recent_narratives=recent_narratives,
            recent_dialogues=recent_dialogues,
            character_name=character.get("name", message.author.display_name),
            character_stats=stats_str,
            character_status=character.get("status", "存活"),
            inventory=inv_str,
            action=action,
            dice_used=dice_used if not is_dialogue else "對話",
            dice_roll=dice_roll,
            result=result,
        )

        # 提示訊息
        if is_dialogue:
            thinking_msg = await message.reply("💬 對話中...", mention_author=False)
        else:
            thinking_msg = await message.reply(f"🎲 擲骰 {dice_used} → {dice_roll} ...", mention_author=False)

        # 呼叫 AI
        response_data = await self._call_ai(prompt, action, dice_roll, result, game["name"])

        narrative = response_data.get("narrative", "")
        stat_changes = response_data.get("stat_changes", {})
        end_game = response_data.get("end_game", False)
        new_items = response_data.get("new_items", [])
        new_npcs = response_data.get("new_npcs", [])
        dialogues = response_data.get("dialogues", [])
        action_log = response_data.get("action_log", f"{action} → {result}")
        next_hint = response_data.get("next_player_hint", "")

        # 更新角色數值
        if stat_changes:
            for stat_key, change in stat_changes.items():
                if change != 0 and stat_key in char_stats:
                    old_val = char_stats[stat_key]
                    char_stats[stat_key] = max(0, old_val + change)
            await trpgDB.update_character_stats(character["id"], json.dumps(char_stats, ensure_ascii=False))

        # 建立/更新道具（去重：同名則更新 + 不開新貼文）
        for item_data in new_items:
            try:
                item_name = item_data.get("name", "未知道具")
                item_desc = item_data.get("description", "")
                item_props = json.dumps(item_data.get("properties", {}), ensure_ascii=False)

                existing_item = await trpgDB.get_item_by_name(game["id"], item_name)
                if existing_item:
                    # 已存在 → 更新描述，不開新貼文
                    await trpgDB.update_item(existing_item["id"], item_desc, item_props)
                    item_id = existing_item["id"]
                else:
                    # 不存在 → 新建 + 開貼文
                    item_id = await trpgDB.create_item(
                        game_id=game["id"],
                        name=item_name,
                        description=item_desc,
                        properties=item_props,
                        is_known=item_data.get("is_known", True),
                    )
                    if forum := thread.parent:
                        try:
                            item_thread, _ = await forum.create_thread(
                                name=f"📦 {item_name}",
                                content=f"**{item_name}**\n\n{item_desc}",
                                reason=f"{game['name']} 新道具",
                            )
                            await trpgDB.update_item_thread(item_id, item_thread.id)
                        except discord.HTTPException:
                            pass

                # 自動放入角色背包
                await trpgDB.add_item_to_inventory(character["id"], item_id)
            except Exception as e:
                print(f"[AI GM] 建立/更新道具失敗: {e}")

        # 建立/更新 NPC/怪物（去重：同名則更新內容與貼文）
        for npc_data in new_npcs:
            try:
                npc_name = npc_data.get("name", "未知")
                npc_desc = npc_data.get("description", "")
                npc_stats = json.dumps(npc_data.get("stats", {}), ensure_ascii=False)
                npc_hostile = npc_data.get("is_hostile", False)

                existing_npc = await trpgDB.get_npc_by_name(game["id"], npc_name)
                if existing_npc:
                    # 已存在 → 更新描述/數值
                    await trpgDB.update_npc(existing_npc["id"], npc_desc, npc_stats)
                    # 若有現有貼文，編輯更新內容
                    if existing_npc["discord_thread_id"]:
                        try:
                            npc_thread = thread.parent.get_thread(existing_npc["discord_thread_id"])
                            if npc_thread:
                                await npc_thread.edit(
                                    name=f"{'🦄' if npc_hostile else '👤'} {npc_name}"
                                )
                        except (discord.NotFound, discord.HTTPException):
                            pass
                else:
                    # 不存在 → 新建
                    npc_id = await trpgDB.create_npc(
                        game_id=game["id"],
                        name=npc_name,
                        description=npc_desc,
                        stats=npc_stats,
                        is_hostile=npc_hostile,
                        is_known=npc_data.get("is_known", True),
                    )
                    if forum := thread.parent:
                        try:
                            icon = "🦄" if npc_hostile else "👤"
                            npc_thread, _ = await forum.create_thread(
                                name=f"{icon} {npc_name}",
                                content=f"**{npc_name}**\n\n{npc_desc}",
                                reason=f"{game['name']} 新NPC",
                            )
                            await trpgDB.update_npc_thread(npc_id, npc_thread.id)
                        except discord.HTTPException:
                            pass
            except Exception as e:
                print(f"[AI GM] 建立/更新NPC失敗: {e}")

        # 記錄對話（NPC 發言）
        for d in dialogues:
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
            action_type="dice_action",
            content=action,
            result_state=result,
        )

        # 遊戲結束偵測
        if end_game:
            await trpgDB.update_game_end(game["id"], "結束")
            game_end_text = "\n\n🏁 **遊戲結束！** 本次行動觸發了遊戲結束條件。"
        else:
            game_end_text = ""

        # 回覆結果
        result_embed = discord.Embed(
            title=f"🎲 {message.author.display_name} 的行動判定",
            color=discord.Color.gold() if not end_game else discord.Color.red(),
            timestamp=discord.utils.utcnow(),
        )
        result_embed.add_field(name="🎯 行動", value=action, inline=False)

        if not is_dialogue:
            result_embed.add_field(name="🎲 擲骰", value=f"**{dice_used}** → `{dice_roll}`", inline=True)

        result_embed.add_field(name="📊 判定", value=result, inline=True)
        result_embed.add_field(name="📖 結果", value=narrative[:1024] + game_end_text, inline=False)

        # 狀態變更
        changed = {k: v for k, v in stat_changes.items() if v != 0}
        if changed:
            status_lines = []
            for k, v in changed.items():
                icon = "❤️" if "hp" in k.lower() or "生命" in k else "🧠" if "san" in k.lower() or "理智" in k or "意志" in k else "📊"
                status_lines.append(f"{icon} {k}: {v:+d}")
            result_embed.add_field(name="📊 狀態變更", value="\n".join(status_lines), inline=False)

        # 新道具/NPC
        if new_items:
            result_embed.add_field(
                name="📦 獲得道具",
                value="\n".join(f"• {it['name']}" for it in new_items),
                inline=False,
            )

        # 回合引導
        if next_hint:
            result_embed.add_field(name="👤 輪到誰？", value=next_hint, inline=False)

        await thinking_msg.edit(content=None, embed=result_embed)

    async def _call_ai(self, prompt: str, action: str, dice_roll: int, result: str, game_name: str) -> dict:
        """### 呼叫 AI 並解析回應"""
        client = get_ai_client()
        if not client:
            import asyncio
            await asyncio.sleep(0.3)
            return self._fallback_response(action, dice_roll, result)

        try:
            raw = await call_ai(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.8,
                max_tokens=1200,
                timeout=60,
            )
            if raw is None:
                return self._fallback_response(action, dice_roll, result)

            parsed = parse_json_response(raw)
            if parsed and "narrative" in parsed:
                return parsed
            return self._fallback_response(action, dice_roll, result)
        except Exception as e:
            print(f"[AI GM] API 錯誤: {e}")
            return self._fallback_response(action, dice_roll, result)

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
            "next_player_hint": "",
        }


async def setup(bot: commands.Bot):
    await bot.add_cog(TRPGActionEvent(bot))
