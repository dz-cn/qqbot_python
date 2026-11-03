from db.db import connect
import sqlite3

def query(sql: str, params: tuple = ()) -> list[dict]:
    conn = connect()
    try:
        cur = conn.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()

def write(sql: str, params: tuple = ()) -> tuple[int, int]:
    conn = connect()
    try:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid or 0, cur.rowcount
    except sqlite3.IntegrityError:
        conn.rollback()
        return 0, 0
    finally:
        conn.close()

def tx(statements: list[tuple[str, tuple]]) -> None:
    """多条语句同一事务：要么全成要么全不成，避免「有整段缺子块」的半套数据。"""
    conn = connect()
    try:
        for sql, params in statements:
            conn.execute(sql, params)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()