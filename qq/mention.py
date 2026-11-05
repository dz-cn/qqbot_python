"""@ 提及：把占位符翻译成平台能渲染的标签，并把模型自创的写法收敛掉。

官方约定（「文本交互 · 使用 @ 能力」）：
- @用户：`<qqbot-at-user id="MEMBER_OPENID" />`
  - **群聊：仅 markdown 消息支持**，id 填被 @ 群成员的 `member_openid`
  - **单聊（C2C）：不支持 @ 用户**（私聊的 markdown 也不能带 at 内容，会被拒绝或按正文显示）
- @全部成员：`<qqbot-at-everyone />`，群聊需机器人为管理员且每群每天 1 次
- 旧协议 `<@userid>`、`@everyone` 即将弃用

因此这里做两件事：
1. 模型不知道 openid，约定它输出 `[[AT_ME]]` 表示「@ 当前发言人」，由本模块换成真实 `member_openid`；
2. 模型偶尔会自创 `<at user_openid="...">` / `<@...>`，统一收敛：认得出是"本人"的转成标签，
   认不出的剥掉——否则用户看到的就是截图里那串 `<at user_openid="..."></at>` 原文。
"""
from __future__ import annotations

import re

# 模型侧的占位符：@ 当前发言人。写在 prompts 里，比让模型拼 openid 可靠
AT_ME_MARKER = "[[AT_ME]]"

_AT_TAG = '<qqbot-at-user id="{openid}" />'

# 模型可能自创的三种写法
_RAW_AT = re.compile(r'<at\b[^>]*user_openid\s*=\s*"([^"]*)"[^>]*>\s*(?:</at>)?', re.IGNORECASE)
_LEGACY_AT = re.compile(r"<@!?([^>\s]+)>")
_CANONICAL_AT = re.compile(r'<qqbot-at-user\s+id\s*=\s*"([^"]*)"\s*/>', re.IGNORECASE)

_EVERYONE = re.compile(r"<qqbot-at-everyone\s*/>", re.IGNORECASE)


def resolve(text: str, member_openid: str) -> tuple[str, bool]:
    """把回复文本收敛成可发送内容。

    :param text: 模型原始回复
    :param member_openid: 当前发言人的 member_openid；单聊传空串（表示平台不支持 @ 用户）
    :return: (清洗后的文本, 是否含有可渲染的 @用户 标签)
    """
    if not text:
        return "", False

    tagged = False

    def canon(raw_id: str) -> str:
        nonlocal tagged
        # 只有「空 id」或「就是当前发言人」才转成标签：模型给不出别人的 member_openid，
        # 猜错就会 @ 到陌生人，宁可不 @。
        if member_openid and (raw_id == "" or raw_id == member_openid):
            tagged = True
            return _AT_TAG.format(openid=member_openid)
        return ""

    # 只在真的出现占位符时才调用 canon：str.replace 的替换值会被提前求值，
    # 直接写 text.replace(AT_ME_MARKER, canon(...)) 会让每条纯文本都被标成"含 @"。
    if AT_ME_MARKER in text:
        text = text.replace(AT_ME_MARKER, canon(member_openid))
    text = _RAW_AT.sub(lambda match: canon(match.group(1)), text)
    text = _CANONICAL_AT.sub(lambda match: canon(match.group(1)), text)
    text = _LEGACY_AT.sub(lambda match: canon(match.group(1)), text)

    if not member_openid:
        text = _EVERYONE.sub("", text)  # 单聊没有 @ 能力，连 @全体 也不该出现

    return _tidy(text), tagged


def strip_mentions(text: str) -> str:
    """去掉所有 @ 标记，只留正文（不支持的场景 / markdown 被拒时的降级用）。"""
    if not text:
        return ""
    text = text.replace(AT_ME_MARKER, "")
    for pattern in (_RAW_AT, _CANONICAL_AT, _LEGACY_AT, _EVERYONE):
        text = pattern.sub("", text)
    return _tidy(text)


def _tidy(text: str) -> str:
    """清掉标签移除后留下的多余空白，但不破坏正常排版。"""
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return text.strip()
