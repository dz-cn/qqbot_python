"""WebSocket 网关客户端：取接入点 → Hello → Identify/Resume → 心跳 → 收事件 → 断线重连。

协议要点（官方「WebSocket 方式」「通用数据结构」）：
- 首帧 `op:10 Hello`，`d.heartbeat_interval` 为毫秒
- `op:2 Identify`：`token="QQBot {access_token}"`、`intents`、`shard`、`properties`
- `op:1` 心跳携带最新 `s`（首帧 null），成功收到 `op:11` ACK
- `op:6 Resume`：`session_id + seq`，平台补发遗漏事件
- `op:7 Reconnect` / `op:9 Invalid Session` 由服务端下发
- 关闭码 4004/4006/4007/4008/4009/4900~4913/4914/4915 分别对应不同处置

本模块只负责「收」：收到事件后翻译成 IncomingMessage 交给 Dispatcher，不在这里发消息。
"""
from __future__ import annotations

import asyncio
import json
from typing import Callable

import aiohttp

from qq.dispatcher import Dispatcher
from qq.events import build_event
from qq.token import TokenManager
from utils.log_util import logger

OP_DISPATCH = 0
OP_HEARTBEAT = 1
OP_IDENTIFY = 2
OP_RESUME = 6
OP_RECONNECT = 7
OP_INVALID_SESSION = 9
OP_HELLO = 10
OP_HEARTBEAT_ACK = 11

# 关闭码分类（官方 WebSocket 错误码表）
_FATAL_CLOSE = {4010, 4011, 4012, 4013, 4014, 4914, 4915}   # 配置/账号问题，重连也不会好
_IDENTIFY_CLOSE = {4006, 4007} | set(range(4900, 4914))      # 清 session，重新 Identify
_RESUME_CLOSE = {4008, 4009}                                 # 退避后 Resume


class FatalGatewayError(RuntimeError):
    """重连无法自愈：intents 无权限、机器人下架/封禁、shard 非法等。"""


