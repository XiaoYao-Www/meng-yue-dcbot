from discord.ext import commands, tasks
from datetime import datetime, time
from database.user_base_db import userBaseDB
from config import TZ, DAILY_REPUTATION_DECAY

class NewDayEvent(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.new_day_task.start()  # 插件載入時啟動每日任務

    def cog_unload(self):
        """### 卸載插件
        """
        self.new_day_task.cancel()  # 插件卸載時取消任務，避免背景殘留

    @tasks.loop(time=time(hour=0, minute=0, second=0, tzinfo=TZ))
    async def new_day_task(self):
        """### 每日任務
        """
        now_str = datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S')
        print(f"--- 🌙 每日任務開始 ({now_str}) ---")

        try:
            # 1. 聲望衰減
            decay_count = await userBaseDB.apply_daily_decay(DAILY_REPUTATION_DECAY)
            print(f"✅ 聲望衰減完成：{decay_count} 人受影響")

            # 2. 身分組完整掃描（衰減影響多人，全掃比多次單人檢查更高效）
            cog = self.bot.get_cog("RoleCheckEvent")
            if cog and hasattr(cog, "run_full_scan"):
                await cog.run_full_scan()

        except Exception as e:
            print(f"❌ 執行每日任務失敗: {e}")

    @new_day_task.before_loop
    async def before_new_day_task(self):
        """巡迴前檢查
        """
        # 確保機器人已經準備好 (已登入並快取載入完畢) 再開始執行迴圈
        await self.bot.wait_until_ready()

async def setup(bot: commands.Bot):
    await bot.add_cog(NewDayEvent(bot))
