"""chat_message 的 SQL 语句构造：只返回 SQL 字符串，不执行、不碰连接。

约定：WHERE 过滤片段（msg_filter）由 service 侧算好传入，这里只负责拼装完整语句。
"""


def sql_append_message() -> str:
    """全量消息写入；msg_id 命中唯一索引则忽略（INSERT OR IGNORE）。"""
    return (
        "INSERT OR IGNORE INTO chat_message"
        "(group_openid, user_openid, role, content, msg_id, created_time) "
        "VALUES (?,?,?,?,?,?)"
    )


def sql_recent_messages(msg_filter: str) -> str:
    """取水位线之后最近的若干条原文（降序取，service 侧再翻正）。"""
    return (
        f"SELECT id, role, content, user_openid, created_time FROM chat_message "
        f"WHERE {msg_filter} AND id > ? ORDER BY id DESC LIMIT ?"
    )


def sql_pending_compress(msg_filter: str) -> str:
    """取水位线之后、按时间正序排列的待压消息（截取逻辑在 service 侧）。"""
    return (
        f"SELECT id, role, content, created_time FROM chat_message "
        f"WHERE {msg_filter} AND id > ? ORDER BY id ASC"
    )

def sql_search_messages(msg_filter: str) -> str:
    """按内容前后模糊回查历史消息，最近的优先（service 侧再翻正成时间线）。

    展开后的私聊条件是 `group_openid IS NULL AND user_openid = ? AND content LIKE ? ESCAPE '\\'`,
    由 service.message_service._msg_filter 传入 —— 本文件开头就约定了过滤片段不在这里写死，
    而且 `group_openid IS NULL` 这几个字必须原样出现，部分索引 idx_msg_private 才可能被选中。

    LIKE 本身用不上索引，但 scope 条件先把范围缩到「这一个会话」的消息，
    不是全表扫：个人机器人的单会话历史是千行量级，不值得为它再建一张 FTS 虚表。
    排序走 id：created_time 虽是可比较的 ISO 文本，但索引帮不上，要多一次 TEMP B-TREE 排序。
    """
    return (
        f"SELECT id, role, content, created_time FROM chat_message "
        f"WHERE {msg_filter} AND content LIKE ? ESCAPE '\\' ORDER BY id DESC LIMIT ?"
    )
