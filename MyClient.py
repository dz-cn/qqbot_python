import botpy
from botpy.message import GroupMessage, C2CMessage
from agent.recat_agent import agent
from botpy import logger


class MyClient(botpy.Client):
    async def _bot_login(self, token):
        await super()._bot_login(token)
        self._install_group_message_parser()

    def _install_group_message_parser(self):
        """把 GROUP_MESSAGE_CREATE 直接写进事件解析字典, 不依赖 parse_ 前缀扫描"""
        conn = self._connection
        state = conn.state
        api = self.api

        def _parse(payload):
            conn.dispatch(
                "group_message_create",
                GroupMessage(api, payload.get("id", None), payload.get("d", {})),
            )

        holders = []
        for obj in (conn, state):
            for attr in ("parser", "parsers"):
                d = getattr(obj, attr, None)
                if isinstance(d, dict):
                    d.setdefault("group_message_create", _parse)
                    holders.append(f"{type(obj).__name__}.{attr}")
        logger.info(f"[patch] 已写入的字典: {holders or '未找到 dict 属性'}")

    async def on_ready(self):
        logger.info(f"[botpy] on_ready: {self.robot.name} ({self.robot.id})")
        d = getattr(self._connection, "parser", None)
        ok = isinstance(d, dict) and "group_message_create" in d
        logger.info(f"[patch] 全量消息解析已就绪: {ok}")
        if not ok:
            logger.info(f"[patch] 当前 parser 类型={type(d)}, keys={sorted(d) if isinstance(d, dict) else d}")

    async def on_group_message_create(self, message: GroupMessage):
        reply = agent.chat(message.content, message.group_openid, message.author.member_openid, message.id)
        if self.can_reply(reply):
            await message.reply(content=reply)

    async def on_group_at_message_create(self, message: GroupMessage):
        reply = agent.chat(message.content, message.group_openid, message.author.member_openid, message.id)
        if self.can_reply(reply):
            await message.reply(content=reply)

    async def on_c2c_message_create(self, message: C2CMessage):
        reply = agent.chat(message.content, None, message.author.user_openid, message.id)
        if self.can_reply(reply):
            await message.reply(content=reply)

    def can_reply(self, reply: str):
        if reply == "[[NO_REPLY]]":
            logger.info("[chat] 模型拒绝判断不予回复")
            return False
        return True
