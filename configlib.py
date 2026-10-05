"""configlib - 分层配置加载（仅标准库）。

来源优先级（低 -> 高，高者覆盖低者）:
    1. default  默认值（schema 声明）
    2. file     配置文件（JSON）
    3. env      环境变量（前缀默认 APP_，如 APP_PORT）
    4. cli      命令行（--key value 或 --key=value）

声明规则:
    - 每项配置在 schema 中声明 name / type / required；可再给 default、choices。
    - required=True 的键若四层都没有有效值，报 MissingRequired。
    - required=False 的键允许全程缺省；给了 default 则缺省时生效 default。
    - 类型: str / int / float / bool / list / dict。list/dict 在 env、cli 层
      用 JSON 文本解析（如 APP_TAGS='["a","b"]'）。
    - bool 的 env/cli 文本接受 true/false/1/0/yes/no/on/off（大小写不敏感）。

空字符串 vs 类型不符（区别对待）:
    - 空字符串（env/cli 中值为 ""，或文件层值就是 ""）-> EmptyString 错误，
      它不是合法值，不会覆盖任何低层值。
    - 非空但无法转换为声明类型（如 PORT=abc）-> TypeMismatch 错误，同样不覆盖。
    - 两种错误分别归类、分别计数，判定结果中 kind 字段不同。

未知键（含拼写近似）:
    - 任一上层中出现 schema 未声明的键 -> UnknownKey 错误。
    - 通过 difflib 计算与已知键的编辑距离，给出近似建议（相似度 >= 0.6）。
"""

from __future__ import annotations

import difflib
import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

LAYER_ORDER = ("default", "file", "env", "cli")

_NO_DEFAULT = object()


class ConfigError(Exception):
    """加载失败时抛出，携带完整 ErrorReport 列表。"""

    def __init__(self, errors: List["ErrorReport"]):
        self.errors = errors
        parts = [f"[{e.kind}] {e.key or '<root>'}: {e.message}" for e in errors]
        super().__init__("配置校验失败:\n  " + "\n  ".join(parts))


@dataclass
class ErrorReport:
    kind: str            # UnknownKey | MissingRequired | TypeMismatch | EmptyString
    key: str
    layer: Optional[str]
    message: str
    raw_value: Any = None
    suggestions: List[str] = field(default_factory=list)


@dataclass
class SourceInfo:
    layer: str                  # 生效值来自哪一层
    value: Any                  # 生效值（已转换类型）
    present_in: List[str]       # 哪些层出现过该键（不论是否合法）
    layer_values: Dict[str, Any] = field(default_factory=dict)  # 各层原始值
    layer_errors: Dict[str, str] = field(default_factory=dict)  # 层 -> 错误kind


@dataclass
class ConfigResult:
    values: Dict[str, Any]
    sources: Dict[str, SourceInfo]
    errors: List[ErrorReport]
    unknown_keys: List[ErrorReport]

    def require_ok(self) -> Dict[str, Any]:
        if self.errors:
            raise ConfigError(self.errors)
        return self.values


# ---------------------------------------------------------------- 类型转换

_TRUE = {"true", "1", "yes", "on"}
_FALSE = {"false", "0", "no", "off"}


def _convert(decl_type: str, raw: Any, layer: str) -> Any:
    """把某层的原始值转换为声明类型；失败抛 ValueError。"""
    if layer in ("env", "cli"):
        if not isinstance(raw, str):
            raise ValueError("env/cli 值应为字符串")
        text = raw
        if decl_type == "str":
            return text
        if decl_type == "int":
            return int(text.strip())
        if decl_type == "float":
            return float(text.strip())
        if decl_type == "bool":
            low = text.strip().lower()
            if low in _TRUE:
                return True
            if low in _FALSE:
                return False
            raise ValueError(f"布尔值需是 {sorted(_TRUE | _FALSE)} 之一")
        if decl_type in ("list", "dict"):
            parsed = json.loads(text)
            if decl_type == "list" and not isinstance(parsed, list):
                raise ValueError("需要 JSON 数组")
            if decl_type == "dict" and not isinstance(parsed, dict):
                raise ValueError("需要 JSON 对象")
            return parsed
        raise ValueError(f"未知类型 {decl_type!r}")

    # 文件层：JSON 已解析，按类型严格校验
    if decl_type == "str":
        if isinstance(raw, bool) or not isinstance(raw, str):
            raise ValueError("需要字符串")
        return raw
    if decl_type == "int":
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise ValueError("需要整数")
        return raw
    if decl_type == "float":
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ValueError("需要数字")
        return float(raw)
    if decl_type == "bool":
        if not isinstance(raw, bool):
            raise ValueError("需要布尔值")
        return raw
    if decl_type == "list":
        if not isinstance(raw, list):
            raise ValueError("需要数组")
        return raw
    if decl_type == "dict":
        if not isinstance(raw, dict):
            raise ValueError("需要对象")
        return raw
    raise ValueError(f"未知类型 {decl_type!r}")


# ---------------------------------------------------------------- schema

@dataclass
class Item:
    name: str
    type: str = "str"
    required: bool = False
    default: Any = None
    has_default: bool = False
    choices: Optional[list] = None

    def __post_init__(self):
        if self.type not in ("str", "int", "float", "bool", "list", "dict"):
            raise ValueError(f"{self.name}: 不支持的类型 {self.type!r}")


def item(name: str, type: str = "str", required: bool = False,
         default: Any = _NO_DEFAULT, choices: Optional[list] = None) -> Item:
    has = default is not _NO_DEFAULT
    return Item(name=name, type=type, required=required,
                default=default if has else None,
                has_default=has, choices=choices)


