import discord
from discord import app_commands, Interaction, Role
from discord.ext import commands
from database.role_db import roleConfigDB


class RoleManageCommand(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="role_setup", description="設定身分組門檻（管理員限定）")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(
        role="要設定的身分組",
        reputation="需要達到的聲望值 (0=不限制)",
        sign_in_days="需要達到的總簽到天數 (0=不限制)"
    )
    async def role_setup(self, interaction: Interaction, role: Role, reputation: int, sign_in_days: int):
        """### 新增或更新身分組門檻"""
        try:
            # 基本驗證
            if reputation < 0 or sign_in_days < 0:
                await interaction.response.send_message("❌ 聲望與簽到天數不能為負數。", ephemeral=True)
                return

            if reputation == 0 and sign_in_days == 0:
                await interaction.response.send_message("❌ 至少要設定一種門檻（聲望或簽到天數）大於 0。", ephemeral=True)
                return

            await roleConfigDB.add_config(role.id, reputation, sign_in_days)

            # 刷新身分組快取
            cog = self.bot.get_cog("RoleCheckEvent")
            if cog and hasattr(cog, "refresh_cache"):
                await cog.refresh_cache()

            conditions = []
            if reputation > 0:
                conditions.append(f"聲望 ≥ `{reputation}`")
            if sign_in_days > 0:
                conditions.append(f"簽到 ≥ `{sign_in_days}` 天")
            embed = discord.Embed(
                title="✅ 身分組門檻設定完成",
                color=discord.Color.green(),
                description=(
                    f"**身分組**: {role.mention}\n"
                    f"**條件**: {' 且 '.join(conditions)}"
                )
            )
            await interaction.response.send_message(embed=embed)
        except Exception as e:
            print(f"❌ role_setup 指令錯誤: {e}")
            try:
                await interaction.response.send_message("❌ 設定失敗，請稍後再試。", ephemeral=True)
            except:
                pass

    @app_commands.command(name="role_remove", description="移除身分組門檻設定（管理員限定）")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(role="要移除設定的身分組")
    async def role_remove(self, interaction: Interaction, role: Role):
        """### 移除身分組設定"""
        try:
            await roleConfigDB.remove_config(role.id)

            # 刷新身分組快取
            cog = self.bot.get_cog("RoleCheckEvent")
            if cog and hasattr(cog, "refresh_cache"):
                await cog.refresh_cache()

            await interaction.response.send_message(
                f"✅ 已移除 {role.mention} 的門檻設定。", ephemeral=True
            )
        except Exception as e:
            print(f"❌ role_remove 指令錯誤: {e}")
            try:
                await interaction.response.send_message("❌ 移除失敗，請稍後再試。", ephemeral=True)
            except:
                pass

    @app_commands.command(name="role_list", description="列出所有已設定的身分組門檻（管理員限定）")
    async def role_list(self, interaction: Interaction):
        """### 列出所有身分組設定"""
        try:
            configs = await roleConfigDB.get_all_configs()
            if not configs:
                await interaction.response.send_message("📭 目前沒有任何身分組門檻設定。", ephemeral=True)
                return

            embed = discord.Embed(
                title="📋 身分組門檻一覽",
                color=discord.Color.blue(),
                timestamp=discord.utils.utcnow()
            )
            description_list = []
            for cfg in configs:
                role_obj = interaction.guild.get_role(cfg["role_id"])
                role_name = role_obj.mention if role_obj else f"`{cfg['role_id']}` (已刪除)"
                parts = []
                if cfg["required_reputation"] > 0:
                    parts.append(f"聲望 ≥ `{cfg['required_reputation']}`")
                if cfg["required_sign_in_days"] > 0:
                    parts.append(f"簽到 ≥ `{cfg['required_sign_in_days']}` 天")
                value = " 且 ".join(parts) if parts else "未設定條件 (資料異常)"
                description_list.append(f"{role_name}: {value}")
            
            embed.description = "\n".join(description_list)

            await interaction.response.send_message(embed=embed)
        except Exception as e:
            print(f"❌ role_list 指令錯誤: {e}")
            try:
                await interaction.response.send_message("❌ 查詢失敗，請稍後再試。", ephemeral=True)
            except:
                pass


async def setup(bot: commands.Bot):
    await bot.add_cog(RoleManageCommand(bot))
