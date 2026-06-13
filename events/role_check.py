import asyncio
import discord
from discord.ext import commands, tasks
from database.user_base_db import userBaseDB
from database.role_db import roleConfigDB, RoleConfigRow
from config import ROLE_CHECK_INTERVAL_MINUTES


class RoleCheckEvent(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.role_check_loop.start()

    def cog_unload(self):
        """### 卸載插件時取消任務，避免背景殘留
        """
        self.role_check_loop.cancel()

    @tasks.loop(minutes=ROLE_CHECK_INTERVAL_MINUTES)
    async def role_check_loop(self):
        """### 定時身分組檢查：比對成員聲望與簽到天數，自動增/減身分組
        """
        configs = await roleConfigDB.get_all_configs()
        if not configs:
            return  # 無任何設定，跳過

        for guild in self.bot.guilds:
            try:
                await self._check_guild(guild, configs)
            except Exception as e:
                print(f"❌ 身分組檢查失敗 (Guild {guild.id}): {e}")

    async def _check_guild(self, guild: discord.Guild, configs: list[RoleConfigRow]) -> None:
        """### 對單一伺服器執行身分組檢查
        """
        try:
            # 取得所有曾有簽到紀錄的成員 (從資料庫撈，避免迭代全 guild)
            users_data = await userBaseDB.get_users(order_by="total_sign_in", descending=True)
        except Exception as e:
            print(f"[RoleCheck] 無法取得使用者資料: {e}")
            return

        # 快取 guild 成員 (避免重複 fetch)
        member_cache: dict[int, discord.Member] = {}
        try:
            async for member in guild.fetch_members():
                member_cache[member.id] = member
        except discord.Forbidden:
            print(f"[RoleCheck] 缺少讀取成員列表權限 (Guild {guild.id})")
            return

        for cfg in configs:
            role_id = cfg["role_id"]
            req_rep = cfg["required_reputation"]
            req_days = cfg["required_sign_in_days"]

            # 確認身分組仍存在於伺服器
            role = guild.get_role(role_id)
            if role is None:
                print(f"[RoleCheck] 身分組 {role_id} 不存在，跳過")
                continue

            # 對每個有資料的成員檢查
            for user_row in users_data:
                user_id = user_row["user_id"]
                member = member_cache.get(user_id)
                if member is None:
                    continue  # 成員不在這個 guild 或已離開

                reputation = user_row["reputation"]
                total_sign_in = user_row["total_sign_in"]
                has_role = role in member.roles

                # 僅檢查有設定 (>0) 的條件，全部滿足才算達標 (AND)
                meets_all = True
                if req_rep > 0:
                    meets_all = meets_all and (reputation >= req_rep)
                if req_days > 0:
                    meets_all = meets_all and (total_sign_in >= req_days)

                if meets_all:
                    # 達標 → 授予身分組
                    if not has_role:
                        try:
                            await member.add_roles(role, reason="自動身分組：達標")
                            print(f"✅ 授予 {member} 身分組 {role.name}")
                            await asyncio.sleep(1)  # 避免速率限制
                        except discord.Forbidden:
                            print(f"[RoleCheck] 無法授予 {member} 身分組 {role.name}：權限不足")
                            break  # 權限問題，跳過此身分組
                        except discord.HTTPException as e:
                            print(f"[RoleCheck] 授予失敗 {member} - {role.name}: {e}")
                else:
                    # 未達標 → 移除身分組
                    if has_role:
                        try:
                            await member.remove_roles(role, reason="自動身分組：條件不符")
                            print(f"🗑️ 移除 {member} 身分組 {role.name}")
                            await asyncio.sleep(1)  # 避免速率限制
                        except discord.Forbidden:
                            print(f"[RoleCheck] 無法移除 {member} 身分組 {role.name}：權限不足")
                            break
                        except discord.HTTPException as e:
                            print(f"[RoleCheck] 移除失敗 {member} - {role.name}: {e}")

    @role_check_loop.before_loop
    async def before_role_check_loop(self):
        """### 確保機器人準備好再開始巡迴
        """
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(RoleCheckEvent(bot))
