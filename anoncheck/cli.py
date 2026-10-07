"""Command line interface: python3 -m anoncheck <command> ..."""

from __future__ import annotations

import argparse
import csv
import json
import sys

from . import core, hierarchies


def _split_fields(text):
    return [f.strip() for f in text.split(",") if f.strip()]


def _transforms_for(fields, gen_specs):
    specs = gen_specs or {}
    names = [specs.get(f, "identity") for f in fields]
    return [hierarchies.get_transform(s) for s in names], names


def _print_report(report, title):
    print("== %s ==" % title)
    print("  QI combination        : %s" % ",".join(report.fields))
    print("  transforms            : %s" % ",".join(report.transforms))
    print("  records               : %d" % report.total_records)
    print("  equivalence classes   : %d" % report.num_classes)
    print("  class size min/mean/median/max: %d / %.2f / %s / %d" % (
        report.min_class_size, report.mean_class_size,
        report.median_class_size, report.max_class_size))
    print("  unique classes (k=1)  : %d (%d records)" % (
        report.unique_classes, report.unique_records))
    print("  rare classes (k<%d)    : %d (%d records)" % (
        report.k_threshold, report.rare_classes, report.rare_records))
    print("  max re-ident prob     : %.4f" % report.max_reident_probability)
    print("  k-anonymous at k=%d    : %s" % (
        report.k_threshold, "YES" if report.is_k_anonymous else "NO"))
    print("  size histogram        : %s" % json.dumps(report.size_histogram))


def _print_comparison(before, after):
    print("== before/after comparison ==")
    header = "%-24s %14s %14s" % ("metric", "before", "after")
    print(header)
    print("-" * len(header))
    rows = [
        ("equivalence classes", before.num_classes, after.num_classes),
        ("min class size", before.min_class_size, after.min_class_size),
        ("unique classes", before.unique_classes, after.unique_classes),
        ("unique records", before.unique_records, after.unique_records),
        ("rare classes (k<%d)" % before.k_threshold,
         before.rare_classes, after.rare_classes),
        ("rare records (k<%d)" % before.k_threshold,
         before.rare_records, after.rare_records),
        ("max re-ident prob", "%.4f" % before.max_reident_probability,
         "%.4f" % after.max_reident_probability),
        ("k-anonymous", "YES" if before.is_k_anonymous else "NO",
         "YES" if after.is_k_anonymous else "NO"),
    ]
    for name, b, a in rows:
        print("%-24s %14s %14s" % (name, b, a))


def cmd_analyze(args):
    fields = _split_fields(args.qi)
    gen_specs = hierarchies.parse_generalize_arg(args.generalize or "")
    counts, total = core.count_classes(args.csv, fields)
    before = core.build_report(counts, total, fields, k=args.k)
    _print_report(before, "risk report (raw values)")

    after = None
    if gen_specs:
        transforms, names = _transforms_for(fields, gen_specs)
        g_counts, g_total = core.count_classes(args.csv, fields, transforms)
        after = core.build_report(g_counts, g_total, fields, k=args.k,
                                  transform_names=names)
        print()
        _print_report(after, "risk report (generalized)")
        print()
        _print_comparison(before, after)

    risky = core.risky_class_map(counts, args.k)
    if args.risky_csv:
        records = core.find_risky_records(args.csv, fields, risky)
        with open(args.risky_csv, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(
                fh, fieldnames=["row", "class_size"] + fields)
            writer.writeheader()
            writer.writerows(records)
        print("\nhigh-risk records written: %s (%d rows)" % (
            args.risky_csv, len(records)))

    if args.report_json:
        payload = {"before": before.to_dict()}
        if after is not None:
            payload["after"] = after.to_dict()
        with open(args.report_json, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)
        print("json report written: %s" % args.report_json)
    return 0


def cmd_screen(args):
    fields = _split_fields(args.fields)
    results = core.screen_combinations(args.csv, fields, k=args.k)
    print("%-32s %8s %8s %8s %8s %10s %8s" % (
        "combination", "classes", "min", "unique", "rare", "rare_recs",
        "max_prob"))
    for r in results:
        print("%-32s %8d %8d %8d %8d %10d %8.4f" % (
            "+".join(r["combination"]), r["num_classes"],
            r["min_class_size"], r["unique_classes"], r["rare_classes"],
            r["rare_records"], r["max_reident_probability"]))
    if args.report_json:
        with open(args.report_json, "w", encoding="utf-8") as fh:
            json.dump(results, fh, indent=2, ensure_ascii=False)
        print("json written: %s" % args.report_json)
    return 0


def cmd_suppress(args):
    fields = _split_fields(args.qi)
    gen_specs = hierarchies.parse_generalize_arg(args.generalize or "")
    transforms, names = _transforms_for(fields, gen_specs)

    counts, total = core.count_classes(args.csv, fields, transforms)
    before = core.build_report(counts, total, fields, k=args.k,
                               transform_names=names)
    risky = core.risky_class_map(counts, args.k)
    stats = core.suppress_records(args.csv, args.out, fields, risky,
                                  transforms, mode=args.mode)

    a_counts, a_total = core.count_classes(args.out, fields, transforms)
    after = core.build_report(a_counts, a_total, fields, k=args.k,
                              transform_names=names)
    _print_report(before, "before suppression")
    print()
    _print_report(after, "after suppression (mode=%s)" % args.mode)
    print()
    _print_comparison(before, after)
    print("\nsuppression stats: %s" % json.dumps(stats))
    print("anonymized file written: %s" % args.out)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="anoncheck",
        description="Re-identification risk check for anonymized datasets")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("analyze", help="quantify risk for a QI combination")
    p.add_argument("--csv", required=True)
    p.add_argument("--qi", required=True,
                   help="comma-separated quasi-identifier fields")
    p.add_argument("--k", type=int, default=5)
    p.add_argument("--generalize", default="",
                   help="e.g. age=age_band:10,zipcode=zip_prefix:3")
    p.add_argument("--report-json", dest="report_json")
    p.add_argument("--risky-csv", dest="risky_csv",
                   help="write auditable list of high-risk records")
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser("screen",
                       help="screen singles/pairs/full set to pick the QI combination")
    p.add_argument("--csv", required=True)
    p.add_argument("--fields", required=True)
    p.add_argument("--k", type=int, default=5)
    p.add_argument("--report-json", dest="report_json")
    p.set_defaults(func=cmd_screen)

    p = sub.add_parser("suppress", help="drop or mask records in risky classes")
    p.add_argument("--csv", required=True)
    p.add_argument("--qi", required=True)
    p.add_argument("--k", type=int, default=5)
    p.add_argument("--generalize", default="")
    p.add_argument("--mode", choices=["drop", "mask"], default="drop")
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_suppress)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
