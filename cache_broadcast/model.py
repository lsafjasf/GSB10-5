"""失效消息模型。"""

from typing import NamedTuple


class Invalidation(NamedTuple):
    """一条缓存失效消息。

    字段语义（详见 SEMANTICS.md）：
    - seq:     全局单调递增的日志序号（1 起），用于去重、乱序重放与补齐。
    - key:     目标缓存键。
    - version: 失效生效时该键的最新数据版本号（单调递增）。
               语义：凡是缓存自 version < 该值 的旧数据的条目一律失效；
               version >= 该值 的条目（即广播之后才写入的新数据）不受影响。
    """

    seq: int
    key: str
    version: int
