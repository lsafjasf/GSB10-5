"""英文词形还原（lemma 候选生成），面向「术语检索」的保守实现。

设计依据
--------
术语一致性检查是高召回、低误报任务，词形处理遵循两条原则：

* 只处理「绝不会改变语义」的屈折后缀（复数、时态、比较级、进行时等），
  不处理派生（-er/-or「执行者」、-ness/-tion 名词化），因为派生词语义
  可能偏移，贸然匹配会误报。
* 先查不规则表，再套用极保守的规则后缀；规则带有最小词长与少量拼写
  约束，避免把 short -> shor、bus -> bu 之类截坏。

术语表中的每个英文词形都会生成「可能还原到它的表层形式集合」，建索引时
一并加入自动机；检查时只对表层词做一次相同的还原，因此规则天然对称，
不会出现「术语表能匹配、正文匹配不到」的不一致。

支持的形态（以术语词为基准）：
  - 完全一致：word -> word
  - 规则复数 / 第三人称单数：+s（cats, runs），+es（boxes, watches）
  - 以 -y 结尾的改写：ies（bodies）
  - 规则过去式 / 过去分词 / 现在分词：+ed, +ing（played, running）
  - 双写辅音：stopped, beginning
  - 比较级 / 最高级：+er, +est（harder, biggest 走双写/去 e 规则）
  - 不规则表：men/women/children/feet/teeth/geese/mice、be 的各人称、
    went/gone、did/done、had、made、took/taken、gave/given、came、
    saw/seen、knew/known、got、found、thought、said、sent、built、
    written/wrote、read（同形）、ran/run、ate/eaten、spoke/spoken、
    began/begun、drove/driven、sold、bought、brought、held、kept、
    left、led、met、paid、stood、understood、won、set、put、cut 等。
"""

import re

__all__ = ["surface_forms", "is_acronym"]

_IRREGULAR = {
    # lemma -> 可能出现的额外表层形式（不含 lemma 本身）
    "be": {"am", "is", "are", "was", "were", "been", "being"},
    "man": {"men"},
    "woman": {"women"},
    "child": {"children"},
    "foot": {"feet"},
    "tooth": {"teeth"},
    "goose": {"geese"},
    "mouse": {"mice"},
    "go": {"went", "gone", "going", "goes"},
    "do": {"did", "done", "doing", "does"},
    "have": {"had", "having", "has"},
    "make": {"made", "making", "makes"},
    "take": {"took", "taken", "taking", "takes"},
    "give": {"gave", "given", "giving", "gives"},
    "come": {"came", "coming", "comes"},
    "see": {"saw", "seen", "seeing", "sees"},
    "know": {"knew", "known", "knowing", "knows"},
    "get": {"got", "gotten", "getting", "gets"},
    "find": {"found", "finding", "finds"},
    "think": {"thought", "thinking", "thinks"},
    "say": {"said", "saying", "says"},
    "send": {"sent", "sending", "sends"},
    "build": {"built", "building", "builds"},
    "write": {"wrote", "written", "writing", "writes"},
    "read": {"reading", "reads"},  # read 过去式同形
    "run": {"ran", "running", "runs"},
    "eat": {"ate", "eaten", "eating", "eats"},
    "speak": {"spoke", "spoken", "speaking", "speaks"},
    "begin": {"began", "begun", "beginning", "begins"},
    "drive": {"drove", "driven", "driving", "drives"},
    "sell": {"sold", "selling", "sells"},
    "buy": {"bought", "buying", "buys"},
    "bring": {"brought", "bringing", "brings"},
    "hold": {"held", "holding", "holds"},
    "keep": {"kept", "keeping", "keeps"},
    "leave": {"left", "leaving", "leaves"},
    "lead": {"led", "leading", "leads"},
    "meet": {"met", "meeting", "meets"},
    "pay": {"paid", "paying", "pays"},
    "stand": {"stood", "standing", "stands"},
    "understand": {"understood", "understanding", "understands"},
    "win": {"won", "winning", "wins"},
    "set": {"setting", "sets"},
    "put": {"putting", "puts"},
    "cut": {"cutting", "cuts"},
    "person": {"people", "persons"},
    "index": {"indices", "indexes"},
    "appendix": {"appendices", "appendixes"},
    "matrix": {"matrices"},
    "crisis": {"crises"},
    "analysis": {"analyses"},
    "criterion": {"criteria"},
    "phenomenon": {"phenomena"},
    "datum": {"data"},
    "medium": {"media"},
    "shelf": {"shelves"},
    "leaf": {"leaves"},
    "life": {"lives"},
    "wife": {"wives"},
    "knife": {"knives"},
    "half": {"halves"},
    "wolf": {"wolves"},
    "good": {"better", "best"},
    "bad": {"worse", "worst"},
    "far": {"farther", "farthest", "further", "furthest"},
}

