"""入站消息的分发：去重 → 队列 → 会话串行 → 线程池执行 agent.chat → 发送回复。

三个必须在这里解决的问题：
1. **事件循环不能被模型推理阻塞**：`agent.chat` 是同步函数（RAG + 精排 + LLM，远端可达数十秒），
   直接 await 会让心跳发不出去、网关断开。因此统一丢进 `asyncio.to_thread`。
2. **平台会重复推送**：相同 msg_id 可能重复到达（官方文档明确），必须挡在模型之前，
   否则同一句话会被回复两次，且白跑一次推理。
3. **同一会话必须串行**：`agent.chat` 内部是「读上下文 → 写消息 → 写卡片」的读改写序列，
   同会话并发会打乱 `chat_message` 顺序与摘要水位线。
"""
from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from typing import Callable

from qq.api import OpenAPIClient, split_text
from qq.errors import PassiveExpiredError, QQError, SendRejectedError
from qq.events import IncomingMessage
from qq.mention import resolve as resolve_mentions, strip_mentions
from utils.log_util import logger

# 与现有 agent.chat(content, group_openid, member_openid, message_id) 完全一致
Handler = Callable[[str, str | None, str, str], str]

# markdown 特有的拒绝码：命中时把 @ 标签剥掉改发纯文本，比整条丢掉好
_MARKDOWN_REJECTED = {22006, 304036, 40034008, 40034009, 40034010, 40034011, 40034124, 40034127, 50037, 50056}


