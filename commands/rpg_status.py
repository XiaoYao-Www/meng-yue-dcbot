import json
import discord
from discord import app_commands, Interaction
from discord.ext import commands
from database.trpg_db import trpgDB


class RpgStatusCommand(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="status", description="查看角色卡狀態")
    async def status(self, interaction: Interaction):
        """### 查看當前遊戲中的角色卡"""
        await interaction.response.defer(ephemeral=True)
        try:
            game, character = await self._find_player(interaction)
            if game is None or character is None:
                return

            stats = {}
            try:
                stats = json.loads(character.get("stats", "{}"))
            except (json.JSONDecodeError, TypeError):
                pass

            embed = discord.Embed(
                title=f"📋 {character['name']}",
                color=discord.Color.blue(),
                timestamp=discord.utils.utcnow(),
            )
            embed.add_field(name="🎮 遊戲", value=game["name"], inline=False)
            embed.add_field(name="📊 狀態", value=character.get("status", "存活"), inline=True)
            embed.add_field(name="🏗️ 階段", value=game.get("current_stage", "?"), inline=True)

            for key, value in stats.items():
                if key == "技能" and isinstance(value, list):
                    embed.add_field(name="🎯 技能", value="、".join(value), inline=False)
                else:
                    embed.add_field(name=f"📊 {key}", value=str(value), inline=True)

            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            print(f"❌ status 錯誤: {e}")
            await interaction.followup.send(f"❌ 查詢失敗：{e}", ephemeral=True)

    @app_commands.command(name="items", description="查看背包中的道具")
    async def items(self, interaction: Interaction):
        """### 查看當前角色的道具"""
        await interaction.response.defer(ephemeral=True)
        try:
            game, character = await self._find_player(interaction)
            if game is None or character is None:
                return

            items = await trpgDB.get_items_by_owner(character["id"])

            if not items:
                await interaction.followup.send("🎒 你的背包是空的。", ephemeral=True)
                return

            embed = discord.Embed(
                title=f"🎒 {character['name']} 的道具",
                color=discord.Color.gold(),
                timestamp=discord.utils.utcnow(),
            )

            for item in items:
                props = {}
                try:
                    props = json.loads(item.get("properties", "{}"))
                except (json.JSONDecodeError, TypeError):
                    pass
                qty = ""
                # 從 inventories 取得數量
                try:
                    async with trpgDB.db.execute(
                        "SELECT quantity FROM inventories WHERE character_id = ? AND item_id = ?",
                        (character["id"], item["id"])
                    ) as cursor:
                        row = await cursor.fetchone()
                        qty = f" ×{row['quantity']}" if row else ""
                except Exception:
                    pass
                desc = item.get("description", "")[:100]
                embed.add_field(
                    name=f"📦 {item['name']}{qty}",
                    value=desc or "（無描述）",
                    inline=False,
                )

            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            print(f"❌ items 錯誤: {e}")
            await interaction.followup.send(f"❌ 查詢失敗：{e}", ephemeral=True)

    @app_commands.command(name="skills", description="查看可用技能")
    async def skills(self, interaction: Interaction):
        """### 查看當前角色的技能"""
        await interaction.response.defer(ephemeral=True)
        try:
            game, character = await self._find_player(interaction)
            if game is None or character is None:
                return

            stats = {}
            try:
                stats = json.loads(character.get("stats", "{}"))
            except (json.JSONDecodeError, TypeError):
                pass

            skills = stats.get("技能", [])
            if not skills:
                # 從 world_rules 找通用技能
                world_rules = {}
                try:
                    world_rules = json.loads(game.get("world_rules", "{}"))
                except (json.JSONDecodeError, TypeError):
                    pass
                skills = [s["name"] for s in world_rules.get("character_creation", {}).get("skills", [])]

            if not skills:
                await interaction.followup.send("📚 目前沒有可用技能。", ephemeral=True)
                return

            embed = discord.Embed(
                title=f"🎯 {character['name']} 的技能",
                color=discord.Color.teal(),
                timestamp=discord.utils.utcnow(),
            )
            embed.add_field(
                name=f"🎯 技能（{len(skills)} 項）",
                value="\n".join(f"• {s}" for s in skills),
                inline=False,
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            print(f"❌ skills 錯誤: {e}")
            await interaction.followup.send(f"❌ 查詢失敗：{e}", ephemeral=True)

    async def _find_player(self, interaction: Interaction) -> tuple:
        """### 找出用戶當前的遊戲與角色"""
        guild = interaction.guild
        if guild is None:
            await interaction.followup.send("❌ 此指令僅能在伺服器中使用。", ephemeral=True)
            return None, None

        games = await trpgDB.get_all_games()
        for game in games:
            character = await trpgDB.get_character_by_discord(game["id"], interaction.user.id)
            if character:
                return game, character

        await interaction.followup.send(
            "❌ 你目前沒有參與任何進行中的角色扮演遊戲。\n"
            "請到 RPG 論壇頻道輸入 `加入` 來參與遊戲。",
            ephemeral=True,
        )
        return None, None


async def setup(bot: commands.Bot):
    await bot.add_cog(RpgStatusCommand(bot))
