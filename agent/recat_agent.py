from langchain.agents import create_agent
from agent.tools.agent_tools import agent_tools
from botpy import logger
from agent.tools.middleware import agent_middleware
from model.factory import deepseek_chat_model
from agent.tools.message_build import build_messages
from utils.prompts_loader import system_prompt, group_prompt, user_prompt
import config
import session
from concurrent.futures import ThreadPoolExecutor

_compress_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="compress")
class ReactAgent:

    def __init__(self):
        self.agent = create_agent(
            model=deepseek_chat_model,
            # 系统提示词，定制角色性格等内容
            system_prompt=system_prompt,
            # AI 可调用的工具（完成网络搜索，相关内容查询等）
            tools=agent_tools,
            # 中间件，也就是AI调用工程中的钩子函数（完成中断响应）
            middleware=agent_middleware
        )


    def chat(self, content: str, group_openid: str, member_openid: str, message_id: str):
        # 转换信息
        scope, openid = session.scope_of(group_openid, member_openid)

        # 按场景选择提示词：群聊用 group_prompt，私聊用 user_prompt
        scenario_prompt = group_prompt if group_openid else user_prompt

        logger.info(f"[chat] 收到消息 content={content[:50]!r} scope={scope} openid={openid} msg_id={message_id}")

        # 压缩的历史内容
        history = session.latest_summary(scope, openid)
        final_msg_id = history["final_msg_id"] if history else 0

        # 最近的未压缩的内容（窗口与压缩阈值对齐，保证未压消息都可见）
        messages = session.recent_messages(scope, openid, final_msg_id, limit=config.RECENT_WINDOW)

        # 当前 + 全局 记忆中的内容
        memory_cards = session.load_cards(session.scope_number_of(group_openid, member_openid))

        logger.info(f"[chat] 上下文装配完成 summary_version={history['version'] if history else 0} "
                    f"水位线={final_msg_id} 原文={len(messages)}条 卡片={len(memory_cards)}条")

        # 拼接输入内容
        input_dict = build_messages(memory_cards, messages, history, content, scenario_prompt)

        # 这里展示完整的输入内容
        logger.info(f"[chat] 输入内容构建完成 input_len={len(input_dict)} input={input_dict}")

        # 全量消息的存储-用户
        msg_id = session.append_message(config.MSG_ROLE_USER, content, group_openid=group_openid, user_openid=member_openid, msg_id=f"{config.MSG_ROLE_USER}: {message_id}")

        # 设置当前会话的上下文，方便后续存储记忆卡片
        session.set_current_session(scope, openid, msg_id)

        try:
            state = self.agent.invoke({"messages": input_dict})
        except Exception:
            logger.exception(f"[chat] 模型调用失败 scope={scope} openid={openid} msg_id={message_id}")
            raise
        reply = state["messages"][-1].content
        logger.info(f"[chat] 模型回复完成 steps={len(state['messages'])} " f"len={len(reply)} reply={reply[:50]!r}")

        # 全量消息的存储-ai
        session.append_message(config.MSG_ROLE_ASSISTANT, reply, group_openid=group_openid, user_openid=member_openid, msg_id=f"{config.MSG_ROLE_ASSISTANT}: {message_id}")

        # 上下文压缩：把溢出的老消息滚动进摘要（未达阈值时返回 False，属正常）
        _compress_pool.submit(session.compress_context, scope, openid)

        return reply


agent = ReactAgent()