class Dispatcher:
    def __init__(self, handler: Handler, sender: OpenAPIClient, *,
                 workers: int = 4, queue_max: int = 20,
                 passive_ttl: float = 300.0, dedupe_ttl: float = 600.0,
                 dedupe_capacity: int = 5000, max_chars: int = 1500, max_parts: int = 3,
                 no_reply_marker: str = "[[NO_REPLY]]", clock: Callable[[], float] = time.monotonic):
        self._handler = handler
        self._sender = sender
        self._workers = max(1, int(workers))
        self._queue_max = max(1, int(queue_max))
        self._passive_ttl = float(passive_ttl)
        self._dedupe_ttl = float(dedupe_ttl)
        self._dedupe_capacity = max(1, int(dedupe_capacity))
        self._max_chars = max(50, int(max_chars))
        self._max_parts = max(1, int(max_parts))
        self._no_reply_marker = no_reply_marker
        self._clock = clock

        self._queue: asyncio.Queue[tuple[IncomingMessage, float] | None] = asyncio.Queue(maxsize=self._queue_max)
        self._seen: OrderedDict[str, float] = OrderedDict()
        self._locks: dict[str, asyncio.Lock] = {}
        self._tasks: list[asyncio.Task] = []
        self._closed = False

    # ------------------------------------------------------------------ 生命周期
    def start(self) -> None:
        """启动 worker。放在单独的 start 里，是为了先在事件循环内创建队列与任务。"""
        if self._tasks:
            return
        for index in range(self._workers):
            self._tasks.append(asyncio.create_task(self._worker(index), name=f"qq-worker-{index}"))
        logger.info("[dispatch] 已启动 %d 个处理线程（队列上限 %d）", self._workers, self._queue_max)

    async def aclose(self, timeout: float = 10.0) -> None:
        """停止收新消息，等待在途任务，最多等 timeout 秒。"""
        if self._closed:
            return
        self._closed = True
        for _ in self._tasks:
            try:
                self._queue.put_nowait(None)
            except asyncio.QueueFull:
                break
        if self._tasks:
            _, pending = await asyncio.wait(self._tasks, timeout=timeout)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        self._tasks.clear()

    # ------------------------------------------------------------------ 入口
    async def submit(self, msg: IncomingMessage) -> bool:
        """网关收到事件后调用：去重 + 入队，永不阻塞（队列满则丢弃）。"""
        if self._closed:
            return False
        if not msg.content or not msg.content.strip():
            logger.debug("[dispatch] 空内容消息，忽略 msg_id=%s", msg.msg_id)
            return False
        if self._is_duplicate(msg.msg_id):
            logger.info("[dispatch] 重复推送，已丢弃 msg_id=%s", msg.msg_id)
            return False
        try:
            self._queue.put_nowait((msg, self._clock()))
        except asyncio.QueueFull:
            logger.warning("[dispatch] 队列已满（%d），丢弃消息 msg_id=%s", self._queue_max, msg.msg_id)
            return False
        self._mark_seen(msg.msg_id)
        return True

    @property
    def pending(self) -> int:
        return self._queue.qsize()

    # ------------------------------------------------------------------ worker
    async def _worker(self, index: int) -> None:
        while True:
            item = await self._queue.get()
            try:
                if item is None:
                    return
                msg, received_at = item
                await self._handle(msg, received_at)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("[dispatch] worker-%d 处理异常", index)
            finally:
                self._queue.task_done()

    async def _handle(self, msg: IncomingMessage, received_at: float) -> None:
        key = msg.group_openid or msg.member_openid
        lock = self._locks.setdefault(key, asyncio.Lock())

        async with lock:
            waited = self._clock() - received_at
            if waited > self._passive_ttl:
                logger.warning("[dispatch] 排队 %.1fs 已超被动回复窗口，放弃 msg_id=%s", waited, msg.msg_id)
                return

            logger.info("[dispatch] 收到 %s scope=%s openid=%s msg_id=%s",
                        "群聊" if msg.is_group else "单聊",
                        msg.group_openid or "private",
                        _mask(msg.member_openid), msg.msg_id)

            try:
                # 同步的 agent.chat 必须进线程：模型推理期间事件循环要留给心跳
                reply = await asyncio.to_thread(
                    self._handler, msg.content, msg.group_openid, msg.member_openid, msg.msg_id
                )
            except Exception:
                logger.exception("[dispatch] agent.chat 失败 msg_id=%s", msg.msg_id)
                return

            if not reply or not str(reply).strip():
                logger.warning("[dispatch] 模型返回空回复，不发送 msg_id=%s", msg.msg_id)
                return
            if str(reply).strip() == self._no_reply_marker:
                logger.info("[dispatch] 模型拒绝回复（%s） msg_id=%s", self._no_reply_marker, msg.msg_id)
                return

            await self._send(msg, str(reply), received_at)

        self._prune_locks()

    async def _send(self, msg: IncomingMessage, reply: str, received_at: float) -> None:
        elapsed = self._clock() - received_at
        if elapsed > self._passive_ttl:
            logger.warning("[dispatch] 生成耗时 %.1fs 超过被动回复窗口，放弃发送 msg_id=%s", elapsed, msg.msg_id)
            return

        # @ 只有「群聊 + markdown」能真正渲染；单聊传空 openid，模型写的标签会被剥掉只留正文
        text, has_mention = resolve_mentions(reply, msg.member_openid if msg.is_group else "")
        if not text:
            logger.warning("[dispatch] 清洗 @ 标记后无正文，不发送 msg_id=%s", msg.msg_id)
            return

        if has_mention:
            await self._send_group_with_mention(msg, text)
            return

        parts = split_text(text, self._max_chars, self._max_parts)
        if not parts:
            return

        for index, part in enumerate(parts, start=1):
            try:
                if msg.is_group:
                    await self._sender.send_group_text(
                        msg.group_openid, part, msg_id=msg.msg_id, msg_seq=index
                    )
                else:
                    await self._sender.send_c2c_markdown(
                        msg.member_openid, part, msg_id=msg.msg_id, msg_seq=index
                    )
            except SendRejectedError as exc:
                # 单聊的 markdown 若被平台拒（权限/参数），退回纯文本，别整条丢掉
                if not msg.is_group and exc.code in _MARKDOWN_REJECTED:
                    logger.warning("[dispatch] 单聊 markdown 被拒（%s），回退纯文本 msg_id=%s", exc, msg.msg_id)
                    try:
                        await self._sender.send_c2c_text(
                            msg.member_openid, part, msg_id=msg.msg_id, msg_seq=index
                        )
                    except Exception:
                        logger.exception("[dispatch] 回退纯文本仍失败 msg_id=%s", msg.msg_id)
                        return
                else:
                    logger.error("[dispatch] 发送失败 msg_id=%s seq=%d：%s", msg.msg_id, index, exc)
                    return
            except PassiveExpiredError as exc:
                logger.warning("[dispatch] 被动回复已失效，停止发送 msg_id=%s：%s", msg.msg_id, exc)
                return
            except QQError as exc:
                logger.error("[dispatch] 发送失败 msg_id=%s seq=%d：%s", msg.msg_id, index, exc)
                return
            except Exception:
                logger.exception("[dispatch] 发送异常 msg_id=%s seq=%d", msg.msg_id, index)
                return
            logger.info("[dispatch] 已回复 msg_id=%s seq=%d/%d len=%d", msg.msg_id, index, len(parts), len(part))

    async def _send_group_with_mention(self, msg: IncomingMessage, text: str) -> None:
        """群聊 @ 当前发言人：官方只支持 markdown 消息，且不拆条（拆条会把标签切断）。

        markdown 被平台拒绝时（权限/参数类错误码），退回纯文本并把 @ 标签剥掉：
        宁可没有 @，也不能让整条回复发不出去。
        """
        content = text if len(text) <= self._max_chars else text[: self._max_chars - 1] + "…"
        try:
            await self._sender.send_group_markdown(
                msg.group_openid, content, msg_id=msg.msg_id, msg_seq=1
            )
        except SendRejectedError as exc:
            if exc.code not in _MARKDOWN_REJECTED:
                logger.error("[dispatch] 含 @ 的 markdown 发送失败 msg_id=%s：%s", msg.msg_id, exc)
                return
            plain = strip_mentions(content)
            logger.warning("[dispatch] markdown 被平台拒绝（%s），回退纯文本 msg_id=%s", exc, msg.msg_id)
            if not plain:
                return
            try:
                await self._sender.send_group_text(
                    msg.group_openid, plain, msg_id=msg.msg_id, msg_seq=1
                )
            except Exception:
                logger.exception("[dispatch] 回退纯文本仍失败 msg_id=%s", msg.msg_id)
                return
            logger.info("[dispatch] 已回复（纯文本降级） msg_id=%s len=%d", msg.msg_id, len(plain))
            return
        except PassiveExpiredError as exc:
            logger.warning("[dispatch] 被动回复已失效，停止发送 msg_id=%s：%s", msg.msg_id, exc)
            return
        except QQError as exc:
            logger.error("[dispatch] 发送失败 msg_id=%s：%s", msg.msg_id, exc)
            return
        except Exception:
            logger.exception("[dispatch] 发送异常 msg_id=%s", msg.msg_id)
            return
        logger.info("[dispatch] 已回复（含 @） msg_id=%s len=%d", msg.msg_id, len(content))

    # ------------------------------------------------------------------ 去重
    def _is_duplicate(self, msg_id: str) -> bool:
        """只判断不登记：入队成功才登记，避免"队列满丢弃"被误判成已处理。"""
        self._prune_seen()
        return msg_id in self._seen

    def _mark_seen(self, msg_id: str) -> None:
        self._seen[msg_id] = self._clock()

    def _prune_seen(self) -> None:
        """按 TTL 与容量清理，内存不会随运行时长无限增长。"""
        now = self._clock()
        while self._seen:
            oldest_id, oldest_at = next(iter(self._seen.items()))
            if now - oldest_at <= self._dedupe_ttl and len(self._seen) < self._dedupe_capacity:
                break
            self._seen.popitem(last=False)

    def _prune_locks(self) -> None:
        """会话锁只在有并发竞争时才有意义：空闲的锁清掉，避免按 openid 无限增长。"""
        if len(self._locks) <= 256:
            return
        for key in [k for k, lock in self._locks.items() if not lock.locked()]:
            self._locks.pop(key, None)


def _mask(openid: str) -> str:
    """日志里 openid 只留前 6 位：排障够用，避免敏感标识整段落盘。"""
    return (openid[:6] + "…") if openid and len(openid) > 6 else (openid or "")
