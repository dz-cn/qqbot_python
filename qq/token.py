"""access_token 的获取、缓存与刷新。

官方口径（获取访问凭证）：
- `POST https://api.bot.qq.com/app/getAppAccessToken`，body `{"appId","clientSecret"}`
- 成功 `{"access_token": "...", "expires_in": "7200"}`（expires_in 是字符串）
- 失败 HTTP 仍为 200，业务错误在 body 的 `code`（100001/100007/100016/10004）
- 有效期 7200s；过期前 60s 内再次获取才会换新；有效期内重复获取返回同一个值

因此：必须提前续期、必须并发单飞、必须按 code 判失败。
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable

import aiohttp

from qq.errors import AuthError, TransientError
from utils.log_util import logger

Fetch = Callable[[str, dict], Awaitable[dict]]


def _parse_expires_in(value: Any) -> int:
    """官方示例是字符串 "7200"，也兼容数字；非法值按 7200 兜底。"""
    try:
        seconds = int(float(value))
    except (TypeError, ValueError):
        return 7200
    return seconds if seconds > 0 else 7200


class TokenManager:
    """全进程唯一的 token 持有者：缓存 + 提前 60s 续期 + 单飞刷新。

    clock/fetch 可注入，方便离线测试（不联网、不等待真实时间）。
    """

    def __init__(self, app_id: str, secret: str, url: str, *,
                 fallback_url: str | None = None, timeout: float = 10.0,
                 clock: Callable[[], float] = time.time, fetch: Fetch | None = None):
        self._app_id = app_id
        self._secret = secret
        self._url = url
        self._fallback_url = fallback_url
        self._timeout = timeout
        self._clock = clock
        self._fetch = fetch or self._http_post
        self._lock = asyncio.Lock()
        self._token: str | None = None
        self._expires_at = 0.0

    @property
    def expires_at(self) -> float:
        return self._expires_at

    async def get(self) -> str:
        """取可用 token：命中缓存直接返回，否则（单飞）刷新。"""
        if self._token and self._clock() < self._expires_at:
            return self._token
        return await self._refresh(force=False)

    async def force_refresh(self) -> str:
        """网关 4004 / HTTP 401 时调用：强制换新，忽略缓存。"""
        return await self._refresh(force=True)

    async def _refresh(self, *, force: bool) -> str:
        async with self._lock:  # 并发单飞：4 个 worker 同时过期也只打一次 token 接口
            now = self._clock()
            if not force and self._token and now < self._expires_at:
                return self._token
            try:
                token = await self._fetch_token()
            except AuthError:
                raise
            except Exception as exc:  # 网络/解析异常：旧 token 还能用就继续用
                if self._token:
                    self._expires_at = now + 30  # 30s 后再试，避免刷新风暴
                    logger.warning("[token] 刷新失败，沿用旧 token（30s 后重试）：%r", exc)
                    return self._token
                raise TransientError(f"获取 access_token 失败：{exc!r}") from exc
            return token

    async def _fetch_token(self) -> str:
        urls = [self._url]
        if self._fallback_url and self._fallback_url != self._url:
            urls.append(self._fallback_url)

        last_exc: Exception | None = None
        for url in urls:
            try:
                data = await self._fetch(url, {"appId": self._app_id, "clientSecret": self._secret})
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
                last_exc = exc
                logger.warning("[token] %s 请求失败：%r", url, exc)
                continue

            if not isinstance(data, dict):
                last_exc = ValueError(f"响应不是 JSON 对象：{data!r}")
                continue

            token = data.get("access_token")
            if token:
                expires_in = _parse_expires_in(data.get("expires_in"))
                # 提前 60s 续期；下限 30s，防止 expires_in 本身很小时疯狂刷新
                ttl = max(expires_in - 60, 30)
                self._token = token
                self._expires_at = self._clock() + ttl
                logger.info("[token] 已刷新，%ss 后过期（提前 60s 续期）", expires_in)
                return token

            # 业务错误：HTTP 200 + code，换域名也没用，直接抛
            if data.get("code") is not None:
                raise AuthError(
                    f"获取 access_token 被拒绝：code={data.get('code')} message={data.get('message')}",
                    code=int(data["code"]),
                )
            last_exc = ValueError(f"响应缺少 access_token：{data!r}")

        raise TransientError(f"获取 access_token 网络失败：{last_exc!r}") from last_exc

    async def _http_post(self, url: str, payload: dict) -> dict:
        """默认实现：每次刷新新建短连接（2 小时一次，不值得为它维护长连接）。"""
        timeout = aiohttp.ClientTimeout(total=self._timeout)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload) as resp:
                return await resp.json(content_type=None)

    async def aclose(self) -> None:
        """对称接口：当前实现不持有长连接，无需释放。"""
        return None
