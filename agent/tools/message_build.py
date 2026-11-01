"""把数据库里的记录，翻译成模型能读懂的自然语言。

这一层的唯一职责：数据 → 文字。不查库、不调模型。
"""

def get_importance(importance: int, clarity: int):
    return f"重要性：{importance}, 清晰度：{clarity}, 内容："

def build_messages(memory_cards: list[dict], messages: list[dict],
                   history: dict, content: str, scenario_prompt: str) -> list[dict]:
        input_dict = []

        if scenario_prompt:
            input_dict.append({"role": "system", "content": scenario_prompt})

        for mc in memory_cards:
            input_dict.append({"role": "system", "content": get_importance(mc["importance"], mc["clarity"]) + mc["content"]})

        if history:
            input_dict.append({"role": "system", "content": f"历史内容回顾：{history["summary"]}"})

        for m in messages:
            input_dict.append({"role": m["role"], "content": m["content"]})

        input_dict.append({"role": "user", "content": content})

        return input_dict