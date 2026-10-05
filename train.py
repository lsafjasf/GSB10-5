"""从 data/train/<lang>.txt 构建拉丁语系 n-gram 画像 -> profiles.json"""
import json
import os
import langid

BASE = os.path.dirname(os.path.abspath(__file__))

def main():
    profiles = {}
    for lang in langid.LATIN_LANGS:
        path = os.path.join(BASE, "data", "train", lang + ".txt")
        with open(path, encoding="utf-8") as f:
            profiles[lang] = langid.build_profile(f.read())
    out = os.path.join(BASE, "profiles.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(profiles, f, ensure_ascii=False)
    print("wrote", out, "langs:", sorted(profiles))

if __name__ == "__main__":
    main()
