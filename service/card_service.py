from service.base_service import write, query
from utils.log_util import logger
import config
import utils
import db as db_sql

def _half_life_days_sql(col: str = "half_life") -> str:
    """把 half_life 档位翻译成"半衰期天数"的 SQL CASE；天数取自 config，避免魔术值写进 SQL。"""
    whens = " ".join(
        f"WHEN {tier} THEN {days}" for tier, days in sorted(config.CARD_HALFLIFE_DAYS.items())
    )
    default_days = config.CARD_HALFLIFE_DAYS.get(config.CARD_HALFLIFE_DEFAULT, 0)
    return f"CASE {col} {whens} ELSE {default_days} END"

def _clamp_score(value: int, lo: int = config.CARD_SCORE_MIN, hi: int = config.CARD_SCORE_MAX,
                 default: int = config.CARD_SCORE_DEFAULT) -> int:
    """把模型传来的分数强制夹到 1~5，非法值退回默认，避免脏数据入库。"""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))

def load_cards(open_ids: list[str], limit: int = config.CARD_LOAD_LIMIT) -> list[dict]:
    """按会话取卡片；群聊场景应同时传 [群openid, 提问者openid, 'global']。
    半衰期衰减：距上次强化(updated_time)每过一个半衰期权重减半；
    经过超过 CARD_MAX_HALF_LIVES 个半衰期的记忆视为遗忘，直接不加载。
    半衰期天数=0(永久档)永不遗忘，并在同级中排在最前。
    """
    if not open_ids:
        return []
    marks = ",".join("?" * len(open_ids))
    hl_days = _half_life_days_sql()
    sql = db_sql.sql_load_cards(hl_days, marks)
    return query(sql, tuple(open_ids) + (config.CARD_MAX_HALF_LIVES, limit))


def upsert_card(scope: str, openid: str, title: str, content: str, *,
                          importance: int = config.CARD_IMPORTANCE_DEFAULT,
                          clarity: int = config.CARD_CLARITY_DEFAULT,
                          half_life: int = config.CARD_HALFLIFE_DEFAULT,
                          source_msg_id: int) -> bool:
    """同名 title 再写即更新：新建或按 (openid,type,title) 覆盖并自动强化，合并逻辑见 db_ops.upsert_card。"""
    title = (title or "").strip()
    content = (content or "").strip()
    if not title or not content:
        logger.warning(f"[insert_or_update_card] 放弃写入空卡片 scope={scope} openid={openid} title={title!r}")
        return False
    imp = _clamp_score(importance)
    cla = _clamp_score(clarity)
    hal = _clamp_score(half_life)
    now = utils.now()
    sql = db_sql.sql_upsert_card()
    write(sql, (openid, scope, title, content.strip(), importance, clarity, half_life,
                source_msg_id, now, now))
    logger.info(f"[insert_or_update_card] 卡片已保存 scope={scope} openid={openid} "
                f"title={title!r} importance={imp} clarity={cla} half_life={hal} content={content[:40]!r}")
    return True