def required(name: str, type: str = "str", choices: Optional[list] = None) -> Item:
    return Item(name=name, type=type, required=True, choices=choices)


# ---------------------------------------------------------------- 层解析

def parse_cli_args(argv: List[str]) -> Dict[str, str]:
    """解析 --key value / --key=value；裸 --flag 记为空串（触发 EmptyString 判定）。"""
    out: Dict[str, str] = {}
    i = 0
    tokens = argv
    while i < len(tokens):
        tok = tokens[i]
        if tok.startswith("--"):
            body = tok[2:]
            if "=" in body:
                key, val = body.split("=", 1)
                out[key] = val
            elif i + 1 < len(tokens) and not tokens[i + 1].startswith("--"):
                out[body] = tokens[i + 1]
                i += 1
            else:
                out[body] = ""  # 裸 --flag 视为空值，触发 EmptyString 判定
        i += 1
    return out


def _fuzzy_suggest(key: str, known: List[str], cutoff: float = 0.6) -> List[str]:
    return difflib.get_close_matches(key, known, n=3, cutoff=cutoff)


# ---------------------------------------------------------------- 加载

@dataclass
class Schema:
    items: List[Item]

    def __post_init__(self):
        self._map = {it.name: it for it in self.items}

    @property
    def names(self) -> List[str]:
        return list(self._map)


def load_config(schema: Schema,
                file_path: Optional[str] = None,
                env: Optional[Dict[str, str]] = None,
                argv: Optional[List[str]] = None,
                env_prefix: str = "APP_") -> ConfigResult:
    """分层加载配置。env/argv 默认取真实进程环境与命令行，可传入以便测试。"""
    import sys
    env = os.environ if env is None else env
    argv = sys.argv[1:] if argv is None else argv

    errors: List[ErrorReport] = []
    unknown: List[ErrorReport] = []

    # 1) 收集各层原始键值
    layers: Dict[str, Dict[str, Any]] = {
        "default": {it.name: it.default for it in schema.items if it.has_default},
        "file": {},
        "env": {},
        "cli": parse_cli_args(argv),
    }

    if file_path is not None:
        with open(file_path, "r", encoding="utf-8") as fh:
            content = json.load(fh)
        if not isinstance(content, dict):
            raise ConfigError([ErrorReport("TypeMismatch", "<root>", "file",
                                           "配置文件顶层必须是 JSON 对象")])
        layers["file"] = content

    for env_key, env_val in env.items():
        if env_key.startswith(env_prefix):
            layers["env"][env_key[len(env_prefix):].lower()] = env_val

    known = schema.names

    # 2) 逐键、逐层：空串与类型不符分别判定
    converted: Dict[str, Dict[str, Any]] = {n: {} for n in known}
    layer_error_kind: Dict[str, Dict[str, str]] = {n: {} for n in known}
    present_in: Dict[str, List[str]] = {n: [] for n in known}

    for it in schema.items:
        if it.has_default:
            converted[it.name]["default"] = it.default
            present_in[it.name].append("default")

    def scan(layer: str, raw_map: Dict[str, Any]):
        for key, raw in raw_map.items():
            if key not in schema._map:
                sugg = _fuzzy_suggest(key, known)
                msg = f"未知配置项 {key!r}（层 {layer}）"
                if sugg:
                    msg += "，是否想写: " + ", ".join(sugg)
                rep = ErrorReport("UnknownKey", key, layer, msg,
                                  raw_value=raw, suggestions=sugg)
                errors.append(rep)
                unknown.append(rep)
                continue
            present_in[key].append(layer)
            decl = schema._map[key]
            if isinstance(raw, str) and raw == "":
                kind_msg = "空字符串不是合法配置值（必填项请提供非空值；可选项请删除该键）"
                rep = ErrorReport("EmptyString", key, layer, kind_msg, raw_value="")
                errors.append(rep)
                layer_error_kind[key][layer] = "EmptyString"
                continue
            try:
                converted_value = _convert(decl.type, raw, layer)
                if decl.choices is not None and converted_value not in decl.choices:
                    raise ValueError(f"值 {converted_value!r} 不在允许集合 {decl.choices} 中")
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                rep = ErrorReport("TypeMismatch", key, layer,
                                  f"类型应为 {decl.type}，原始值 {raw!r} 转换失败: {exc}",
                                  raw_value=raw)
                errors.append(rep)
                layer_error_kind[key][layer] = "TypeMismatch"
                continue
            converted[key][layer] = converted_value

    scan("file", layers["file"])
    scan("env", layers["env"])
    scan("cli", layers["cli"])

    # 3) 高优先级覆盖低优先级，计算生效值与来源
    values: Dict[str, Any] = {}
    sources: Dict[str, SourceInfo] = {}

    for it in schema.items:
        key = it.name
        effective_layer = None
        for layer in reversed(LAYER_ORDER):
            if layer in converted[key]:
                effective_layer = layer
                break
        if effective_layer is None:
            # 没有任何合法值
            if it.required:
                errors.append(ErrorReport(
                    "MissingRequired", key, None,
                    f"必填项 {key!r}（类型 {it.type}）在 默认/文件/环境/命令行 中均无有效值"))
            continue
        val = converted[key][effective_layer]
        values[key] = val
        sources[key] = SourceInfo(
            layer=effective_layer,
            value=val,
            present_in=present_in[key],
            layer_values={l: layers[l].get(key) for l in LAYER_ORDER
                          if l in layers and key in layers[l]},
            layer_errors=layer_error_kind[key],
        )

    return ConfigResult(values=values, sources=sources, errors=errors,
                        unknown_keys=unknown)
