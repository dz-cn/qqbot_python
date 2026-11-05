from langchain.agents import create_agent
from agent.tools.agent_tools import agent_tools
from utils.log_util import logger
from agent.tools.middleware import agent_middleware
from model.factory import chat_model
from agent.tools.message_build import build_messages
import utils
from concurrent.futures import ThreadPoolExecutor
import service.rag as retrieval
import service
import config


_compress_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="compress")
class ReactAgent:

    def __init__(self):
        self.agent = create_agent(
            model=chat_model,
            # 系统提示词，定制角色性格等内容
            system_prompt=utils.system_prompt,
            # AI 可调用的工具（完成网络搜索，相关内容查询等）
            tools=agent_tools,
            # 中间件，也就是AI调用工程中的钩子函数（完成中断响应）
            middleware=agent_middleware
        )


    def chat(self, content: str, group_openid: str, member_openid: str, message_id: str):
        logger.info(f"----------------------------------------【对话分隔符】---------------------------------------------")
        # 转换信息
        scope, openid = utils.scope_of(group_openid, member_openid)

        # RAG + 精确匹配 + 知识库 的混合搜索
        passage = retrieval.retrieve(content, [openid])

        # 按场景选择提示词：群聊用 group_prompt，私聊用 user_prompt
        scenario_prompt = utils.group_prompt if group_openid else utils.user_prompt

        logger.info(f"[chat] 收到消息 content={content[:50]!r} scope={scope} openid={openid} msg_id={message_id}")

        # 压缩的历史内容
        history = service.latest_summary(scope, openid)
        final_msg_id = history["final_msg_id"] if history else 0

        # 最近的未压缩的内容（窗口与压缩阈值对齐，保证未压消息都可见）
        messages = service.recent_messages(scope, openid, final_msg_id, limit=config.RECENT_WINDOW)

        # 当前 + 全局 记忆中的内容
        memory_cards = service.load_cards(utils.scope_number_of(group_openid, member_openid))

        logger.info(f"[chat] 上下文装配完成 summary_version={history['version'] if history else 0} "
                    f"水位线={final_msg_id} 原文={len(messages)}条 卡片={len(memory_cards)}条")

        # 拼接输入内容（群聊带上发言人别名，模型才能分清谁在说话）
        input_dict = build_messages(memory_cards, messages, history, content, scenario_prompt, passage,
                                    group_openid=group_openid, current_openid=member_openid)

        # 这里展示完整的输入内容
        logger.info(f"[chat] 输入内容构建完成 input_len={len(input_dict)} input={input_dict}")

        # 全量消息的存储-用户
        msg_id = service.append_message(config.MSG_ROLE_USER, content, group_openid=group_openid, user_openid=member_openid, msg_id=f"{config.MSG_ROLE_USER}: {message_id}")

        # 设置当前会话的上下文，方便后续存储记忆卡片
        utils.set_current_session(scope, openid, msg_id)

        try:
            state = self.agent.invoke({"messages": input_dict})
        except Exception:
            logger.exception(f"[chat] 模型调用失败 scope={scope} openid={openid} msg_id={message_id}")
            raise
        reply = state["messages"][-1].content
        logger.info(f"[chat] 模型回复完成 steps={len(state['messages'])} " f"len={len(reply)} reply={reply[:50]!r}")

        # 全量消息的存储-ai
        service.append_message(config.MSG_ROLE_ASSISTANT, reply, group_openid=group_openid, user_openid=member_openid, msg_id=f"{config.MSG_ROLE_ASSISTANT}: {message_id}")

        # 上下文压缩：把溢出的老消息滚动进摘要（未达阈值时返回 False，属正常）
        _compress_pool.submit(service.compress_context, scope, openid)

        # 向检索向量库中插入内容
        _compress_pool.submit(retrieval.ingest, content, f"{config.RAG_MESSAGE_PROXY}{message_id}", openid, scope)

        return reply


agent = ReactAgent()