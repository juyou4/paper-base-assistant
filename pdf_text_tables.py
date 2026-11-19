"""从表题上方连续、对齐的数字列恢复小型无框表。"""

import re


NUMBER = re.compile(r"[+−–-]?\d+(?:\.\d+)?%?")
CAPTION = re.compile(r"^(Table|表)\s+([A-Z\d]+)[.:：]\s+(.+)", re.I)


def _lines(words):
    lines = []
    for word in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if not lines or word["top"] - lines[-1][0]["top"] > 2.2:
            lines.append([])
        lines[-1].append(word)
    for line in lines:
        line.sort(key=lambda w: w["x0"])
    return lines


def _center(word):
    return (word["x0"] + word["x1"]) / 2


def _numeric_row(words):
    split = len(words)
    while split and NUMBER.fullmatch(words[split - 1]["text"]):
        split -= 1
    if not 1 <= split <= 6 or not 2 <= len(words) - split <= 8:
        return None
    label = " ".join(w["text"] for w in words[:split])
    if len(label) > 48 or not any(c.isalpha() for c in label):
        return None
    return [label, *(w["text"] for w in words[split:])], [_center(w) for w in words[split:]]


def _above(lines, caption_words):
    # 只使用表题本栏的横向区域，相邻栏正文不能成为行名或列头。
    left, right = caption_words[0]["x0"] - 3, caption_words[-1]["x1"] + 3
    last_y = caption_words[0]["top"]
    rows, centers = [], []
    for line in reversed(lines):
        words = [w for w in line if left <= w["x0"] and w["x1"] <= right]
        if not words:
            continue
        if last_y - words[0]["top"] > 22 or len(rows) > 16:
            break
        last_y = words[0]["top"]
        row = _numeric_row(words)
        if row:
            values, positions = row
            if centers and (len(centers) != len(positions) or
                            any(abs(x - y) > 4 for x, y in zip(centers, positions))):
                break
            centers = positions
            rows.append(values)
            continue
        if len(rows) < 2 or len(words) != len(centers):
            break
        headers = [w["text"] for w in words]
        if (any(abs(_center(w) - x) > 6 for w, x in zip(words, centers))
                or any(not any(c.isalpha() for c in h) for h in headers)
                or len(set(headers)) != len(headers) or len({row[0] for row in rows}) != len(rows)):
            break
        return [["", *headers], *reversed(rows)]
    return None


def captioned_numeric_tables(page):
    lines = _lines(page.extract_words(x_tolerance=0.8, y_tolerance=2))
    for index, line in enumerate(lines):
        for start, word in enumerate(line):
            if word["text"].lower() not in {"table", "表"}:
                continue
            caption_words = [word]
            for following in line[start + 1:]:
                if following["x0"] - caption_words[-1]["x1"] > 17:
                    break
                caption_words.append(following)
            match = CAPTION.match(" ".join(w["text"] for w in caption_words))
            if match and (cells := _above(lines[:index], caption_words)):
                yield match[1] + " " + match[2], match[3], cells
