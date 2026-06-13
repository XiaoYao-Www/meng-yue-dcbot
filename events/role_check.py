import asyncio
import time as time_module
import discord
from discord.ext import commands, tasks
from database.user_base_db import userBaseDB
from database.role_db import roleConfigDB, RoleConfigRow
from config import ROLE_CHECK_INTERVAL_MINUTES, ROLE_CHECK_SINGLE_ENABLED


class RoleCheckEvent(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._role_configs_cache: list[RoleConfigRow] = []
        """角色設定記憶體快取，全掃時刷新，單人檢查直接讀取避免重複查 DB"""

        self._cache_timestamp: float = 0
        """快取時間戳"""

        self.role_check_loop.start()

    def cog_unload(self):
        """### 卸載插件時取消任務，避免背景殘留
        """
        self.role_check_loop.cancel()

    ##### 公開方法 — 供外部 Cog 調用 #####

    async def refresh_cache(self) -> None:
        """### 刷新身分組設定快取
        """
        self._role_configs_cache = await roleConfigDB.get_all_configs()
        self._cache_timestamp = time_module.time()

    async def check_single_user(self, guild: discord.Guild, user_id: int) -> None:
        """### 即時單人檢查：比對單一使用者的所有身分組設定

        當成員聲望或總簽到天數變動時呼叫，只檢查該使用者一人。

        Args:
            guild (discord.Guild): 使用者所在的伺服器
            user_id (int): 使用者 ID
        """
        if not ROLE_CHECK_SINGLE_ENABLED:
            return

        configs = self._role_configs_cache
        if not configs:
            return

        try:
            user = await userBaseDB.get_user(user_id)
        except Exception as e:
            print(f"[RoleCheck] 單人檢查無法取得使用者資料 {user_id}: {e}")
            return
        if user is None:
            return

        # 從快取取得 member（避免 fetch）
        member = guild.get_member(user_id)
        if member is None:
            # 不在這個 guild 的快取中，嘗試 fetch
            try:
                member = await guild.fetch_member(user_id)
            except (discord.NotFound, discord.Forbidden):
                return

        reputation = user.get("reputation", 0)
        total_sign_in = user.get("total_sign_in", 0)

        for cfg in configs:
            role_id = cfg["role_id"]
            req_rep = cfg["required_reputation"]
            req_days = cfg["required_sign_in_days"]

            role = guild.get_role(role_id)
            if role is None:
                continue

            has_role = role in member.roles

            # 僅檢查有設定 (>0) 的條件，全部滿足才算達標 (AND)
            meets_all = True
            if req_rep > 0:
                meets_all = meets_all and (reputation >= req_rep)
            if req_days > 0:
                meets_all = meets_all and (total_sign_in >= req_days)

            if meets_all:
                if not has_role:
                    try:
                        await member.add_roles(role, reason="即時身分組：達標")
                    except discord.Forbidden:
                        pass
                    except discord.HTTPException as e:
                        print(f"[RoleCheck] 即時授予失敗 {member} - {role.name}: {e}")
            else:
                if has_role:
                    try:
                        await member.remove_roles(role, reason="即時身分組：條件不符")
                    except discord.Forbidden:
                        pass
                    except discord.HTTPException as e:
                        print(f"[RoleCheck] 即時移除失敗 {member} - {role.name}: {e}")

    async def run_full_scan(self) -> None:
        """### 執行完整掃描：所有 guild × 所有成員 × 所有身分組設定

        作為安全網使用，一般在每日衰減後或管理員手動觸發。
        """
        # 刷新快取
        await self.refresh_cache()
        configs = self._role_configs_cache
        if not configs:
            return

        for guild in self.bot.guilds:
            try:
                await self._check_guild(guild, configs)
            except Exception as e:
                print(f"❌ 身分組完整掃描失敗 (Guild {guild.id}): {e}")

    ##### 定時任務 #####

    @tasks.loop(minutes=ROLE_CHECK_INTERVAL_MINUTES)
    async def role_check_loop(self):
        """### 定時身分組檢查（安全網）：比對成員聲望與簽到天數，自動增/減身分組
        """
        await self.run_full_scan()

    @role_check_loop.before_loop
    async def before_role_check_loop(self):
        """### 確保機器人準備好再開始巡迴
        """
        await self.bot.wait_until_ready()

    ##### 私有方法 #####

    async def _check_guild(self, guild: discord.Guild, configs: list[RoleConfigRow]) -> None:
        """### 對單一伺服器執行完整身分組檢查
        """
        try:
            users_data = await userBaseDB.get_users(order_by="total_sign_in", descending=True)
        except Exception as e:
            print(f"[RoleCheck] 無法取得使用者資料: {e}")
            return

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

            role = guild.get_role(role_id)
            if role is None:
                print(f"[RoleCheck] 身分組 {role_id} 不存在，跳過")
                continue

            for user_row in users_data:
                user_id = user_row["user_id"]
                member = member_cache.get(user_id)
                if member is None:
                    continue

                reputation = user_row["reputation"]
                total_sign_in = user_row["total_sign_in"]
                has_role = role in member.roles

                meets_all = True
                if req_rep > 0:
                    meets_all = meets_all and (reputation >= req_rep)
                if req_days > 0:
                    meets_all = meets_all and (total_sign_in >= req_days)

                if meets_all:
                    if not has_role:
                        try:
                            await member.add_roles(role, reason="自動身分組：達標")
                            await asyncio.sleep(1)
                        except discord.Forbidden:
                            break
                        except discord.HTTPException as e:
                            print(f"[RoleCheck] 授予失敗 {member} - {role.name}: {e}")
                else:
                    if has_role:
                        try:
                            await member.remove_roles(role, reason="自動身分組：條件不符")
                            await asyncio.sleep(1)
                        except discord.Forbidden:
                            break
                        except discord.HTTPException as e:
                            print(f"[RoleCheck] 移除失敗 {member} - {role.name}: {e}")


async def setup(bot: commands.Bot):
    await bot.add_cog(RoleCheckEvent(bot))
