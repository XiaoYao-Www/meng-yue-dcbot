from datetime import datetime
from typing import Optional

from discord import app_commands, Interaction
from discord.ext import commands

from config import TZ
from database.daily_content_db import dailyContentDB
from events.daily_message import build_daily_embed


class DailyQueryCommand(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="daily", description="查詢指定日期的每日知識內容")
    @app_commands.describe(date="日期（選填，預設今天），格式 YYYY-MM-DD")
    async def daily(self, interaction: Interaction, date: Optional[str] = None):
        """### 查詢每日知識

        Args:
            interaction: Interaction
            date: 日期字串 YYYY-MM-DD，預設今天
        """
        # 如果未指定日期，預設今天
        if date is None:
            date = datetime.now(TZ).strftime("%Y-%m-%d")

        # 驗證日期格式
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            await interaction.response.send_message(
                "❌ 日期格式錯誤，請使用 YYYY-MM-DD 格式（例如 2025-01-01）",
                ephemeral=True,
            )
            return

        await interaction.response.defer()

        try:
            content = await dailyContentDB.get_daily_content(date)

            if content is None:
                await interaction.followup.send(
                    f"📭 **{date}** 尚無每日知識內容",
                    ephemeral=True,
                )
                return

            # 使用共用 Embed 建置函式
            embed = build_daily_embed(content)
            await interaction.followup.send(embed=embed)

        except Exception as e:
            print(f"❌ daily 指令錯誤: {e}")
            await interaction.followup.send("❌ 查詢失敗，請稍後再試。", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(DailyQueryCommand(bot))
