"""configkit — 分层配置加载库（仅标准库）。

来源优先级（高 -> 低）：
    命令行 (cli) > 环境变量 (env) > 配置文件 (file) > 默认值 (default)

规则：
    1. 每个键必须在 schema 中声明：类型、是否必填、默认值（非必填时）。
    2. 同一键被多层提供时，高优先级层生效，低层被覆盖（记录在 overridden 中）。
    3. 空字符串（"" 或仅空白）视为"显式空值"：记 empty_value 问题，
       该层不提供值，继续向低层回退；必填键最终无值时报 missing_required。
    4. 类型不符记 type_error 问题，该层不提供值，继续向低层回退。
    5. 任何层中出现 schema 未声明的键，记 unknown_key 问题（附拼写近似建议）。
    6. 非必填键所有层都未提供时，使用 schema 默认值，来源记为 default。
"""

from __future__ import annotations

import difflib
import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

LAYER_CLI = "cli"
LAYER_ENV = "env"
LAYER_FILE = "file"
LAYER_DEFAULT = "default"

# 解析顺序即优先级顺序（高 -> 低）
LAYER_ORDER = (LAYER_CLI, LAYER_ENV, LAYER_FILE)

TRUE_WORDS = {"1", "true", "yes", "on"}
FALSE_WORDS = {"0", "false", "no", "off"}

KIND_UNKNOWN_KEY = "unknown_key"
KIND_MISSING_REQUIRED = "missing_required"
KIND_EMPTY_VALUE = "empty_value"
KIND_TYPE_ERROR = "type_error"


@dataclass(frozen=True)
class Field:
    """单个配置项的声明。"""

    type: type = str
    required: bool = False
    default: Any = None
    env: Optional[str] = None  # 环境变量名，缺省为 prefix + 键名大写
    doc: str = ""

    def __post_init__(self):
        if self.required and self.default is not None:
            raise ValueError("required 字段不允许声明 default")
        if not self.required and self.default is not None:
            ok, _ = _check_native_type(self.default, self.type)
            if not ok:
                raise ValueError(
                    f"default 值 {self.default!r} 与声明类型 {self.type.__name__} 不符"
                )


@dataclass(frozen=True)
class Issue:
    """加载过程中发现的问题。"""

    kind: str
    key: str
    layer: Optional[str] = None
    message: str = ""
    expected: Optional[str] = None
    actual: Optional[str] = None
    suggestions: Tuple[str, ...] = ()


