#!/usr/bin/env python3
"""交互式命令行补全演示（仅标准库）。

启动后按 Tab 触发补全；补全结果只包含当前用户有权限看到的名字。
支持命令（以 ':' 开头，非交互模式下也可用）：

    :whoami            查看当前用户与已授权组
    :user <id>         切换用户
    :grant <gid>       给当前用户授权一个组（权限变更立即生效）
    :revoke <gid>      撤销当前用户的一个组
    :groups            列出所有权限组
    :epoch             查看当前权限版本号
    :help              帮助
    :quit              退出

非交互（无 TTY）时自动跑一段演示脚本并退出。
"""
from __future__ import annotations

import sys

from completion import ACL, CompletionIndex

try:
    import readline
except ImportError:  # pragma: no cover - 非 POSIX 平台
    readline = None

LIMIT = 10


def build_demo() -> tuple:
    """构造演示数据：含敏感名称（组 900 为管理员专属）。"""
    items = []
    for svc in ("api", "auth", "billing", "cache", "db", "gateway", "mail",
                "queue", "search", "storage", "user", "worker"):
        for region in ("cn", "eu", "us"):
            for i in range(40):
                items.append((f"{svc}-{region}-{i:03d}", None))
    for i in range(30):
        items.append((f"secret-admin-{i:03d}", 900))
        items.append((f"secret-token-{i:03d}", 901))
    acl = ACL()
    acl.set_groups(
        grants={"alice": {900, 901}, "bob": {901}},
        superusers={"root"},
    )
    return CompletionIndex(items), acl


class Shell:
    def __init__(self) -> None:
        self.index, self.acl = build_demo()
        self.user = "alice"
        self._matches: list = []

    # ---- readline 补全 --------------------------------------------------
    def _completer(self, text: str, state: int) -> object:
        if state == 0:
            result = self.index.complete(text, self.user, self.acl, limit=LIMIT)
            self._matches = list(result.names)
        try:
            return self._matches[state]
        except IndexError:
            return None

    # ---- 命令处理 -------------------------------------------------------
    def handle_command(self, line: str) -> bool:
        parts = line[1:].split()
        if not parts:
            return True
        cmd, args = parts[0], parts[1:]
        if cmd in ("quit", "exit", "q"):
            return False
        if cmd == "whoami":
            snap = self.acl.snapshot()
            print(f"user={self.user} groups={sorted(snap.groups_of(self.user))} "
                  f"superuser={snap.is_superuser(self.user)}")
        elif cmd == "user" and args:
            self.user = args[0]
            print(f"当前用户: {self.user}")
        elif cmd == "grant" and args:
            snap = self.acl.grant(self.user, [int(args[0])])
            print(f"已授权组 {args[0]}，权限版本 -> {snap.epoch}")
        elif cmd == "revoke" and args:
            snap = self.acl.revoke(self.user, [int(args[0])])
            print(f"已撤销组 {args[0]}，权限版本 -> {snap.epoch}")
        elif cmd == "groups":
            groups = sorted({g for _, g in self.index._scan("", self.user,
                             self.acl.snapshot(), 10**9).items if g is not None})
            print(f"权限组: {groups}（None 表示公开，无需授权）")
        elif cmd == "epoch":
            print(f"权限版本: {self.acl.snapshot().epoch}")
        elif cmd == "help":
            print(__doc__)
        else:
            print("未知命令，输入 :help 查看帮助")
        return True

    def query(self, prefix: str) -> None:
        result = self.index.complete(prefix, self.user, self.acl, limit=LIMIT)
        suffix = " …(已截断)" if result.truncated else ""
        print(f"[epoch={result.epoch}] {len(result.names)} 条{suffix}: "
              + ", ".join(result.names))

    def run_script(self, script: list) -> None:
        for line in script:
            print(f"> {line}")
            if line.startswith(":"):
                if not self.handle_command(line):
                    break
            else:
                self.query(line)

    def repl(self) -> None:
        if readline is not None:
            readline.set_completer(self._completer)
            readline.parse_and_bind("tab: complete")
            readline.set_completer_delims(" \t\n")
        print("交互式补全演示。输入前缀后按 Tab 补全，:help 查看命令。")
        while True:
            try:
                line = input(f"{self.user}> ")
            except (EOFError, KeyboardInterrupt):
                print()
                break
            line = line.strip()
            if not line:
                continue
            if line.startswith(":"):
                if not self.handle_command(line):
                    break
            else:
                self.query(line)


def main() -> None:
    shell = Shell()
    if not sys.stdin.isatty():
        shell.run_script([
            ":whoami",
            "api-",
            "secret-",
            ":user root",
            "secret-",
            ":user bob",
            "secret-",
            ":revoke 901",
            "secret-",
            ":quit",
        ])
        return
    shell.repl()


if __name__ == "__main__":
    main()
