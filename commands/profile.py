import discord
from discord import app_commands, Interaction
from discord.ext import commands
from database.user_base_db import userBaseDB

class ProfileCommand(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="profile", description="查看你的資料")
    async def profile(self, interaction: Interaction):
        try:
            user_id = interaction.user.id
            user = await userBaseDB.get_user(user_id)

            # 建立 embed
            embed = discord.Embed(
                title=f"📊 {interaction.user.display_name} 的個人檔案",
                color=discord.Color.random(),
                timestamp=discord.utils.utcnow()
            )

            # 添加頭像
            embed.set_thumbnail(url=interaction.user.display_avatar.url)

            # 添加資料欄位
            embed.add_field(name="✨ 經驗值", value=f"`{user['xp']}`", inline=False)
            # embed.add_field(name="💰 金幣", value=f"`{user['coins']}`", inline=True)
            embed.add_field(name="🎖️ 聲望", value=f"`{user['reputation']}`", inline=False)
            
            embed.add_field(name="🔥 當前連續簽到", value=f"{user['streak_count']} 天", inline=False)
            embed.add_field(name="🏆 最高紀錄", value=f"{user['max_streak']} 天", inline=False)
            embed.add_field(name="🗓️ 總紀錄", value=f"{user['total_sign_in']} 天", inline=False)

            await interaction.response.send_message(embed=embed)
        except Exception as e:
            print(f"❌ profile 指令錯誤: {e}")
            try:
                await interaction.response.send_message("❌ 查詢失敗，請稍後再試。", ephemeral=True)
            except:
                pass

async def setup(bot: commands.Bot):
    await bot.add_cog(ProfileCommand(bot))