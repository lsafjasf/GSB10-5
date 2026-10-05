"""跨进程确定性校验：固定工作负载，输出全部结果的规范化 JSON 的 sha256。

用法（两次运行哈希必须一致）：
  PYTHONHASHSEED=0 python -m export_negotiation.check_determinism
  PYTHONHASHSEED=1 python -m export_negotiation.check_determinism
"""

import hashlib
import json
import random

from . import negotiate, FORMATS, FIELD_TYPES


def main():
    rng = random.Random(20261006)
    tokens = list(FORMATS) + ["docx", "parquet", "JSON", " yml "]
    h = hashlib.sha256()
    n = 0
    for _ in range(5000):
        fmt = rng.choice(tokens)
        fields = [
            {"name": "f%d" % i, "type": rng.choice(FIELD_TYPES)}
            for i in range(rng.randint(0, 8))
        ]
        r = negotiate(fmt, fields)
        h.update(json.dumps(r, ensure_ascii=False, sort_keys=True).encode())
        h.update(b"\n")
        n += 1
    print("cases=%d sha256=%s" % (n, h.hexdigest()))


if __name__ == "__main__":
    main()
