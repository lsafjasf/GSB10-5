"""术语库加载与索引。

术语库为 JSON，顶层 {"entries": [...]}；也接受顶层就是列表。
条目结构（字段释义见 README「术语库格式」）：

{
  "id": "E001",
  "source": "file",
  "allow_multiple": false,
  "canonical": "文件",
  "synonyms": ["档案"],
  "forbidden": ["档"],
  "senses": [ ... 多义项，与 canonical/synonyms/forbidden/cues 互斥 ... ]
}

义项结构：
{"id": "file.computer", "canoninal": "文件", "synonyms": [], "forbidden": [],
 "cues": {"positive": ["目录", "delete", "directory"], "negative": [...]}}
单义项条目在加载时被规整成唯一义项，义项 id 等于条目 id。
"""

import json
import unicodedata

from .lemma import surface_forms, is_acronym

__all__ = ["Termbase", "load", "load_file"]


class Sense:
    __slots__ = ("entry_id", "sense_id", "canonical", "synonyms",
                 "forbidden", "pos_cues", "neg_cues")

    def __init__(self, entry_id, sense_id, data):
        self.entry_id = entry_id
        self.sense_id = sense_id
        self.canonical = unicodedata.normalize("NFKC", data["canonical"])
        self.synonyms = [unicodedata.normalize("NFKC", s)
                         for s in data.get("synonyms", [])]
        self.forbidden = [unicodedata.normalize("NFKC", s)
                          for s in data.get("forbidden", [])]
        cues = data.get("cues", {})
        self.pos_cues = [unicodedata.normalize("NFKC", c).lower()
                         for c in cues.get("positive", [])]
        self.neg_cues = [unicodedata.normalize("NFKC", c).lower()
                         for c in cues.get("negative", [])]


