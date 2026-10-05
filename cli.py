"""CLI demo for pathnorm.

Examples:
  python3 cli.py norm 'C:\\a//b/./c' --style windows
  python3 cli.py equiv 'C:\\A\\b' 'c:/a/b' --style windows --ci
  python3 cli.py check 'C:\\work\\CON' --style windows
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
import pathnorm as pn  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(prog="pathnorm-cli")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_common(p, with_b=False):
        p.add_argument("a")
        if with_b:
            p.add_argument("b")
        p.add_argument("--style", choices=["windows", "posix"],
                       default="posix")
        p.add_argument("--ci", action="store_true",
                       help="case-insensitive filesystem")

    add_common(sub.add_parser("norm"))
    add_common(sub.add_parser("equiv"), with_b=True)
    add_common(sub.add_parser("check"))
    args = ap.parse_args()
    cs = not args.ci

    try:
        if args.cmd == "norm":
            print(pn.normalize(args.a, args.style, case_sensitive=cs))
        elif args.cmd == "equiv":
            eq = pn.equivalent(args.a, args.b, args.style, case_sensitive=cs)
            print("EQUAL" if eq else "DIFFERENT")
            return 0 if eq else 1
        else:
            print(json.dumps(pn.check(args.a, args.style),
                             ensure_ascii=False, indent=2))
    except pn.PathNormError as exc:
        print(f"ERROR {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
