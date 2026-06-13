from discord import app_commands, Interaction
from discord.ext import commands

class PingCommand(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="ping", description="測試延遲指令")
    async def ping(self, interaction: Interaction):
        # 取得延遲並轉換為毫秒
        latency = round(self.bot.latency * 1000)
        
        # 發送回應
        await interaction.response.send_message(f"目前的延遲為：**{latency} ms**")

async def setup(bot: commands.Bot):
    await bot.add_cog(PingCommand(bot))