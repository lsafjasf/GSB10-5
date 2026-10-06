"""测试公共逻辑：加载手工标注、运行引擎、计算定位/误报/漏报指标。"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from revcheck import check_project  # noqa: E402
from revcheck.model import Finding  # noqa: E402

FIXTURES_DIR = os.path.join(REPO_ROOT, "fixtures")
MANIFEST_PATH = os.path.join(FIXTURES_DIR, "manifest.json")


def load_manifest() -> Dict[str, List[dict]]:
    with open(MANIFEST_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)["expectations"]


def run_fixtures() -> List[Finding]:
    return check_project(FIXTURES_DIR)


@dataclass
class Evaluation:
    """对照手工标注的评估结果。"""

    expected: List[Tuple[str, str, int, str]] = field(default_factory=list)
    actual: List[Finding] = field(default_factory=list)
    true_positives: List[Tuple[str, str, int, str]] = field(default_factory=list)
    false_negatives: List[Tuple[str, str, int, str]] = field(default_factory=list)
    false_positives: List[Finding] = field(default_factory=list)

    @property
    def precision(self) -> float:
        denom = len(self.true_positives) + len(self.false_positives)
        return len(self.true_positives) / denom if denom else 1.0

    @property
    def recall(self) -> float:
        denom = len(self.true_positives) + len(self.false_negatives)
        return len(self.true_positives) / denom if denom else 1.0

    @property
    def location_accuracy(self) -> float:
        """期望报警中 (规则,行,结构) 三元组被精确命中的比例。"""
        if not self.expected:
            return 1.0
        return len(self.true_positives) / len(self.expected)


def evaluate() -> Evaluation:
    manifest = load_manifest()
    findings = run_fixtures()

    expected: List[Tuple[str, str, int, str]] = []
    for rel_path, entries in manifest.items():
        for entry in entries:
            expected.append(
                (rel_path, entry["rule"], entry["line"], entry["structure"])
            )

    actual_keys = set()
    for finding in findings:
        rel = os.path.relpath(finding.path, FIXTURES_DIR)
        actual_keys.add((rel, finding.rule_id, finding.line, finding.structure))

    expected_keys = set(expected)
    ev = Evaluation(expected=expected, actual=findings)
    ev.true_positives = sorted(expected_keys & actual_keys)
    ev.false_negatives = sorted(expected_keys - actual_keys)
    ev.false_positives = [
        f
        for f in findings
        if (
            os.path.relpath(f.path, FIXTURES_DIR),
            f.rule_id,
            f.line,
            f.structure,
        )
        not in expected_keys
    ]
    return ev
