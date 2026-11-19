"""把读者点名的图表、指标和实验条件带到原文行列筛选阶段。"""

import re
import unicodedata

from paper_selection import paper_positions, separate_paper_clauses


_NUMBER = r"(?:[A-Za-z]?\d+(?:[.-]\d+)*|[IVXLCDM]+|[一二三四五六七八九十]+)"
_REF = re.compile(rf"(?:第\s*({_NUMBER})\s*[张个幅]?\s*(表|图)|"
                  rf"(表格?|插?图|(?<![a-z])tables?|(?<![a-z])fig(?:ures?)?\.?)\s*({_NUMBER}))(?![a-z0-9])", re.I)
_NEGATIVE = re.compile(r"排除|不要|不用|不看|跳过|去掉|不含|不包括|别提取|except\b|exclude\b", re.I)
_METRICS = {
    "top1": r"top[\s‐‑–-]*1(?!\d)", "top5": r"top[\s‐‑–-]*5(?!\d)",
    "accuracy": r"准确率|正确率|(?<![a-z])acc(?:uracy)?(?![a-z])",
    "error": r"错误率|误差率|(?<![a-z])error(?: rate)?(?![a-z])",
    "recall": r"召回率|(?<![a-z])recall(?![a-z])", "precision": r"查准率|(?<![a-z])precision(?![a-z])",
    "f1": r"(?<![a-z0-9])f[\s-]?1(?![a-z0-9])", "bleu": r"(?<![a-z])bleu(?![a-z])",
    "rouge": r"(?<![a-z])rouge(?![a-z])", "map": r"(?-i:\bmAP\b)|平均精度均值",
    "rmse": r"(?<![a-z])rmse(?![a-z])", "auc": r"(?<![a-z])(?:roc[- ])?auc(?![a-z])",
}
_METRICS = {name: re.compile(expr, re.I) for name, expr in _METRICS.items()}
_DIMENSIONS = re.compile(
    r"(?<![a-z0-9])(?:ImageNet(?:[- ]?(?:1k|21k|100|a|r|v2))?|CIFAR[- ]?(?:10|100)\+?|COCO(?:[- ]?20\d\d)?|"
    r"ResNet[- ]?\d+|ViT[-/][a-z0-9/-]+|DenseNet[- ]?\d*|RAG[- ](?:Token|Seq(?:uence)?)|"
    r"NQ|WQ|EN[-–](?:DE|FR)|BLEU[- ]\d|ROUGE[- ][12L])(?=$|[^a-z0-9])|"
    r"验证集|测试集|训练集|(?<![a-z])(?:validation|test|train)(?:\s+set)?(?![a-z])", re.I)
_NAMED = re.compile(r"(模型(?:行|名)?|数据集|指标|行名|列名)\s*(?:为|是|用|等于|[:：])?\s*"
                    r"(?:[「“\"]([^」”\"\n]{1,75})[」”\"]|([a-zA-Z][a-zA-Z0-9_./+-]*))")
_MODEL = re.compile(r"^(?:ResNet|ViT[-/]|DenseNet|RAG[- ])", re.I)
_IN_DATASET = re.compile(r"(?:在|使用|基于)\s*([A-Za-z][\w.-]*?(?:[-/][\w.-]+)*)(?=\s*(?:数据集|上|中|的))", re.ASCII)
_ROW = re.compile(r"(?:只要|只看|仅保留|仅取)\s*([A-Za-z][a-zA-Z0-9./_-]*)(?=\s*(?:模型|这[一条]*行|在))")
_NORMAL = {"验证集": "validation", "测试集": "test", "训练集": "train"}
_TOP = {"top1", "top5"}
_RATES = {"accuracy", "error"}


def _number(value):
    value = value.strip().casefold()
    digits = "零一二三四五六七八九"
    if all(c in digits + "十" for c in value):
        if "十" in value:
            a, b = value.split("十", 1)
            if len(a) <= 1 and len(b) <= 1:
                return str(10 * (digits.index(a) if a else 1) + (digits.index(b) if b else 0))
        if len(value) == 1:
            return str(digits.index(value))
    roman = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
    if all(c in roman for c in value):
        return str(sum(-roman[c] if i + 1 < len(value) and roman[c] < roman[value[i + 1]] else roman[c]
                       for i, c in enumerate(value)))
    return value


def refs(text):
    for m in _REF.finditer(text):
        name, num = (m[2], m[1]) if m[1] else (m[3], m[4])
        yield ("tables" if name.lower().startswith(("表", "tab")) else "figures"), _number(num)


def without_locators(text):
    return _REF.sub(" ", text)


