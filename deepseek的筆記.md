# Deepseek TRPG 開發筆記

> 最後更新：2025-06-14

## 當前架構理解

### 已實作
- `trpg_db.py`：8 張表（games, characters, items, npcs, inventories, dialogues, narrative_log, action_logs）
- `trpg_world_gen.py`：AI 世界生成引擎，含 fallback
- `trpg_listener.py`：`加入` 指令 + 創角流程
- `trpg_action.py`：D20 擲骰 + AI GM 迴圈
- `rpg_start.py`：建立論壇頻道 + 5 標籤 + 呼叫世界生成

### 待擴充
- [x] `locations` 表（追蹤玩家位置）
- [x] `final_goal` 欄位（遊戲最終目標，AI 生成後不可變更）
- [x] 多輪創角（種族 → 職業 → 屬性分配）
- [x] `creation_step` 追蹤創角進度
- [x] `validity_rules` 合理性準則

## 設計決策

### 硬編碼 5 大設定（生成後不可變更）
1. **`world_setting`**：世界觀、背景、寫作風格 → `games.world_setting`
2. **`world_rules`**：規則參數、合理性準則 → `games.world_rules` JSON 內 `validity_rules`
3. **`final_goal`**：遊戲最終目標 → `games.final_goal`（新增欄位，AI 生成或手動輸入）
4. **角色定義**：種族/職業/技能/屬性 → `world_rules` 內 `races`/`classes`/`skills`
5. **生物/道具模板**：AI 初始生成池 → `npcs`/`items` 表

### 多輪創角設計
- `characters.creation_step` 追蹤進度：0=等待開始, 1=選擇種族, 2=選擇職業, 3=確認屬性, 4=完成
- step=0：AI 生成引導 → 玩家輸入種族選擇 → step=1
- step=1：AI 根據種族推薦職業 → 玩家選擇 → step=2
- step=2：AI 生成完整數值 → 玩家確認或重新選擇 → step=3/4
- Fallback：從 `world_rules` 提取選項列表顯示給玩家

### Fallback 設計（三層）
1. **世界生成 fallback**：預設奇幻模板（含 `final_goal` + `validity_rules`）
2. **創角 fallback**：從 `world_rules` 提取 races/classes 顯示選項，hash-based 隨機數值
3. **GM fallback**：預設敘事模板（保留）

### 資料庫變更記錄
- `games` 表新增 `final_goal TEXT`
- `characters` 表新增 `creation_step INTEGER DEFAULT 0`、`current_location_id INTEGER DEFAULT 0`
- 新增 `locations` 表（含 `game_id`, `name`, `description`, `properties`, `is_known`, `discord_thread_id`, `created_at`）

## 已知限制
- Discord embed 欄位 1024 字元上限
- Deepseek API rate limit
- 論壇頻道最多 5 個標籤
- SQLite `CREATE TABLE IF NOT EXISTS` 不會自動新增欄位，需重建 `trpg.db`
