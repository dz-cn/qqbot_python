"""chat_summary 的 SQL 语句构造：只返回 SQL 字符串，不执行、不碰连接。"""


def sql_latest_summary() -> str:
    """取版本最大的一条摘要（即最新水位线快照）。"""
    return ("SELECT version, summary, final_msg_id, created_time FROM chat_summary "
            "WHERE type = ? AND openid = ? ORDER BY version DESC LIMIT 1")


def sql_next_version() -> str:
    """查当前最大版本号，service 侧 +1 作为新版本。"""
    return "SELECT MAX(version) AS v FROM chat_summary WHERE type = ? AND openid = ?"


def sql_save_summary() -> str:
    """写入新摘要（同时即推进水位线）；(type, openid, version) 唯一冲突会在 service 侧被识别。"""
    return ("INSERT INTO chat_summary(type, openid, version, summary, final_msg_id, created_time) "
            "VALUES (?,?,?,?,?,?)")
