"""Runs all rollback-feasibility scenarios and writes reports to ./reports."""
import os
import sys

from rollback_verify import codec
from rollback_verify.checker import CompatibilityChecker, verdict_and_steps
from rollback_verify.report import build_report, write_json, render_text
import scenarios as S

REPORT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")
RECORDS_PER_SCENARIO = 2000

SCENARIOS = [
    ("fully_compatible", S.SCHEMA_COMPAT, "compat", None, "完全兼容"),
    ("additive_fields", S.SCHEMA_ADDITIVE, "additive", None, "仅新增字段"),
    ("new_enum_value", S.SCHEMA_NEW_ENUM, "new_enum", S.ENUM_MAPPINGS, "新增枚举值"),
    ("index_change", S.SCHEMA_INDEX_CHANGE, "index_change", S.ENUM_MAPPINGS, "索引结构变化"),
    ("destructive_change", S.SCHEMA_DESTRUCTIVE, "destructive", None, "破坏性变更(删必填字段/删接口)"),
]


def run_scenario(name, new_schema, mode, enum_mappings, desc):
    checker = CompatibilityChecker(S.OLD, new_schema, enum_mappings=enum_mappings)

    records = list(S.gen_records(seed=42, count=RECORDS_PER_SCENARIO, mode=mode))
    data_file = codec.encode_data_file(records, new_schema.data_format_version)
    index_files = S.new_index_files(new_schema, records)

    _, decoded, fmt_issues = checker.reader.read_data_file(data_file)

    all_issues = list(fmt_issues)
    all_issues.extend(checker.check_index_files(index_files))
    all_issues.extend(checker.check_contract())

    ok_records = 0
    sample_ok, sample_bad = [], []
    for seq, rec in enumerate(decoded):
        rec_issues = checker.check_record(rec, seq)
        blocking = [i for i in rec_issues if i.kind != "unknown_field_ignored"]
        if blocking:
            all_issues.extend(rec_issues)
            if len(sample_bad) < 3:
                sample_bad.append(rec)
        else:
            ok_records += 1
            if len(sample_ok) < 3:
                sample_ok.append(rec)

    verdict, steps = verdict_and_steps(all_issues)
    report = build_report(name, S.OLD.version, new_schema.version,
                          len(decoded), ok_records, all_issues, verdict, steps,
                          sample_ok, sample_bad)
    return report


def main():
    os.makedirs(REPORT_DIR, exist_ok=True)
    text_parts = []
    for name, schema, mode, mappings, desc in SCENARIOS:
        report = run_scenario(name, schema, mode, mappings, desc)
        write_json(report, os.path.join(REPORT_DIR, "%s.json" % name))
        text_parts.append("### %s\n%s" % (desc, render_text(report)))
    summary_path = os.path.join(REPORT_DIR, "summary.txt")
    with open(summary_path, "w", encoding="utf-8") as fh:
        fh.write("\n\n".join(text_parts) + "\n")
    print(open(summary_path, encoding="utf-8").read())
    return 0


if __name__ == "__main__":
    sys.exit(main())
