"""从论文题名本身提取可追溯的简称，供收藏回看和清单筛选共用。"""

from dataclasses import dataclass
import re
import unicodedata


@dataclass(frozen=True)
class AliasMatch:
    kind: str
    evidence: str


_HYPHENS = "-‐‑−－"
_DASH_MAP = str.maketrans({char: "-" for char in _HYPHENS})
_WORD = re.compile(r"[^\W_]+")
_NAME = re.compile(r"[^\W_]+(?:[-‐‑−－][^\W_]+)*(?:[.．]\d+)*")
_KEY = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*(?:\.\d+)*")
_SEPARATOR = re.compile(r"[\s‐‑−－-]+")
_BOUNDARIES = set(
    "a an and are as at be by can for from how in into is it of on or our the their through "
    "to using via we what when where which with without you your".split()
)
_GENERIC = set(
    "all analysis approach approaches based improved improving method methods need new novel "
    "paper proposed recent revisiting study surveying survey towards".split()
)


def _normalized(text):
    return unicodedata.normalize("NFKC", text).translate(_DASH_MAP)


def _key(text):
    value = _normalized(text).casefold()
    if 3 <= len(value) <= 20 and _KEY.fullmatch(value) and sum(c.isalpha() for c in value) >= 2:
        return value
    return ""


def _explicit_names(title):
    names = list(_NAME.finditer(title))
    # 全大写的英文题名里，普通单词也会大写。只接受冒号前或括号内明确标出的名字。
    all_caps = len(names) > 1 and _normalized(title).isupper()
    for match in names:
        word = _normalized(match[0])
        key = _key(word)
        if not key or key in _BOUNDARIES or key in _GENERIC:
            continue
        if all_caps:
            before, after = title[:match.start()].rstrip(), title[match.end():].lstrip()
            if not (after.startswith((":", "：")) or
                    (before.endswith(("(", "（")) and after.startswith((")", "）")))):
                continue
        # Retrieval-Augmented 是两个普通词。大小写标识须出现在同一组成部分中。
        if (any(sum(c.isupper() for c in part) >= 2 for part in word.split("-"))
                or (any(c.isupper() for c in word) and any(c.isdigit() for c in word))):
            yield key, AliasMatch("explicit", match[0])


def _word_groups(title):
    group = []
    for match in _WORD.finditer(title):
        word = _normalized(match[0])
        word_valid = word.isascii() and word.isalpha() and word.casefold() not in _BOUNDARIES
        if group and (not word_valid or not _SEPARATOR.fullmatch(title[group[-1].end():match.start()])):
            yield group
            group = []
        if word_valid:
            group.append(match)
    if group:
        yield group


def _candidates(title):
    if not isinstance(title, str) or not title.strip():
        return {}
    aliases = dict(_explicit_names(title))
    for group in _word_groups(title):
        # 只取完整连续词组。任取子串或滑动窗口会让不同论文共享大量虚构简称。
        if not 3 <= len(group) <= 7:
            continue
        phrase = title[group[0].start():group[-1].end()]
        words = [_normalized(m[0]) for m in group]
        if any(len(word) < 2 or word.casefold() in _GENERIC or word.isupper() for word in words):
            continue
        if not (any(dash in phrase for dash in _HYPHENS) or all(word.istitle() for word in words)):
            continue
        key = "".join(word[0].lower() for word in words)
        if key not in _BOUNDARIES and key not in _GENERIC:
            aliases.setdefault(key, AliasMatch("initials", phrase))
    return aliases


def title_alias_terms(title) -> tuple[str, ...]:
    """返回去重的小写匹配键，例如 ('rag',)，便于清单执行相同的精确筛选。"""
    return tuple(_candidates(title))


def match_title_alias(query, title) -> AliasMatch | None:
    """匹配单个完整简称，并返回可以在原始题名中定位的证据。"""
    key = _key(query.strip()) if isinstance(query, str) else ""
    return _candidates(title).get(key) if key else None
