import importlib
import json

from util import process


def main():
    process(b"payload")
    # 动态导入（字面量，可静态解析）
    importlib.import_module("dynpkg.loader")
    with open("config/plugins.json", encoding="utf-8") as fh:
        json.load(fh)


if __name__ == "__main__":
    main()
