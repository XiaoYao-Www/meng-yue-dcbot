import aiosqlite
import asyncio
import os
import datetime
from typing import Optional, List, TypedDict, cast
from config import TZ, INIT_SIGN_TIME, MAX_REPUTATION, DB_PATH

class UserBaseRow(TypedDict):
    user_id: int
    xp: int
    coins: int
    reputation: int
    last_sign_in: str
    streak_count: int
    max_streak: int
    total_sign_in: int

class UserDatabase:
    def __init__(self, db_path: str):
        """
        初始化資料庫路徑與並發鎖
        """
        self.db_path = os.path.join(db_path, "user_base.db")
        self.db: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()  # 加入寫入鎖，防止同時多工寫入導致 Locked 錯誤

    async def connect(self) -> None:
        """在應用啟動時呼叫一次，保持連線"""
        if self.db is not None:
            return  # 已連線，防止 RESUME 事件重複初始化
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.db = await aiosqlite.connect(self.db_path)
        self.db.row_factory = aiosqlite.Row
        
        # 開啟 WAL 模式與 NORMAL 同步，大幅降低磁碟 IO 等待，提升效能
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
        """初始化表格 (請確保在呼叫此方法前，已經執行過 await db.connect())"""
        if self.db is None:
            await self.connect()
            
        async with self._lock:  # 涉及寫入與表格建立，使用鎖保護
            await self.db.execute(f"""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    
                    xp INTEGER DEFAULT 0,
                    coins INTEGER DEFAULT 0,
                    reputation INTEGER DEFAULT 0,
                    
                    last_sign_in TEXT DEFAULT '{INIT_SIGN_TIME}',
                    streak_count INTEGER DEFAULT 0,
                    max_streak INTEGER DEFAULT 0,
                    total_sign_in INTEGER DEFAULT 0
                )
            """)
            await self.db.commit()

    ##### 內部核心工具 #####

    async def _ensure_user(self, user_id: int) -> None:
        """內部輔助：確保使用者存在。
        注意：此方法內部不加鎖，因為呼叫它的主方法已經上鎖過了 (防止 Deadlock)。
        """
        await self.db.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))

    ##### 查詢功能 #####

    async def get_user(self, user_id: int) -> UserBaseRow:
        """### 取得使用者資料"""
        await self._ensure_connection()
        # 因為 _ensure_user 會進行 INSERT (寫入)，所以這裡需要加鎖
        async with self._lock:
            await self._ensure_user(user_id)
            await self.db.commit()
        
        # 單純的讀取操作可以獨立執行，WAL 模式下不會被寫入阻塞
        async with self.db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            
        assert row is not None, f"無法取得 user_id 為 {user_id} 的資料，儘管已嘗試建立。"    
        return cast(UserBaseRow, dict(row))

    async def get_users(self, order_by: str = "user_id", limit: Optional[int] = None, descending: bool = True) -> List[UserBaseRow]:
        """### 取得過濾/排序後的使用者資料"""
        await self._ensure_connection()
        valid_columns = ["user_id", "xp", "coins", "reputation", "streak_count", "max_streak", "total_sign_in"]
        if order_by not in valid_columns:
            order_by = "user_id"

        order = "DESC" if descending else "ASC"
        query = f"SELECT * FROM users ORDER BY {order_by} {order}"
        
        if limit:
            query += f" LIMIT {limit}"

        try:
            # 單純讀取，不需使用 self._lock
            async with self.db.execute(query) as cursor:
                rows = await cursor.fetchall()
                
                # 關鍵修改：將每一筆 aiosqlite.Row 轉成 dict，並轉型為 UserBaseRow
                return [cast(UserBaseRow, dict(row)) for row in rows]
                
        except Exception as e:
            print(f"[DB Error] 查詢失敗: {e}")
            return []

    ##### 基本數值修改 #####

    async def update_user_stats(self, user_id: int, xp: int = 0, coins: int = 0, reputation: int = 0) -> None:
        """### 更新使用者資料 (變動數值)"""
        await self._ensure_connection()
        async with self._lock:  # 所有涉及變更 (UPDATE/INSERT) 的操作都必須鎖定
            await self._ensure_user(user_id)
            
            await self.db.execute("""
                UPDATE users 
                SET xp = xp + ?, 
                    coins = coins + ?, 
                    reputation = MAX(0, MIN(reputation + ?, ?)) 
                WHERE user_id = ?
            """, (xp, coins, reputation, MAX_REPUTATION, user_id))
            await self.db.commit()

    async def set_user_stats(self, user_id: int, xp: Optional[int] = None, coins: Optional[int] = None, reputation: Optional[int] = None) -> None:
        """### 設定使用者資料 (強制設定數值)"""
        await self._ensure_connection()
        updates = []
        params = []
        
        fields = {
            "xp": xp,
            "coins": coins,
            "reputation": reputation
        }
        
        for field, value in fields.items():
            if value is not None:
                if field == "reputation":
                    value = max(0, min(value, MAX_REPUTATION))
                
                updates.append(f"{field} = ?")
                params.append(value)
        
        if not updates:
            return

        sql = f"UPDATE users SET {', '.join(updates)} WHERE user_id = ?"
        params.append(user_id)
        
        async with self._lock:
            await self._ensure_user(user_id)
            await self.db.execute(sql, params)
            await self.db.commit()

    ##### 簽到功能 #####

    async def update_user_sign_in(self, user_id: int, new_streak: int) -> None:
        """### 簽到更新"""
        await self._ensure_connection()
        today = datetime.datetime.now(TZ).strftime('%Y-%m-%d')
        
        async with self._lock:
            await self._ensure_user(user_id)
            await self.db.execute("""
                UPDATE users 
                SET last_sign_in = ?, 
                    streak_count = ?, 
                    max_streak = MAX(max_streak, ?),
                    total_sign_in = total_sign_in + 1
                WHERE user_id = ?
            """, (today, new_streak, new_streak, user_id))
            await self.db.commit()

    ##### 每日遞減 #####

    async def apply_daily_decay(self, amount: int) -> int:
        """### 全員聲望衰減
        Returns: 受影響的使用者數量
        """
        await self._ensure_connection()
        async with self._lock:
            cursor = await self.db.execute("""
                UPDATE users 
                SET reputation = MAX(0, reputation - ?)
                WHERE reputation > 0
            """, (amount,))
            count = cursor.rowcount
            await self.db.commit()
            return count

    async def reset_expired_streaks(self) -> int:
        """### 將昨天沒簽到的人連續天數歸零
        Returns: 受影響的使用者數量
        """
        await self._ensure_connection()
        yesterday = (datetime.datetime.now(TZ) - datetime.timedelta(days=1)).strftime('%Y-%m-%d')
        today = datetime.datetime.now(TZ).strftime('%Y-%m-%d')
        
        async with self._lock:
            cursor = await self.db.execute("""
                UPDATE users 
                SET streak_count = 0 
                WHERE last_sign_in != ? AND last_sign_in != ? AND streak_count > 0
            """, (yesterday, today))
            count = cursor.rowcount
            await self.db.commit()
            return count
        

userBaseDB = UserDatabase(DB_PATH)