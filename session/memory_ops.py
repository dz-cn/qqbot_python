"""记忆卡片的存储与读取，以及上下文压缩的业务层。
SQL 都在 db_ops 里，这里只做「参数清洗 + 编排」。"""
import config
from botpy import logger
from session import db_ops
from model.factory import deepseek_summary_model

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

# -------------------------------------------- 记忆卡片的储存
def _clamp_score(value: int, lo: int = config.CARD_SCORE_MIN, hi: int = config.CARD_SCORE_MAX,
                 default: int = config.CARD_SCORE_DEFAULT) -> int:
    """把模型传来的分数强制夹到 1~5，非法值退回默认，避免脏数据入库。"""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def insert_card(scope: str, openid: str, title: str, content: str, *,
                importance: int = config.CARD_IMPORTANCE_DEFAULT, clarity: int = config.CARD_CLARITY_DEFAULT,
                source_msg_id: int) -> bool:
    title = (title or "").strip()
    content = (content or "").strip()
    if not title or not content:
        logger.warning(f"[insert_card] 放弃写入空卡片 scope={scope} openid={openid} title={title!r}")
        return False
    imp = _clamp_score(importance)
    cla = _clamp_score(clarity)
    db_ops.upsert_card(scope, openid, title, content,
                       importance=imp,
                       clarity=cla,
                       source_msg_id=source_msg_id)
    logger.info(f"[insert_card] 卡片已保存 scope={scope} openid={openid} "
                f"title={title!r} importance={imp} clarity={cla} content={content[:40]!r}")
    return True


# -------------------------------------------- 上下问的压缩
def compress_context(scope: str, openid: str, *,
                     batch: int = config.COMPRESS_BATCH,
                     keep_recent: int = config.COMPRESS_KEEP_RECENT) -> bool:
    """把「最老的一批过期消息」滚动进摘要，并推进水位线。
    返回 True 表示确实压缩并写入了新摘要；False 表示未达阈值或写入失败
    （并发下版本被抢先 / 模型给出空摘要），此时绝不推进水位线。
    """

    # 去除最新的一条压缩内容 + 已经压缩的最后一个语句的 id
    latest = db_ops.latest_summary(scope, openid)
    after_id = latest["final_msg_id"] if latest else 0
    old_summary = latest["summary"] if latest else ""

    # 获取要压缩的内容
    pending = db_ops.pending_compress(scope, openid, after_id, batch=batch, keep_recent=keep_recent)
    if not pending:
        logger.info(f"[compress] 未达阈值，跳过压缩 scope={scope} openid={openid} 水位线={after_id}")
        return False
    logger.info(f"[compress] 触发压缩 scope={scope} openid={openid} 水位线={after_id} 本批={len(pending)}条")

    dialog = "\n".join(f"{m['role']}: {m['content']}" for m in pending)
    new_summary = deepseek_summary_model.invoke(
        _SUMMARY_PROMPT.format(old=old_summary or "（无）", new=dialog)
    ).content.strip()

    # 空摘要绝不能推进水位线——一旦推进，这批原文就再也取不回来了
    if not new_summary:
        logger.warning(f"[compress] 模型返回空摘要，放弃本次压缩 scope={scope} openid={openid}")
        return False

    version = db_ops.next_version(scope, openid)
    saved = db_ops.save_summary(scope, openid, version, new_summary, pending[-1]["id"])
    if saved:
        logger.info(f"[compress] 摘要写入成功 scope={scope} openid={openid} "
                    f"version={version} 新水位线={pending[-1]['id']} 摘要长度={len(new_summary)}")
    else:
        logger.warning(f"[compress] 版本冲突，本次压缩作废 scope={scope} openid={openid} version={version}")
    return saved