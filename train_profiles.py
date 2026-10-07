"""训练语言模型并生成 profiles.json。

语料划分(确定性): data/<lang>.txt 中行号 i % 5 != 4 的行用于训练,
i % 5 == 4 的行留出作为评测集(见 evaluate.py), 保证训练/测试不重叠。
"""
import os
from langid import Identifier, SUPPORTED_LANGUAGES

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "profiles.json")


def load_split(lang):
    path = os.path.join(DATA_DIR, lang + ".txt")
    with open(path, encoding="utf-8") as f:
        lines = [ln.strip() for ln in f if ln.strip()]
    train = [ln for i, ln in enumerate(lines) if i % 5 != 4]
    test = [ln for i, ln in enumerate(lines) if i % 5 == 4]
    return train, test


def main():
    corpora = {}
    for lang in SUPPORTED_LANGUAGES:
        train, _ = load_split(lang)
        corpora[lang] = "\n".join(train)
    idf = Identifier.train(corpora)
    idf.save(OUT)
    ngrams = sum(p["vocab"] for p in idf.profiles.values())
    print(f"trained {len(corpora)} languages, {ngrams} distinct n-grams -> {OUT}")


if __name__ == "__main__":
    main()
