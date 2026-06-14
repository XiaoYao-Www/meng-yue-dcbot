import discord
from discord import app_commands, Interaction
from discord.ext import commands
from database.rpg_db import rpgSessionDB


class RpgInfoCommand(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="rpg_info", description="查詢進行中的角色扮演場次資訊")
    @app_commands.describe(
        session_id="可選：指定場次 ID，不填則列出全部"
    )
    async def rpg_info(self, interaction: Interaction, session_id: int | None = None):
        """### 查詢 RPG 場次資訊（全部或單一場次）"""
        await interaction.response.defer(ephemeral=True)

        try:
            guild = interaction.guild
            if guild is None:
                await interaction.followup.send("❌ 此指令僅能在伺服器中使用。", ephemeral=True)
                return

            if session_id is not None:
                await self._show_single(interaction, guild, session_id)
            else:
                await self._show_all(interaction, guild)

        except Exception as e:
            print(f"❌ rpg_info 指令錯誤: {e}")
            try:
                await interaction.followup.send(f"❌ 查詢失敗：{e}", ephemeral=True)
            except Exception:
                pass

    async def _show_single(self, interaction: Interaction, guild: discord.Guild, session_id: int) -> None:
        """顯示單一場次詳細資訊"""
        session = await rpgSessionDB.get_session(session_id)
        if session is None:
            await interaction.followup.send(f"📭 找不到 ID 為 `{session_id}` 的場次。", ephemeral=True)
            return

        owner = guild.get_member(session["owner_id"])
        owner_text = owner.mention if owner else f"`{session['owner_id']}`"

        embed = discord.Embed(
            title=f"📜 {session['name']}",
            color=discord.Color.purple(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="🆔 場次 ID", value=f"`{session['id']}`", inline=False)
        embed.add_field(name="🎭 主持人", value=owner_text, inline=False)
        embed.add_field(name="📅 建立時間", value=session["created_at"], inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    async def _show_all(self, interaction: Interaction, guild: discord.Guild) -> None:
        """列出全部場次"""
        sessions = await rpgSessionDB.get_all_sessions()
        if not sessions:
            await interaction.followup.send("📭 目前沒有任何進行中的角色扮演場次。", ephemeral=True)
            return

        embed = discord.Embed(
            title="📋 角色扮演場次一覽",
            color=discord.Color.blue(),
            timestamp=discord.utils.utcnow(),
        )

        for s in sessions:
            owner = guild.get_member(s["owner_id"])
            owner_text = owner.display_name if owner else f"`{s['owner_id']}`"

            embed.add_field(
                name=f"`#{s['id']}` {s['name']}",
                value=(
                    f"🎭 {owner_text}\n"
                    f"📅 {s['created_at'][:10]}"
                ),
                inline=False,
            )

        embed.set_footer(text=f"共 {len(sessions)} 個場次 ｜ 使用 /rpg_info <id> 查看詳細資訊")
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(RpgInfoCommand(bot))
