"""干净代码：任何规则都不应命中。"""
import json
import os


def load_config(path, default=None):
    if default is None:
        default = {}
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


class Registry:
    def __init__(self):
        self._items = {}

    def add(self, name, value):
        self._items[name] = value

    def get(self, name):
        try:
            return self._items[name]
        except KeyError:
            return None
