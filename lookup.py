from pathlib import Path
import sqlite3
import random


# ============================================================
# 設定
# ============================================================

DB_PATH = Path(r"C:\Users\lll09\files\Projects\python\en-teacher-gen\assets\en_word_release_v0.db")


# ============================================================
# 顯示單一詞義
# ============================================================

def show_word(conn: sqlite3.Connection, word: str) -> None:
    rows = conn.execute(
        """
        SELECT
            w.word,
            w.translate,
            p.value AS pos,
            d.en,
            d.zh
        FROM word w
        JOIN semantics_id s
            ON s.id = w.semantics_id
        LEFT JOIN part_of_speech p
            ON p.semantics_id = s.id
        LEFT JOIN definition d
            ON d.semantics_id = s.id
        WHERE w.word = ?
        ORDER BY s.id, d.id
        """,
        (word,),
    ).fetchall()

    if not rows:
        print(f"\n找不到：{word}")
        return

    print()
    print("═" * 72)
    print(f"  {word}")
    print("═" * 72)

    current_semantics = None
    definition_index = 0

    for row in rows:
        word_value, translate, pos, definition_en, definition_zh = row

        semantics_key = (word_value, translate, pos)

        if current_semantics != semantics_key:
            if current_semantics is not None:
                print("--------------------")

            current_semantics = semantics_key
            definition_index = 0

            print()
            print(f"單字     : {word_value}")
            print(f"翻譯     : {translate}")
            print(f"詞性     : {pos}")

        if definition_en is not None:
            definition_index += 1

            print()
            print(f"定義 {definition_index}")
            print(f"  EN : {definition_en}")
            print(f"  ZH : {definition_zh}")

    if current_semantics is not None:
        print("--------------------")


# ============================================================
# 精確查詢
# ============================================================

def search_exact(conn: sqlite3.Connection, word: str) -> None:
    show_word(conn, word)


# ============================================================
# 模糊查詢
# ============================================================

def search_partial(conn: sqlite3.Connection, keyword: str) -> None:
    rows = conn.execute(
        """
        SELECT DISTINCT
            w.word,
            w.translate,
            p.value
        FROM word w
        JOIN semantics_id s
            ON s.id = w.semantics_id
        LEFT JOIN part_of_speech p
            ON p.semantics_id = s.id
        WHERE w.word LIKE ?
        ORDER BY w.word
        """,
        (f"%{keyword}%",),
    ).fetchall()

    if not rows:
        print(f"\n找不到包含「{keyword}」的詞")
        return

    print()
    print(f"找到 {len(rows)} 筆：")
    print()

    for word, translate, pos in rows:
        print(f"{word:<35} {translate:<20} {pos}")


# ============================================================
# 隨機抽詞
# ============================================================

def random_word(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        """
        SELECT word
        FROM word
        ORDER BY RANDOM()
        LIMIT 1
        """
    ).fetchone()

    if row is None:
        print("\n詞庫沒有任何單字。")
        return

    word = row[0]

    print(f"\n🎲 隨機抽到：{word}")

    show_word(conn, word)


# ============================================================
# 主程式
# ============================================================

def main() -> None:
    if not DB_PATH.exists():
        print(f"找不到資料庫：{DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)

    print(f"資料庫：{DB_PATH}")

    try:
        while True:
            try:
                query = input("\n查詢：").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break

            if not query:
                continue

            # 離開
            if query.lower() in {"q", "quit", "exit"}:
                break

            # 隨機抽詞
            if query.lower() in {"-random", "--random", "random"}:
                random_word(conn)
                continue

            # 以 ? 開頭 → 模糊搜尋
            if query.startswith("?"):
                keyword = query[1:].strip()

                if keyword:
                    search_partial(conn, keyword)
                else:
                    print("\n請輸入要搜尋的關鍵字，例如：?tion")

                continue

            # 一般輸入 → 精確查詢
            search_exact(conn, query)

    finally:
        conn.close()


if __name__ == "__main__":
    main()
