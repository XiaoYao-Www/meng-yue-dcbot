from dotenv import load_dotenv
load_dotenv()

import os
import discord
from discord.ext import commands
import importlib.util
from database.user_base_db import userBaseDB
from database.role_db import roleConfigDB


##### 讀取設定 #####

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID", 0))
DB_PATH = os.getenv("DB_PATH")

if TOKEN is None:
    raise RuntimeError("❌ DISCORD_TOKEN 環境變數未設定！請在 .env 檔案中設定 DISCORD_TOKEN")
if DB_PATH is None:
    raise RuntimeError("❌ DB_PATH 環境變數未設定！請在 .env 檔案中設定 DB_PATH")


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

bot = MyBot(command_prefix="!", intents=intents)

##### 機器人啟動 #####

@bot.event
async def on_ready():
    # 資料庫初始化
    await userBaseDB.connect()
    await userBaseDB.setup()
    await roleConfigDB.connect()
    await roleConfigDB.setup()
    # 啟動完成
    print(f"{bot.user} 已上線！")

@bot.event
async def on_disconnect():
    """斷線時關閉資料庫連線"""
    await userBaseDB.close()
    await roleConfigDB.close()
    print("🔄 資料庫連線已關閉")

print("TOKEN loaded:", "✅" if TOKEN else "❌ MISSING")
if TOKEN is None:
    print("❌ DISCORD_TOKEN 未設定，無法啟動 Bot。")
    exit(1)
bot.run(TOKEN)