@dataclass
class LoadResult:
    values: Dict[str, Any] = field(default_factory=dict)
    sources: Dict[str, str] = field(default_factory=dict)  # key -> 生效层
    overridden: Dict[str, List[str]] = field(default_factory=dict)  # key -> 被覆盖的层
    issues: List[Issue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues

    def unknown_keys(self) -> List[str]:
        return [i.key for i in self.issues if i.kind == KIND_UNKNOWN_KEY]

    def raise_if_failed(self) -> None:
        if self.issues:
            raise ConfigError(self.issues)


class ConfigError(Exception):
    def __init__(self, issues: List[Issue]):
        self.issues = list(issues)
        super().__init__("; ".join(f"[{i.kind}] {i.key}: {i.message}" for i in issues))


def _check_native_type(value: Any, typ: type) -> Tuple[bool, Optional[Any]]:
    """文件层（JSON 原生类型）的类型判定。bool 是 int 子类，需特判。"""
    if typ is bool:
        return (isinstance(value, bool), value if isinstance(value, bool) else None)
    if typ is int:
        ok = isinstance(value, int) and not isinstance(value, bool)
        return (ok, value if ok else None)
    if typ is float:
        ok = isinstance(value, (int, float)) and not isinstance(value, bool)
        return (ok, float(value) if ok else None)
    if typ is str:
        return (isinstance(value, str), value if isinstance(value, str) else None)
    return (isinstance(value, typ), value if isinstance(value, typ) else None)


def _parse_string(raw: str, typ: type) -> Tuple[bool, Optional[Any]]:
    """环境变量 / 命令行层（字符串）的类型解析。"""
    if typ is str:
        return True, raw
    if typ is int:
        try:
            return True, int(raw, 10)
        except ValueError:
            return False, None
    if typ is float:
        try:
            return True, float(raw)
        except ValueError:
            return False, None
    if typ is bool:
        low = raw.strip().lower()
        if low in TRUE_WORDS:
            return True, True
        if low in FALSE_WORDS:
            return True, False
        return False, None
    raise ValueError(f"不支持的类型: {typ!r}")


def _is_empty(value: Any) -> bool:
    return isinstance(value, str) and value.strip() == ""


def parse_cli_args(argv: List[str]) -> Dict[str, str]:
    """解析 ['--db-host=x', '--db-port', '5432'] 形式的命令行参数。

    不做 schema 校验，未知键原样保留，交由 load() 统一报告。
    """
    out: Dict[str, str] = {}
    i = 0
    while i < len(argv):
        token = argv[i]
        if token.startswith("--"):
            body = token[2:]
            if "=" in body:
                name, value = body.split("=", 1)
                out[name.strip().replace("-", "_")] = value
            elif i + 1 < len(argv) and not argv[i + 1].startswith("--"):
                out[body.strip().replace("-", "_")] = argv[i + 1]
                i += 1
            else:
                out[body.strip().replace("-", "_")] = ""
        i += 1
    return out


def load(
    schema: Mapping[str, Field],
    *,
    file_path: Optional[str] = None,
    file_data: Optional[Mapping[str, Any]] = None,
    env: Optional[Mapping[str, str]] = None,
    cli_args: Optional[List[str]] = None,
    cli_data: Optional[Mapping[str, str]] = None,
    env_prefix: str = "APP_",
) -> LoadResult:
    """按优先级合并各层配置并校验，返回 LoadResult（不抛异常）。"""
    env_map = os.environ if env is None else env
    result = LoadResult()

    # ---- 收集各层原始数据 ----
    layers: Dict[str, Dict[str, Any]] = {LAYER_CLI: {}, LAYER_ENV: {}, LAYER_FILE: {}}

    if file_path is not None:
        with open(file_path, "r", encoding="utf-8") as fh:
            file_data = json.load(fh)
    if file_data:
        if not isinstance(file_data, Mapping):
            raise ConfigError([Issue(KIND_TYPE_ERROR, "<file>", LAYER_FILE,
                                     "配置文件顶层必须是对象/字典")])
        layers[LAYER_FILE] = dict(file_data)

    # env：schema 键 -> 环境变量名 的反向映射，同时记录带前缀的未知变量
    env_name_to_key: Dict[str, str] = {}
    for key, f in schema.items():
        env_name_to_key[f.env or (env_prefix + key.upper())] = key
    for name, value in env_map.items():
        if name in env_name_to_key:
            layers[LAYER_ENV][env_name_to_key[name]] = value
        elif name.startswith(env_prefix):
            layers[LAYER_ENV][name[len(env_prefix):].lower()] = value  # 未知键

    cli = dict(cli_data) if cli_data else {}
    if cli_args:
        cli.update(parse_cli_args(cli_args))
    layers[LAYER_CLI] = cli

    # ---- 未知键检测（含拼写近似建议） ----
    known = set(schema)
    for layer in LAYER_ORDER:
        for key in layers[layer]:
            if key not in known:
                sugg = tuple(difflib.get_close_matches(key, known, n=3, cutoff=0.6))
                hint = f"，是否想写: {', '.join(sugg)}" if sugg else ""
                result.issues.append(Issue(
                    KIND_UNKNOWN_KEY, key, layer,
                    f"层 {layer} 中的键 {key!r} 未在 schema 中声明{hint}",
                    suggestions=sugg,
                ))

    # ---- 逐键解析：按优先级取第一个"有效提供"的层 ----
    for key, f in schema.items():
        # 所有"出现过该键"的层（无论值是否有效），用于覆盖链记录
        present = [l for l in LAYER_ORDER if key in layers[l]]
        effective: Optional[str] = None
        value: Any = None
        # 所有出现的层都做校验（空串/类型问题即使在被覆盖的层也要报告），
        # 但生效值只取第一个有效层。
        for layer in present:
            raw = layers[layer][key]
            if _is_empty(raw):
                result.issues.append(Issue(
                    KIND_EMPTY_VALUE, key, layer,
                    f"层 {layer} 中键 {key!r} 的值为空字符串，该层不参与取值",
                ))
                continue
            if layer == LAYER_FILE:
                ok, parsed = _check_native_type(raw, f.type)
            else:
                ok, parsed = _parse_string(raw, f.type) if isinstance(raw, str) \
                    else _check_native_type(raw, f.type)
            if not ok:
                result.issues.append(Issue(
                    KIND_TYPE_ERROR, key, layer,
                    f"层 {layer} 中键 {key!r} 的值 {raw!r} 类型不符，该层不参与取值",
                    expected=f.type.__name__, actual=type(raw).__name__,
                ))
                continue
            if effective is None:
                effective = layer
                value = parsed

        if effective is not None:
            result.values[key] = value
            result.sources[key] = effective
            lower = [l for l in present if l != effective]
            # 低层中虽未提供但存在默认值的，也记入覆盖链
            if not f.required:
                lower = lower + [LAYER_DEFAULT]
            if lower:
                result.overridden[key] = lower
        elif f.required:
            result.issues.append(Issue(
                KIND_MISSING_REQUIRED, key, None,
                f"必填键 {key!r} 在 cli/env/file 各层均未提供有效值",
            ))
        else:
            result.values[key] = f.default
            result.sources[key] = LAYER_DEFAULT
            if present:
                result.overridden[key] = list(present)  # 高层值无效，默认生效

    return result


def load_or_raise(schema: Mapping[str, Field], **kwargs: Any) -> LoadResult:
    result = load(schema, **kwargs)
    result.raise_if_failed()
    return result
