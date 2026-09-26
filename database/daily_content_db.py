import aiosqlite
import asyncio
import os
from typing import Optional, List, TypedDict, cast
from config import DB_PATH


class DailyContentRow(TypedDict):
    id: int
    date: str                  # 日期 YYYY-MM-DD（同一天可有多筆；庫存為空字串）
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
    status: str                # 狀態：'published' (已發送) 或 'stock' (庫存中)


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
        # 記憶體調優：限制連線頁面快取為 1MB，禁用大檔案記憶體映射
        await self.db.execute("PRAGMA cache_size = -1024")
        await self.db.execute("PRAGMA mmap_size = 0")

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
        """初始化表格（含舊 schema 遷移與 status 欄位維護）"""
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

            # 建立新表：一篇一筆，同一天可有多筆
            await self.db.execute("""
                CREATE TABLE IF NOT EXISTS daily_content (
                    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                    date                  TEXT NOT NULL DEFAULT '',
                    section_type          TEXT NOT NULL,
                    section_title         TEXT NOT NULL,
                    section_summary       TEXT NOT NULL,
                    section_detail        TEXT NOT NULL,
                    section_quick_learn   TEXT NOT NULL DEFAULT '',
                    section_sources       TEXT NOT NULL,
                    section_credibility   TEXT NOT NULL DEFAULT '未驗證',
                    generated_at          TEXT NOT NULL,
                    verified_at           TEXT NOT NULL DEFAULT '',
                    verification_notes    TEXT NOT NULL DEFAULT '',
                    status                TEXT NOT NULL DEFAULT 'published'
                )
            """)

            # 先檢查並動態補充欄位（確保既有舊資料庫先完成欄位升級，再建立索引）
            col_cursor = await self.db.execute("PRAGMA table_info(daily_content)")
            columns = [row[1] async for row in col_cursor]
            if "status" not in columns:
                print("[DailyContentDB] 補全 status 欄位 (預設 'published')...")
                await self.db.execute("ALTER TABLE daily_content ADD COLUMN status TEXT NOT NULL DEFAULT 'published'")
            if "section_quick_learn" not in columns:
                print("[DailyContentDB] 補全 section_quick_learn 欄位...")
                await self.db.execute("ALTER TABLE daily_content ADD COLUMN section_quick_learn TEXT NOT NULL DEFAULT ''")

            # 欄位確保存在後，再建立索引
            await self.db.execute(
                "CREATE INDEX IF NOT EXISTS idx_daily_content_date ON daily_content(date)"
            )
            await self.db.execute(
                "CREATE INDEX IF NOT EXISTS idx_daily_content_status ON daily_content(status)"
            )

            # 建立每日單字表
            await self.db.execute("""
                CREATE TABLE IF NOT EXISTS daily_words (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    date          TEXT NOT NULL UNIQUE,
                    word          TEXT NOT NULL,
                    data_json     TEXT NOT NULL,
                    published_at  TEXT NOT NULL
                )
            """)
            await self.db.execute(
                "CREATE INDEX IF NOT EXISTS idx_daily_words_date ON daily_words(date)"
            )
            await self.db.commit()

            if needs_legacy_split:
                await self._migrate_legacy_rows()

    async def _migrate_legacy_rows(self) -> None:
        """### 將舊雙篇表 daily_content_legacy 每筆拆成兩行單篇插入新表
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
                             verification_notes, status)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'published')
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
        status: str = "published",
    ) -> None:
        """### 寫入一篇每日內容"""
        await self._ensure_connection()
        async with self._lock:
            await self.db.execute(
                """
                INSERT INTO daily_content
                    (date, section_type, section_title, section_summary,
                     section_detail, section_quick_learn, section_sources,
                     section_credibility, generated_at, verified_at,
                     verification_notes, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    date, section_type, section_title, section_summary,
                    section_detail, section_quick_learn, section_sources,
                    section_credibility, generated_at, verified_at,
                    verification_notes, status,
                ),
            )
            await self.db.commit()

    async def add_stock_content(
        self,
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
        """### 新增一篇驗證完成的文章至庫存庫 (status='stock')"""
        await self.set_daily_content(
            date="",
            section_type=section_type,
            section_title=section_title,
            section_summary=section_summary,
            section_detail=section_detail,
            section_quick_learn=section_quick_learn,
            section_sources=section_sources,
            section_credibility=section_credibility,
            generated_at=generated_at,
            verified_at=verified_at,
            verification_notes=verification_notes,
            status="stock",
        )

    ##### 庫存管理功能 #####

    async def get_stock_count(self) -> int:
        """### 取得目前庫存中的文章數量"""
        await self._ensure_connection()
        try:
            async with self.db.execute(
                "SELECT COUNT(*) as cnt FROM daily_content WHERE status = 'stock'"
            ) as cursor:
                row = await cursor.fetchone()
                return row["cnt"] if row else 0
        except Exception as e:
            print(f"[DailyContentDB Error] 查詢庫存數量失敗: {e}")
            return 0

    async def get_stock_contents(self, limit: int) -> List[DailyContentRow]:
        """### 取得最舊的未發送庫存文章"""
        await self._ensure_connection()
        try:
            async with self.db.execute(
                "SELECT * FROM daily_content WHERE status = 'stock' ORDER BY id ASC LIMIT ?",
                (limit,),
            ) as cursor:
                rows = await cursor.fetchall()
                return [cast(DailyContentRow, dict(row)) for row in rows]
        except Exception as e:
            print(f"[DailyContentDB Error] 取得庫存內容失敗: {e}")
            return []

    async def publish_stock_content(self, article_id: int, date_str: str) -> None:
        """### 將指定庫存文章劃歸為當日已發送 (status='published', date=date_str)"""
        await self._ensure_connection()
        async with self._lock:
            await self.db.execute(
                "UPDATE daily_content SET date = ?, status = 'published' WHERE id = ?",
                (date_str, article_id),
            )
            await self.db.commit()

    ##### 查詢功能 #####

    async def get_daily_contents(self, date: str) -> List[DailyContentRow]:
        """### 查詢指定日期的已發送每日內容"""
        await self._ensure_connection()
        try:
            async with self.db.execute(
                "SELECT * FROM daily_content WHERE date = ? AND status = 'published' ORDER BY id ASC", (date,)
            ) as cursor:
                rows = await cursor.fetchall()
                return [cast(DailyContentRow, dict(row)) for row in rows]
        except Exception as e:
            print(f"[DailyContentDB Error] 查詢失敗: {e}")
            return []

    async def get_all_contents(self) -> List[DailyContentRow]:
        """### 取得所有已儲存的內容（含已發送與庫存中，供 AI 提示詞去重使用）"""
        await self._ensure_connection()
        try:
            async with self.db.execute(
                "SELECT * FROM daily_content ORDER BY id DESC"
            ) as cursor:
                rows = await cursor.fetchall()
                return [cast(DailyContentRow, dict(row)) for row in rows]
        except Exception as e:
            print(f"[DailyContentDB Error] 查詢全部失敗: {e}")
            return []

    ##### 每日單字功能 #####

    async def get_daily_word(self, date: str) -> Optional[dict]:
        """### 查詢指定日期的每日單字"""
        await self._ensure_connection()
        try:
            async with self.db.execute(
                "SELECT * FROM daily_words WHERE date = ?", (date,)
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None
        except Exception as e:
            print(f"[DailyContentDB Error] 查詢每日單字失敗: {e}")
            return None

    async def set_daily_word(self, date: str, word: str, data_json: str, published_at: str) -> None:
        """### 儲存當日每日單字"""
        await self._ensure_connection()
        async with self._lock:
            await self.db.execute(
                """
                INSERT OR REPLACE INTO daily_words (date, word, data_json, published_at)
                VALUES (?, ?, ?, ?)
                """,
                (date, word, data_json, published_at),
            )
            await self.db.commit()

    async def get_all_published_words(self) -> set[str]:
        """### 取得所有已發布過的單字集合（用於去重）"""
        await self._ensure_connection()
        try:
            async with self.db.execute(
                "SELECT word FROM daily_words"
            ) as cursor:
                rows = await cursor.fetchall()
                return {row["word"].lower() for row in rows}
        except Exception as e:
            print(f"[DailyContentDB Error] 查詢已發布單字失敗: {e}")
            return set()


dailyContentDB = DailyContentDatabase(DB_PATH)