def parse(text):
    found = {}
    for part in re.split(r"[，,；;。\n]|但是|不过|然后|(?=只要|只看|排除|不要|不看|跳过)", text):
        prefix = "omit_" if _NEGATIVE.search(part) else ""
        values = list(refs(part))
        values.extend(("metrics", key) for key, pattern in _METRICS.items() if pattern.search(part))
        for m in _DIMENSIONS.finditer(part):
            values.append(("rows" if _MODEL.match(m[0]) else "terms", m[0]))
        for m in _NAMED.finditer(part):
            group = {"数据集": "terms", "指标": "columns", "列名": "columns"}.get(m[1], "rows")
            word = m[2] or m[3]
            if not any(pattern.fullmatch(word) for pattern in _METRICS.values()):
                values.append((group, word))
        values.extend(("terms", m[1]) for m in _IN_DATASET.finditer(part) if not _MODEL.match(m[1]))
        values.extend(("rows", m[1]) for m in _ROW.finditer(part))
        for key, value in values:
            group = found.setdefault(prefix + key, [])
            if value not in group:
                group.append(value)
    return found


def for_papers(text, urls):
    clauses = separate_paper_clauses(text)
    if len(clauses) > 1 and urls:
        scoped = {}
        for clause in clauses:
            for i in paper_positions(without_locators(clause), len(urls)) or ():
                scope = parse(clause)
                if scope:
                    scoped[urls[i]] = scope
        return scoped
    scope = parse(text)
    return {"*": scope} if scope else {}


def normalize(value):
    value = unicodedata.normalize("NFKC", value).casefold().replace("–", "-")
    return re.sub(r"[\s‐‑_/-]+", "", value)


def _term(term, content):
    term = _NORMAL.get(term, re.sub(r"\s+set$", "", term, flags=re.I))
    # 数字结尾时保留边界，以免把 CIFAR-10 匹配为 CIFAR-100。
    return bool(re.search(re.escape(normalize(term)) + (r"(?![0-9])" if term[-1:].isdigit() else ""),
                          normalize(content)))


def requested_metrics(scope):
    wanted = set(scope.get("metrics", ()))
    return wanted - _RATES if wanted & _TOP else wanted


def metric_tags(item):
    header = item.get("column", item["label"])
    tags = {name for name, pattern in _METRICS.items() if pattern.search(header)}
    if tags & _TOP and not tags & _RATES:
        # “Top-1”有时表示错误率。原列未写明时，保留表题中对数值方向的说明。
        caption = item.get("caption", "")
        tags.update(rate for rate in _RATES if _METRICS[rate].search(caption))
    return tags


def matches(item, scope):
    if not scope or item["kind"] not in {"metric", "figure", "table"}:
        return True
    kind = item["kind"]
    group = "figures" if kind == "figure" else "tables"
    numbers = {n for g, n in refs(item.get("locator") or item.get("source", "")) if g == group}
    if scope.get(group) and not numbers.intersection(scope[group]) or numbers.intersection(scope.get("omit_" + group, ())):
        return False
    if kind != "metric":
        return True
    header = item.get("column", item["label"])
    # 表格指标以真实列名判定；正文数字没有行列绑定，不能充当指定实验单元格。
    if any(scope.get(k) for k in ("tables", "terms", "columns", "rows")) and not item.get("column"):
        return False
    actual = metric_tags(item)
    wanted = requested_metrics(scope)
    matched = actual & wanted
    rates = set(scope.get("metrics", ())) & _RATES
    if matched & _TOP and len(rates) == 1:
        if rates == {"accuracy"} and "error" in actual or rates == {"error"} and actual & _RATES != {"error"}:
            matched -= _TOP
    if wanted and not matched:
        return False
    if actual.intersection(scope.get("omit_metrics", ())):
        return False
    caption = item.get("caption", "")
    # 同一表题提及多个实验时，用数据行和列区分，不能把整段表题当每个数值的条件。
    if re.search(r"\band\b|\bvs\.?\b|[,，;；、&]|以及", caption, re.I):
        caption = ""
    context = " | ".join((item["label"], caption))
    for group, content in (("rows", item.get("row", "")), ("columns", header), ("terms", context)):
        if (not all(_term(term, content) for term in scope.get(group, ())) or
                any(_term(term, content) for term in scope.get("omit_" + group, ()))):
            return False
    return True


def describe(scope):
    parts = ["表 " + n for n in scope.get("tables", [])] + ["图 " + n for n in scope.get("figures", [])]
    labels = {"top1": "Top-1", "top5": "Top-5", "accuracy": "准确率", "error": "错误率"}
    parts += [labels.get(v, v.upper()) for v in scope.get("metrics", [])]
    parts += [v for group in ("columns", "rows", "terms") for v in scope.get(group, ())]
    return "、".join(parts) or "指定的筛选条件"
