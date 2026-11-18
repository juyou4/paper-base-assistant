"""把追问中的口语答复对齐到本轮实际展示的选项。"""

import re


_ALIASES = {
    "ambiguous_intent": {
        "保存论文链接": r"(?:论文)?(?:链接|网址|地址)",
        "填写摘要": r"(?:中文|论文)?摘要|内容简介",
        "填写方法": r"(?:论文|研究)?方法(?:概述)?|技术路线",
        "换一篇论文": r"换(?:一篇|篇|另一篇)(?:论文)?",
    },
    "language": {"使用英文摘录": r"(?:保留|沿用|直接用|留)?(?:英文(?:原文|摘录)?|原文)"},
}
_ORDINALS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "两": 2}
_PREFIX = (r"(?:那(?:就)?)?(?:麻烦|请)?(?:帮我|我想|我选|我要)?(?:就|只|直接)?"
           r"(?:选(?:择)?|要|用|放(?:到|在|进)?|填(?:到|进|入)?|写(?:到|进|入)?|保存(?:到)?|留)?")
_SUFFIX = r"(?:这一列|这列|那一列|那列|这一栏|这栏|那栏|里)?(?:就好(?:了)?|就行(?:了)?|即可|吧|好了)?"


def resolve_choice(message, stage, choices):
    text = message.strip().rstrip("。.!！").strip()
    if text in choices:
        return text
    if "取消" in choices and re.fullmatch(
            r"(?:那就|那|先)?(?:算了|取消(?:这(?:次|批))?|不用了|暂时不填了|不写了|不填了|不弄了|别填了)"
            r"(?:吧|了)?(?:[，,]?谢谢)?", text):
        return "取消"
    if stage not in {"field", "language", "ambiguous_intent", "admin_field"}:
        return message
    if re.search(r"[?？]|还是|或者|要不要|可以吗|别|不要|暂不|先不", text):
        return message

    # 仅匹配完整答复，避免一段话提到多个字段时取错目标。
    aliases = _ALIASES.get(stage, {})
    for choice in choices:
        if choice == "取消":
            continue
        words = re.escape(choice)
        if choice in aliases:
            words += "|" + aliases[choice]
        if re.fullmatch(_PREFIX + r"\s*[「“\"]?(?:" + words + r")[」”\"]?\s*" + _SUFFIX, text):
            return choice
    if stage in {"field", "admin_field"}:
        match = re.fullmatch(_PREFIX + r"第([一二两三四五六七八]|[1-9]\d?)(?:个|项|列|栏)?" + _SUFFIX, text)
        if match:
            number = _ORDINALS.get(match[1]) or int(match[1])
            if number <= len(choices) and choices[number - 1] != "取消":
                return choices[number - 1]
    return message
