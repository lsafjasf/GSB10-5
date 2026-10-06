"""Interactive demo: TAB-completion against the index, with live ACL edits.

    python3 demo_cli.py [--size 200000]

Type a prefix and hit TAB.  Meta-commands (prefix ':'):
    :users                 list known users
    :user NAME             switch identity (default: dev)
    :grant USER GROUP      grant a permission group
    :revoke USER GROUP     revoke a permission group
    :quit                  exit

Try: complete `secret_` as dev (nothing), then `:grant dev secret`,
TAB again (results appear), `:revoke dev secret` (gone again).
"""
import argparse
import gc
import readline
import time

from bench import STEMS
from completion_lib import CompletionIndex


def build(size):
    idx = CompletionIndex(limit=30)
    n_secret = max(10, size // 50)
    items = []
    for i in range(size - n_secret):
        groups = ["public"] + (["ops"] if i % 10 == 0 else [])
        items.append((f"{STEMS[i % len(STEMS)]}_{i:07d}", groups))
    for i in range(n_secret):
        items.append((f"secret_{i:07d}", ["secret"]))
    idx.bulk_add(items)
    idx.set_user_groups("dev", ["public", "ops"])
    idx.set_user_groups("admin", ["public", "ops", "secret"])
    idx.set_user_groups("guest", [])
    gc.collect()
    gc.freeze()
    return idx


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", type=int, default=200_000)
    args = ap.parse_args()

    idx = build(args.size)
    state = {"user": "dev", "last": []}
    print(f"index: {idx.size():,} names | you are 'dev' "
          f"(groups public,ops) | TAB to complete, :quit to exit")

    def completer(text, n):
        if n == 0:
            t0 = time.perf_counter_ns()
            state["last"] = idx.complete(state["user"], text)
            dt = (time.perf_counter_ns() - t0) / 1000.0
            print(f"\n[{len(state['last'])} matches in {dt:.1f} us "
                  f"as {state['user']!r}]", end="")
        try:
            return state["last"][n]
        except IndexError:
            return None

    readline.set_completer(completer)
    readline.parse_and_bind("tab: complete")
    readline.set_completer_delims(" \t\n")

    while True:
        try:
            line = input(f"\n{state['user']}> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line.startswith(":"):
            parts = line[1:].split()
            cmd = parts[0] if parts else ""
            if cmd == "quit":
                break
            elif cmd == "users":
                print("  " + ", ".join(sorted(idx._user_groups)))
            elif cmd == "user" and len(parts) == 2:
                state["user"] = parts[1]
                print(f"  now completing as {parts[1]!r} "
                      f"(groups: {sorted(idx._user_groups.get(parts[1], ()))})")
            elif cmd in ("grant", "revoke") and len(parts) == 3:
                _, user, group = parts
                cur = set(idx._user_groups.get(user, ()))
                cur.add(group) if cmd == "grant" else cur.discard(group)
                idx.set_user_groups(user, cur)
                print(f"  {user} groups -> {sorted(cur)} (index v{idx.version()})")
            else:
                print("  commands: :users :user U :grant U G :revoke U G :quit")
            continue
        # Non-TAB path: run one completion explicitly and show results.
        t0 = time.perf_counter_ns()
        res = idx.complete(state["user"], line)
        dt = (time.perf_counter_ns() - t0) / 1000.0
        print(f"  {len(res)} matches in {dt:.1f} us:")
        for r in res:
            print("   ", r)


if __name__ == "__main__":
    main()
