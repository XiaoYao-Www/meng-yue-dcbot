import aiosqlite
import asyncio
import os
from typing import Optional, List, TypedDict, cast


class RoleConfigRow(TypedDict):
    role_id: int
    required_reputation: int
    required_sign_in_days: int


class RoleConfigDatabase:
    def __init__(self, db_path: str):
        """
        初始化身分組設定資料庫路徑與並發鎖
        """
        self.db_path = os.path.join(db_path, "role_config.db")
        self.db: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        """在應用啟動時呼叫一次，保持連線"""
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

    async def setup(self) -> None:
        """初始化表格"""
        if self.db is None:
            await self.connect()

        async with self._lock:
            await self.db.execute("""
                CREATE TABLE IF NOT EXISTS role_configs (
                    role_id               INTEGER PRIMARY KEY,
                    required_reputation   INTEGER DEFAULT 0,
                    required_sign_in_days INTEGER DEFAULT 0
                )
            """)
            await self.db.commit()

    ##### 查詢功能 #####

    async def get_all_configs(self) -> List[RoleConfigRow]:
        """### 取得所有身分組設定"""
        if self.db is None:
            await self.connect()
        try:
            async with self.db.execute("SELECT * FROM role_configs") as cursor:
                rows = await cursor.fetchall()
                return [cast(RoleConfigRow, dict(row)) for row in rows]
        except Exception as e:
            print(f"[RoleDB Error] 查詢全設定失敗: {e}")
            return []

    ##### 修改功能 #####

    async def add_config(self, role_id: int, required_reputation: int, required_sign_in_days: int) -> None:
        """### 新增或更新身分組門檻"""
        async with self._lock:
            await self.db.execute("""
                INSERT INTO role_configs (role_id, required_reputation, required_sign_in_days)
                VALUES (?, ?, ?)
                ON CONFLICT(role_id) DO UPDATE SET
                    required_reputation   = excluded.required_reputation,
                    required_sign_in_days = excluded.required_sign_in_days
            """, (role_id, required_reputation, required_sign_in_days))
            await self.db.commit()

    async def remove_config(self, role_id: int) -> None:
        """### 移除身分組設定"""
        async with self._lock:
            await self.db.execute("DELETE FROM role_configs WHERE role_id = ?", (role_id,))
            await self.db.commit()


DB_PATH = os.getenv("DB_PATH")

if DB_PATH is None:
    raise RuntimeError("❌ DB_PATH 環境變數未設定！請在 .env 檔案中設定 DB_PATH")

roleConfigDB = RoleConfigDatabase(DB_PATH)
