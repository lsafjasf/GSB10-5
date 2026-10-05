"""Simulates the OLD deployed version reading bytes that the NEW version wrote."""
from dataclasses import dataclass, asdict
from typing import Any, Dict, List

from . import codec

SURFACE_FORMAT = "data_format"
SURFACE_ENUM = "enum"
SURFACE_INDEX = "index"
SURFACE_CONTRACT = "contract"

# Kind constants, reused by checker/steps and by self-tests.
KIND_FORMAT_VERSION = "data_format_version_unsupported"
KIND_UNKNOWN_FIELD = "unknown_field_ignored"
KIND_MISSING_REQUIRED = "missing_required_field"
KIND_TYPE_MISMATCH = "type_mismatch"
KIND_ENUM_UNKNOWN = "unknown_enum_value"
KIND_INDEX_FORMAT = "index_format_version_unsupported"
KIND_INDEX_LAYOUT = "index_key_layout_changed"
KIND_CONTRACT_MISSING = "contract_field_removed"
KIND_CONTRACT_TYPE = "contract_field_type_changed"
KIND_CONTRACT_OP_MISSING = "contract_operation_removed"


@dataclass
class Issue:
    surface: str
    kind: str
    field: str
    expected: str
    actual: str
    record_seq: int = -1
    record_id: str = ""
    convertible: bool = False
    conversion: str = ""

    def as_dict(self):
        return asdict(self)


_TYPE_MAP = {
    "int": int,
    "float": (int, float),
    "str": str,
    "bool": bool,
}


def _value_matches_type(value, ftype):
    if ftype == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if ftype == "float":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, _TYPE_MAP.get(ftype, ()))


class OldVersionReader:
    """Reader behaviour of the old release.

    - Unknown JSON object keys are IGNORED (forward-tolerant deserialization).
    - Every known required field must exist; type is checked strictly.
    - Enum values are validated against the old version's closed enum set.
    - Index files are rejected unless their on-disk format version is supported
      and their key layout matches the old version's definition.
    """

    def __init__(self, schema):
        self.schema = schema

    def read_record(self, record, record_seq=0):
        issues = []
        rid = str(record.get("id", "?"))
        known = self.schema.field_map()

        for fname, fspec in known.items():
            if fname not in record:
                if fspec.required:
                    issues.append(Issue(
                        SURFACE_FORMAT, KIND_MISSING_REQUIRED, fname,
                        ftype_label(fspec), "<absent>", record_seq, rid))
                continue
            value = record[fname]
            if fspec.enum_values and value not in fspec.enum_values:
                issues.append(Issue(
                    SURFACE_ENUM, KIND_ENUM_UNKNOWN, fname,
                    "one of %s" % list(fspec.enum_values), repr(value),
                    record_seq, rid))
                continue
            if not _value_matches_type(value, fspec.ftype):
                issues.append(Issue(
                    SURFACE_FORMAT, KIND_TYPE_MISMATCH, fname,
                    ftype_label(fspec), type(value).__name__, record_seq, rid))

        for fname in record:
            if fname not in known:
                issues.append(Issue(
                    SURFACE_FORMAT, KIND_UNKNOWN_FIELD, fname,
                    "<ignored>", "present", record_seq, rid,
                    convertible=True,
                    conversion="旧版本忽略该未知字段，无需处理"))
        return issues

    def read_data_file(self, data):
        header, lines = codec.split_data_file(data)
        issues = []
        if header.get("encoding") != "jsonl":
            issues.append(Issue(SURFACE_FORMAT, KIND_FORMAT_VERSION,
                                "encoding", "jsonl", repr(header.get("encoding"))))
        elif header.get("format_version", 1) > self.schema.data_format_version:
            issues.append(Issue(SURFACE_FORMAT, KIND_FORMAT_VERSION,
                                "format_version",
                                "<= %s" % self.schema.data_format_version,
                                str(header.get("format_version"))))
        records = []
        for seq, line in enumerate(lines):
            records.append(codec.decode_record(line))
        return header, records, issues

    def read_index_file(self, data):
        first_nl = data.index(b"\n")
        header = codec.parse_index_header(data[:first_nl])
        old_index = self.schema.index_map().get(header.get("index"))
        issues = []
        name = header.get("index", "?")
        if old_index is None:
            return header, issues
        if header.get("format_version") not in self.schema.supported_index_formats:
            issues.append(Issue(
                SURFACE_INDEX, KIND_INDEX_FORMAT,
                "indexes.%s.format_version" % name,
                "one of %s" % list(self.schema.supported_index_formats),
                str(header.get("format_version")),
                convertible=old_index.rebuildable,
                conversion=("删除新版索引文件并用旧版本重建（该索引可由数据重建）"
                            if old_index.rebuildable else "索引无法由现有数据重建")))
        if tuple(header.get("keys", ())) != tuple(old_index.keys):
            issues.append(Issue(
                SURFACE_INDEX, KIND_INDEX_LAYOUT,
                "indexes.%s.keys" % name,
                list(old_index.keys), list(header.get("keys", ())),
                convertible=old_index.rebuildable,
                conversion=("丢弃新版索引文件，按旧版键序重建索引"
                            if old_index.rebuildable else "键结构变化且无法重建")))
        return header, issues


def ftype_label(fspec):
    if fspec.enum_values:
        return "enum(%s)" % ",".join(fspec.enum_values)
    return fspec.ftype
