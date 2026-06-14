import discord
from discord import app_commands, Interaction, CategoryChannel
from discord.ext import commands
from config import RPG_CLASS_ID, RPG_ROLE_ID
from database.rpg_db import rpgSessionDB


class RpgStartCommand(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="rpg_start", description="建立一個新的角色扮演場次")
    @app_commands.describe(name="角色扮演名稱")
    async def rpg_start(self, interaction: Interaction, name: str):
        """### 建立 RPG 場次（論壇頻道 + 主貼文）"""
        await interaction.response.defer()

        try:
            guild = interaction.guild
            if guild is None:
                await interaction.followup.send("❌ 此指令僅能在伺服器中使用。", ephemeral=True)
                return

            # 權限檢查：使用者必須持有 RPG_ROLE_ID 身分組
            rpg_role = guild.get_role(RPG_ROLE_ID)
            if rpg_role is None:
                await interaction.followup.send(
                    "❌ 系統尚未設定角色扮演身分組，請管理員檢查 config.py 的 RPG_ROLE_ID。",
                    ephemeral=True
                )
                return
            if rpg_role not in interaction.user.roles:
                await interaction.followup.send(
                    f"❌ 你必須擁有 {rpg_role.mention} 身分組才能建立角色扮演。",
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

            # 頻道權限設定：只有 RPG_ROLE 看得到
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
                reason=f"由 {interaction.user} 建立的角色扮演場次",
            )

            # 創建基礎標籤（Discord 上限 5 個）
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
                    name=tag_name,
                    emoji=emoji,
                    reason=f"{name} 角色扮演標籤",
                )
                if tag_name == "主頻道":
                    main_tag = tag

            # 建立初始貼文（主頻道）
            embed = discord.Embed(
                title=f"📜 {name} — 主頻道",
                description=f"🎭 **發起人** {interaction.user.mention}",
                color=discord.Color.purple(),
                timestamp=discord.utils.utcnow(),
            )

            main_thread, _ = await forum_channel.create_thread(
                name=f"{name} - 主頻道",
                embed=embed,
                applied_tags=[main_tag],
                reason=f"{name} 角色扮演主頻道",
            )

            # 儲存到資料庫
            await rpgSessionDB.create_session(
                name=name,
                forum_channel_id=forum_channel.id,
                main_thread_id=main_thread.id,
                owner_id=interaction.user.id,
            )

            # 成功回應
            success_embed = discord.Embed(
                title="✅ 角色扮演場次已建立",
                color=discord.Color.green(),
                description=(
                    f"**名稱**: {name}\n"
                    f"**論壇頻道**: {forum_channel.mention}\n"
                    f"**主頻道**: {main_thread.mention}\n"
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
                    f"❌ 建立角色扮演場次時發生錯誤：{e}", ephemeral=True
                )
            except Exception:
                pass


async def setup(bot: commands.Bot):
    await bot.add_cog(RpgStartCommand(bot))
