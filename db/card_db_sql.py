import config

def sql_load_cards(hl_days: str, marks: str):
    return (
        f"SELECT type, openid, title, content, importance, clarity, half_life, updated_time FROM ("
        f"  SELECT *, CASE WHEN ({hl_days}) = 0 THEN 0.0 "
        f"    ELSE (julianday('now') - julianday(updated_time)) * 1.0 / ({hl_days}) END AS elapsed_ratio "
        f"  FROM memory_card WHERE openid IN ({marks})"
        f") WHERE elapsed_ratio <= ? "
        f"ORDER BY importance DESC, clarity DESC, elapsed_ratio ASC LIMIT ?"
    )

def sql_upsert_card():
    return (
        "INSERT INTO memory_card"
        "(openid, type, title, content, importance, clarity, half_life, source_msg_id, created_time, updated_time) "
        "VALUES (?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(openid, type, title) DO UPDATE SET "
        "  content = excluded.content, "
        "  importance = MAX(memory_card.importance, excluded.importance), "
        f"  clarity = MIN({config.CARD_SCORE_MAX}, memory_card.clarity + {config.CARD_CLARITY_BUMP}), "
        "  half_life = MAX(memory_card.half_life, excluded.half_life), "
        "  updated_time = excluded.updated_time"
    )