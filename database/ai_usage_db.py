import aiosqlite
import asyncio
import os
from typing import Optional, TypedDict, Tuple
from config import DB_PATH


class AIDailyUsageRow(TypedDict):
    date: str
    call_count: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class AIUsageDatabase:
    """### AI 每日用量數據庫

    負責持久化追蹤每日 AI API 的呼叫次數與 Token 消耗量。
    在重啟機器人後仍能維持真實用量記錄，防止超額。
    """

    def __init__(self, db_path: str):
        self.db_path = os.path.join(db_path, "ai_usage.db")
        self.db: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        """應用啟動時呼叫"""
        if self.db is not None:
            return
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.db = await aiosqlite.connect(self.db_path)
        self.db.row_factory = aiosqlite.Row

        await self.db.execute("PRAGMA journal_mode=WAL")
        await self.db.execute("PRAGMA synchronous=NORMAL")

    async def close(self) -> None:
        """應用關閉時呼叫"""
        if self.db:
            await self.db.close()
            self.db = None

    async def _ensure_connection(self) -> None:
        """確保連線存活"""
        if self.db is None:
            await self.connect()
            await self.setup()
            return
        try:
            async with self.db.execute("SELECT 1") as cursor:
                await cursor.fetchone()
        except Exception:
            try:
                await self.db.close()
            except Exception:
                pass
            self.db = None
            await self.connect()
            await self.setup()

    async def setup(self) -> None:
        """初始化表格"""
        if self.db is None:
            await self.connect()

        async with self._lock:
            await self.db.execute("""
                CREATE TABLE IF NOT EXISTS ai_daily_usage (
                    date                TEXT PRIMARY KEY,
                    call_count          INTEGER NOT NULL DEFAULT 0,
                    prompt_tokens       INTEGER NOT NULL DEFAULT 0,
                    completion_tokens   INTEGER NOT NULL DEFAULT 0,
                    total_tokens        INTEGER NOT NULL DEFAULT 0
                )
            """)
            await self.db.commit()

    async def get_today_usage(self, date_str: str) -> AIDailyUsageRow:
        """### 查詢特定日期的用量數據"""
        await self._ensure_connection()
        async with self.db.execute(
            "SELECT * FROM ai_daily_usage WHERE date = ?", (date_str,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return dict(row) # type: ignore
            return {
                "date": date_str,
                "call_count": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            }

    async def check_quota_exceeded(
        self, date_str: str, max_tokens: int, max_calls: int
    ) -> Tuple[bool, str]:
        """### 檢查特定日期是否已達到限額

        Args:
            date_str: 日期字串 (YYYY-MM-DD)
            max_tokens: Token 上限 (0 代表不限)
            max_calls: 呼叫次數上限 (0 代表不限)

        Returns:
            (是否超額, 原因說明)
        """
        usage = await self.get_today_usage(date_str)

        if max_calls > 0 and usage["call_count"] >= max_calls:
            return True, f"今日呼叫次數已達上限 ({usage['call_count']}/{max_calls} 次)"

        if max_tokens > 0 and usage["total_tokens"] >= max_tokens:
            return True, f"今日 Token 用量已達上限 ({usage['total_tokens']}/{max_tokens} Tokens)"

        return False, ""

    async def record_usage(
        self, date_str: str, prompt_tokens: int, completion_tokens: int
    ) -> AIDailyUsageRow:
        """### 原子累加記錄一次 API 呼叫與 Token 用量"""
        await self._ensure_connection()
        add_total = prompt_tokens + completion_tokens

        async with self._lock:
            await self.db.execute(
                """
                INSERT INTO ai_daily_usage (date, call_count, prompt_tokens, completion_tokens, total_tokens)
                VALUES (?, 1, ?, ?, ?)
                ON CONFLICT(date) DO UPDATE SET
                    call_count        = call_count + 1,
                    prompt_tokens     = prompt_tokens + excluded.prompt_tokens,
                    completion_tokens = completion_tokens + excluded.completion_tokens,
                    total_tokens      = total_tokens + excluded.total_tokens
                """,
                (date_str, prompt_tokens, completion_tokens, add_total),
            )
            await self.db.commit()

        return await self.get_today_usage(date_str)


aiUsageDB = AIUsageDatabase(DB_PATH)
