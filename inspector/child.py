"""子进程内执行的规则运行器。

启动方式（由父进程 runner.py 发起）：
    python3 -I -S -c "import sys; sys.path.insert(0, <pkg_parent>);
                      from inspector.child import main; main()"

-I -S：忽略环境变量/用户 site 包，规则代码拿不到任何环境信息。
输入协议：stdin 传入一个 JSON 文档 {"source": <规则源码>, "data": <巡检输入>}。
输出协议：规则 print 的内容在前，随后是 RESULT_DELIMITER + 结果 JSON。
"""

import json
import os
import resource
import sys


def _preimport_allowed_modules():
    """在收紧 RLIMIT_NOFILE 之前，把白名单模块全部导入。

    之后规则里的 import 只是查 sys.modules，不再需要打开任何文件。
    """
    from .limits import ALLOWED_MODULES

    for name in ALLOWED_MODULES:
        __import__(name)


def _apply_rlimits():
    from .limits import MAX_MEMORY_BYTES, RLIMIT_CPU_SEC

    # CPU 软上界：CPU 打满时内核 SIGKILL（比墙钟超时更快生效）
    resource.setrlimit(resource.RLIMIT_CPU, (RLIMIT_CPU_SEC, RLIMIT_CPU_SEC))
    # 虚拟地址空间硬上界：超限分配立即 MemoryError
    resource.setrlimit(resource.RLIMIT_AS, (MAX_MEMORY_BYTES, MAX_MEMORY_BYTES))
    # 不允许创建任何文件（配合 AST 层禁 open，双保险）
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    # 不允许再打开新 fd（无 socket/文件）；0/1/2 已打开，保留
    try:
        resource.setrlimit(resource.RLIMIT_NOFILE, (3, 3))
    except (ValueError, OSError):
        pass
    # 不允许 fork 出新进程（防 fork 炸弹）；失败不致命，跳过即可
    try:
        resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))
    except (ValueError, OSError, AttributeError):
        pass
    # 剥离环境变量，规则经 os.environ 也读不到任何东西
    os.environ.clear()


def _jsonable(value):
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return repr(value)


def main():
    from .limits import (
        RESULT_DELIMITER,
        STATUS_ASSERT_FAILED,
        STATUS_OK,
        STATUS_OUTPUT_LIMIT,
        STATUS_RULE_ERROR,
    )
    from .sandbox import RuleRejected, build_restricted_builtins, validate_source

    _preimport_allowed_modules()
    _apply_rlimits()

    raw = sys.stdin.buffer.read()
    try:
        request = json.loads(raw.decode("utf-8", errors="replace"))
        source = request["source"]
        data = request.get("data")
    except (ValueError, KeyError, AttributeError) as exc:
        source, data = None, None
        payload = {
            "status": STATUS_RULE_ERROR,
            "error": f"请求格式错误: {exc}",
            "result": None,
        }
        sys.stdout.write(RESULT_DELIMITER.decode())
        sys.stdout.write(json.dumps(payload, ensure_ascii=False))
        sys.stdout.flush()
        os._exit(0)

    status = STATUS_OK
    error = None
    result = None
    output_acc = [0]

    try:
        tree = validate_source(source)
        env = {
            "__builtins__": build_restricted_builtins(output_acc),
            "__name__": "__rule__",
        }
        code = compile(tree, "<rule>", "exec")
        exec(code, env)  # noqa: S102 - 有意为之，沙箱由白名单+rlimit 构成

        check = env.get("check")
        if not callable(check):
            raise RuleRejected("规则必须定义 check(data) 函数")

        result = _jsonable(check(data))
    except AssertionError as exc:
        status = STATUS_ASSERT_FAILED
        error = str(exc) or "assert 判定不通过"
    except MemoryError:
        status = STATUS_RULE_ERROR
        error = "MemoryError: 超出内存上界"
    except ValueError as exc:
        if "输出量超过上限" in str(exc):
            status = STATUS_OUTPUT_LIMIT
            error = str(exc)
        else:
            status = STATUS_RULE_ERROR
            error = f"ValueError: {exc}"
    except RuleRejected as exc:
        # 理论上父进程已预校验，走到这里说明父子校验不一致
        status = STATUS_RULE_ERROR
        error = f"规则被拒绝: {exc}"
    except BaseException as exc:  # noqa: BLE001 - 兜底，任何异常都不能让子进程裸崩
        status = STATUS_RULE_ERROR
        error = f"{type(exc).__name__}: {exc}"

    payload = {"status": status, "error": error, "result": result}
    sys.stdout.write(RESULT_DELIMITER.decode())
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
