"""统一日志：控制台 + logs/bot.log（按天轮转，保留 14 天）。

全项目共用这一份：入口、通道层（qq/）、agent、service、utils 都从这里取 `logger`。
格式沿用原 botpy 的日志格式串，排障习惯不用切换。
"""
import logging
import os
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

# 沿用原 botpy 的 DEFAULT_FILE_FORMAT：时间、级别、位置、正文
_FORMAT = "%(asctime)s\t[%(levelname)s]\t(%(filename)s:%(lineno)s)%(funcName)s\t%(message)s"


def _level_from_env() -> int:
    name = os.getenv("QQ_LOG_LEVEL", "INFO").upper()
    return getattr(logging, name, logging.INFO)


def _build_logger() -> logging.Logger:
    logger = logging.getLogger("qqbot")
    # 二次 import（测试里反复加载）不重复挂 handler，否则日志会成倍输出
    if logger.handlers:
        return logger

    level = _level_from_env()
    logger.setLevel(level)
    logger.propagate = False

    stream = logging.StreamHandler()
    stream.setFormatter(logging.Formatter(_FORMAT))
    logger.addHandler(stream)

    path = Path(os.getenv("QQ_LOG_FILE", "logs/bot.log"))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = TimedRotatingFileHandler(path, when="midnight", backupCount=14, encoding="utf-8")
        file_handler.setFormatter(logging.Formatter(_FORMAT))
        logger.addHandler(file_handler)
    except OSError as exc:
        # 日志写不了不该拦着机器人启动，降级为仅控制台
        logger.warning("日志文件无法创建，仅输出到控制台：%s（%s）", path, exc)
    return logger


logger = _build_logger()
