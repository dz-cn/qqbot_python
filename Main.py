import asyncio
from utils.log_util import logger


async def _run_native() -> None:
    """通道入口：WebSocket 收消息 + OpenAPI 发消息。"""
    # 延迟 import：只有真正启动机器人才加载 langchain/chromadb 这一大坨
    from agent.recat_agent import agent
    import qq

    logger.info("[main] 启动通道：WebSocket 收 + OpenAPI 发")
    await qq.run_bot(agent.chat)


def main() -> None:
    from db.db import init_db

    init_db()
    try:
        asyncio.run(_run_native())
    except KeyboardInterrupt:
        logger.info("[main] 收到退出信号，已停止")


if __name__ == "__main__":
    main()
