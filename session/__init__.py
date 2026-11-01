"""会话记忆层：对外只暴露「建表」+「读写函数」，内部表结构变化不影响业务层。"""
from session.db import DB_PATH, init_db
from session.db_ops import (
    GLOBAL_OPENID,
    append_message,
    latest_summary,
    load_cards,
    next_version,
    pending_compress,
    recent_messages,
    save_summary,
    scope_of,
    upsert_card,
    scope_number_of
)
from session.memory_ops import insert_card, compress_context
from session.current import SessionContext, set_current_session, get_context

__all__ = [
    "DB_PATH",
    "GLOBAL_OPENID",
    "init_db",
    "scope_of",
    "append_message",
    "recent_messages",
    "latest_summary",
    "next_version",
    "save_summary",
    "pending_compress",
    "load_cards",
    "upsert_card",
    "scope_number_of",
    "insert_card",
    "compress_context",
    "SessionContext",
    "set_current_session",
    "get_context",
]