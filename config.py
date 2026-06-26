# 配置文件
import os
import datetime
from datetime import time


# ── 環境變數讀取（一次讀取、集中驗證）──

DISCORD_TOKEN: str | None = os.getenv("DISCORD_TOKEN")
GUILD_ID: int = int(os.getenv("GUILD_ID", "0"))
DB_PATH: str | None = os.getenv("DB_PATH")
DEEPSEEK_API_KEY: str | None = os.getenv("DEEPSEEK_API_KEY")
DAILY_CHANNEL: int = int(os.getenv("DAILY_CHANNEL", "0"))

# 環境變數驗證
_MISSING: list[str] = []
if not DISCORD_TOKEN:
    _MISSING.append("DISCORD_TOKEN")
if not DB_PATH:
    _MISSING.append("DB_PATH")
if _MISSING:
    raise RuntimeError(
        f"❌ 以下環境變數未設定：{'、'.join(_MISSING)}，請檢查 .env 檔案"
    )


# ── 系統時區 ──

TZ: datetime.timezone = datetime.timezone(datetime.timedelta(hours=8))  # 臺灣時區 UTC+8


# ── 每日排程時間 ──

NEW_DAY_TIME: time = time(hour=0, minute=0, second=0, tzinfo=TZ)          # 每日任務（聲望衰減、簽到重置）
DAILY_MESSAGE_TIME: time = time(hour=8, minute=0, second=0, tzinfo=TZ)    # 每日佳句發送


# ── 簽到相關 ──

INIT_SIGN_TIME: str = "1970-01-01"           # 預設簽到時間
SIGN_IN_XP: int = 100                        # 簽到經驗值
SIGN_IN_REPUTATION: int = 100                # 簽到聲望值


# ── 聲望相關 ──

DAILY_REPUTATION_DECAY: int = 50             # 每日聲望衰減量
MAX_REPUTATION: int = 10000                  # 聲望上限
MAX_MESSAGE_REPUTATION: int = 50             # 單則訊息聲望上限


# ── 身分組檢查 ──

ROLE_CHECK_INTERVAL_MINUTES: int = 360       # 完整掃描間隔（分鐘），6 小時一次
ROLE_CHECK_SINGLE_ENABLED: bool = True       # 啟用即時單人檢查


# ── 使用者自訂項目清單 ──

MAX_USER_ITEMS_PER_TAG: int = 50             # 單一標籤下項目數量上限


# ── 每日學習 AI 重試 ──

DAILY_AI_MAX_RETRIES: int = 3               # API 呼叫失敗/回傳錯誤時的最大重試次數
DAILY_AI_RETRY_BASE_DELAY: float = 2.0      # 重試基礎等待秒數（指數退避：delay * (2 ** attempt)）