class Termbase:
    def __init__(self, entries_raw):
        # 英文侧：单个 token -> [(entry_id, head_form, acronym_only)]
        self.head_forms = {}
        # 英文多词源术语：entry_id -> (原形小写序列, 各词位词形集合)
        self.multiword = {}
        # 中文译名：归一化字符串 -> render_id
        self.renders = {}
        self.render_meta = []  # render_id -> dict(entry_id, sense_id, kind, text)
        # render_id 被多个条目共用时记录（扫描时跳过其归属判断）
        self.shared_renders = set()
        self.entries = {}        # entry_id -> {"source", "allow_multiple", "senses": [Sense]}
        self.issues = []         # 术语库自身问题 [{type, entry_id, message}]
        self.safe_embeddings = []  # [(entry_id, text)] 禁用译名允许出现的组合词

        for pos, raw in enumerate(entries_raw):
            self._load_entry(raw, pos)
        self._detect_overlaps()

    # ------------------------------------------------------------------ #
    def _issue(self, itype, entry_id, message):
        self.issues.append({"type": itype, "entry_id": entry_id,
                            "where": "termbase", "message": message})

    def _norm_render(self, text):
        return unicodedata.normalize("NFKC", text)

    def _register_render(self, kind, entry_id, sense_id, text):
        if not text:
            self._issue("empty_render", entry_id, "存在空译名")
            return
        rid = self.renders.get(text)
        if rid is None:
            rid = len(self.render_meta)
            self.renders[text] = rid
            self.render_meta.append(
                {"entry_id": entry_id, "sense_id": sense_id,
                 "kind": kind, "text": text})
        else:
            meta = self.render_meta[rid]
            if meta["entry_id"] != entry_id:
                self.shared_renders.add(rid)
                self._issue("shared_render", entry_id,
                            "译名 %r 同时属于条目 %s 与 %s"
                            % (text, meta["entry_id"], entry_id))
            # 同条目不同义项共用译名：允许，扫描时按义项归属再判 wrong_sense
            self.render_meta[rid].setdefault("owners", []).append(
                (entry_id, sense_id, kind))

    def _load_entry(self, raw, pos):
        entry_id = raw.get("id") or "entry#%d" % pos
        if entry_id in self.entries:
            self._issue("duplicate_id", entry_id, "条目 id 重复")
        source = raw.get("source")
        if not source or not isinstance(source, str):
            self._issue("missing_source", entry_id, "缺少 source")
            return
        src_norm = unicodedata.normalize("NFKC", source).strip()
        entry = {"source": src_norm,
                 "allow_multiple": bool(raw.get("allow_multiple", False)),
                 "safe_embeddings": [self._norm_render(s)
                                     for s in raw.get("safe_embeddings", [])],
                 "senses": []}
        self.entries[entry_id] = entry
        for text in entry["safe_embeddings"]:
            self.safe_embeddings.append((entry_id, text))

        if "senses" in raw:
            sense_list = raw["senses"]
            if not sense_list:
                self._issue("empty_senses", entry_id, "senses 为空")
            for i, sd in enumerate(sense_list):
                sid = sd.get("id") or "%s.%d" % (entry_id, i + 1)
                sense = Sense(entry_id, sid, sd)
                entry["senses"].append(sense)
                self._register_sense_renders(sense)
        else:
            if "canonical" not in raw:
                self._issue("missing_canonical", entry_id, "缺少 canonical")
                return
            sense = Sense(entry_id, entry_id, raw)
            entry["senses"].append(sense)
            self._register_sense_renders(sense)

        # 英文侧注册
        words = src_norm.split()
        forms_per_word = []
        acronym_flags = []
        for w in words:
            acr = is_acronym(w)
            forms_per_word.append(surface_forms(w))
            acronym_flags.append(acr)
        entry["acronym"] = len(words) == 1 and acronym_flags[0]
        if len(words) == 1:
            for f in forms_per_word[0]:
                self.head_forms.setdefault(f, []).append(
                    (entry_id, entry["acronym"]))
        else:
            # 多词短语不做词形展开（会指数膨胀），检查器按
            # 「词位词形集合」在 token 流上直接匹配
            base = tuple(w.lower() for w in words)
            self.multiword[entry_id] = (
                base, tuple(frozenset(f) for f in forms_per_word))

    def _register_sense_renders(self, sense):
        self._register_render("canonical", sense.entry_id, sense.sense_id,
                              sense.canonical)
        for s in sense.synonyms:
            self._register_render("synonym", sense.entry_id, sense.sense_id, s)
        for s in sense.forbidden:
            self._register_render("forbidden", sense.entry_id, sense.sense_id, s)

    def _detect_overlaps(self):
        # 同一英文表层形式被多个条目声明（词形/大小写冲突会让归属有歧义）
        for form, owners in self.head_forms.items():
            ids = {e for e, _ in owners}
            if len(ids) > 1:
                acr = [e for e, isacr in owners if isacr]
                non_acr = [e for e, isacr in owners if not isacr]
                # 缩写 vs 普通词可以靠大小写区分（API/api），不算问题
                if not (acr and non_acr
                        and len(set(acr) | set(non_acr)) == len(ids)):
                    for e in sorted(ids):
                        self._issue("overlapping_source", e,
                                    "英文表层形式 %r 同时属于多个条目: %s"
                                    % (form, sorted(ids)))
        seen = {}
        for eid, (base, _) in self.multiword.items():
            if base in seen:
                self._issue("overlapping_source", eid,
                            "多词术语 %r 同时属于条目 %s 与 %s"
                            % (" ".join(base), seen[base], eid))
            else:
                seen[base] = eid
    # 查询辅助 #
    def sense(self, entry_id, sense_id):
        for s in self.entries[entry_id]["senses"]:
            if s.sense_id == sense_id:
                return s
        return None

    def render_owners(self, rid):
        meta = self.render_meta[rid]
        owners = meta.get("owners")
        if owners is None:
            return [(meta["entry_id"], meta["sense_id"], meta["kind"])]
        return owners


def load(data):
    if isinstance(data, str):
        data = json.loads(data)
    if isinstance(data, dict) and "entries" in data:
        data = data["entries"]
    if not isinstance(data, list):
        raise ValueError("术语库应为条目列表或含 entries 的对象")
    return Termbase(data)


def load_file(path):
    with open(path, "r", encoding="utf-8") as fh:
        return load(json.load(fh))
