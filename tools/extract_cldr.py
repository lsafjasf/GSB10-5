#!/usr/bin/env python3
"""Extract CLDR plural rules + samples for the shipped locales.

Source: data/plurals.json, data/ordinals.json
(CLDR 48, npm package cldr-core@48.2.0; Unicode CLDR).
Run from repo root:  python3 tools/extract_cldr.py
Output: cldr_data.py
"""
import json
import pprint

LOCALES = ["en", "ru", "ar", "fr", "ja"]


def load(path, key):
    root = json.load(open(path, encoding="utf-8"))["supplemental"][key]
    rules, samples = {}, {}
    for loc in LOCALES:
        loc_rules, loc_samples = {}, {}
        for field, raw in root.get(loc, {}).items():
            count = field.split("-")[-1]
            body = raw.strip()
            rule, sample_text = None, ""
            if "@" in body:
                head, sample_text = body.split("@", 1)
                rule = head.strip() or None
            else:
                rule = body or None
            loc_rules[count] = rule
            loc_samples[count] = sample_text.strip()
        rules[loc] = loc_rules
        samples[loc] = loc_samples
    return rules, samples


def main():
    card_r, card_s = load("data/plurals.json", "plurals-type-cardinal")
    ord_r, ord_s = load("data/ordinals.json", "plurals-type-ordinal")
    with open("cldr_data.py", "w", encoding="utf-8") as fh:
        fh.write('"""CLDR 48 plural rule strings and raw sample lists.\n\n')
        fh.write("Extracted from data/plurals.json + data/ordinals.json\n")
        fh.write("(cldr-core@48.2.0, https://cldr.unicode.org) by tools/extract_cldr.py.\n")
        fh.write('Do not edit by hand.\n"""\n\n')
        fh.write("CARDINAL_RULES = " + pprint.pformat(card_r, width=120) + "\n\n")
        fh.write("CARDINAL_SAMPLE_TEXT = " + pprint.pformat(card_s, width=120) + "\n\n")
        fh.write("ORDINAL_RULES = " + pprint.pformat(ord_r, width=120) + "\n\n")
        fh.write("ORDINAL_SAMPLE_TEXT = " + pprint.pformat(ord_s, width=120) + "\n")
    print("wrote cldr_data.py")


if __name__ == "__main__":
    main()
