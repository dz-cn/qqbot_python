from langchain_core.tools import tool
from botpy import logger
import config
import session

@tool(description=
      """仅当出现值得长期记住的稳定事实时，保存或更新一张记忆卡片。

    该记：
    1. 用户身份、长期偏好、明确说出的重要设定、对既有记忆的更正。
    2. 不该记：闲聊、一次性提问、临时情绪、你自己的猜测。
    3. 只记稳定事实：身份、长期偏好、明确说过的设定、对旧记忆的更正；闲聊、一次性提问、临时情绪不要记。
    4. 更新优先于新建：若已有同义卡片，复用它的 title 再写，让旧卡片被覆盖更新，别造重复卡片。
    5. 诚实打分：importance/clarity 按实际判断给 1~5，拿不准就压低 clarity，别虚高。
    6. 被纠正时：默默用 memory_card 更新对应卡片，不要把"我记住了""以谁为准"这类话对用户说出来。
    7. 克制：一轮最多记 1~2 条，宁缺毋滥，记多了反而污染上下文。

    作用范围（is_global）：
    - 跨场景都成立的长期事实（用户身份、常年偏好、明确的原则性设定）→ is_global=true，写入全局记忆桶。
    - 只在当前群/当前私聊才成立的信息（"这个群在聊项目X""他今天心情不好"）→ is_global=false（默认），沿用当前会话身份。

    参数：
    - title: 极短主题标签（如"用户职业""忌口"）。同一含义请复用旧标题，以便更新而非新建。
    - content: 一句话事实描述。
    - importance: 1~5，5=身份/长期设定，3=一般事实，1=可忽略（这种别存）。
    - clarity: 1~5，5=用户明确说过，3=对话中推断，1=只是猜测（这种别存）。
    - is_global: 该事实是否跨场景通用。true=写全局；false=只在当前会话生效（默认）。
    """)
def memory_card(title: str, content: str, importance: int = 3, clarity: int = 3,
                is_global: bool = False) -> str:

    ctx = session.get_context()
    if is_global:
        # 全局记忆：openid 固定用 "global"，与 scope_number_of 读侧 IN 列表里的 "global" 对齐，才能被加载回来
        scope, openid = config.SCOPE_GLOBAL, config.GLOBAL_OPENID
    else:
        # 局部记忆：沿用当前会话身份，写在 private/group 对应的真实 openid 桶里
        scope, openid = ctx.scope, ctx.openid

    saved = session.insert_card(scope, openid, title, content,
                                importance=importance, clarity=clarity, source_msg_id=f"{ctx.message_id}")
    if not saved:
        logger.warning(f"[memory_card] 卡片标题或内容为空，未写入 title={title!r}")
    return f"已保存记忆卡片：{title}" if saved else "卡片内容为空，未保存"



agent_tools = [memory_card]