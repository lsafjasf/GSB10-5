"""On-disk encoding used by both versions: JSON Lines for records,
header-prefixed text files for indexes."""
import json

DATA_FORMAT_HEADER = {"encoding": "jsonl"}


def encode_record(record):
    return json.dumps(record, ensure_ascii=False, sort_keys=True).encode("utf-8") + b"\n"


def decode_record(line):
    return json.loads(line)


def encode_data_file(records, data_format_version):
    header = dict(DATA_FORMAT_HEADER)
    header["format_version"] = data_format_version
    out = [json.dumps(header, sort_keys=True).encode("utf-8") + b"\n"]
    out.extend(encode_record(r) for r in records)
    return b"".join(out)


def split_data_file(data):
    lines = data.split(b"\n")
    header = json.loads(lines[0])
    return header, (ln for ln in lines[1:] if ln.strip())


def encode_index_file(index_spec, entries):
    header = {
        "index": index_spec.name,
        "format_version": index_spec.format_version,
        "keys": list(index_spec.keys),
        "unique": index_spec.unique,
    }
    out = [json.dumps(header, sort_keys=True).encode("utf-8") + b"\n"]
    for key, row_id in entries:
        out.append(("%s\t%s\n" % (key, row_id)).encode("utf-8"))
    return b"".join(out)


def parse_index_header(first_line):
    return json.loads(first_line)
