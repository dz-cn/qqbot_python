"""chat_message 的业务层：编排参数、组装 SQL（取自 db.message_db_sql）、执行读写。
SQL 语句本身不写在这里，只调用 db_sql.sql_* 拿到语句，再交给 BaseOps 的 query/write。"""
from service.base_service import write, query
import db as db_sql
import config
import utils


def _msg_filter(scope: str) -> str:
    """返回 chat_message 的过滤条件；两种写法都只带一个 openid 占位。"""
    if scope == config.SCOPE_GROUP:
        return "group_openid = ?"
    return "group_openid IS NULL AND user_openid = ?"

def _like_pattern(keyword: str) -> str:
    """把关键词包成前后模糊模式并转义。
    转义不是可选的：不转义时搜「100%」会拼成 '%100%%'，% 是通配符 —— 于是所有记录都被捞回来，
    而这正是「模糊查询」最典型的误用形态（用户搜的数字里带 % 或 _ 很常见）。
    反斜杠自身也要先转义，否则搜「A\\B」时转义符被吞，只剩 'B' 生效。"""
    escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"

def append_message(role: str, content: str, *, group_openid: str | None = None,
                   user_openid: str, msg_id: str | None = None) -> int | None:
    """追加一条全量消息；msg_id 命中唯一索引则忽略，返回新行 id（被忽略时返回 None）。"""
    sql = db_sql.sql_append_message()
    new_id, rowcount = write(sql, (group_openid, user_openid, role, content,
                                        msg_id, utils.now()))
    return new_id if rowcount else None

def recent_messages(scope: str, openid: str, after_id: int,
                    limit: int = config.RECENT_WINDOW) -> list[dict]:
    """取水位线之后最近的 limit 条原文，按 id 升序返回（越新越靠后）。"""
    sql = db_sql.sql_recent_messages(_msg_filter(scope))
    rows = query(sql, (openid, after_id, limit))
    rows.reverse()
    return rows

def pending_compress(scope: str, openid: str, after_id: int,
                     batch: int = config.COMPRESS_BATCH,
                     keep_recent: int = config.COMPRESS_KEEP_RECENT) -> list[dict]:
    """取「最老的待压消息」，并保留最近 keep_recent 条不压。
    每次只压最老的一批，prompt 里的原文窗口才会始终是满的；
    一次压完会让上下文突然塌缩，表现得像失忆。
    """
    sql = db_sql.sql_pending_compress(_msg_filter(scope))
    rows = query(sql, (openid, after_id))
    if len(rows) <= keep_recent:
        return []
    return rows[:batch]

def search_messages(scope: str, openid: str, keyword: str,
                    limit: int = 5) -> list[dict]:
    """按内容模糊回查历史消息，按时间正序返回（最近的在最末）。
    过滤条件由 _msg_filter 决定：私聊=user_openid 且 group_openid IS NULL，群聊=group_openid。"""
    keyword = (keyword or "").strip()
    if not keyword:
        return []
    rows = query(db_sql.sql_search_messages(_msg_filter(scope)), (openid, _like_pattern(keyword), limit))
    rows.reverse()
    return rows

# if __name__ == '__main__':
#     print(search_messages('private', 'A8E76E6E7B75577529250B054BD35A78', "你", 5))
