"""
为整个工程提供统一的绝对路径
"""
from pathlib import Path


def get_project_root_path() -> Path:
    """
    获取项目根目录
    """
    current_file_path = Path(__file__).resolve()
    return current_file_path.parent.parent


def get_abs_path(relative_path: str | Path) -> Path:
    """
    传入相对路径，得到绝对路径
    :param relative_path: 相对路径
    :return: 绝对路径
    """
    return get_project_root_path() / relative_path