# 配置文件
import datetime

TZ: datetime.timezone = datetime.timezone(datetime.timedelta(hours=8)) # 臺灣時區
INIT_SIGN_TIME: str = "1970-01-01"                              # 預設簽到時間
DAILY_REPUTATION_DECAY: int = 50                                  # 每日聲望減衰率
MAX_REPUTATION: int = 10000                                       # 聲望上限
MAX_MESSAGE_REPUTATION: int = 50                                   # 消息聲望上限
ROLE_CHECK_INTERVAL_MINUTES: int = 360                               # 完整掃描間隔（分鐘），6小時一次
ROLE_CHECK_SINGLE_ENABLED: bool = True                              # 啟用即時單人檢查