_VOWELS = frozenset("aeiou")
_CONSONANT = set("bcdfghjklmnpqrstvwxz")
# 英语中允许双写的末尾辅音
_DOUBLABLE = set("bdgklmnprt")

_TOKEN_RE = re.compile(r"[0-9a-zA-Z]+(?:[-./][0-9a-zA-Z]+)*")


def is_acronym(word):
    """大写缩写判定：全大写且长度 2-8（如 API、URL、HTTP）。

    单字母（如 I、A）不算缩写，避免与普通单词规则冲突；
    含小写字母的（如 iPhone）按普通词处理。
    """
    return 2 <= len(word) <= 8 and word.isalpha() and word.isupper()


def _rule_forms(w):
    """对小写规则词 w 生成屈折表层形式。"""
    forms = set()
    n = len(w)
    # 复数 / 三单
    forms.add(w + "s")
    if w.endswith(("s", "x", "z", "ch", "sh")) and n >= 3:
        forms.add(w + "es")
    if w.endswith("y") and n >= 3 and w[-2] not in _VOWELS:
        forms.add(w[:-1] + "ies")  # body -> bodies
    # -f/-fe -> -ves（常见词，只对短词开放；不规则表覆盖高频项）
    # 过去式 / 分词 / 进行时
    forms.add(w + "ed")
    forms.add(w + "ing")
    if w.endswith("e") and n >= 3:
        forms.add(w[:-1] + "ed")   # like -> liked
        forms.add(w[:-1] + "ing")  # make -> making（不规则表也会覆盖）
    # 重读闭音节双写：辅音+元音+可双写辅音，且前面不是元音（粗略保守）
    if (n >= 3 and w[-1] in _DOUBLABLE and w[-2] in _VOWELS
            and w[-3] not in _VOWELS):
        forms.add(w + w[-1] + "ed")   # stop -> stopped
        forms.add(w + w[-1] + "ing")  # run -> running（不规则表也覆盖）
    # 比较级 / 最高级
    forms.add(w + "er")
    forms.add(w + "est")
    if w.endswith("e") and n >= 3:
        forms.add(w[:-1] + "er")
        forms.add(w[:-1] + "est")
    if (n >= 3 and w[-1] in _DOUBLABLE and w[-2] in _VOWELS
            and w[-3] not in _VOWELS):
        forms.add(w + w[-1] + "er")
        forms.add(w + w[-1] + "est")
    if w.endswith("y") and n >= 3 and w[-2] not in _VOWELS:
        forms.add(w[:-1] + "ier")  # easy -> easier
        forms.add(w[:-1] + "iest")
    return forms


def surface_forms(word):
    """给定术语表中的英文词（原形），返回它在正文中可能出现的全部表层形式（小写）。"""
    forms = {word.lower()}
    forms |= _IRREGULAR.get(word.lower(), set())
    if is_acronym(word):
        forms.add(word.lower() + "s")  # API -> APIs
    if word.isalpha() and word.islower() and len(word) >= 2:
        forms |= _rule_forms(word.lower())
    # 含数字、连字符、点的 token（USB-C, IPv4, v2.0）只按字面匹配
    return forms


def tokens(text_lower):
    """从已小写的英文文本中抽取 token 序列 [(token, start, end), ...]。

    token 允许内部出现 - . /（USB-C、v2.0、TCP/IP），这三类字符不触发
    词边界歧义，整体作为一个匹配单元。
    """
    return [(m.group(0), m.start(), m.end()) for m in _TOKEN_RE.finditer(text_lower)]