class GatewayClient:
    def __init__(self, token: TokenManager, dispatcher: Dispatcher, *,
                 gateway_url: str, intents: int, shard: tuple[int, int] = (0, 1),
                 heartbeat_factor: float = 0.9, hello_timeout: float = 15.0,
                 http_timeout: float = 10.0, session_factory: Callable[[], aiohttp.ClientSession] | None = None):
        self._token = token
        self._dispatcher = dispatcher
        self._gateway_url = gateway_url
        self._intents = int(intents)
        self._shard = [int(shard[0]), int(shard[1])]
        self._heartbeat_factor = max(0.2, float(heartbeat_factor))
        self._hello_timeout = float(hello_timeout)
        self._http_timeout = float(http_timeout)
        self._session_factory = session_factory or aiohttp.ClientSession

        self._session_id = ""
        self._last_seq = 0
        self._pending = ""            # 服务端要求的下一次动作：identify / resume
        self._heartbeat_interval = 45.0
        self._awaiting_ack = False
        self._missed_acks = 0
        self._stop = asyncio.Event()

    # ------------------------------------------------------------------ 对外
    async def close(self) -> None:
        """请求退出：run_forever 会在当前连接结束后返回。"""
        self._stop.set()

    async def run_forever(self) -> None:
        """重连主循环：4010~4014/4914/4915 直接抛 FatalGatewayError，其余按退避重试。"""
        backoff = 1.0
        while not self._stop.is_set():
            try:
                url = await self._fetch_gateway()
                await self._connect_once(url)
                if self._stop.is_set():
                    break
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except FatalGatewayError:
                logger.error("[gateway] 致命错误，停止重连")
                raise
            except Exception as exc:
                if self._stop.is_set():
                    break
                logger.warning("[gateway] 连接中断：%r；%.0fs 后重连（resume=%s 已有 session）",
                               exc, backoff, bool(self._session_id))
                await _sleep(backoff)
                backoff = min(backoff * 2, 30.0)
        logger.info("[gateway] 已停止")

    # ------------------------------------------------------------------ 连接生命周期
    async def _connect_once(self, url: str) -> None:
        async with self._session_factory() as session:
            async with session.ws_connect(url, heartbeat=None, max_msg_size=0,
                                          timeout=aiohttp.ClientWSTimeout(ws_close=10.0)) as ws:
                await self._handshake(ws)
                await self._serve(ws)
                if self._stop.is_set():
                    # 主动退出时发一个正常关闭帧，服务端日志里就不会留下异常断连
                    await ws.close(code=1000, message=b"bye")
                code = ws.close_code
        await self._decide_after_close(code)

    async def _handshake(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        first = await asyncio.wait_for(ws.receive(), timeout=self._hello_timeout)
        payload = self._decode(first)
        if payload.get("op") != OP_HELLO:
            raise RuntimeError(f"首帧不是 Hello：{payload!r}")

        interval_ms = (payload.get("d") or {}).get("heartbeat_interval") or 45000
        try:
            self._heartbeat_interval = max(int(interval_ms) / 1000.0, 0.05)
        except (TypeError, ValueError):
            self._heartbeat_interval = 45.0

        token = await self._token.get()
        if self._session_id:
            await ws.send_json({
                "op": OP_RESUME,
                "d": {"token": f"QQBot {token}", "session_id": self._session_id, "seq": self._last_seq},
            })
            logger.info("[gateway] 已发送 Resume（session=%s… seq=%s）", self._session_id[:8], self._last_seq)
        else:
            await ws.send_json({
                "op": OP_IDENTIFY,
                "d": {
                    "token": f"QQBot {token}",
                    "intents": self._intents,
                    "shard": self._shard,
                    "properties": {"$os": "windows", "$browser": "qqbot_python", "$device": "qqbot_python"},
                },
            })
            logger.info("[gateway] 已发送 Identify（intents=%d shard=%s）", self._intents, self._shard)

    async def _serve(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        """并发跑心跳与收帧；任一结束就收敛，避免残留任务。"""
        self._awaiting_ack = False
        self._missed_acks = 0

        heartbeat = asyncio.create_task(self._heartbeat_loop(ws), name="qq-heartbeat")
        receiver = asyncio.create_task(self._receive_loop(ws), name="qq-receive")
        stopper = asyncio.create_task(self._stop.wait(), name="qq-stop")
        try:
            done, _ = await asyncio.wait({heartbeat, receiver, stopper}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (heartbeat, receiver, stopper):
                if not task.done():
                    task.cancel()
            await asyncio.gather(heartbeat, receiver, stopper, return_exceptions=True)

        if self._stop.is_set() and stopper in done:
            return
        if receiver.done() and not receiver.cancelled() and receiver.exception():
            raise receiver.exception()  # 网络异常：交给外层退避重连

    async def _receive_loop(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        while True:
            message = await ws.receive()
            if message.type == aiohttp.WSMsgType.TEXT:
                payload = json.loads(message.data)
            elif message.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING,
                                 aiohttp.WSMsgType.CLOSED):
                return
            elif message.type == aiohttp.WSMsgType.ERROR:
                raise RuntimeError(f"WebSocket 错误：{ws.exception()!r}")
            else:
                continue

            op = payload.get("op")
            if op == OP_DISPATCH:
                await self._dispatch(payload)
            elif op == OP_RECONNECT:
                self._pending = "resume"
                logger.info("[gateway] 服务端要求重连（Reconnect）")
                return
            elif op == OP_INVALID_SESSION:
                self._pending = "identify"
                logger.info("[gateway] 会话失效（Invalid Session），将重新 Identify")
                return
            elif op == OP_HEARTBEAT_ACK:
                self._awaiting_ack = False
                self._missed_acks = 0
            elif op == OP_HEARTBEAT:
                # 少数情况下服务端主动要心跳：立即回一帧
                await ws.send_json({"op": OP_HEARTBEAT, "d": self._last_seq or None})

    async def _heartbeat_loop(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        interval = self._heartbeat_interval * self._heartbeat_factor
        await _sleep(interval)
        while True:
            if self._awaiting_ack:
                self._missed_acks += 1
                logger.warning("[gateway] 心跳未收到 ACK（连续 %d 次）", self._missed_acks)
                if self._missed_acks >= 2:
                    await ws.close(code=1000, message=b"heartbeat timeout")
                    return
            await ws.send_json({"op": OP_HEARTBEAT, "d": self._last_seq or None})
            self._awaiting_ack = True
            await _sleep(interval)

    # ------------------------------------------------------------------ 事件
    async def _dispatch(self, payload: dict) -> None:
        seq = payload.get("s")
        if isinstance(seq, int) and seq > 0:
            self._last_seq = seq          # 先记 seq 再处理：Resume 语义是"补发 s 之后的事件"

        t = payload.get("t") or ""
        if t == "READY":
            data = payload.get("d") or {}
            self._session_id = str(data.get("session_id") or "")
            user = data.get("user") or {}
            logger.info("[gateway] 就绪：%s（id=%s）shard=%s",
                        user.get("username"), user.get("id"), data.get("shard"))
            return
        if t == "RESUMED":
            logger.info("[gateway] 重连成功，遗漏事件已补发")
            return

        msg = build_event(t, payload.get("d") or {}, str(payload.get("id") or ""))
        if msg is None:
            logger.debug("[gateway] 忽略事件 t=%s op=%s", t, payload.get("op"))
            return
        await self._dispatcher.submit(msg)

    # ------------------------------------------------------------------ 关闭码
    async def _decide_after_close(self, code: int | None) -> None:
        pending, self._pending = self._pending, ""

        if pending == "identify":
            self._reset_session()
            logger.info("[gateway] 清空 session，下次重新 Identify")
            return
        if pending == "resume":
            return

        if code in _FATAL_CLOSE:
            raise FatalGatewayError(_fatal_hint(code))
        if code == 4004:
            logger.warning("[gateway] 鉴权失败（4004），强制刷新 token 后重新 Identify")
            await self._token.force_refresh()
            self._reset_session()
            return
        if code in _IDENTIFY_CLOSE:
            logger.info("[gateway] 关闭码 %s：清空 session，重新 Identify", code)
            self._reset_session()
            return
        if code in _RESUME_CLOSE:
            logger.info("[gateway] 关闭码 %s：稍后 Resume", code)
            return
        if code is None:
            return
        if not self._session_id:
            logger.info("[gateway] 关闭码 %s：无可用 session，下次 Identify", code)
            return
        logger.info("[gateway] 关闭码 %s：保留 session，下次尝试 Resume", code)

    def _reset_session(self) -> None:
        self._session_id = ""
        self._last_seq = 0

    # ------------------------------------------------------------------ 工具
    async def _fetch_gateway(self) -> str:
        token = await self._token.get()
        headers = {"Authorization": f"QQBot {token}"}
        timeout = aiohttp.ClientTimeout(total=self._http_timeout)
        async with self._session_factory() as session:
            async with session.get(self._gateway_url, headers=headers, timeout=timeout) as resp:
                if resp.status == 401:
                    await self._token.force_refresh()
                    raise RuntimeError("取网关地址鉴权失败（401）")
                data = await resp.json(content_type=None)

        url = (data or {}).get("url") if isinstance(data, dict) else None
        if not url:
            raise RuntimeError(f"网关地址无效：{data!r}")
        return str(url)

    @staticmethod
    def _decode(message: aiohttp.WSMessage) -> dict:
        if message.type != aiohttp.WSMsgType.TEXT:
            raise RuntimeError(f"期望文本帧，实际 {message.type}")
        payload = json.loads(message.data)
        if not isinstance(payload, dict):
            raise RuntimeError(f"帧不是 JSON 对象：{payload!r}")
        return payload


def _fatal_hint(code: int) -> str:
    hints = {
        4010: "shard 非法",
        4011: "需要处理的 guild 过多，需要分片",
        4012: "version 非法",
        4013: "intents 非法",
        4014: "intents 无权限：请到开放平台确认群聊/C2C 事件权限",
        4914: "机器人已下架，只允许沙箱环境",
        4915: "机器人已封禁",
    }
    return f"网关关闭码 {code}（{hints.get(code, '配置错误')}），不会自动重连"


async def _sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)
