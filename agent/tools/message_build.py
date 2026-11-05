"""把数据库里的记录，翻译成模型能读懂的自然语言。

这一层的唯一职责：数据 → 文字。不查库、不调模型。
"""
import config
from service.rag.retrieve_service import Passage
from utils.speaker_util import label_message


def _scope_label(card_type: str) -> str:
    """卡片作用域标签：全局桶→「全局」，其余(群/私聊)→「本会话」，供模型更新时对齐 is_global。"""
    return "全局" if card_type == config.SCOPE_GLOBAL else "本会话"


def format_card(mc: dict) -> str:
    """渲染一张卡片：带标题与作用域，让模型判断该复用旧标题更新、还是新建，并据作用域选对 is_global。"""
    return (f"[标题：{mc['title']}｜{_scope_label(mc['type'])}] "
            f"重要性：{mc['importance']}, 清晰度：{mc['clarity']}, 内容：{mc['content']}")


# 预注入资料的话术：与「工具结果」刻意相反 —— 工具是模型主动问，落空就是真没有；
# 预注入是工程每条都查，落空可能只是门没触发，这时说「未收录」就是假阴性。
_FETCH_LENS = ("【知识库里可能相关的资料】以下是系统自动检索到的原文摘录，"
               "**可能与本条消息无关，也可能只是节选**。\n"
               "采信前先确认它确实在讲这件事；无关或不够用时按你正常的理解回应，"
               "不要因为资料里没写就告诉用户「知识库未收录」。")


def blocks(passages: list[Passage]) -> str:
    """逐条渲染：编号 + 出处 + 是否节选。
    - 编号是必需的：没有它，模型无法在回答里指认「资料2 讲的是另一件事」；
    - truncated 一定要兑现成文字，否则模型把 ±2 块的窗口当成整节全文复述；
    - 来源原样输出 parent_id（形如「手册.md::001」），不解析成「《手册.md》第 1 节」：
      '::' 是 store_service 的私有约定，扩散到展示层就得跟着改一处。"""
    blocks = []
    for index, p in enumerate(passages, start=1):
        head = f"资料{index}（来源 {p.parent_id}）"
        if p.truncated:
            head += "，节选，未包含该节全部内容"
        blocks.append(f"{head}\n{p.text}")
    return "\n\n".join(blocks)


def build_messages(memory_cards: list[dict], messages: list[dict],
                   history: dict, content: str, scenario_prompt: str,
                   passages: list[Passage], *,
                   group_openid: str | None = None,
                   current_openid: str | None = None) -> list[dict]:
        """装配进模型的上下文。passages 故意是必填：
        可选参数会让「忘了传」变成静默降级 —— 检索照常执行、日志照常打，只是资料没进 prompt。

        群聊里 role=user 的每条可能来自不同人，因此带上 `[群友xxxx]` 稳定别名：
        同一个人始终同一别名（openid 短哈希），模型才能分清「谁之前说过什么」。
        私聊只有一个对话者，role 足够，不加前缀以免干扰语气。
        """
        input_dict = []

        if scenario_prompt:
            input_dict.append({"role": "system", "content": scenario_prompt})

        for mc in memory_cards:
            input_dict.append({"role": "system", "content": format_card(mc)})

        if passages:
            input_dict.append({"role": "system", "content": f"{_FETCH_LENS}\n\n{blocks(passages)}"})

        if history:
            input_dict.append({"role": "system", "content": f"历史内容回顾：{history["summary"]}"})

        for m in messages:
            body = m["content"]
            if group_openid and m["role"] == config.MSG_ROLE_USER:
                body = label_message(body, m.get("user_openid"))
            input_dict.append({"role": m["role"], "content": body})

        # 本条消息也标上别名：模型据此知道"现在说话的是谁"，才能把历史和当下对上
        current = label_message(content, current_openid) if group_openid else content
        input_dict.append({"role": "user", "content": current})

        return input_dict