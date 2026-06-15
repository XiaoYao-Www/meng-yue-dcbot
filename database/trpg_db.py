import aiosqlite
import asyncio
import os
from datetime import datetime, timezone
from typing import Optional, List, TypedDict, cast


class GameRow(TypedDict):
    id: int
    name: str
    story_outline: str
    goal_type: str
    max_players: int
    current_stage: str          # 創角 / 進行中 / 結束
    world_setting: str          # AI 生成的世界觀描述（用於每次 prompt）
    world_rules: str            # AI 生成的規則集 JSON（種族/職業/技能/物品模板）
    world_state_summary: str    # 記憶壓縮摘要
    narrative_context: str      # 劇情上下文（用於 prompt 節省 token）
    final_goal: str              # 遊戲最終目標（AI 生成後不可變更）
    end_conditions: str          # JSON 陣列：遊戲結束條件
    forum_channel_id: int
    main_thread_id: int
    owner_id: int
    created_at: str


class CharacterRow(TypedDict):
    id: int
    game_id: int
    discord_user_id: Optional[int]  # None = NPC
    name: str
    character_type: str             # player / npc
    stats: str                      # JSON: 所有數值動態定義
    status: str                     # 存活 / 死亡 / 昏迷 / 離開
    thread_id: int                  # Discord 子貼文 ID (0 = 無)
    creation_step: int              # 0=等待, 1=選種族, 2=選職業, 3=確認屬性, 4=完成
    current_location_id: int        # FK → locations.id, 0=無
    created_at: str


class ItemRow(TypedDict):
    id: int
    game_id: int
    name: str
    description: str
    properties: str                 # JSON 動態屬性
    owner_id: Optional[int]         # FK → characters.id
    is_known: bool
    discord_thread_id: int
    created_at: str


class NPCRow(TypedDict):
    id: int
    game_id: int
    name: str
    description: str
    stats: str                      # JSON
    is_hostile: bool
    is_known: bool
    discord_thread_id: int
    created_at: str


class LocationRow(TypedDict):
    id: int
    game_id: int
    name: str
    description: str
    properties: str                 # JSON
    is_known: bool
    discord_thread_id: int
    created_at: str


class InventoryRow(TypedDict):
    id: int
    character_id: int
    item_id: int
    quantity: int


class DialogueRow(TypedDict):
    id: int
    game_id: int
    character_id: int               # FK → characters.id (發話者)
    content: str                    # 完整對話/敘事
    created_at: str


class NarrativeLogRow(TypedDict):
    id: int
    game_id: int
    character_id: int
    action: str                     # 玩家行動描述
    narrative: str                  # AI 生成的劇情
    stat_changes: str               # JSON 狀態變更
    result: str                     # 判定結果
    created_at: str


class ActionLogRow(TypedDict):
    id: int
    game_id: int
    timestamp: str
    actor_id: int
    action_type: str
    content: str
    result_state: str


