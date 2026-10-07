"""导入名只出现在注释/字符串里，仍应判定为未使用导入（CR004）。"""
import hashlib  # 本注释提到 hashlib，但不算使用

TEXT = "hashlib.md5 很快"  # 字符串里提到 hashlib，同样不算使用
