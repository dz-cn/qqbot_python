"""OpenAPI 发送客户端（HTTPS）。

与收消息的 WebSocket 完全解耦：WSS 断线不影响已建立的 HTTP 会话，HTTP 出错也不影响收事件。
两边的唯一纽带是同一个 TokenManager 提供的 access_token。

调用约束（官方文档）：
- 头：`Authorization: QQBot {token}`；body 里 `msg_id` 即被动回复凭据
- 被动回复：群 5 分钟 / 5 次，单聊 4 次；`msg_id` + `msg_seq` 相同视为重复，会返回 40054005
- 失败时 HTTP 可能仍是 200，必须看 body 里的 `err_code`，不能只看状态码
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable
from urllib.parse import quote

import aiohttp

from qq.errors import (
    AuthError,
    DuplicateMessage,
    QQError,
    RateLimitError,
    SendRejectedError,
    TransientError,
    error_for,
    error_for_http,
)
from qq.token import TokenManager
from utils.log_util import logger

_SLEEP = Callable[[float], Awaitable[None]]


class OpenAPIClient:
    """一个进程一个实例、一个 ClientSession，避免每次发送都重建连接。"""

    def __init__(self, token: TokenManager, base_url: str, *,
                 timeout: float = 10.0, retry: int = 2, app_id: str = "",
                 allow_active: bool = False, session: aiohttp.ClientSession | None = None,
                 sleep: _SLEEP | None = None):
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._retry = max(0, int(retry))
        self._app_id = app_id
        self._allow_active = allow_active
        self._session = session
        self._owns_session = session is None
        self._sleep = sleep or _sleep

    # ------------------------------------------------------------------ 发送接口
    async def send_c2c_text(self, user_openid: str, content: str, *,
                            msg_id: str | None = None, msg_seq: int = 1) -> dict:
        """发送单聊文本。带 msg_id 即被动回复，否则是主动消息（默认禁止）。"""
        self._check_active(msg_id)
        payload = {"msg_type": 0, "content": content}
        self._fill_passive(payload, msg_id, msg_seq)
        return await self._request("POST", f"/v2/users/{quote(user_openid, safe='')}/messages", payload)

    async def send_group_text(self, group_openid: str, content: str, *,
                             msg_id: str | None = None, msg_seq: int = 1) -> dict:
        """发送群聊文本。"""
        self._check_active(msg_id)
        payload = {"msg_type": 0, "content": content}
        self._fill_passive(payload, msg_id, msg_seq)
        return await self._request("POST", f"/v2/groups/{quote(group_openid, safe='')}/messages", payload)

    async def send_c2c_markdown(self, user_openid: str, markdown: str, *,
                                msg_id: str | None = None, msg_seq: int = 1) -> dict:
        """发送单聊 Markdown（需要机器人有 markdown 权限，否则 304036/40034127）。"""
        self._check_active(msg_id)
        payload = {"msg_type": 2, "markdown": {"content": markdown}}
        self._fill_passive(payload, msg_id, msg_seq)
        return await self._request("POST", f"/v2/users/{quote(user_openid, safe='')}/messages", payload)

    async def send_group_markdown(self, group_openid: str, markdown: str, *,
                                  msg_id: str | None = None, msg_seq: int = 1) -> dict:
        """发送群聊 Markdown。"""
        self._check_active(msg_id)
        payload = {"msg_type": 2, "markdown": {"content": markdown}}
        self._fill_passive(payload, msg_id, msg_seq)
        return await self._request("POST", f"/v2/groups/{quote(group_openid, safe='')}/messages", payload)

    # ------------------------------------------------------------------ 内部实现
    def _check_active(self, msg_id: str | None) -> None:
        """不带 msg_id 就是主动消息：有独立频控与权限，默认直接拒绝，防止误发。"""
        if not msg_id and not self._allow_active:
            raise SendRejectedError("未开启主动消息（QQ_ALLOW_ACTIVE_MSG=False），发送必须带 msg_id")

    @staticmethod
    def _fill_passive(payload: dict, msg_id: str | None, msg_seq: int) -> None:
        if msg_id:
            payload["msg_id"] = msg_id
            payload["msg_seq"] = int(msg_seq)

    def _session_obj(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=50))
            self._owns_session = True
        return self._session

    def _headers(self, token: str) -> dict:
        headers = {
            "Authorization": f"QQBot {token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        if self._app_id:
            headers["X-Union-Appid"] = self._app_id
        return headers

    async def _request(self, method: str, path: str, payload: dict | None) -> dict:
        """统一处理：取 token → 发请求 → 解析 err_code → 按类别重试/抛出。"""
        url = f"{self._base_url}{path}"
        auth_retried = False
        attempt = 0

        while True:
            token = await self._token.get()
            try:
                body, status = await self._send_once(method, url, payload, self._headers(token))
            except (aiohttp.ClientError, TimeoutError) as exc:
                err: QQError = TransientError(f"网络错误：{exc!r}")
            else:
                err = self._error_from(body, status)
                if err is None:
                    return body if isinstance(body, dict) else {}
                if isinstance(err, DuplicateMessage):
                    # 重试时撞到去重 = 上一次其实已送达，视为成功，别再重试
                    logger.info("[api] 消息被去重，视为已送达 path=%s", path)
                    return {"duplicate": True}

            if isinstance(err, AuthError) and not auth_retried:
                auth_retried = True
                logger.warning("[api] 鉴权失败，刷新 token 后重试一次：%s", err)
                await self._token.force_refresh()
                continue

            if isinstance(err, (TransientError, RateLimitError)) and attempt < self._retry:
                attempt += 1
                delay = 2 ** (attempt - 1)  # 1s, 2s
                logger.warning("[api] %s 第 %d/%d 次重试（%s）", path, attempt, self._retry, err)
                await self._sleep(delay)
                continue

            raise err

    async def _send_once(self, method: str, url: str, payload: dict | None,
                         headers: dict) -> tuple[Any, int]:
        session = self._session_obj()
        timeout = aiohttp.ClientTimeout(total=self._timeout)
        async with session.request(method, url, json=payload, headers=headers, timeout=timeout) as resp:
            status = resp.status
            try:
                body = await resp.json(content_type=None)
            except (aiohttp.ContentTypeError, ValueError):
                body = None  # 204 或非 JSON 错误页
            return body, status

    @staticmethod
    def _error_from(body: Any, status: int) -> QQError | None:
        """先看 body 的 err_code（HTTP 200 也可能是错误），再看状态码。"""
        if isinstance(body, dict):
            code = body.get("err_code")
            if code:
                try:
                    code_int = int(code)
                except (TypeError, ValueError):
                    code_int = None
                if code_int:
                    return error_for(code_int, str(body.get("message") or ""),
                                     trace_id=body.get("trace_id"))
        if 200 <= status < 300:
            return None
        return error_for_http(status)

    async def aclose(self) -> None:
        if self._session is not None and self._owns_session and not self._session.closed:
            await self._session.close()


async def _sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)


def split_text(text: str, limit: int, max_parts: int) -> list[str]:
    """按段落/句号/空格把超长文本切成不超过 limit 的若干段。

    被动回复对同一 msg_id 的发送次数有限（单聊 4 次、群聊 5 次），因此 max_parts 要留余量；
    实在切不完就截断并加省略号 —— 截断比"发不出去"对用户更友好。
    """
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]

    parts: list[str] = []
    rest = text
    while rest and len(parts) < max_parts:
        if len(rest) <= limit:
            parts.append(rest)
            rest = ""
            break
        cut = rest.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = rest.rfind("。", 0, limit)
        if cut < limit // 2:
            cut = rest.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit  # 没有任何可读断点：硬切
        parts.append(rest[:cut].strip())
        rest = rest[cut:].strip()

    if rest:
        last = parts[-1]
        parts[-1] = last[: max(0, limit - 1)].rstrip() + "…"
    return [p for p in parts if p]
