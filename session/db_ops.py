"""对话存取：所有 SQL 都写在这里，业务层只调函数、不碰 SQL。

约定：
- scope 取值 'group' / 'private' / 'global'，对应表里的 type 列
  （Python 参数刻意不叫 type，避免遮蔽内置名）
- 私聊消息：group_openid 传 None，用 user_openid 定位会话
- assistant 行的 user_openid 存「提问者的 openid」，表示这条回答是给他的
"""
import sqlite3
from datetime import datetime, timezone
import config
from session.db import connect

GLOBAL_OPENID = config.GLOBAL_OPENID


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def scope_of(group_openid: str | None, user_openid: str) -> tuple[str, str]:
    """把「群/人」折算成 (scope, openid)，业务层不需要再判断是群聊还是私聊，这里直接返回的是两个参数，一个是 scope，一个是 openid"""
    if group_openid:
        return config.SCOPE_GROUP, group_openid
    return config.SCOPE_PRIVATE, user_openid

def scope_number_of(group_openid: str, user_openid: str) -> list[str]:
    """把「群/人」折算成 (scope, openid)，业务层不需要再判断是群聊还是私聊，这里直接返回的是两个参数，一个是 scope，一个是 openid"""
    if group_openid:
        return [config.SCOPE_GLOBAL, group_openid]
    return [config.SCOPE_GLOBAL, user_openid]


def _msg_filter(scope: str) -> str:
    """返回 chat_message 的过滤条件；两种写法都只带一个 openid 占位。"""
    if scope == config.SCOPE_GROUP:
        return "group_openid = ?"
    return "group_openid IS NULL AND user_openid = ?"


def _query(sql: str, params: tuple = ()) -> list[dict]:
    conn = connect()
    try:
        cur = conn.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def _write(sql: str, params: tuple = ()) -> tuple[int, int]:
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


# ---------------------------------------------------------------- 全量消息


def append_message(role: str, content: str, *, group_openid: str | None = None,
                         user_openid: str, msg_id: str | None = None) -> int | None:
    sql = (
        "INSERT OR IGNORE INTO chat_message"
        "(group_openid, user_openid, role, content, msg_id, created_time) "
        "VALUES (?,?,?,?,?,?)"
    )
    new_id, rowcount = _write(sql, (group_openid, user_openid, role, content,
                                    msg_id, _now()))
    return new_id if rowcount else None


def recent_messages(scope: str, openid: str, after_id: int, limit: int = config.RECENT_WINDOW) -> list[dict]:
    sql = (
        f"SELECT id, role, content, user_openid, created_time FROM chat_message "
        f"WHERE {_msg_filter(scope)} AND id > ? ORDER BY id DESC LIMIT ?"
    )
    rows = _query(sql, (openid, after_id, limit))
    rows.reverse()
    return rows


# ---------------------------------------------------------------- 摘要与水位线


def latest_summary(scope: str, openid: str) -> dict | None:
    sql = ("SELECT version, summary, final_msg_id, created_time FROM chat_summary "
           "WHERE type = ? AND openid = ? ORDER BY version DESC LIMIT 1")
    rows = _query(sql, (scope, openid))
    return rows[0] if rows else None


def next_version(scope: str, openid: str) -> int:
    sql = "SELECT MAX(version) AS v FROM chat_summary WHERE type = ? AND openid = ?"
    return (_query(sql, (scope, openid))[0]["v"] or 0) + 1


def save_summary(scope: str, openid: str, version: int, summary: str,
                       final_msg_id: int) -> bool:
    """写入摘要即等于推进水位线；返回 False 表示该版本已被别的请求写过，应放弃本次压缩。
    摘要为空时禁止写入——水位线一旦前进，那段消息就再也取不回来了。
    """
    sql = ("INSERT INTO chat_summary(type, openid, version, summary, final_msg_id, created_time) "
           "VALUES (?,?,?,?,?,?)")
    _, rowcount = _write(sql, (scope, openid, version, summary.strip(),
                               final_msg_id, _now()))
    return bool(rowcount)


def pending_compress(scope: str, openid: str, after_id: int, batch: int = config.COMPRESS_BATCH,
                           keep_recent: int = config.COMPRESS_KEEP_RECENT) -> list[dict]:
    """取「最老的待压消息」，并保留最近 keep_recent 条不压。
    每次只压最老的一批，prompt 里的原文窗口才会始终是满的；
    一次压完会让上下文突然塌缩，表现得像失忆。
    """
    sql = (
        f"SELECT id, role, content, created_time FROM chat_message "
        f"WHERE {_msg_filter(scope)} AND id > ? ORDER BY id ASC"
    )
    rows = _query(sql, (openid, after_id))
    if len(rows) <= keep_recent:
        return []
    return rows[:batch]


# ---------------------------------------------------------------- 记忆卡片


def load_cards(openids: list[str], limit: int = config.CARD_LOAD_LIMIT) -> list[dict]:
    """按会话取卡片；群聊场景应同时传 [群openid, 提问者openid, 'global']。"""
    if not openids:
        return []
    marks = ",".join("?" * len(openids))
    sql = (
        f"SELECT type, openid, title, content, importance, clarity FROM memory_card "
        f"WHERE openid IN ({marks}) "
        f"ORDER BY importance DESC, clarity DESC, updated_time DESC LIMIT ?"
    )
    return _query(sql, tuple(openids) + (limit,))


def upsert_card(scope: str, openid: str, title: str, content: str, *,
                      importance: int = config.CARD_IMPORTANCE_DEFAULT, clarity: int = config.CARD_CLARITY_DEFAULT,
                      source_msg_id: int | None = None) -> None:
    """同一 (openid, title) 只保留一张卡片：重复出现时覆盖内容、可信度 +1。"""
    now = _now()
    sql = (
        "INSERT INTO memory_card"
        "(openid, type, title, content, importance, clarity, source_msg_id, created_time, updated_time) "
        "VALUES (?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(openid, type, title) DO UPDATE SET "
        "  content = excluded.content, "
        "  importance = MAX(memory_card.importance, excluded.importance), "
        f"  clarity = MIN({config.CARD_SCORE_MAX}, memory_card.clarity + {config.CARD_CLARITY_BUMP}), "
        "  updated_time = excluded.updated_time"
    )
    _write(sql, (openid, scope, title, content.strip(), importance, clarity,
                 source_msg_id, now, now))
