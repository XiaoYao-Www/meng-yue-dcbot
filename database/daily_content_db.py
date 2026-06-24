import aiosqlite
import asyncio
import os
from typing import Optional, List, TypedDict, cast
from config import DB_PATH


class DailyContentRow(TypedDict):
    date: str
    quote_text: str
    quote_source: str
    quote_author: str
    quote_translation: str
    idiom_text: str
    idiom_explanation: str
    idiom_usage: str
    idiom_origin: str
    generated_at: str


class DailyContentDatabase:
    def __init__(self, db_path: str):
        """
        初始化每日內容資料庫路徑與並發鎖
        """
        self.db_path = os.path.join(db_path, "daily_content.db")
        self.db: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        """在應用啟動時呼叫一次，保持連線"""
        if self.db is not None:
            return  # 已連線，防止 RESUME 事件重複初始化
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.db = await aiosqlite.connect(self.db_path)
        self.db.row_factory = aiosqlite.Row

        # 開啟 WAL 模式與 NORMAL 同步，降低磁碟 IO 等待
        await self.db.execute("PRAGMA journal_mode=WAL")
        await self.db.execute("PRAGMA synchronous=NORMAL")

    async def close(self) -> None:
        """在應用關閉時呼叫"""
        if self.db:
            await self.db.close()
            self.db = None

    async def _ensure_connection(self) -> None:
        """### 確保資料庫連線存活，若已斷則自動重連並重建表格
        """
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
                CREATE TABLE IF NOT EXISTS daily_content (
                    date               TEXT PRIMARY KEY,
                    quote_text         TEXT NOT NULL,
                    quote_source       TEXT NOT NULL,
                    quote_author       TEXT NOT NULL,
                    quote_translation  TEXT NOT NULL,
                    idiom_text         TEXT NOT NULL,
                    idiom_explanation  TEXT NOT NULL,
                    idiom_usage        TEXT NOT NULL,
                    idiom_origin       TEXT NOT NULL,
                    generated_at       TEXT NOT NULL
                )
            """)
            await self.db.commit()

    ##### 寫入功能 #####

    async def set_daily_content(
        self,
        date: str,
        quote_text: str,
        quote_source: str,
        quote_author: str,
        quote_translation: str,
        idiom_text: str,
        idiom_explanation: str,
        idiom_usage: str,
        idiom_origin: str,
        generated_at: str,
    ) -> None:
        """### 寫入每日內容（以 date 為 PK，同日期重複寫入會覆蓋）

        Args:
            date: 日期 YYYY-MM-DD
            quote_text: 佳句原文
            quote_source: 佳句出處（書名/作品名）
            quote_author: 佳句作者
            quote_translation: 佳句繁體中文翻譯
            idiom_text: 成語/諺語
            idiom_explanation: 成語解釋
            idiom_usage: 成語用法例句
            idiom_origin: 成語出處
            generated_at: 生成時間
        """
        await self._ensure_connection()
        async with self._lock:
            await self.db.execute("""
                INSERT OR REPLACE INTO daily_content
                    (date, quote_text, quote_source, quote_author, quote_translation,
                     idiom_text, idiom_explanation, idiom_usage, idiom_origin, generated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (date, quote_text, quote_source, quote_author, quote_translation,
                  idiom_text, idiom_explanation, idiom_usage, idiom_origin, generated_at))
            await self.db.commit()

    ##### 查詢功能 #####

    async def get_daily_content(self, date: str) -> Optional[DailyContentRow]:
        """### 查詢指定日期的每日內容

        Args:
            date: 日期 YYYY-MM-DD

        Returns:
            DailyContentRow 或 None
        """
        await self._ensure_connection()
        try:
            async with self.db.execute(
                "SELECT * FROM daily_content WHERE date = ?", (date,)
            ) as cursor:
                row = await cursor.fetchone()
                return cast(DailyContentRow, dict(row)) if row else None
        except Exception as e:
            print(f"[DailyContentDB Error] 查詢失敗: {e}")
            return None

    async def get_all_contents(self) -> List[DailyContentRow]:
        """### 取得所有已儲存的每日內容（供 AI 去重使用）

        Returns:
            List[DailyContentRow]
        """
        await self._ensure_connection()
        try:
            async with self.db.execute("SELECT * FROM daily_content ORDER BY date DESC") as cursor:
                rows = await cursor.fetchall()
                return [cast(DailyContentRow, dict(row)) for row in rows]
        except Exception as e:
            print(f"[DailyContentDB Error] 查詢全部失敗: {e}")
            return []



dailyContentDB = DailyContentDatabase(DB_PATH)
