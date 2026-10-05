"""Schema model: describes what a given version of the system writes/reads."""
from dataclasses import dataclass, field
from typing import Any, Tuple

SCALAR_TYPES = ("int", "float", "str", "bool")


@dataclass(frozen=True)
class FieldSpec:
    name: str
    ftype: str
    required: bool = True
    enum_values: Tuple[str, ...] = ()
    default: Any = None


@dataclass(frozen=True)
class IndexSpec:
    name: str
    keys: Tuple[str, ...]
    unique: bool = False
    format_version: int = 1
    rebuildable: bool = True


@dataclass(frozen=True)
class ContractOp:
    name: str
    request_fields: Tuple[FieldSpec, ...] = ()
    response_fields: Tuple[FieldSpec, ...] = ()


@dataclass(frozen=True)
class Schema:
    version: str
    fields: Tuple[FieldSpec, ...]
    indexes: Tuple[IndexSpec, ...] = ()
    contract: Tuple[ContractOp, ...] = ()
    data_format_version: int = 1
    supported_index_formats: Tuple[int, ...] = (1,)

    def field_map(self):
        return {f.name: f for f in self.fields}

    def index_map(self):
        return {i.name: i for i in self.indexes}

    def contract_map(self):
        return {c.name: c for c in self.contract}
