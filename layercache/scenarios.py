"""自测与报告共用的场景定义。

四个必测场景：
  identical    完全相同的两组脚本
  reordered    仅指令顺序调整（COPY 两条互换）
  mid_change   中间一层变化（RUN 命令文本改变）
  metadata     仅时间戳与属主变化（mtime/uid/gid 全部改变）
另提供若干负例场景（内容/权限/链接目标变化）用于证明不会错误复用。
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from .model import BuildScript, FileRecord, Instruction


def _ctx(app_content: bytes = b"print('hello')\n",
         mtime: int = 1_700_000_000, uid: int = 1000, gid: int = 1000,
         app_mode: int = 0o644) -> Tuple[FileRecord, ...]:
    return (
        FileRecord(path="/src/app.py", kind="file", content=app_content,
                   mode=app_mode, mtime=mtime, uid=uid, gid=gid),
        FileRecord(path="/src/util.py", kind="file",
                   content=b"def util():\n    return 42\n",
                   mode=0o644, mtime=mtime + 1, uid=uid, gid=gid),
        FileRecord(path="/conf/settings.ini", kind="file",
                   content=b"[server]\nport=8080\n",
                   mode=0o644, mtime=mtime + 2, uid=uid, gid=gid),
    )


def _instructions(copy_order: Tuple[str, ...] = ("/src/app.py", "/conf/settings.ini"),
                  mid_cmd: str = "mkdir -p data && write data/version.txt v1"
                  ) -> Tuple[Instruction, ...]:
    return (
        Instruction("FROM", "ubuntu:22.04"),
        Instruction("RUN", "mkdir -p app"),
        Instruction("COPY", f"{copy_order[0]} /app/{copy_order[0].rsplit('/', 1)[-1]}"),
        Instruction("COPY", f"{copy_order[1]} /app/{copy_order[1].rsplit('/', 1)[-1]}"),
        Instruction("RUN", mid_cmd),
        Instruction("ENV", "APP_HOME=/app"),
        Instruction("WORKDIR", "/app"),
    )


def scenario_pairs() -> List[Tuple[str, BuildScript, BuildScript]]:
    """返回 [(场景名, base, candidate), ...]。"""
    base = BuildScript(name="base", instructions=_instructions(), context=_ctx())

    identical = BuildScript(name="identical", instructions=_instructions(),
                            context=_ctx())

    reordered = BuildScript(
        name="reordered",
        instructions=_instructions(
            copy_order=("/conf/settings.ini", "/src/app.py")),
        context=_ctx())

    mid_change = BuildScript(
        name="mid_change",
        instructions=_instructions(
            mid_cmd="mkdir -p data && write data/version.txt v2"),
        context=_ctx())

    metadata = BuildScript(
        name="metadata",
        instructions=_instructions(),
        context=_ctx(mtime=1_800_000_000, uid=2000, gid=3000))

    return [
        ("完全相同", base, identical),
        ("仅顺序调整", base, reordered),
        ("中间一层变化", base, mid_change),
        ("仅时间戳与属主变化", base, metadata),
    ]


def negative_pairs() -> List[Tuple[str, BuildScript, BuildScript]]:
    """必须触发缓存失效的变化（防止因忽略无关字段而错误复用）。"""
    base = BuildScript(name="neg_base", instructions=_instructions(),
                       context=_ctx())
    content_changed = BuildScript(
        name="content_changed", instructions=_instructions(),
        context=_ctx(app_content=b"print('hello v2')\n"))
    mode_changed = BuildScript(
        name="mode_changed", instructions=_instructions(),
        context=_ctx(app_mode=0o755))
    return [
        ("文件内容变化（必须失效）", base, content_changed),
        ("权限位变化（必须失效）", base, mode_changed),
    ]
