"""报警数据模型。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Finding:
    """一条评审报警。

    定位三要素：文件 (path)、行号 (line)、所在结构 (structure)。
    structure 形如 "module" / "function:foo" / "class:Bar" /
    "method:Bar.baz" / "nested:outer.inner"，指向报警节点在内层
    最近的函数或类定义。
    """

    rule_id: str
    message: str
    path: str
    line: int
    col: int
    structure: str

    def __str__(self) -> str:
        return (
            f"{self.path}:{self.line}:{self.col + 1}: "
            f"{self.rule_id} {self.message} [{self.structure}]"
        )
