from discord.ext import commands, tasks
from discord import Message
from database.user_base_db import userBaseDB
from datetime import datetime, timedelta, time
from config import TZ

class SignInEvent(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._today_cache: set[int] = set() # 儲存今天已簽到的 user_id
        self.new_day_task.start()  # 插件載入時啟動每日任務

    def cog_unload(self):
        """### 卸載插件
        """
        self.new_day_task.cancel()  # 插件卸載時取消任務，避免背景殘留

    @commands.Cog.listener()
    async def on_message(self, message: Message):
        """偵測簽到

        Args:
            message (Message): 訊息
        """
        try:
            # 排除機器人訊息和已經簽到過
            if message.author.bot or message.author.id in self._today_cache:
                return
            
            user_id = message.author.id
            now = datetime.now(TZ)
            today_str = now.strftime('%Y-%m-%d')
            
            # 取得使用者資料
            user = await userBaseDB.get_user(user_id)

            # 判斷是否今天已簽到 (雙重檢查)
            # user['last_sign_in'] 取出來會是字串 "YYYY-MM-DD"
            if user['last_sign_in'] == today_str:
                self._today_cache.add(user_id)
                return
            
            # 準備日期資料
            yesterday_str = (now - timedelta(days=1)).strftime('%Y-%m-%d')

            # 判斷是否連續簽到
            if user['last_sign_in'] == yesterday_str:
                new_streak = user['streak_count'] + 1
            else:
                new_streak = 1

            # 更新資料庫
            await userBaseDB.update_user_sign_in(user_id, new_streak)             # 更新簽到
            await userBaseDB.update_user_stats(user_id, xp=100, reputation=100)   # 增加經驗與聲望

            # 即時身分組檢查（單人）
            if message.guild:
                cog = self.bot.get_cog("RoleCheckEvent")
                if cog and hasattr(cog, "check_single_user"):
                    await cog.check_single_user(message.guild, user_id)

            # 成功簽到，給表情符號
            self._today_cache.add(user_id)
            await message.add_reaction("🗓️")
        except Exception as e:
            print(f"❌ sign_in on_message 處理錯誤: {e}")

    @tasks.loop(time=time(hour=0, minute=0, second=0, tzinfo=TZ))
    async def new_day_task(self):
        """### 每日任務
        """
        now_str = datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S')
        print(f"--- 簽到重製開始 ({now_str}) ---")

        try:
            self._today_cache.clear()

            # 簽到過期檢查
            reset_count = await userBaseDB.reset_expired_streaks()
            print(f"✅ 簽到重置完成：{reset_count} 人天數歸零")

        except Exception as e:
            print(f"❌ 執行簽到重製失敗: {e}")

    @new_day_task.before_loop
    async def before_new_day_task(self):
        """巡迴前檢查
        """
        # 確保機器人已經準備好 (已登入並快取載入完畢) 再開始執行迴圈
        await self.bot.wait_until_ready()

async def setup(bot: commands.Bot):
    await bot.add_cog(SignInEvent(bot))