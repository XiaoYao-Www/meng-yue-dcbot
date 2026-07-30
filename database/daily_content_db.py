import aiosqlite
import asyncio
import os
from typing import Optional, List, TypedDict, cast
from config import DB_PATH


class DailyContentRow(TypedDict):
    date: str
    section1_type: str          # 心理學／社會學
    section1_title: str
    section1_summary: str       # 頻道簡述
    section1_detail: str        # 討論串詳細資料
    section1_sources: str       # 出處引用
    section1_credibility: str   # 可信度評級
    section2_type: str          # 哲學／神話／神祕學
    section2_title: str
    section2_summary: str       # 頻道簡述
    section2_detail: str        # 討論串詳細資料
    section2_sources: str       # 出處引用
    section2_credibility: str   # 可信度評級
    generated_at: str
    verified_at: str            # 驗證時間
    verification_notes: str     # 驗證備註


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

        # 自動 checkpoint
        await self.db.execute("PRAGMA wal_autocheckpoint=1")

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
        """初始化表格（含舊 schema 遷移）"""
        if self.db is None:
            await self.connect()

        async with self._lock:
            # 檢查是否為舊 schema（有舊欄位則代表需遷移）
            cursor = await self.db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='daily_content'"
            )
            table_exists = await cursor.fetchone()

            if table_exists:
                # 檢查是否有新欄位（section1_type）來判斷 schema 版本
                col_cursor = await self.db.execute("PRAGMA table_info(daily_content)")
                columns = [row[1] async for row in col_cursor]

                if "section1_type" not in columns:
                    # 舊 schema：重新命名舊表，建立新表
                    print("[DailyContentDB] ⚠️ 偵測到舊版 schema，進行遷移...")
                    await self.db.execute("ALTER TABLE daily_content RENAME TO daily_content_old")
                    await self.db.commit()

            await self.db.execute("""
                CREATE TABLE IF NOT EXISTS daily_content (
                    date                  TEXT PRIMARY KEY,
                    section1_type         TEXT NOT NULL,
                    section1_title        TEXT NOT NULL,
                    section1_summary      TEXT NOT NULL,
                    section1_detail       TEXT NOT NULL,
                    section1_sources      TEXT NOT NULL,
                    section1_credibility  TEXT NOT NULL,
                    section2_type         TEXT NOT NULL,
                    section2_title        TEXT NOT NULL,
                    section2_summary      TEXT NOT NULL,
                    section2_detail       TEXT NOT NULL,
                    section2_sources      TEXT NOT NULL,
                    section2_credibility  TEXT NOT NULL,
                    generated_at          TEXT NOT NULL,
                    verified_at           TEXT NOT NULL DEFAULT '',
                    verification_notes    TEXT NOT NULL DEFAULT ''
                )
            """)
            await self.db.commit()

            # 若存在舊表，嘗試複製相容資料並刪除
            try:
                await self.db.execute(
                    "SELECT COUNT(*) FROM daily_content_old"
                )
                old_exists = True
            except Exception:
                old_exists = False

            if old_exists:
                print("[DailyContentDB] ℹ️ 舊表 daily_content_old 仍存在，可手動刪除")
                # 不自動刪除，保留以備回查

    ##### 寫入功能 #####

    async def set_daily_content(
        self,
        date: str,
        section1_type: str,
        section1_title: str,
        section1_summary: str,
        section1_detail: str,
        section1_sources: str,
        section1_credibility: str,
        section2_type: str,
        section2_title: str,
        section2_summary: str,
        section2_detail: str,
        section2_sources: str,
        section2_credibility: str,
        generated_at: str,
        verified_at: str = "",
        verification_notes: str = "",
    ) -> None:
        """### 寫入每日內容（以 date 為 PK，同日期重複寫入會覆蓋）

        Args:
            date: 日期 YYYY-MM-DD
            section1_type: 第一則類型（心理學／社會學）
            section1_title: 第一則標題
            section1_summary: 第一則簡述（頻道用）
            section1_detail: 第一則詳細資料（討論串用）
            section1_sources: 第一則出處引用
            section1_credibility: 第一則可信度評級
            section2_type: 第二則類型（哲學／神話／神祕學）
            section2_title: 第二則標題
            section2_summary: 第二則簡述（頻道用）
            section2_detail: 第二則詳細資料（討論串用）
            section2_sources: 第二則出處引用
            section2_credibility: 第二則可信度評級
            generated_at: 生成時間
            verified_at: 驗證時間
            verification_notes: 驗證備註
        """
        await self._ensure_connection()
        async with self._lock:
            await self.db.execute("""
                INSERT OR REPLACE INTO daily_content
                    (date, section1_type, section1_title, section1_summary, section1_detail,
                     section1_sources, section1_credibility,
                     section2_type, section2_title, section2_summary, section2_detail,
                     section2_sources, section2_credibility,
                     generated_at, verified_at, verification_notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (date, section1_type, section1_title, section1_summary, section1_detail,
                  section1_sources, section1_credibility,
                  section2_type, section2_title, section2_summary, section2_detail,
                  section2_sources, section2_credibility,
                  generated_at, verified_at, verification_notes))
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
