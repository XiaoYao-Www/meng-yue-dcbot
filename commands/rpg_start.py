import discord
from discord import app_commands, Interaction, CategoryChannel
from discord.ext import commands
from config import RPG_CLASS_ID, RPG_ROLE_ID
from database.trpg_db import trpgDB
from events.trpg_world_gen import generate_world


class RpgStartCommand(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="rpg_start", description="建立一個新的角色扮演遊戲")
    @app_commands.describe(
        name="遊戲名稱",
        story_outline="故事大綱（世界觀、背景設定）",
        goal_type="目標類型",
        max_players="最大玩家數（預設 4）",
        final_goal="最終目標（留空則由 AI 自動生成）",
    )
    @app_commands.choices(goal_type=[
        app_commands.Choice(name="明確目標", value="明確目標"),
        app_commands.Choice(name="隱藏目標", value="隱藏目標"),
    ])
    async def rpg_start(
        self, interaction: Interaction,
        name: str,
        story_outline: str,
        goal_type: app_commands.Choice[str],
        max_players: app_commands.Range[int, 1, 20] = 4,
        final_goal: str = "",
    ):
        """### 建立 RPG 遊戲（論壇頻道 + 主貼文 + 資料庫）"""
        await interaction.response.defer()

        try:
            guild = interaction.guild
            if guild is None:
                await interaction.followup.send("❌ 此指令僅能在伺服器中使用。", ephemeral=True)
                return

            # 權限檢查
            rpg_role = guild.get_role(RPG_ROLE_ID)
            if rpg_role is None:
                await interaction.followup.send(
                    "❌ 系統尚未設定角色扮演身分組，請管理員檢查 config.py 的 RPG_ROLE_ID。",
                    ephemeral=True
                )
                return
            if rpg_role not in interaction.user.roles:
                await interaction.followup.send(
                    f"❌ 你必須擁有 {rpg_role.mention} 身分組才能建立遊戲。",
                    ephemeral=True
                )
                return

            # 取得 RPG 分類
            category = guild.get_channel(RPG_CLASS_ID)
            if category is None or not isinstance(category, CategoryChannel):
                await interaction.followup.send(
                    f"❌ 找不到角色扮演類別（ID: {RPG_CLASS_ID}），請確認 config.py 設定正確。",
                    ephemeral=True
                )
                return

            # 頻道權限設定
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(read_messages=False),
                rpg_role: discord.PermissionOverwrite(
                    read_messages=True,
                    send_messages_in_threads=True,
                    create_public_threads=True,
                ),
                guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            }

            # 建立論壇頻道
            forum_channel = await guild.create_forum(
                name=name,
                category=category,
                overwrites=overwrites,
                reason=f"由 {interaction.user} 建立的角色扮演遊戲",
            )

            # 創建標籤
            tags_data = [
                ("💬", "主頻道"),
                ("📋", "角色卡"),
                ("📦", "物品"),
                ("🦄", "生物"),
                ("👤", "NPC"),
            ]
            main_tag = None
            for emoji, tag_name in tags_data:
                tag = await forum_channel.create_tag(
                    name=tag_name, emoji=emoji,
                    reason=f"{name} 標籤",
                )
                if tag_name == "主頻道":
                    main_tag = tag

            # 建立初始貼文（主頻道）
            embed = discord.Embed(
                title=f"📜 {name}",
                description=f"🎭 **發起人** {interaction.user.mention}",
                color=discord.Color.purple(),
                timestamp=discord.utils.utcnow(),
            )
            embed.add_field(name="📖 故事大綱", value=story_outline[:1024], inline=False)
            embed.add_field(name="🎯 目標類型", value=goal_type.value, inline=True)
            embed.add_field(name="👥 最大玩家", value=str(max_players), inline=True)

            main_thread, _ = await forum_channel.create_thread(
                name=f"{name} - 主頻道",
                embed=embed,
                applied_tags=[main_tag],
                reason=f"{name} 主頻道",
            )

            # 寫入資料庫
            game_id = await trpgDB.create_game(
                name=name,
                story_outline=story_outline,
                goal_type=goal_type.value,
                max_players=max_players,
                forum_channel_id=forum_channel.id,
                main_thread_id=main_thread.id,
                owner_id=interaction.user.id,
                final_goal=final_goal,
            )

            # 呼叫 AI 生成世界觀與規則
            await interaction.followup.send("🌍 AI 正在生成世界觀與規則，請稍候...", ephemeral=True)
            world_setting, world_rules_str, ai_final_goal = await generate_world(
                game_id, name, story_outline, goal_type.value
            )
            effective_final_goal = final_goal or ai_final_goal

            # 更新主貼文：加入世界觀資訊
            world_embed = discord.Embed(
                title=f"📜 {name}",
                description=f"🎭 **發起人** {interaction.user.mention}",
                color=discord.Color.purple(),
                timestamp=discord.utils.utcnow(),
            )
            world_embed.add_field(name="📖 故事大綱", value=story_outline[:1024], inline=False)
            world_embed.add_field(name="🎯 目標類型", value=goal_type.value, inline=True)
            world_embed.add_field(name="👥 最大玩家", value=str(max_players), inline=True)
            world_embed.add_field(name="🌍 世界設定", value=world_setting[:1024], inline=False)
            if effective_final_goal:
                world_embed.add_field(name="🏁 最終目標", value=effective_final_goal[:1024], inline=False)

            try:
                async for msg in main_thread.history(limit=1):
                    if msg.author == self.bot.user:
                        await msg.edit(embed=world_embed)
                    break
            except Exception:
                pass

            # 成功回應
            success_embed = discord.Embed(
                title="✅ 角色扮演遊戲已建立",
                color=discord.Color.green(),
                description=(
                    f"**名稱**: {name}\n"
                    f"**論壇頻道**: {forum_channel.mention}\n"
                    f"**主頻道**: {main_thread.mention}\n"
                    f"**遊戲 ID**: `{game_id}`"
                ),
                timestamp=discord.utils.utcnow(),
            )
            await interaction.followup.send(embed=success_embed)

        except discord.Forbidden:
            await interaction.followup.send(
                "❌ 機器人權限不足，無法建立頻道。請確認我有「管理頻道」權限。",
                ephemeral=True,
            )
        except Exception as e:
            print(f"❌ rpg_start 指令錯誤: {e}")
            try:
                await interaction.followup.send(
                    f"❌ 建立遊戲時發生錯誤：{e}", ephemeral=True
                )
            except Exception:
                pass


async def setup(bot: commands.Bot):
    await bot.add_cog(RpgStartCommand(bot))
