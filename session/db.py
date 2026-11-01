"""会话数据库：只负责「连接」与「建表」，不写任何查询语句。"""
import sqlite3

import config
from utils.path_tool import get_project_root_path

DB_PATH = get_project_root_path() / "data" / "app.db"

_SCHEMA = f"""
-- 1. 全量消息表（原始层）
CREATE TABLE IF NOT EXISTS chat_message (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  group_openid  TEXT,
  user_openid   TEXT NOT NULL,
  role          TEXT NOT NULL CHECK (role IN ('{config.MSG_ROLE_USER}', '{config.MSG_ROLE_ASSISTANT}')),
  content       TEXT NOT NULL,
  msg_id        TEXT,
  created_time  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_msg_group   ON chat_message(group_openid, id);
CREATE INDEX IF NOT EXISTS idx_msg_private ON chat_message(user_openid, id)
  WHERE group_openid IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_msg_dedup ON chat_message(msg_id)
  WHERE msg_id IS NOT NULL;

-- 2. 摘要表（滚动版本，final_msg_id 即唯一水位线）
CREATE TABLE IF NOT EXISTS chat_summary (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  openid        TEXT NOT NULL DEFAULT '{config.GLOBAL_OPENID}',
  type          TEXT NOT NULL CHECK (type IN ('{config.SCOPE_GROUP}', '{config.SCOPE_PRIVATE}', '{config.SCOPE_GLOBAL}')),
  version       INTEGER NOT NULL,
  summary       TEXT NOT NULL,
  final_msg_id  INTEGER NOT NULL,
  created_time  TEXT NOT NULL,
  UNIQUE (type, openid, version)
);
CREATE INDEX IF NOT EXISTS idx_summary_latest
  ON chat_summary(type, openid, version DESC);

-- 3. 记忆卡片（事实层）
CREATE TABLE IF NOT EXISTS memory_card (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  openid        TEXT NOT NULL DEFAULT '{config.GLOBAL_OPENID}',
  type          TEXT NOT NULL CHECK (type IN ('{config.SCOPE_GROUP}', '{config.SCOPE_PRIVATE}', '{config.SCOPE_GLOBAL}')),
  title         TEXT NOT NULL,
  content       TEXT NOT NULL,
  importance    INTEGER NOT NULL DEFAULT {config.CARD_IMPORTANCE_DEFAULT} CHECK (importance BETWEEN {config.CARD_SCORE_MIN} AND {config.CARD_SCORE_MAX}),
  clarity       INTEGER NOT NULL DEFAULT {config.CARD_CLARITY_DEFAULT} CHECK (clarity BETWEEN {config.CARD_SCORE_MIN} AND {config.CARD_SCORE_MAX}),
  source_msg_id INTEGER,
  created_time  TEXT NOT NULL,
  updated_time  TEXT NOT NULL,
  UNIQUE (openid, type, title)
);
CREATE INDEX IF NOT EXISTS idx_card_read
  ON memory_card(openid, importance DESC, clarity DESC);
"""


def connect() -> sqlite3.Connection:
    """一次操作一个连接：SQLite 连接不能跨线程复用，而开启连接的开销极小。"""
    conn = sqlite3.connect(DB_PATH, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    """在 main.py 启动时调用一次；DDL 全部是 IF NOT EXISTS，可重复执行。"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = connect()
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()
