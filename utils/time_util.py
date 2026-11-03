from datetime import datetime, timezone, timedelta

_CN_TZ = timezone(timedelta(hours=8))   # 中国标准时间 UTC+8（无夏令时，固定偏移即可，免依赖 tzdata）
_WEEKDAYS = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")

def now() -> str:
    """当前中国时间(UTC+8)，ISO8601 带 +08:00 偏移；julianday 会按偏移换算，衰减计算仍准确。"""
    return datetime.now(_CN_TZ).isoformat()

def now_text() -> str:
    """给模型看的当前时间：日期 + 星期 + 分钟 + 时区，一行。三处加工各有各的必要性：
    - 星期要算好了给它：模型从日期反推 weekday 靠训练语料里的偶然记忆，会算错，而「周五了」「周末」是高频问法；
    - 只到分钟：秒对回答没有信息量，还会诱导它复述「现在是 14:23:45」这种精确到秒的怪话；
    - 时区写进文字：不写，模型会默认按用户所在时区理解，人在境外就差几小时。"""
    dt = datetime.now(_CN_TZ)
    return f"{dt:%Y-%m-%d} {_WEEKDAYS[dt.weekday()]} {dt:%H:%M}（北京时间 UTC+8）"
