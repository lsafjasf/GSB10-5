"""资源上界与规则能力定义（全部为纯标准库可验证的常量）。

上界依据
========
1. 时间 ``WALL_TIMEOUT_SEC = 2.0``
   - 巡检场景单条规则是“判定型”逻辑，正常规则应在毫秒级完成；
   - 2 秒是“明显异常（死循环/超量计算）”与“正常慢规则”之间留出
     两个数量级裕量的经验值。
   - 强制手段：父进程 ``Popen.wait(timeout)`` + ``SIGKILL``（硬上界，
     解释器内的 SIGXCPU 无法可靠中断纯 C 层忙等，SIGKILL 才能保证
     “一定停得下来”）；子进程内另设 ``RLIMIT_CPU=1`` 作为软上界，
     CPU 打满时更快地被内核杀掉。

2. 内存 ``MAX_MEMORY_BYTES = 256 MiB``
   - 规则语言里唯一允许分配“大对象”的入口是字符串/bytes 字面量与
     纯计算容器；一次巡检判定 256 MiB 远大于任何合理需求
     （匹配 1 MiB 输入只需数 MB）。
   - 强制手段：``RLIMIT_AS``（虚拟地址地址空间硬上限，超限立即
     ``MemoryError``，由子进程兜底为失败结果）。选 RLIMIT_AS 而非
     RLIMIT_DATA，是因为 Python 小对象分配走 mmap，RLIMIT_DATA
     管不住，RLIMIT_AS 是唯一对 CPython 实际有效的内核闸门。

3. 输出 ``MAX_OUTPUT_BYTES = 1 MiB``
   - 结果通道是 JSON，1 MiB 足以容纳任何结构化判定结果；
   - 超出即判定规则“滥用输出”，防止刷屏/拖垮汇总端。
   - 强制手段：
     a. 规则侧：受限 ``print`` 累加字节计数，超 ``ValueError``；
     b. 传输侧：父进程只从管道读取 ``MAX_OUTPUT_BYTES + 1`` 字节，
        多出来的读不到（写满管道后规则会阻塞，再被超时杀掉，
        双重保险）。

4. 规则源码 ``MAX_SOURCE_BYTES = 64 KiB``
   - 防止编译期资源消耗（超大字面量/深嵌套 AST）；在 AST 校验前拒绝。

5. 进程数 / 文件描述符
   - ``RLIMIT_NPROC=0``：禁止 fork/clone（无 subprocess、无 multiprocessing）；
   - ``RLIMIT_NOFILE=0``：规则启动前关闭 0/1/2 以外全部描述符，并把
     上限压到 3，无法 open 任何新 fd（无文件读写、无网络 socket）。
"""

WALL_TIMEOUT_SEC = 2.0
RLIMIT_CPU_SEC = 1
MAX_MEMORY_BYTES = 256 * 1024 * 1024
MAX_OUTPUT_BYTES = 1 * 1024 * 1024
MAX_SOURCE_BYTES = 64 * 1024

# 子进程结果协议：结果 JSON 之后以分隔符结尾，分隔符之后的内容算作 stdout 输出
RESULT_DELIMITER = b"\n<<<INSPECTOR_RESULT>>>\n"

# 结果分类（稳定的机器可读状态码）
STATUS_OK = "ok"
STATUS_ASSERT_FAILED = "assert_failed"   # 规则主动判定不通过（业务失败，非崩溃）
STATUS_RULE_ERROR = "rule_error"         # 规则运行时异常 / 输出超限 / MemoryError
STATUS_TIMEOUT = "timeout"               # 超过 WALL_TIMEOUT_SEC，被 SIGKILL
STATUS_OUTPUT_LIMIT = "output_limit"     # stdout 总量超过 MAX_OUTPUT_BYTES
STATUS_BAD_RULE = "bad_rule"             # 静态校验不通过（禁用语法/节点）
STATUS_INTERNAL_ERROR = "internal_error"

# ---------------------------------------------------------------------------
# 能力清单（白名单）。原则：默认全部禁止，只放开“纯计算”所需。
# ---------------------------------------------------------------------------

# 允许 import 的模块：全部为 Python 标准库中的纯计算模块，
# 不接触文件系统、网络、进程、线程、环境变量、时钟随机源等。
ALLOWED_MODULES = frozenset({
    "re",       # 正则（巡检匹配的核心需求）
    "json",     # 结构化编解码
    "math",
    "statistics",
    "itertools",
    "functools",
    "collections",
    "datetime",
    "ipaddress",
    "hashlib",
    "base64",
    "binascii",
})

# 可用内建：去掉了 open/input/eval/exec/compile/__import__/breakpoint/
# exit/quit/globals/locals/vars/memoryview 等；保留 type 但其属性访问
# （如 type.__subclasses__）在 AST 层被双下划线属性禁令一并封死。
ALLOWED_BUILTINS = frozenset({
    # 字面量与类型构造
    "bool", "bytes", "bytearray", "complex", "dict", "float", "frozenset",
    "int", "list", "set", "str", "tuple", "range",
    # 判定与转换
    "abs", "all", "any", "bin", "chr", "divmod", "enumerate", "filter",
    "hex", "isinstance", "issubclass", "len", "map", "max", "min", "oct",
    "ord", "pow", "repr", "reversed", "round", "sorted", "sum", "zip",
    # 异常（规则里 try/except、raise 需要）
    "Exception", "ValueError", "TypeError", "KeyError", "IndexError",
    "AttributeError", "RuntimeError", "StopIteration", "ZeroDivisionError",
    "ArithmeticError", "NotImplementedError", "NameError", "AssertionError",
    # 其它纯函数
    "format", "hash", "repr", "slice", "super",
    # 常量
    "True", "False", "None", "NotImplemented", "Ellipsis",
})

# 受限 print：唯一的输出通道，输出量被计数并封顶。
ALLOWED_EXTRA_BUILTINS = frozenset({"print"})
