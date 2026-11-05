"""chat_summary 的业务层：编排参数、组装 SQL（取自 db.summary_db_sql）、执行读写。
SQL 语句本身不写在这里，只调用 db_sql.sql_* 拿到语句，再交给 BaseOps 的 query/write。"""
from service.base_service import write, query
from utils.log_util import logger
from model.factory import summary_model
from service.message_service import pending_compress
import db as db_sql
import config
import utils

_SUMMARY_PROMPT = f"""你在维护一段长期对话的滚动摘要。
【已有摘要】（可能为空）
{{old}}

【需要并入的新对话】
{{new}}

请把新对话融入已有摘要，输出更新后的摘要。要求：
1. 只保留长期有用的事实、偏好、待办、结论，去掉寒暄和一次性内容。
2. 用第三人称、条目化，控制在 {config.SUMMARY_MAX_LINES} 行以内。
3. 已有摘要里仍然有效的信息要保留；不要编造对话中没提到的内容。
只输出摘要正文，不要前言后语。"""


def latest_summary(scope: str, openid: str) -> dict | None:
    """取版本最大的一条摘要（即最新水位线快照）；没有则返回 None。"""
    sql = db_sql.sql_latest_summary()
    rows = query(sql, (scope, openid))
    return rows[0] if rows else None

def next_version(scope: str, openid: str) -> int:
    """当前最大版本 +1，作为新摘要的版本号；空表时为 1。"""
    sql = db_sql.sql_next_version()
    return (query(sql, (scope, openid))[0]["v"] or 0) + 1

def save_summary(scope: str, openid: str, version: int, summary: str,
                 final_msg_id: int) -> bool:
    """写入摘要即等于推进水位线；返回 False 表示该版本已被别的请求写过（唯一冲突），应放弃本次压缩。
    摘要为空时禁止写入——水位线一旦前进，那段消息就再也取不回来了。
    """
    sql = db_sql.sql_save_summary()
    _, rowcount = write(sql, (scope, openid, version, summary.strip(),
                                   final_msg_id, utils.now()))
    return bool(rowcount)


def compress_context(scope: str, openid: str, *,
                     batch: int = config.COMPRESS_BATCH,
                     keep_recent: int = config.COMPRESS_KEEP_RECENT) -> bool:
    """把「最老的一批过期消息」滚动进摘要，并推进水位线。
    返回 True 表示确实压缩并写入了新摘要；False 表示未达阈值或写入失败
    （并发下版本被抢先 / 模型给出空摘要），此时绝不推进水位线。
    """

    # 去除最新的一条压缩内容 + 已经压缩的最后一个语句的 id
    latest = latest_summary(scope, openid)
    after_id = latest["final_msg_id"] if latest else 0
    old_summary = latest["summary"] if latest else ""

    # 获取要压缩的内容
    pending = pending_compress(scope, openid, after_id, batch=batch, keep_recent=keep_recent)
    if not pending:
        logger.info(f"[compress] 未达阈值，跳过压缩 scope={scope} openid={openid} 水位线={after_id}")
        return False
    logger.info(f"[compress] 触发压缩 scope={scope} openid={openid} 水位线={after_id} 本批={len(pending)}条")

    dialog = "\n".join(f"{m['role']}: {m['content']}" for m in pending)
    new_summary = summary_model.invoke(
        _SUMMARY_PROMPT.format(old=old_summary or "（无）", new=dialog)
    ).content.strip()

    # 空摘要绝不能推进水位线——一旦推进，这批原文就再也取不回来了
    if not new_summary:
        logger.warning(f"[compress] 模型返回空摘要，放弃本次压缩 scope={scope} openid={openid}")
        return False

    version = next_version(scope, openid)
    saved = save_summary(scope, openid, version, new_summary, pending[-1]["id"])
    if saved:
        logger.info(f"[compress] 摘要写入成功 scope={scope} openid={openid} "
                    f"version={version} 新水位线={pending[-1]['id']} 摘要长度={len(new_summary)}")
    else:
        logger.warning(f"[compress] 版本冲突，本次压缩作废 scope={scope} openid={openid} version={version}")
    return saved
