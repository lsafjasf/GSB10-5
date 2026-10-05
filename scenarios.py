"""Scenario definitions: one old release and several new releases whose
written data is checked for old-version readability."""
import random

from rollback_verify import FieldSpec as F, IndexSpec as I, ContractOp as C, Schema
from rollback_verify import codec

STATUS_V1 = ("ACTIVE", "SUSPENDED", "CLOSED")


def build_old_schema():
    id_ = F("id", "int")
    name = F("name", "str")
    status = F("status", "str", enum_values=STATUS_V1)
    balance = F("balance", "float", required=False)
    created_at = F("created_at", "str", required=False)
    return Schema(
        version="v1",
        fields=(id_, name, status, balance, created_at),
        indexes=(
            I("pk_id", ("id",), unique=True),
            I("idx_status", ("status",), unique=False),
        ),
        contract=(
            C("get_user", response_fields=(id_, name, status, balance)),
            C("update_user", request_fields=(id_, F("status", "str", required=False,
                                                     enum_values=STATUS_V1))),
        ),
        data_format_version=1,
        supported_index_formats=(1,),
    )


OLD = build_old_schema()

# 1) 完全兼容：新版本字段/枚举/索引/契约与旧版本完全一致
SCHEMA_COMPAT = Schema(
    version="v2-compat",
    fields=OLD.fields,
    indexes=OLD.indexes,
    contract=OLD.contract,
    data_format_version=1,
    supported_index_formats=(1,),
)

# 2) 仅新增字段：新增可选 email/tags；旧版本反序列化时忽略未知键
SCHEMA_ADDITIVE = Schema(
    version="v2-additive-field",
    fields=OLD.fields + (F("email", "str", required=False),
                         F("remark", "str", required=False)),
    indexes=OLD.indexes,
    contract=(
        C("get_user", response_fields=(F("id", "int"), F("name", "str"),
                                       F("status", "str", enum_values=STATUS_V1),
                                       F("balance", "float", required=False))),
        C("update_user", request_fields=(F("id", "int"),
                                         F("status", "str", required=False,
                                           enum_values=STATUS_V1))),
    ),
)

# 3) 新增枚举值：status 增加 PENDING_DELETE
SCHEMA_NEW_ENUM = Schema(
    version="v2-new-enum",
    fields=(F("id", "int"), F("name", "str"),
            F("status", "str", enum_values=STATUS_V1 + ("PENDING_DELETE",)),
            F("balance", "float", required=False),
            F("created_at", "str", required=False)),
    indexes=(I("pk_id", ("id",), unique=True),
             I("idx_status", ("status",), unique=False)),
    contract=(
        C("get_user", response_fields=(F("id", "int"), F("name", "str"),
                                       F("status", "str",
                                         enum_values=STATUS_V1 + ("PENDING_DELETE",)),
                                       F("balance", "float", required=False))),
        C("update_user", request_fields=(F("id", "int"),
                                         F("status", "str", required=False,
                                           enum_values=STATUS_V1))),
    ),
)

# 4) 索引结构变化：idx_status 改为复合键并升级索引文件格式版本
SCHEMA_INDEX_CHANGE = Schema(
    version="v2-index-change",
    fields=SCHEMA_NEW_ENUM.fields,
    indexes=(I("pk_id", ("id",), unique=True, format_version=1),
             I("idx_status", ("status", "created_at"), unique=False,
               format_version=2)),
    contract=SCHEMA_NEW_ENUM.contract,
    supported_index_formats=(1, 2),
)

# 5) 破坏性变更：删除旧版本必填的 name 字段、删除 get_user 契约操作 —— 用于证明"不可回滚"
SCHEMA_DESTRUCTIVE = Schema(
    version="v2-destructive",
    fields=(F("id", "int"),
            F("status", "str", enum_values=STATUS_V1),
            F("balance", "float", required=False)),
    indexes=(I("pk_id", ("id",), unique=True),
             I("idx_status", ("status",), unique=False)),
    contract=(
        C("update_user", request_fields=(F("id", "int"),
                                         F("status", "str", required=False,
                                           enum_values=STATUS_V1))),
    ),
)

ENUM_MAPPINGS = {"status": {"PENDING_DELETE": "SUSPENDED"}}


def gen_records(seed, count, mode):
    rng = random.Random(seed)
    for i in range(count):
        rec = {
            "id": 1000 + i,
            "name": "user-%d" % i,
            "status": rng.choice(list(STATUS_V1)),
            "balance": round(rng.uniform(0, 9999), 2),
            "created_at": "2026-10-%02dT08:00:00Z" % ((i % 28) + 1),
        }
        if mode == "compat":
            pass
        elif mode == "additive":
            rec["email"] = "u%d@example.com" % i
            rec["remark"] = "created by v2"
        elif mode == "new_enum":
            if i % 10 == 0:
                rec["status"] = "PENDING_DELETE"
        elif mode == "index_change":
            if i % 10 == 0:
                rec["status"] = "PENDING_DELETE"
        elif mode == "destructive":
            del rec["name"]
            del rec["created_at"]
        yield rec


def new_index_files(schema, records):
    files = []
    by_index = {}
    for idx in schema.indexes:
        entries = []
        for r in records:
            key = "|".join(str(r.get(k, "")) for k in idx.keys)
            entries.append((key, r["id"]))
        files.append(codec.encode_index_file(idx, entries))
        by_index[idx.name] = entries
    return files
