#!/usr/bin/env python3
"""Parse the CLDR plural-rules chart HTML snapshot and emit
data/cldr_rules_samples.json with rule text + sample values per
language / type (cardinal|ordinal) / category.

Only the Python standard library is used.
"""
import html
import json
import re
import sys
from decimal import Decimal

CHART = "data/cldr_plural_rules_47.html"
OUT = "data/cldr_rules_samples.json"
SOURCE_URL = "https://www.unicode.org/cldr/charts/47/supplemental/language_plural_rules.html"
LANGS = ["en", "ru", "ar", "fr"]
CATEGORIES = {"zero", "one", "two", "few", "many", "other"}

ROW_RE = re.compile(r"<tr>(.*?)</tr>", re.S)
CELL_RE = re.compile(r"<td class='dtf-s'[^>]*>(.*?)</td>", re.S)
TAG_RE = re.compile(r"<[^>]+>")


def clean(cell: str) -> str:
    cell = cell.replace("<br>", " ").replace("&nbsp;", " ")
    cell = TAG_RE.sub("", cell)
    return html.unescape(cell).strip()


def clean_examples(cell: str) -> str:
    # keep the <br> separator between integer and decimal sample lists
    cell = cell.replace("<br>", "\n").replace("&nbsp;", " ")
    cell = TAG_RE.sub("", cell)
    return html.unescape(cell).strip()


def expand_token(tok: str):
    """Expand one sample token into a list of sample strings.

    Forms: '3', '3~10', '0.1~0.9', '1.0', '1c6', '1.0000001c6'.
    Ranges are expanded with a cap so the JSON stays small.
    """
    tok = tok.strip()
    if not tok or tok == "…":
        return []
    if "~" in tok:
        lo_s, hi_s = tok.split("~", 1)
        lo, hi = Decimal(lo_s), Decimal(hi_s)
        frac = max(-lo.as_tuple().exponent, 0)
        step = Decimal(1).scaleb(-frac)
        out, cur = [], lo
        while cur <= hi and len(out) < 120:
            out.append(format(cur, "f"))
            cur += step
        if cur <= hi:  # capped: always include the upper bound
            out.append(format(hi, "f"))
        return out
    return [tok]


def parse_examples(cell: str):
    # Integer examples and decimal examples are separate <br>-separated lists in
    # the chart; flatten them while preserving the literal decimal notation,
    # because the visible fraction digits (operand v) are part of the sample.
    samples = []
    for line in clean_examples(cell).split("\n"):
        for tok in line.split(","):
            samples.extend(expand_token(tok))
    return samples


def extract_language(block: str):
    """block: HTML rows of one language. Returns {type: {category: {...}}}."""
    result = {}
    cur_type = None
    for row_m in ROW_RE.finditer(block):
        cells = CELL_RE.findall(row_m.group(1))
        if not cells:
            continue
        texts = [clean(c) for c in cells]
        # The first row of a language also carries the name/code cells; locate
        # the cardinal/ordinal marker wherever it appears in the row.
        for idx, txt in enumerate(texts):
            if txt in ("cardinal", "ordinal"):
                cur_type = txt
                texts = texts[idx + 1:]
                cells = cells[idx + 1:]
                break
        if not texts or texts[0] not in CATEGORIES:
            continue  # skips 'range' rows
        category = texts[0]
        samples = parse_examples(cells[1])
        rule = clean(cells[3]) if len(cells) > 3 else ""
        result.setdefault(cur_type, {})[category] = {
            "rule": " ".join(rule.split()),
            "samples": samples,
        }
    return result


def main():
    doc = open(CHART, encoding="utf-8").read()
    data = {}
    for code in LANGS:
        anchor = "<a name='%s' href='#%s'>%s</a>" % (code, code, code)
        anchor_pos = doc.index(anchor)
        # include the <tr> that the language-code anchor lives inside
        start = doc.rfind("<tr>", 0, anchor_pos)
        # language block ends at the next header row (<th class='dtf-th'>Name</th>)
        end = doc.index("<th class='dtf-th'>Name</th>", anchor_pos)
        data[code] = extract_language(doc[start:end])
    out = {
        "source": SOURCE_URL,
        "snapshot": CHART,
        "cldr_chart_version": "47",
        "languages": data,
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    total = sum(
        len(v["samples"])
        for lang in data.values()
        for typ in lang.values()
        for v in typ.values()
    )
    print("wrote %s (%d samples)" % (OUT, total))


if __name__ == "__main__":
    sys.exit(main())
