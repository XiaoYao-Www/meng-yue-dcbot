import aiosqlite
import asyncio
import os
from datetime import datetime, timezone
from typing import Optional, List, TypedDict, cast


class RPGSessionRow(TypedDict):
    id: int
    name: str
    forum_channel_id: int
    main_thread_id: int
    owner_id: int
    created_at: str


class RPGSessionDatabase:
    def __init__(self, db_path: str):
        self.db_path = os.path.join(db_path, "rpg_sessions.db")
        self.db: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.db = await aiosqlite.connect(self.db_path)
        self.db.row_factory = aiosqlite.Row
        await self.db.execute("PRAGMA journal_mode=WAL")
        await self.db.execute("PRAGMA synchronous=NORMAL")

    async def close(self) -> None:
        if self.db:
            await self.db.close()
            self.db = None

    async def _ensure_connection(self) -> None:
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
        if self.db is None:
            await self.connect()
        async with self._lock:
            await self.db.execute("""
                CREATE TABLE IF NOT EXISTS rpg_sessions (
                    id                INTEGER PRIMARY KEY AUTOINCREMENT,
                    name              TEXT NOT NULL,
                    forum_channel_id  INTEGER NOT NULL,
                    main_thread_id    INTEGER NOT NULL,
                    owner_id          INTEGER NOT NULL,
                    created_at        TEXT NOT NULL
                )
            """)
            await self.db.commit()

    ##### 查詢功能 #####

    async def get_session(self, session_id: int) -> Optional[RPGSessionRow]:
        """### 取得單一 RPG 場次"""
        await self._ensure_connection()
        try:
            async with self.db.execute(
                "SELECT * FROM rpg_sessions WHERE id = ?", (session_id,)
            ) as cursor:
                row = await cursor.fetchone()
                return cast(RPGSessionRow, dict(row)) if row else None
        except Exception as e:
            print(f"[RPGDB Error] 查詢場次失敗: {e}")
            return None

    async def get_all_sessions(self) -> List[RPGSessionRow]:
        """### 取得所有 RPG 場次"""
        await self._ensure_connection()
        try:
            async with self.db.execute("SELECT * FROM rpg_sessions ORDER BY id DESC") as cursor:
                rows = await cursor.fetchall()
                return [cast(RPGSessionRow, dict(row)) for row in rows]
        except Exception as e:
            print(f"[RPGDB Error] 查詢全場次失敗: {e}")
            return []

    ##### 修改功能 #####

    async def create_session(
        self, name: str, forum_channel_id: int, main_thread_id: int, owner_id: int
    ) -> int:
        """### 建立 RPG 場次

        Returns:
            int: 新增場次的 id
        """
        await self._ensure_connection()
        created_at = datetime.now(timezone.utc).isoformat()
        async with self._lock:
            cursor = await self.db.execute("""
                INSERT INTO rpg_sessions (name, forum_channel_id, main_thread_id, owner_id, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (name, forum_channel_id, main_thread_id, owner_id, created_at))
            await self.db.commit()
            return cursor.lastrowid

    async def delete_session(self, session_id: int) -> None:
        """### 刪除 RPG 場次（僅移除資料庫記錄，頻道需另外處理）"""
        await self._ensure_connection()
        async with self._lock:
            await self.db.execute("DELETE FROM rpg_sessions WHERE id = ?", (session_id,))
            await self.db.commit()


DB_PATH = os.getenv("DB_PATH")

if DB_PATH is None:
    raise RuntimeError("❌ DB_PATH 環境變數未設定！請在 .env 檔案中設定 DB_PATH")

rpgSessionDB = RPGSessionDatabase(DB_PATH)
