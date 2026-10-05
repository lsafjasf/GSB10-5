"""把评估结果导出为可审计的 JSON 清单（仅标准库）。"""

import json
from dataclasses import asdict
from pathlib import Path

from .core import CleanupReport


def write_report_files(report: CleanupReport, out_dir) -> dict:
    """写出三份清单：幸免清单、删除清单、汇总。返回各文件路径。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    spared_path = out_dir / "spared_list.json"
    deletion_path = out_dir / "deletion_manifest.json"
    summary_path = out_dir / "summary.json"

    spared_payload = [
        {
            "id": e.id,
            "decision": e.decision.value,
            "reason": e.reason,
            "referrers": list(e.referrers),
        }
        for e in report.spared
    ]
    deletion_payload = [asdict(e) for e in report.deletion_manifest]
    summary_payload = report.summary()
    summary_payload["dangling_references_detail"] = [
        {"source_id": d.source_id, "target_id": d.target_id}
        for d in report.dangling_references
    ]

    spared_path.write_text(json.dumps(spared_payload, indent=2, ensure_ascii=False))
    deletion_path.write_text(json.dumps(deletion_payload, indent=2, ensure_ascii=False))
    summary_path.write_text(json.dumps(summary_payload, indent=2, ensure_ascii=False))

    return {
        "spared_list": str(spared_path),
        "deletion_manifest": str(deletion_path),
        "summary": str(summary_path),
    }
