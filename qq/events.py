"""把网关下发的事件体翻译成项目内部的统一消息结构。

为什么需要这一层：单聊用 `author.user_openid`、群聊用 `author.member_openid`，
而现有 `agent.chat(content, group_openid, member_openid, message_id)` 只有一个
"人"的槽位。归一化放在这里，agent/service/db 就完全不用感知平台字段差异。

同时负责两件"喂给模型前必须做"的事：
1. 群全量模式下 `<@member_openid>` / `<@all>` 是占位符，直接进 prompt 会浪费 token 且答非所问；
2. `message_type != 0`（卡片/聊天记录/引用）时 `content` 可能为空或是单个空格，需要兜底文案。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from utils.log_util import logger

# 触发业务处理的事件类型；其余事件（READY/RESUMED/FRIEND_ADD/...）只记日志
TEXT_EVENTS = frozenset({"C2C_MESSAGE_CREATE", "GROUP_AT_MESSAGE_CREATE", "GROUP_MESSAGE_CREATE"})

_MENTION_RE = re.compile(r"<@([^>]+)>")
_MAX_MENTION_NAME = 16


@dataclass(frozen=True, slots=True)
class IncomingMessage:
    """一条被归一化的入站消息。字段名刻意与 agent.chat 的形参对齐。"""

    msg_id: str                  # 事件 d.id：被动回复凭据
    event_id: str                # 事件外层 id：event_id 被动回复用（本方案暂不用，留作排障）
    content: str                 # 已清洗的文本
    group_openid: str | None     # None = 单聊
    member_openid: str           # 群聊=member_openid，单聊=user_openid
    message_type: int            # 0 文本 / 3 卡片 / 101 并行 / 102 聊天记录 / 103 引用
    timestamp: str               # RFC3339 东八区
    msg_idx: str = ""            # message_scene.ext 里的 msg_idx，引用回复用
    raw: dict = field(default_factory=dict, repr=False, compare=False)

    @property
    def is_group(self) -> bool:
        return self.group_openid is not None


def build_event(t: str, d: dict, event_id: str = "") -> IncomingMessage | None:
    """事件 → IncomingMessage；非白名单事件或缺关键字段时返回 None。"""
    if t not in TEXT_EVENTS or not isinstance(d, dict):
        return None

    author = d.get("author") or {}
    if t == "C2C_MESSAGE_CREATE":
        member_openid = author.get("user_openid") or author.get("id") or ""
        group_openid = None
    else:
        member_openid = author.get("member_openid") or author.get("id") or ""
        group_openid = d.get("group_openid") or None

    msg_id = d.get("id") or ""
    if not msg_id or not member_openid:
        # 缺 id 就无法被动回复，缺 openid 就无法定位会话：留在日志里比硬塞给业务层安全
        logger.warning("[events] 事件缺少关键字段，已丢弃 t=%s id=%r openid=%r", t, msg_id, member_openid)
        return None

    return IncomingMessage(
        msg_id=msg_id,
        event_id=event_id or "",
        content=normalize_content(d),
        group_openid=group_openid,
        member_openid=member_openid,
        message_type=_as_int(d.get("message_type")),
        timestamp=d.get("timestamp") or "",
        msg_idx=_extract_msg_idx(d),
        raw=d,
    )


def normalize_content(d: dict) -> str:
    """文本消息清洗 @占位符；非文本消息给模型一句可理解的兜底描述。"""
    text = _clean_mentions((d.get("content") or ""), d.get("mentions") or []).strip()
    if text:
        return text

    mtype = _as_int(d.get("message_type"))
    ark = d.get("ark_data") or {}
    if mtype == 3 and ark:
        title = ark.get("prompt") or ((ark.get("fields") or {}).get("title") or "")
        name = ark.get("ark_name") or "卡片"
        return f"[{name}] {title}".strip()
    if mtype == 102:
        return "[聊天记录]"
    if mtype == 103:
        parts = [str(e.get("content") or "").strip() for e in d.get("msg_elements") or [] if isinstance(e, dict)]
        parts = [p for p in parts if p]
        return "[引用] " + " / ".join(parts) if parts else "[引用消息]"

    attachments = [a for a in d.get("attachments") or [] if isinstance(a, dict)]
    if attachments:
        kinds = "、".join(str(a.get("content_type") or "附件") for a in attachments)
        return f"[用户发送了附件：{kinds}]"
    return ""


def _clean_mentions(text: str, mentions: list) -> str:
    """把 `<@openid>` 换成 `@昵称`；昵称为空时退化为 `@成员`，不泄露 openid 给模型。"""
    if not text or "<@" not in text:
        return text

    names: dict[str, str] = {}
    for m in mentions:
        if not isinstance(m, dict):
            continue
        key = m.get("member_openid") or m.get("user_openid") or m.get("id")
        if key:
            names[str(key)] = str(m.get("username") or "成员")[:_MAX_MENTION_NAME]

    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        if key == "all":
            return "@全体成员"
        return "@" + names.get(key, "成员")

    return _MENTION_RE.sub(repl, text)


def _extract_msg_idx(d: dict) -> str:
    """message_scene.ext 是 ["msg_idx=REFIDX_xxx==", "auth_token=..."] 这样的 key=value 列表。"""
    ext = (d.get("message_scene") or {}).get("ext") or []
    for item in ext:
        if isinstance(item, str) and item.startswith("msg_idx="):
            return item.split("=", 1)[1]
    return ""


def _as_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
