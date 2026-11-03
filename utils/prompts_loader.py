from utils.path_tool import get_abs_path
from botpy import logger
import config


def load_group_prompt():
    try:
        group_prompt_path = get_abs_path(config.GROUP_PROMPT_FILE)
    except KeyError as e:
        logger.error(f"[load_group_prompt] 配置行中，没有main_prompt_path配置项")
        raise e

    try:
        return open(group_prompt_path, "r", encoding=config.PROMPT_ENCODING).read()
    except Exception as e:
        logger.error(f"[load_group_prompt] 读取文件失败，请检查文件路径是否正确{str(e)}")
        raise e

def load_system_prompt():
    try:
        system_prompt_path = get_abs_path(config.SYSTEM_PROMPT_FILE)
    except KeyError as e:
        logger.error(f"[load_system_prompt] 配置行中，没有main_prompt_path配置项")
        raise e

    try:
        return open(system_prompt_path, "r", encoding=config.PROMPT_ENCODING).read()
    except Exception as e:
        logger.error(f"[load_system_prompt] 读取文件失败，请检查文件路径是否正确{str(e)}")
        raise e

def load_user_prompt():
    try:
        user_prompt_path = get_abs_path(config.USER_PROMPT_FILE)
    except KeyError as e:
        logger.error(f"[load_user_prompt] 配置行中，没有main_prompt_path配置项")
        raise e

    try:
        return open(user_prompt_path, "r", encoding=config.PROMPT_ENCODING).read()
    except Exception as e:
        logger.error(f"[load_user_prompt] 读取文件失败，请检查文件路径是否正确{str(e)}")
        raise e




group_prompt = load_group_prompt()
system_prompt = load_system_prompt()
user_prompt = load_user_prompt()
