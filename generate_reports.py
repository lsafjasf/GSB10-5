"""Generate deliverable data files under reports/.

Run: python3 generate_reports.py
"""

import csv
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
import pathnorm as pn  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "reports")

# (path_a, path_b, style, note)
EQUIV_CASES = [
    ("/etc/../etc/passwd", "/etc/passwd", "posix", "dot-segment resolution"),
    ("a//b///c", "a/b/c", "posix", "duplicate separators"),
    ("a/./b/", "a/b", "posix", "trailing slash + dot"),
    ("/A/B/../b/c", "/A/b/c", "posix", "dot-segment cancels component"),
    ("/Users/Alice/Report.TXT", "/users/alice/report.txt", "posix",
     "case difference"),
    (r"C:\Projects\app/config.yaml", r"C:/Projects/app/config.yaml",
     "windows", "pure separator difference"),
    (r"C:\a//b\.\c", r"c:/a/b/c", "windows", "mixed + duplicate separators"),
    (r"\\srv\share\dir\f", r"//srv/share/dir/f", "windows", "UNC slash style"),
    (r"C:\A\B.TXT", r"c:\a\b.txt", "windows", "drive + case difference"),
    (r"\\?\C:\x\y\..\z", r"C:\x\z", "windows", "extended prefix cannot equal"),
    (r"..\a\.\b", r"../a/b", "windows", "relative with mixed separators"),
    ("a/b\\c", r"a\b\c", "windows", "backslash accepted as separator"),
    ("a/b\\c", r"a\b\c", "posix", "backslash is a literal char on POSIX"),
]

RESERVED_CASES = [
    (r"C:\work\CON", "windows"),
    (r"C:\work\con.txt", "windows"),
    (r"C:\work\NUL", "windows"),
    (r"C:\work\LPT9.out", "windows"),
    (r"C:\work\config", "windows"),
    ("/work/CON", "posix"),
]

LONG_CASES = [
    ("C:\\" + "\\".join(["x" * 50] * 6), "windows", "259+ chars, no \\?\\"),
    ("\\\\?\\C:\\" + "\\".join(["x" * 50] * 6), "windows",
     "same path with extended prefix"),
    ("/" + "/".join(["x" * 100] * 41), "posix", "4141 chars"),
    ("/dir/" + "z" * (pn.NAME_MAX + 1), "posix", "component > NAME_MAX"),
]


def main() -> None:
    os.makedirs(OUT, exist_ok=True)

    # 1. Equivalence data under BOTH case policies.
    with open(os.path.join(OUT, "equivalence.csv"), "w", newline="",
              encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["path_a", "path_b", "style", "note",
                    "equal_case_sensitive", "equal_case_insensitive",
                    "norm_a_cs", "norm_b_cs",
                    "norm_a_ci", "norm_b_ci"])
        for a, b, style, note in EQUIV_CASES:
            try:
                na_cs = pn.normalize(a, style, case_sensitive=True)
                nb_cs = pn.normalize(b, style, case_sensitive=True)
                na_ci = pn.normalize(a, style, case_sensitive=False)
                nb_ci = pn.normalize(b, style, case_sensitive=False)
                eq_cs, eq_ci = na_cs == nb_cs, na_ci == nb_ci
            except pn.PathNormError as exc:
                w.writerow([a, b, style, note, "ERROR", "ERROR", "", "",
                            type(exc).__name__, str(exc)])
                continue
            w.writerow([a, b, style, note, eq_cs, eq_ci,
                        na_cs, nb_cs, na_ci, nb_ci])

    # 2. Case-sensitivity side-by-side comparison.
    with open(os.path.join(OUT, "case_sensitivity.csv"), "w", newline="",
              encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["input", "style", "normalized_case_sensitive",
                    "normalized_case_insensitive", "verdict"])
        seen = {
            (a, style) for a, _, style, _ in EQUIV_CASES
        } | {
            (b, style) for _, b, style, _ in EQUIV_CASES
        }
        for raw, style in sorted(seen):
            try:
                cs = pn.normalize(raw, style, case_sensitive=True)
                ci = pn.normalize(raw, style, case_sensitive=False)
            except pn.PathNormError as exc:
                w.writerow([raw, style, type(exc).__name__,
                            type(exc).__name__, "rejected"])
                continue
            verdict = "identical" if cs == ci else "DIFFERS by case"
            w.writerow([raw, style, cs, ci, verdict])

    # 3. Reserved-name report (deterministic rejection, no truncation).
    with open(os.path.join(OUT, "reserved_names.csv"), "w", newline="",
              encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["input", "style", "status", "detail"])
        for raw, style in RESERVED_CASES:
            try:
                norm = pn.normalize(raw, style)
                w.writerow([raw, style, "accepted", norm])
            except pn.PathNormError as exc:
                w.writerow([raw, style, "rejected",
                            f"{type(exc).__name__}: {exc}"])

    # 4. Long-path report.
    with open(os.path.join(OUT, "long_paths.csv"), "w", newline="",
              encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["input_length", "style", "scenario", "status", "detail"])
        for raw, style, scenario in LONG_CASES:
            try:
                norm = pn.normalize(raw, style)
                w.writerow([len(raw), style, scenario, "accepted",
                            f"normalized length {len(norm)}"])
            except pn.PathNormError as exc:
                w.writerow([len(raw), style, scenario, "rejected",
                            f"{type(exc).__name__}: {exc}"])

    print("reports written to", OUT)
    for name in sorted(os.listdir(OUT)):
        print(" -", name)


if __name__ == "__main__":
    main()
