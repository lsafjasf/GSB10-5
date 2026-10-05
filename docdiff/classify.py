"""结构变化归类。

对每一对配对节按优先级归类，主类别优先级：
    carried（随整章搬迁） > moved（移动） > releveled（层级调整）
    > renamed（改名） > modified（内容修改）
未配对节按"未配对子树的根"归类为 added / deleted，子树其余节不重复记录。

结构判定依据（同一配对节的旧/新版本）：
- parent_changed：最近已配对祖先在新版本中发生变化（跨父移动）；
- delta_level：新旧标题层级差 != 0（层级调整）；
- reordered：同一配对父组内，用 LCS 最小编辑得出的非保序元素（同级换位）。
判定规则：
- parent_changed 且 delta_level != 0 -> releveled（flags 同时含 moved）
- parent_changed                    -> moved
- delta_level != 0                  -> releveled
- reordered                         -> moved
搬迁根（moved/releveled 的节）的已配对后代：层级差与根一致且子树完整
（配对后代集合与先序相对顺序一致）时记为 carried，否则退回逐节判定。
配对节标题规范化后不同即记 renamed（配对本身由正文证据保证）；
正文指纹不同记 modified。
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Optional

from .fingerprint import jaccard, title_sim
from .model import Tree

PRIMARY_KINDS = ("added", "deleted", "moved", "releveled",
                 "carried", "renamed", "modified")


def _parent_groups(tree: Tree) -> Dict[Optional[int], List[int]]:
    groups: Dict[Optional[int], List[int]] = defaultdict(list)
    for sid in tree.preorder():
        groups[tree.sections[sid].parent].append(sid)
    return groups


def _desc_preorder(tree: Tree, root: int) -> List[int]:
    out: List[int] = []
    stack = list(reversed(tree.sections[root].children))
    while stack:
        sid = stack.pop()
        out.append(sid)
        stack.extend(reversed(tree.sections[sid].children))
    return out


def _lcs_movers(old_seq: List[int], new_seq: List[int],
                subtree_sizes: Dict[int, int]) -> set:
    """加权 LCS 求同级换位的移动者集合。

    主目标：留在保序序列里的元素最多（移动数最少）；
    平局目标：留在保序序列里的元素子树规模之和最小，
    即优先让"整章"（子树大的节）充当移动者，其后代才能折叠为 carried；
    再平局时回溯顺序固定（结果确定）。
    """
    n, m = len(old_seq), len(new_seq)
    dp = [[(0, 0)] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        w = subtree_sizes[old_seq[i - 1]]
        for j in range(1, m + 1):
            best = dp[i - 1][j - 1]
            if old_seq[i - 1] == new_seq[j - 1]:
                cand = (dp[i - 1][j - 1][0] + 1,
                        dp[i - 1][j - 1][1] - w)
                if cand > best:
                    best = cand
            if dp[i - 1][j] > best:
                best = dp[i - 1][j]
            if dp[i][j - 1] > best:
                best = dp[i][j - 1]
            dp[i][j] = best
    kept = set()
    i, j = n, m
    while i > 0 and j > 0:
        w = subtree_sizes[old_seq[i - 1]]
        if (old_seq[i - 1] == new_seq[j - 1]
                and dp[i][j] == (dp[i - 1][j - 1][0] + 1,
                                 dp[i - 1][j - 1][1] - w)):
            kept.add(old_seq[i - 1])
            i -= 1
            j -= 1
        elif dp[i - 1][j] >= dp[i][j - 1]:
            i -= 1
        else:
            j -= 1
    return set(old_seq) - kept


def classify(old: Tree, new: Tree, matched: Dict[int, int],
             scores: Dict[int, float], tau_title: float = 0.70) -> List[dict]:
    inv = {v: k for k, v in matched.items()}
    old_groups = _parent_groups(old)
    new_groups = _parent_groups(new)

    def nma_old(sid: int) -> Optional[int]:
        """旧文档中最近的已配对祖先（不含自身）。"""
        cur = old.sections[sid].parent
        while cur is not None and cur not in matched:
            cur = old.sections[cur].parent
        return cur

    def nma_new(sid: int) -> Optional[int]:
        """新文档中最近的已配对祖先（不含自身）。"""
        cur = new.sections[matched[sid]].parent
        while cur is not None and cur not in inv:
            cur = new.sections[cur].parent
        return cur

    # ---- 同级换位分析：每个配对父组内做加权 LCS，非保序元素记为 reordered ----
    reordered = set()
    subtree_sizes = {sid: old.subtree_size(sid) for sid in old.sections}
    for pa, members in old_groups.items():
        if pa is not None and pa not in matched:
            continue
        pb = matched[pa] if pa is not None else None
        old_seq = [s for s in members if s in matched
                   and new.sections[matched[s]].parent == pb]
        if len(old_seq) < 2:
            continue
        new_seq = [inv[s] for s in new_groups.get(pb, []) if s in inv
                   and old.sections[inv[s]].parent == pa]
        reordered.update(_lcs_movers(old_seq, new_seq, subtree_sizes))

    def local_kind(osid: int):
        """逐节结构判定（随迁验证失败时也用它回退）。"""
        nsid = matched[osid]
        o, nn = old.sections[osid], new.sections[nsid]
        d = nn.level - o.level
        pa, pb = nma_old(osid), nma_new(osid)
        same_group = (pa is None and pb is None) or (
            pa is not None and pb is not None and matched.get(pa) == pb)
        parent_changed = not same_group
        if parent_changed and d != 0:
            return "releveled", parent_changed
        if parent_changed:
            return "moved", parent_changed
        if d != 0:
            return "releveled", parent_changed
        if osid in reordered:
            return "moved", parent_changed
        return None, parent_changed

    # ---- 第一遍（先序，根优先）：搬迁根 + 候选随迁 ----
    rel_root: Dict[int, int] = {}
    carried_of: Dict[int, int] = {}
    struct: Dict[int, Optional[str]] = {}
    for osid in old.preorder():
        if osid not in matched:
            continue
        nsid = matched[osid]
        d = new.sections[nsid].level - old.sections[osid].level
        anc = nma_old(osid)
        root = anc if anc in rel_root else carried_of.get(anc)
        if root is not None and d == rel_root[root]:
            struct[osid] = "pending_carried"
            carried_of[osid] = root
            continue
        kind, _ = local_kind(osid)
        struct[osid] = kind
        if kind in ("moved", "releveled"):
            rel_root[osid] = d

    # ---- 第二遍：随迁验证——子树完整保持才成立，否则逐节回退 ----
    pendings: Dict[int, List[int]] = defaultdict(list)
    for osid, root in carried_of.items():
        pendings[root].append(osid)
    for root, members in pendings.items():
        nroot = matched[root]
        old_desc = [s for s in _desc_preorder(old, root) if s in matched]
        new_desc = [s for s in _desc_preorder(new, nroot) if s in inv]
        preserved = [matched[s] for s in old_desc] == new_desc
        for osid in members:
            struct[osid] = "carried" if preserved else local_kind(osid)[0]

    # ---- 第三遍：生成配对节记录 ----
    records: List[dict] = []
    for osid in old.preorder():
        if osid not in matched:
            continue
        nsid = matched[osid]
        o, nn = old.sections[osid], new.sections[nsid]
        skind = struct[osid]
        renamed = o.ntitle != nn.ntitle
        body_changed = o.body_fp != nn.body_fp
        if skind is None and not renamed and not body_changed:
            continue
        d = nn.level - o.level
        _, parent_changed = local_kind(osid)
        flags = []
        if skind:
            flags.append(skind)
        if skind in ("moved", "releveled"):
            if parent_changed and "moved" not in flags:
                flags.append("moved")
            if d != 0 and "releveled" not in flags:
                flags.append("releveled")
        if renamed:
            flags.append("renamed")
        if body_changed:
            flags.append("modified")
        primary = skind or ("renamed" if renamed else "modified")
        rec = {
            "rid": "",
            "kind": primary,
            "flags": flags,
            "old_path": old.path(osid),
            "new_path": new.path(nsid),
            "old_level": o.level,
            "new_level": nn.level,
            "delta_level": d,
            "old_line": o.start_line,
            "new_line": nn.start_line,
            "evidence": {
                "title_sim": round(title_sim(o.ntitle, nn.ntitle), 4),
                "body_fp_equal": not body_changed,
                "body_jaccard": round(jaccard(o.body_shingles, nn.body_shingles), 4),
                "match_score": round(scores.get(osid, 1.0), 4),
            },
        }
        if skind in ("moved", "releveled"):
            rec["struct_evidence"] = {
                "old_parent": old.sections[o.parent].title if o.parent is not None else None,
                "new_parent": new.sections[nn.parent].title if nn.parent is not None else None,
                "parent_changed": parent_changed,
                "reordered": osid in reordered,
                "delta_level": d,
            }
        if skind == "carried":
            root = carried_of[osid]
            rec["carried_by"] = " > ".join(old.path(root))
        records.append(rec)

    # ---- 未配对节：只记录未配对子树的根 ----
    for sid in old.preorder():
        if sid in matched:
            continue
        sec = old.sections[sid]
        if sec.parent is not None and sec.parent not in matched:
            continue
        records.append(_unmatched_record("deleted", old, sid))
    for sid in new.preorder():
        if sid in inv:
            continue
        sec = new.sections[sid]
        if sec.parent is not None and sec.parent not in inv:
            continue
        records.append(_unmatched_record("added", new, sid))

    records.sort(key=lambda r: (
        r["old_line"] if r["old_line"] is not None else 1 << 60,
        r["new_line"] if r["new_line"] is not None else 1 << 60,
        str(r["old_path"] or r["new_path"]),
    ))
    for i, rec in enumerate(records, 1):
        rec["rid"] = f"C{i:04d}"
    return records


def _unmatched_record(kind: str, tree: Tree, sid: int) -> dict:
    sec = tree.sections[sid]
    is_old = kind == "deleted"
    return {
        "rid": "",
        "kind": kind,
        "flags": [kind],
        "old_path": tree.path(sid) if is_old else None,
        "new_path": None if is_old else tree.path(sid),
        "old_level": sec.level if is_old else None,
        "new_level": None if is_old else sec.level,
        "delta_level": None,
        "old_line": sec.start_line if is_old else None,
        "new_line": None if is_old else sec.start_line,
        "subtree": {
            "sections": tree.subtree_size(sid),
            "lines": tree.subtree_lines(sid),
            "children": [tree.sections[c].title for c in sec.children],
        },
    }
