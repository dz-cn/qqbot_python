"""会话身份的折算：把「群/人」的 openid 组合统一成业务层要用的形态。
与具体数据表无关，故放在 utils，供入口层直接调用。"""
import config


def scope_of(group_openid: str | None, user_openid: str) -> tuple[str, str]:
    """把「群/人」折算成 (scope, openid)：有 group_openid 就是群聊，否则是私聊。
    业务层不需要再自行判断群/私聊，直接用返回的 scope 与 openid 定位会话。
    """
    if group_openid:
        return config.SCOPE_GROUP, group_openid
    return config.SCOPE_PRIVATE, user_openid


def scope_number_of(group_openid: str | None, user_openid: str) -> list[str]:
    """把「群/人」折算成读卡片要用的 openid 列表：全局桶 + 当前会话桶（群或私聊本人）。"""
    if group_openid:
        return [config.SCOPE_GLOBAL, group_openid]
    return [config.SCOPE_GLOBAL, user_openid]