class TRPGDatabase:
    def __init__(self, db_path: str):
        self.db_path = os.path.join(db_path, "trpg.db")
        self.db: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.db = await aiosqlite.connect(self.db_path)
        self.db.row_factory = aiosqlite.Row
        await self.db.execute("PRAGMA journal_mode=WAL")
        await self.db.execute("PRAGMA synchronous=NORMAL")
        await self.db.execute("PRAGMA foreign_keys=ON")

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
            await self.db.executescript("""
                CREATE TABLE IF NOT EXISTS games (
                    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                    name                TEXT NOT NULL,
                    story_outline       TEXT NOT NULL DEFAULT '',
                    goal_type           TEXT NOT NULL DEFAULT '明確目標',
                    max_players         INTEGER NOT NULL DEFAULT 4,
                    current_stage       TEXT NOT NULL DEFAULT '創角',
                    world_setting       TEXT NOT NULL DEFAULT '',
                    world_rules         TEXT NOT NULL DEFAULT '{}',
                    world_state_summary TEXT NOT NULL DEFAULT '',
                    narrative_context   TEXT NOT NULL DEFAULT '',
                    final_goal           TEXT NOT NULL DEFAULT '',
                    end_conditions        TEXT NOT NULL DEFAULT '[]',
                    forum_channel_id    INTEGER NOT NULL,
                    main_thread_id      INTEGER NOT NULL,
                    owner_id            INTEGER NOT NULL,
                    created_at          TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS characters (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id         INTEGER NOT NULL,
                    discord_user_id INTEGER,
                    name            TEXT NOT NULL DEFAULT '',
                    character_type  TEXT NOT NULL DEFAULT 'player',
                    stats           TEXT NOT NULL DEFAULT '{}',
                    status          TEXT NOT NULL DEFAULT '存活',
                    thread_id       INTEGER NOT NULL DEFAULT 0,
                    creation_step       INTEGER NOT NULL DEFAULT 0,
                    current_location_id INTEGER NOT NULL DEFAULT 0,
                    created_at      TEXT NOT NULL,
                    FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS items (
                    id                INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id           INTEGER NOT NULL,
                    name              TEXT NOT NULL,
                    description       TEXT NOT NULL DEFAULT '',
                    properties        TEXT NOT NULL DEFAULT '{}',
                    owner_id          INTEGER,
                    is_known          INTEGER NOT NULL DEFAULT 0,
                    discord_thread_id INTEGER NOT NULL DEFAULT 0,
                    created_at        TEXT NOT NULL,
                    FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE,
                    FOREIGN KEY (owner_id) REFERENCES characters(id) ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS npcs (
                    id                INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id           INTEGER NOT NULL,
                    name              TEXT NOT NULL,
                    description       TEXT NOT NULL DEFAULT '',
                    stats             TEXT NOT NULL DEFAULT '{}',
                    is_hostile        INTEGER NOT NULL DEFAULT 0,
                    is_known          INTEGER NOT NULL DEFAULT 0,
                    discord_thread_id INTEGER NOT NULL DEFAULT 0,
                    created_at        TEXT NOT NULL,
                    FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS inventories (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    character_id  INTEGER NOT NULL,
                    item_id       INTEGER NOT NULL,
                    quantity      INTEGER NOT NULL DEFAULT 1,
                    FOREIGN KEY (character_id) REFERENCES characters(id) ON DELETE CASCADE,
                    FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE,
                    UNIQUE(character_id, item_id)
                );

                CREATE TABLE IF NOT EXISTS dialogues (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id       INTEGER NOT NULL,
                    character_id  INTEGER NOT NULL,
                    content       TEXT NOT NULL,
                    created_at    TEXT NOT NULL,
                    FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE,
                    FOREIGN KEY (character_id) REFERENCES characters(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS narrative_log (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id       INTEGER NOT NULL,
                    character_id  INTEGER NOT NULL,
                    action        TEXT NOT NULL DEFAULT '',
                    narrative     TEXT NOT NULL DEFAULT '',
                    stat_changes  TEXT NOT NULL DEFAULT '{}',
                    result        TEXT NOT NULL DEFAULT '',
                    created_at    TEXT NOT NULL,
                    FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE,
                    FOREIGN KEY (character_id) REFERENCES characters(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS action_logs (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id     INTEGER NOT NULL,
                    timestamp   TEXT NOT NULL,
                    actor_id    INTEGER NOT NULL,
                    action_type TEXT NOT NULL,
                    content     TEXT NOT NULL DEFAULT '',
                    result_state TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS locations (
                    id                INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id           INTEGER NOT NULL,
                    name              TEXT NOT NULL,
                    description       TEXT NOT NULL DEFAULT '',
                    properties        TEXT NOT NULL DEFAULT '{}',
                    is_known          INTEGER NOT NULL DEFAULT 0,
                    discord_thread_id INTEGER NOT NULL DEFAULT 0,
                    created_at        TEXT NOT NULL,
                    FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE
                );

            """)
            await self.db.commit()

        # migration: 舊 games 表可能缺少新欄位
        for col, col_def in [
            ("final_goal", "TEXT NOT NULL DEFAULT ''"),
            ("end_conditions", "TEXT NOT NULL DEFAULT '[]'"),
        ]:
            try:
                async with self._lock:
                    await self.db.execute(f"ALTER TABLE games ADD COLUMN {col} {col_def}")
                    await self.db.commit()
            except Exception:
                pass  # 欄位已存在

        # migration: 舊 characters 表可能缺少新欄位
        for col, col_def in [
            ("creation_step", "INTEGER NOT NULL DEFAULT 0"),
            ("current_location_id", "INTEGER NOT NULL DEFAULT 0"),
        ]:
            try:
                async with self._lock:
                    await self.db.execute(f"ALTER TABLE characters ADD COLUMN {col} {col_def}")
                    await self.db.commit()
            except Exception:
                pass  # 欄位已存在

    ########################################################################
    # Game CRUD
    ########################################################################

    async def create_game(
        self, name: str, story_outline: str, goal_type: str,
        max_players: int, forum_channel_id: int, main_thread_id: int, owner_id: int,
        final_goal: str = ""
    ) -> int:
        await self._ensure_connection()
        created_at = datetime.now(timezone.utc).isoformat()
        async with self._lock:
            cursor = await self.db.execute("""
                INSERT INTO games (name, story_outline, goal_type, max_players,
                                   forum_channel_id, main_thread_id, owner_id, created_at, final_goal)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (name, story_outline, goal_type, max_players,
                  forum_channel_id, main_thread_id, owner_id, created_at, final_goal))
            await self.db.commit()
            return cursor.lastrowid

    async def get_game(self, game_id: int) -> Optional[GameRow]:
        await self._ensure_connection()
        try:
            async with self.db.execute("SELECT * FROM games WHERE id = ?", (game_id,)) as cursor:
                row = await cursor.fetchone()
                return cast(GameRow, dict(row)) if row else None
        except Exception as e:
            print(f"[TRPGDB] get_game error: {e}")
            return None

    async def get_all_games(self) -> List[GameRow]:
        await self._ensure_connection()
        try:
            async with self.db.execute("SELECT * FROM games ORDER BY id DESC") as cursor:
                rows = await cursor.fetchall()
                return [cast(GameRow, dict(row)) for row in rows]
        except Exception as e:
            print(f"[TRPGDB] get_all_games error: {e}")
            return []

    async def get_game_by_forum(self, forum_channel_id: int) -> Optional[GameRow]:
        await self._ensure_connection()
        try:
            async with self.db.execute(
                "SELECT * FROM games WHERE forum_channel_id = ?", (forum_channel_id,)
            ) as cursor:
                row = await cursor.fetchone()
                return cast(GameRow, dict(row)) if row else None
        except Exception as e:
            print(f"[TRPGDB] get_game_by_forum error: {e}")
            return None

    async def update_game_stage(self, game_id: int, stage: str) -> None:
        await self._ensure_connection()
        async with self._lock:
            await self.db.execute("UPDATE games SET current_stage = ? WHERE id = ?", (stage, game_id))
            await self.db.commit()

    async def update_game_world(self, game_id: int, world_setting: str, world_rules: str, final_goal: str = "", end_conditions: str = "[]") -> None:
        """### 更新 AI 生成的世界觀與規則"""
        await self._ensure_connection()
        async with self._lock:
            await self.db.execute(
                "UPDATE games SET world_setting = ?, world_rules = ?, final_goal = ?, end_conditions = ? WHERE id = ?",
                (world_setting, world_rules, final_goal, end_conditions, game_id)
            )
            await self.db.commit()

    async def update_game_summary(self, game_id: int, summary: str) -> None:
        await self._ensure_connection()
        async with self._lock:
            await self.db.execute(
                "UPDATE games SET world_state_summary = ? WHERE id = ?", (summary, game_id)
            )
            await self.db.commit()

    async def update_narrative_context(self, game_id: int, context: str) -> None:
        """### 更新劇情上下文（記憶壓縮用）"""
        await self._ensure_connection()
        async with self._lock:
            await self.db.execute(
                "UPDATE games SET narrative_context = ? WHERE id = ?", (context, game_id)
            )
            await self.db.commit()

    ########################################################################
    # Character CRUD
    ########################################################################

    async def create_character(
        self, game_id: int, discord_user_id: Optional[int] = None,
        name: str = "", character_type: str = "player",
        thread_id: int = 0
    ) -> int:
        await self._ensure_connection()
        created_at = datetime.now(timezone.utc).isoformat()
        async with self._lock:
            cursor = await self.db.execute("""
                INSERT INTO characters (game_id, discord_user_id, name, character_type, thread_id, creation_step, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (game_id, discord_user_id, name, character_type, thread_id, 0, created_at))
            await self.db.commit()
            return cursor.lastrowid

    async def get_character(self, character_id: int) -> Optional[CharacterRow]:
        await self._ensure_connection()
        try:
            async with self.db.execute(
                "SELECT * FROM characters WHERE id = ?", (character_id,)
            ) as cursor:
                row = await cursor.fetchone()
                return cast(CharacterRow, dict(row)) if row else None
        except Exception as e:
            print(f"[TRPGDB] get_character error: {e}")
            return None

    async def get_character_by_discord(self, game_id: int, discord_user_id: int) -> Optional[CharacterRow]:
        """### 查詢玩家在某遊戲局中的角色"""
        await self._ensure_connection()
        try:
            async with self.db.execute(
                "SELECT * FROM characters WHERE game_id = ? AND discord_user_id = ?",
                (game_id, discord_user_id)
            ) as cursor:
                row = await cursor.fetchone()
                return cast(CharacterRow, dict(row)) if row else None
        except Exception as e:
            print(f"[TRPGDB] get_character_by_discord error: {e}")
            return None

    async def get_characters_by_game(self, game_id: int) -> List[CharacterRow]:
        await self._ensure_connection()
        try:
            async with self.db.execute(
                "SELECT * FROM characters WHERE game_id = ? ORDER BY id", (game_id,)
            ) as cursor:
                rows = await cursor.fetchall()
                return [cast(CharacterRow, dict(row)) for row in rows]
        except Exception as e:
            print(f"[TRPGDB] get_characters_by_game error: {e}")
            return []

    async def update_character_stats(self, character_id: int, stats: str) -> None:
        """### 更新角色數值（完整覆蓋 stats JSON）"""
        await self._ensure_connection()
        async with self._lock:
            await self.db.execute(
                "UPDATE characters SET stats = ? WHERE id = ?", (stats, character_id)
            )
            await self.db.commit()

    async def update_character(
        self, character_id: int, **kwargs
    ) -> None:
        """### 更新角色欄位（name, stats, status, thread_id）"""
        allowed = {"name", "stats", "status", "thread_id", "creation_step", "current_location_id"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [character_id]
        async with self._lock:
            await self.db.execute(f"UPDATE characters SET {set_clause} WHERE id = ?", values)
            await self.db.commit()

    async def update_character_creation_step(self, character_id: int, step: int) -> None:
        """### 更新創角進度"""
        await self._ensure_connection()
        async with self._lock:
            await self.db.execute(
                "UPDATE characters SET creation_step = ? WHERE id = ?", (step, character_id)
            )
            await self.db.commit()

    async def get_character_by_thread(self, thread_id: int, game_id: int) -> Optional[CharacterRow]:
        """### 透過 Discord 貼文 ID 查詢角色"""
        await self._ensure_connection()
        try:
            async with self.db.execute(
                "SELECT * FROM characters WHERE thread_id = ? AND game_id = ?",
                (thread_id, game_id)
            ) as cursor:
                row = await cursor.fetchone()
                return cast(CharacterRow, dict(row)) if row else None
        except Exception as e:
            print(f"[TRPGDB] get_character_by_thread error: {e}")
            return None

    async def get_player_count(self, game_id: int) -> int:
        """### 取得遊戲局內玩家角色數量"""
        await self._ensure_connection()
        try:
            async with self.db.execute(
                "SELECT COUNT(*) as cnt FROM characters WHERE game_id = ? AND character_type = 'player'",
                (game_id,)
            ) as cursor:
                row = await cursor.fetchone()
                return row["cnt"] if row else 0
        except Exception as e:
            print(f"[TRPGDB] get_player_count error: {e}")
            return 0

    ########################################################################
    # Location CRUD
    ########################################################################

    async def create_location(
        self, game_id: int, name: str, description: str = "",
        properties: str = "{}", is_known: bool = False, discord_thread_id: int = 0
    ) -> int:
        await self._ensure_connection()
        created_at = datetime.now(timezone.utc).isoformat()
        async with self._lock:
            cursor = await self.db.execute("""
                INSERT INTO locations (game_id, name, description, properties, is_known, discord_thread_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (game_id, name, description, properties, int(is_known), discord_thread_id, created_at))
            await self.db.commit()
            return cursor.lastrowid

    async def get_locations_by_game(self, game_id: int) -> List[LocationRow]:
        await self._ensure_connection()
        try:
            async with self.db.execute(
                "SELECT * FROM locations WHERE game_id = ? ORDER BY id", (game_id,)
            ) as cursor:
                rows = await cursor.fetchall()
                return [cast(LocationRow, dict(row)) for row in rows]
        except Exception as e:
            print(f"[TRPGDB] get_locations_by_game error: {e}")
            return []

    async def update_location_thread(self, location_id: int, thread_id: int) -> None:
        await self._ensure_connection()
        async with self._lock:
            await self.db.execute(
                "UPDATE locations SET discord_thread_id = ? WHERE id = ?", (thread_id, location_id)
            )
            await self.db.commit()

    async def set_character_location(self, character_id: int, location_id: int) -> None:
        await self._ensure_connection()
        async with self._lock:
            await self.db.execute(
                "UPDATE characters SET current_location_id = ? WHERE id = ?",
                (location_id, character_id)
            )
            await self.db.commit()

    ########################################################################
    # Item CRUD
    ########################################################################

    async def create_item(
        self, game_id: int, name: str, description: str = "",
        properties: str = "{}", owner_id: Optional[int] = None,
        is_known: bool = True, discord_thread_id: int = 0
    ) -> int:
        await self._ensure_connection()
        created_at = datetime.now(timezone.utc).isoformat()
        async with self._lock:
            cursor = await self.db.execute("""
                INSERT INTO items (game_id, name, description, properties, owner_id, is_known, discord_thread_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (game_id, name, description, properties, owner_id, int(is_known), discord_thread_id, created_at))
            await self.db.commit()
            return cursor.lastrowid

    async def get_items_by_game(self, game_id: int) -> List[ItemRow]:
        await self._ensure_connection()
        try:
            async with self.db.execute(
                "SELECT * FROM items WHERE game_id = ? ORDER BY id", (game_id,)
            ) as cursor:
                rows = await cursor.fetchall()
                return [cast(ItemRow, dict(row)) for row in rows]
        except Exception as e:
            print(f"[TRPGDB] get_items_by_game error: {e}")
            return []

    async def get_items_by_owner(self, character_id: int) -> List[ItemRow]:
        """### 取得角色持有的道具（透過 inventories 聯表）"""
        await self._ensure_connection()
        try:
            async with self.db.execute("""
                SELECT i.* FROM items i
                JOIN inventories inv ON i.id = inv.item_id
                WHERE inv.character_id = ?
                ORDER BY i.id
            """, (character_id,)) as cursor:
                rows = await cursor.fetchall()
                return [cast(ItemRow, dict(row)) for row in rows]
        except Exception as e:
            print(f"[TRPGDB] get_items_by_owner error: {e}")
            return []

    async def get_item_by_name(self, game_id: int, name: str) -> Optional[ItemRow]:
        """### 依名稱查詢道具（去重用）"""
        await self._ensure_connection()
        try:
            async with self.db.execute(
                "SELECT * FROM items WHERE game_id = ? AND name = ?", (game_id, name)
            ) as cursor:
                row = await cursor.fetchone()
                return cast(ItemRow, dict(row)) if row else None
        except Exception as e:
            print(f"[TRPGDB] get_item_by_name error: {e}")
            return None

    async def update_item(self, item_id: int, description: str, properties: str) -> None:
        """### 更新道具描述與屬性"""
        await self._ensure_connection()
        async with self._lock:
            await self.db.execute(
                "UPDATE items SET description = ?, properties = ? WHERE id = ?",
                (description, properties, item_id)
            )
            await self.db.commit()

    async def update_item_thread(self, item_id: int, thread_id: int) -> None:
        await self._ensure_connection()
        async with self._lock:
            await self.db.execute(
                "UPDATE items SET discord_thread_id = ? WHERE id = ?", (thread_id, item_id)
            )
            await self.db.commit()

    ########################################################################
    # NPC CRUD
    ########################################################################

    async def create_npc(
        self, game_id: int, name: str, description: str = "",
        stats: str = "{}", is_hostile: bool = False,
        is_known: bool = False, discord_thread_id: int = 0
    ) -> int:
        await self._ensure_connection()
        created_at = datetime.now(timezone.utc).isoformat()
        async with self._lock:
            cursor = await self.db.execute("""
                INSERT INTO npcs (game_id, name, description, stats, is_hostile, is_known, discord_thread_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (game_id, name, description, stats, int(is_hostile), int(is_known), discord_thread_id, created_at))
            await self.db.commit()
            return cursor.lastrowid

    async def get_npcs_by_game(self, game_id: int) -> List[NPCRow]:
        await self._ensure_connection()
        try:
            async with self.db.execute(
                "SELECT * FROM npcs WHERE game_id = ? ORDER BY id", (game_id,)
            ) as cursor:
                rows = await cursor.fetchall()
                return [cast(NPCRow, dict(row)) for row in rows]
        except Exception as e:
            print(f"[TRPGDB] get_npcs_by_game error: {e}")
            return []

    async def update_npc_thread(self, npc_id: int, thread_id: int) -> None:
        await self._ensure_connection()
        async with self._lock:
            await self.db.execute(
                "UPDATE npcs SET discord_thread_id = ? WHERE id = ?", (thread_id, npc_id)
            )
            await self.db.commit()

    async def get_npc_by_name(self, game_id: int, name: str) -> Optional[NPCRow]:
        """### 依名稱查詢 NPC/怪物（去重用）"""
        await self._ensure_connection()
        try:
            async with self.db.execute(
                "SELECT * FROM npcs WHERE game_id = ? AND name = ?", (game_id, name)
            ) as cursor:
                row = await cursor.fetchone()
                return cast(NPCRow, dict(row)) if row else None
        except Exception as e:
            print(f"[TRPGDB] get_npc_by_name error: {e}")
            return None

    async def update_npc(self, npc_id: int, description: str, stats: str) -> None:
        """### 更新 NPC 描述與數值"""
        await self._ensure_connection()
        async with self._lock:
            await self.db.execute(
                "UPDATE npcs SET description = ?, stats = ? WHERE id = ?",
                (description, stats, npc_id)
            )
            await self.db.commit()

    ########################################################################
    # Inventory CRUD
    ########################################################################

    async def add_item_to_inventory(self, character_id: int, item_id: int, quantity: int = 1) -> None:
        """### 角色獲得道具（有則增加數量，無則新增）"""
        await self._ensure_connection()
        async with self._lock:
            await self.db.execute("""
                INSERT INTO inventories (character_id, item_id, quantity)
                VALUES (?, ?, ?)
                ON CONFLICT(character_id, item_id) DO UPDATE SET quantity = quantity + excluded.quantity
            """, (character_id, item_id, quantity))
            await self.db.commit()

    async def remove_item_from_inventory(self, character_id: int, item_id: int, quantity: int = 1) -> None:
        """### 角色消耗道具（減少數量，歸零則刪除）"""
        await self._ensure_connection()
        async with self._lock:
            cursor = await self.db.execute(
                "SELECT quantity FROM inventories WHERE character_id = ? AND item_id = ?",
                (character_id, item_id)
            )
            row = await cursor.fetchone()
            if row is None:
                return
            new_qty = row["quantity"] - quantity
            if new_qty <= 0:
                await self.db.execute(
                    "DELETE FROM inventories WHERE character_id = ? AND item_id = ?",
                    (character_id, item_id)
                )
            else:
                await self.db.execute(
                    "UPDATE inventories SET quantity = ? WHERE character_id = ? AND item_id = ?",
                    (new_qty, character_id, item_id)
                )
            await self.db.commit()

    ########################################################################
    # Dialogue CRUD
    ########################################################################

    async def add_dialogue(self, game_id: int, character_id: int, content: str) -> int:
        """### 新增對話/敘事紀錄"""
        await self._ensure_connection()
        created_at = datetime.now(timezone.utc).isoformat()
        async with self._lock:
            cursor = await self.db.execute("""
                INSERT INTO dialogues (game_id, character_id, content, created_at)
                VALUES (?, ?, ?, ?)
            """, (game_id, character_id, content, created_at))
            await self.db.commit()
            return cursor.lastrowid

    async def get_recent_dialogues(self, game_id: int, limit: int = 20) -> List[DialogueRow]:
        """### 取得最近的對話歷史（最新在前）"""
        await self._ensure_connection()
        try:
            async with self.db.execute(
                "SELECT * FROM dialogues WHERE game_id = ? ORDER BY id DESC LIMIT ?",
                (game_id, limit)
            ) as cursor:
                rows = await cursor.fetchall()
                return [cast(DialogueRow, dict(row)) for row in rows]
        except Exception as e:
            print(f"[TRPGDB] get_recent_dialogues error: {e}")
            return []

    async def get_dialogue_count(self, game_id: int) -> int:
        """### 取得遊戲局的對話總數"""
        await self._ensure_connection()
        try:
            async with self.db.execute(
                "SELECT COUNT(*) as cnt FROM dialogues WHERE game_id = ?", (game_id,)
            ) as cursor:
                row = await cursor.fetchone()
                return row["cnt"] if row else 0
        except Exception as e:
            print(f"[TRPGDB] get_dialogue_count error: {e}")
            return 0

    ########################################################################
    # Narrative Log CRUD
    ########################################################################

    async def add_narrative_log(
        self, game_id: int, character_id: int, action: str,
        narrative: str, stat_changes: str = "{}", result: str = ""
    ) -> int:
        """### 新增劇情紀錄"""
        await self._ensure_connection()
        created_at = datetime.now(timezone.utc).isoformat()
        async with self._lock:
            cursor = await self.db.execute("""
                INSERT INTO narrative_log (game_id, character_id, action, narrative, stat_changes, result, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (game_id, character_id, action, narrative, stat_changes, result, created_at))
            await self.db.commit()
            return cursor.lastrowid

    async def get_recent_narratives(self, game_id: int, limit: int = 10) -> List[NarrativeLogRow]:
        """### 取得最近的劇情紀錄（最新在前）"""
        await self._ensure_connection()
        try:
            async with self.db.execute(
                "SELECT * FROM narrative_log WHERE game_id = ? ORDER BY id DESC LIMIT ?",
                (game_id, limit)
            ) as cursor:
                rows = await cursor.fetchall()
                return [cast(NarrativeLogRow, dict(row)) for row in rows]
        except Exception as e:
            print(f"[TRPGDB] get_recent_narratives error: {e}")
            return []

    ########################################################################
    # ActionLog CRUD
    ########################################################################

    async def add_action_log(
        self, game_id: int, actor_id: int, action_type: str,
        content: str = "", result_state: str = ""
    ) -> None:
        await self._ensure_connection()
        timestamp = datetime.now(timezone.utc).isoformat()
        async with self._lock:
            await self.db.execute("""
                INSERT INTO action_logs (game_id, timestamp, actor_id, action_type, content, result_state)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (game_id, timestamp, actor_id, action_type, content, result_state))
            await self.db.commit()

    async def get_action_logs(self, game_id: int, limit: int = 50) -> List[ActionLogRow]:
        await self._ensure_connection()
        try:
            async with self.db.execute(
                "SELECT * FROM action_logs WHERE game_id = ? ORDER BY id DESC LIMIT ?",
                (game_id, limit)
            ) as cursor:
                rows = await cursor.fetchall()
                return [cast(ActionLogRow, dict(row)) for row in rows]
        except Exception as e:
            print(f"[TRPGDB] get_action_logs error: {e}")
            return []


DB_PATH = os.getenv("DB_PATH")
if DB_PATH is None:
    raise RuntimeError("❌ DB_PATH 環境變數未設定！請在 .env 檔案中設定 DB_PATH")

trpgDB = TRPGDatabase(DB_PATH)
