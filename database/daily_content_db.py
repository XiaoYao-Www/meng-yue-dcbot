import aiosqlite
import asyncio
import os
from typing import Optional, List, TypedDict, cast
from config import DB_PATH


class DailyContentRow(TypedDict):
    id: int
    date: str                  # 日期 YYYY-MM-DD（同一天可有多筆）
    section_type: str          # 次領域（如「犯罪心理學」）
    section_title: str
    section_summary: str       # 頻道簡述
    section_detail: str        # 討論串詳細資料
    section_quick_learn: str   # 快速學習（新手速懂區塊）
    section_sources: str       # 出處引用
    section_credibility: str   # 可信度評級
    generated_at: str
    verified_at: str           # 驗證時間
    verification_notes: str    # 驗證備註


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
        """初始化表格（含舊 schema 遷移：雙篇一行 → 單篇一行）"""
        if self.db is None:
            await self.connect()

        async with self._lock:
            # 檢查是否為舊 schema（有舊欄位則代表需遷移）
            cursor = await self.db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='daily_content'"
            )
            table_exists = await cursor.fetchone()

            needs_legacy_split = False
            if table_exists:
                col_cursor = await self.db.execute("PRAGMA table_info(daily_content)")
                columns = [row[1] async for row in col_cursor]

                if "section1_type" in columns:
                    # 舊版雙篇 schema（一天一筆、含 section1_/section2_ 欄位）：
                    # 保留舊表，重建新表後拆行遷移
                    print("[DailyContentDB] 偵測到舊版雙篇 schema，進行單篇化遷移...")
                    await self.db.execute("ALTER TABLE daily_content RENAME TO daily_content_legacy")
                    await self.db.commit()
                    needs_legacy_split = True
                elif "section_type" not in columns:
                    # 更舊的未知 schema：不嘗試映射，保留舊表並重建新表
                    print("[DailyContentDB] 偵測到未知舊版 schema，保留舊表並重建新表...")
                    await self.db.execute("ALTER TABLE daily_content RENAME TO daily_content_old")
                    await self.db.commit()

            # 建立新表：一篇一筆，同一天可有多筆（date 建索引供查詢）
            await self.db.execute("""
                CREATE TABLE IF NOT EXISTS daily_content (
                    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                    date                  TEXT NOT NULL,
                    section_type          TEXT NOT NULL,
                    section_title         TEXT NOT NULL,
                    section_summary       TEXT NOT NULL,
                    section_detail        TEXT NOT NULL,
                    section_quick_learn   TEXT NOT NULL DEFAULT '',
                    section_sources       TEXT NOT NULL,
                    section_credibility   TEXT NOT NULL DEFAULT '未驗證',
                    generated_at          TEXT NOT NULL,
                    verified_at           TEXT NOT NULL DEFAULT '',
                    verification_notes    TEXT NOT NULL DEFAULT ''
                )
            """)
            await self.db.execute(
                "CREATE INDEX IF NOT EXISTS idx_daily_content_date ON daily_content(date)"
            )
            await self.db.commit()

            if needs_legacy_split:
                await self._migrate_legacy_rows()

    async def _migrate_legacy_rows(self) -> None:
        """### 將舊雙篇表 daily_content_legacy 每筆拆成兩行單篇插入新表

        舊表結構：date（PK）+ section1_* 六欄 + section2_* 六欄。
        新表結構：每筆一篇，同一天多筆，不再區分第一則／第二則。
        舊資料沒有快速學習欄位，遷移後為空字串（顯示端自動跳過）。
        """
        try:
            async with self.db.execute("SELECT * FROM daily_content_legacy") as cursor:
                rows = await cursor.fetchall()

            inserted = 0
            skipped = 0
            for row in rows:
                r = dict(row)
                date = r.get("date", "")
                generated_at = r.get("generated_at", "")
                verified_at = r.get("verified_at", "")
                notes = r.get("verification_notes", "")

                for prefix in ("section1", "section2"):
                    title = r.get(f"{prefix}_title", "")
                    if not title:
                        skipped += 1
                        continue
                    await self.db.execute(
                        """
                        INSERT INTO daily_content
                            (date, section_type, section_title, section_summary,
                             section_detail, section_quick_learn, section_sources,
                             section_credibility, generated_at, verified_at,
                             verification_notes)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            date,
                            r.get(f"{prefix}_type", ""),
                            title,
                            r.get(f"{prefix}_summary", ""),
                            r.get(f"{prefix}_detail", ""),
                            "",
                            r.get(f"{prefix}_sources", ""),
                            r.get(f"{prefix}_credibility", "未驗證"),
                            generated_at,
                            verified_at,
                            notes,
                        ),
                    )
                    inserted += 1
            await self.db.commit()
            print(
                f"[DailyContentDB] 單篇化遷移完成：舊表 {len(rows)} 筆 "
                f"→ 新表 {inserted} 篇（跳過空欄位 {skipped} 筆）"
            )
        except Exception as e:
            print(f"[DailyContentDB] 單篇化遷移失敗: {e}")

    ##### 寫入功能 #####

    async def set_daily_content(
        self,
        date: str,
        section_type: str,
        section_title: str,
        section_summary: str,
        section_detail: str,
        section_quick_learn: str,
        section_sources: str,
        section_credibility: str,
        generated_at: str,
        verified_at: str = "",
        verification_notes: str = "",
    ) -> None:
        """### 寫入一篇每日內容（每筆一篇文章，同一天可有多筆）

        Args:
            date: 日期 YYYY-MM-DD
            section_type: 次領域（如「犯罪心理學」）
            section_title: 標題
            section_summary: 簡述（頻道用）
            section_detail: 詳細資料（討論串用）
            section_quick_learn: 快速學習（新手速懂區塊）
            section_sources: 出處引用
            section_credibility: 可信度評級
            generated_at: 生成時間
            verified_at: 驗證時間
            verification_notes: 驗證備註
        """
        await self._ensure_connection()
        async with self._lock:
            await self.db.execute(
                """
                INSERT INTO daily_content
                    (date, section_type, section_title, section_summary,
                     section_detail, section_quick_learn, section_sources,
                     section_credibility, generated_at, verified_at,
                     verification_notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    date, section_type, section_title, section_summary,
                    section_detail, section_quick_learn, section_sources,
                    section_credibility, generated_at, verified_at,
                    verification_notes,
                ),
            )
            await self.db.commit()

    ##### 查詢功能 #####

    async def get_daily_contents(self, date: str) -> List[DailyContentRow]:
        """### 查詢指定日期的所有每日內容（同一天多筆）

        Args:
            date: 日期 YYYY-MM-DD

        Returns:
            該日全部文章（依 id 順序），無則空清單
        """
        await self._ensure_connection()
        try:
            async with self.db.execute(
                "SELECT * FROM daily_content WHERE date = ? ORDER BY id ASC", (date,)
            ) as cursor:
                rows = await cursor.fetchall()
                return [cast(DailyContentRow, dict(row)) for row in rows]
        except Exception as e:
            print(f"[DailyContentDB Error] 查詢失敗: {e}")
            return []

    async def get_all_contents(self) -> List[DailyContentRow]:
        """### 取得所有已儲存的每日內容（供 AI 去重使用）

        Returns:
            List[DailyContentRow]
        """
        await self._ensure_connection()
        try:
            async with self.db.execute(
                "SELECT * FROM daily_content ORDER BY date DESC, id ASC"
            ) as cursor:
                rows = await cursor.fetchall()
                return [cast(DailyContentRow, dict(row)) for row in rows]
        except Exception as e:
            print(f"[DailyContentDB Error] 查詢全部失敗: {e}")
            return []


dailyContentDB = DailyContentDatabase(DB_PATH)
