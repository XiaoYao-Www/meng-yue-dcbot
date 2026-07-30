from dotenv import load_dotenv
load_dotenv()

import os
import shutil
import discord
from discord.ext import commands
import importlib.util
from config import DISCORD_TOKEN, GUILD_ID, DB_PATH
from database.user_base_db import userBaseDB
from database.role_db import roleConfigDB
from database.daily_content_db import dailyContentDB
from utils.article_exporter import export_all_history


def _migrate_legacy_db_path() -> None:
    """### 將舊版 data/*.db 遷移至 data/db/*.db

    檢查 data/ 下是否有 legacy .db 檔案，若有則搬移至新的 DB_PATH (data/db/)。
    僅在首次部署或更新後執行一次，後續當 data/ 下無 .db 檔即跳過。
    """
    old_dir = os.path.join(os.path.dirname(__file__), "data")
    new_dir = os.path.join(os.path.dirname(__file__), DB_PATH)  # data/db
    db_files = ["daily_content.db", "user_base.db", "role_config.db"]

    legacy_files = [f for f in db_files if os.path.exists(os.path.join(old_dir, f))]
    if not legacy_files:
        return  # 無舊檔，跳過遷移

    os.makedirs(new_dir, exist_ok=True)
    for fname in legacy_files:
        old_path = os.path.join(old_dir, fname)
        new_path = os.path.join(new_dir, fname)
        try:
            shutil.move(old_path, new_path)
            print(f"📦 資料庫遷移: {old_path} → {new_path}")
        except Exception as e:
            print(f"❌ 資料庫遷移失敗 {old_path}: {e}")

        # 一併搬移 WAL/SHM journal 檔案（若存在）
        for ext in ("-wal", "-shm", "-journal"):
            old_j = old_path + ext
            new_j = new_path + ext
            if os.path.exists(old_j):
                try:
                    shutil.move(old_j, new_j)
                    print(f"📦 搬移附屬檔案: {old_j} → {new_j}")
                except Exception as e:
                    print(f"⚠️ 搬移 {old_j} 失敗: {e}")


##### 函式定義 #####
    
async def load_folder(folder_path: str) -> None:
    """### 載入插件資料夾

    Args:
        folder_path (str): 目標資料夾
    """
    for filename in os.listdir(folder_path):
        if filename.endswith(".py"):
            path = f"{folder_path}/{filename}"
            spec = importlib.util.spec_from_file_location(filename[:-3], path)
            module = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(module)

                if hasattr(module, "setup"):
                    await module.setup(bot)
                    print(f"已載入插件: {filename}")
            except Exception as e:
                print(f"❌ 載入失敗 {filename}: {e}")

##### 機器人設定 #####

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

class MyBot(commands.Bot):
    async def setup_hook(self):
        """### 載入插件與註冊命令
        """
        await load_folder("./commands") # 載入命令
        await load_folder("./events")   # 載入事件

        if GUILD_ID:
            guild = discord.Object(id=GUILD_ID)     # 取得群組物件
            self.tree.copy_global_to(guild=guild)   # 複製全域命令到群組
            synced = await self.tree.sync(guild=guild)
            print(f"已從 Guild {GUILD_ID} 同步了 {len(synced)} 個指令")
        else:
            synced = await self.tree.sync()
            print(f"Slash commands 已同步到全局，共 {len(synced)} 個")

    async def on_app_command_error(self, interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
        """### 全域指令錯誤處理
        """
        print(f"❌ 指令錯誤: {interaction.command} - {error}")
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ 指令執行發生錯誤，請稍後再試。", ephemeral=True)
            else:
                await interaction.followup.send("❌ 指令執行發生錯誤，請稍後再試。", ephemeral=True)
        except Exception as e:
            print(f"❌ 錯誤處理器自身異常: {e}")

    async def close(self) -> None:
        """### 完整關閉機器人（僅在 shutdown 時觸發，不會被 RESUME 干擾）
        """
        await userBaseDB.close()
        await roleConfigDB.close()
        await dailyContentDB.close()
        print("🔄 資料庫連線已關閉")
        await super().close()

bot: MyBot = MyBot(command_prefix="!", intents=intents)

##### 機器人啟動 #####

@bot.event
async def on_ready():
    """### 機器人啟動完成事件
    """
    try:
        # 遷移舊版 data/*.db → data/db/*.db（安全起見僅執行一次）
        _migrate_legacy_db_path()

        # 資料庫初始化
        await userBaseDB.connect()
        await userBaseDB.setup()
        await roleConfigDB.connect()
        await roleConfigDB.setup()
        await dailyContentDB.connect()
        await dailyContentDB.setup()
        # 啟動時自動匯出遺漏的歷史文章
        await export_all_history(dailyContentDB)

        # 啟動完成
        print(f"{bot.user} 已上線！")
    except Exception as e:
        print(f"❌ on_ready 資料庫初始化失敗: {e}")

print("TOKEN loaded:", "✅" if DISCORD_TOKEN else "❌ MISSING")
if DISCORD_TOKEN is None:
    print("❌ DISCORD_TOKEN 未設定，無法啟動 Bot。")
    exit(1)
bot.run(DISCORD_TOKEN)