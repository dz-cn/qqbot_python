from typing import Callable
from langchain.agents.middleware import wrap_tool_call
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command
from botpy import logger


@wrap_tool_call
def monitor_tool(
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command]
) -> ToolMessage | Command:
    """
    工具执行的监控
    :return:
    """
    logger.info(f"[tool monitor] 执行工具：{request.tool_call['name']}")
    logger.info(f"[tool monitor] 传入参数：{request.tool_call['args']}")

    try:
        res = handler(request)
        logger.info(f"[tool monitor] 工具执行结果：{res}")
        if request.tool_call['name'] == "fill_context_for_report":
            request.runtime.context["report"] = True
        return res
    except Exception as e:
        logger.error(f"[tool monitor] 工具执行异常：{e}")
        raise e


agent_middleware = [monitor_tool]