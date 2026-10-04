"""命令行交互式补全库（仅标准库）。

公开接口：
    Snapshot         —— 不可变的权限快照（谁能看到哪些 group）
    ACL              —— 可原子更新的权限存储，带读写锁与版本号
    CompletionIndex  —— 排序数组 + 二分查找的补全索引
"""
from .acl import Snapshot, ACL
from .index import CompletionIndex, Session, CompletionResult

__all__ = [
    "Snapshot",
    "ACL",
    "CompletionIndex",
    "Session",
    "CompletionResult",
]
