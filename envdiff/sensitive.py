"""敏感参数（密钥、证书、令牌等）的识别与脱敏。

处理方式：
1. 按参数名正则 + 值特征（PEM 块等）识别敏感项；
2. 对比仍在内存中用规范化后的原文进行，结果准确；
3. 报告中一律不输出原文，只输出掩码与 SHA-256 指纹（前 12 位），
   指纹可用于人工核对"两边到底是不是同一个密钥"，且不可逆。
"""
from __future__ import annotations

import hashlib
import re

SENSITIVE_KEY_RE = re.compile(
    r"password|passwd|pwd|secret|token|api[_-]?key|private[_-]?key|"
    r"credential|cert|certificate|\bpem\b|ssh|access[_-]?key|auth",
    re.IGNORECASE,
)
_SENSITIVE_VALUE_RE = re.compile(
    r"-----BEGIN [A-Z ]*(PRIVATE KEY|CERTIFICATE|OPENSSH|RSA|EC|PGP)",
    re.IGNORECASE,
)

MASK = "******"


def is_sensitive(key: str, value=None) -> bool:
    if SENSITIVE_KEY_RE.search(str(key)):
        return True
    if value is not None and _SENSITIVE_VALUE_RE.search(str(value)):
        return True
    return False


def fingerprint(value) -> str:
    """对规范化后的值取 SHA-256 指纹（前 12 位），用于安全地核对一致性。"""
    collapsed = " ".join(str(value).split())
    return hashlib.sha256(collapsed.encode("utf-8")).hexdigest()[:12]
