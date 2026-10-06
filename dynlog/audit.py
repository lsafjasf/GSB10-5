"""审计日志：谁在什么时候把哪个日志源调成了什么级别。

记录为只追加（append-only），每条带单调递增序号与墙钟时间戳，
可整体导出为文本文件供归档或接入 SIEM。
"""

import time
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class AuditRecord:
    seq: int
    ts: float
    operator: str
    source: str
    old_level: str
    new_level: str
    version: int
    reason: str

    def line(self):
        when = datetime.fromtimestamp(self.ts).isoformat(timespec="milliseconds")
        return (
            "#%d %s operator=%s source=%s %s->%s version=%d reason=%s"
            % (
                self.seq,
                when,
                self.operator,
                self.source,
                self.old_level,
                self.new_level,
                self.version,
                self.reason or "-",
            )
        )


class AuditLog:
    def __init__(self, clock=time.time):
        self._clock = clock
        self._records = []
        self._seq = 0

    def append(self, operator, source, old_level, new_level, version, reason=""):
        self._seq += 1
        record = AuditRecord(
            seq=self._seq,
            ts=self._clock(),
            operator=operator,
            source=source,
            old_level=old_level,
            new_level=new_level,
            version=version,
            reason=reason,
        )
        self._records.append(record)
        return record

    @property
    def records(self):
        return list(self._records)

    def __len__(self):
        return len(self._records)

    def query(self, source=None, operator=None):
        return [
            r
            for r in self._records
            if (source is None or r.source == source)
            and (operator is None or r.operator == operator)
        ]

    def dump(self, path):
        with open(path, "w", encoding="utf-8") as fh:
            for record in self._records:
                fh.write(record.line() + "\n")
