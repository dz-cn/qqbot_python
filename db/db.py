"""会话数据库：只负责「连接」与「建表」，不写任何查询语句。"""
import sqlite3
import config
from utils.path_tool import get_project_root_path

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
  half_life     INTEGER NOT NULL DEFAULT {config.CARD_HALFLIFE_DEFAULT} CHECK (half_life BETWEEN {config.CARD_SCORE_MIN} AND {config.CARD_SCORE_MAX}),
  source_msg_id INTEGER,
  created_time  TEXT NOT NULL,
  updated_time  TEXT NOT NULL,
  UNIQUE (openid, type, title)
);
CREATE INDEX IF NOT EXISTS idx_card_read
  ON memory_card(openid, importance DESC, clarity DESC);
  
-- 4. RAG 父级整段（「给」的单位：整节原文 + 权限的唯一真相）
CREATE TABLE IF NOT EXISTS rag_parent (
  parent_id     TEXT PRIMARY KEY,
  openid        TEXT NOT NULL DEFAULT '{config.GLOBAL_OPENID}',
  type          TEXT NOT NULL CHECK (type IN ('{config.SCOPE_GROUP}', '{config.SCOPE_PRIVATE}', '{config.SCOPE_GLOBAL}')),
  full_text     TEXT NOT NULL CHECK (length(full_text) > 0)
);
-- 权限过滤写作 openid IN(...) OR type='global'：两侧各有单列索引，SQLite 才可能走 OR 优化
-- （复合索引 (type, openid) 服务不了「只按 openid」那一半，所以这里刻意拆成两条）
CREATE INDEX IF NOT EXISTS idx_rag_parent_openid ON rag_parent(openid);
CREATE INDEX IF NOT EXISTS idx_rag_parent_type   ON rag_parent(type);

-- 5. RAG 细子块（「查」的单位 + chunk→parent 关系桥；权限不冗余，只从 parent 过滤）
CREATE TABLE IF NOT EXISTS rag_chunk (
  chunk_id      TEXT PRIMARY KEY,
  parent_id     TEXT NOT NULL REFERENCES rag_parent(parent_id) ON DELETE CASCADE,
  seq           INTEGER NOT NULL CHECK (seq >= 0),
  chunk_text    TEXT NOT NULL CHECK (length(chunk_text) > 0),
  UNIQUE (parent_id, seq)
);

-- 6. RAG 关键词分支（FTS5 虚拟表：建一次即可，倒排索引由 fts5 模块自动维护，不给它建普通索引）
--    正文独立存一份（与 rag_chunk 冗余），换来插入/删除完全显式可控、无需触发器同步
CREATE VIRTUAL TABLE IF NOT EXISTS rag_fts USING fts5(
  chunk_text,
  chunk_id  UNINDEXED,
  parent_id UNINDEXED,
  tokenize='{config.RAG_FTS_TOKENIZER}'
);
"""

DB_PATH = get_project_root_path() / "data" / "app.db"

def connect() -> sqlite3.Connection:
    """一次操作一个连接：SQLite 连接不能跨线程复用，而开启连接的开销极小。"""
    conn = sqlite3.connect(DB_PATH, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    # 外键约束默认关闭且「按连接」生效，不加这句则 rag_chunk 的 ON DELETE CASCADE 静默无效
    conn.execute("PRAGMA foreign_keys=ON")
    # LIKE 默认 ASCII 大小写不敏感，与 parent_id 的 BINARY collation 不匹配则走不了索引；
    # 全项目仅此一处 LIKE（rag_db_sql.sql_parent_ids_by_prefix），改此开关无副作用
    conn.execute("PRAGMA case_sensitive_like=ON")
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
