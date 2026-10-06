"""真实问题集中样例：每行问题都可在源码中直接核对。"""
import json
import sys


def add_item(bucket, item, cache=[]):
    cache.append(item)
    bucket[item] = len(cache)
    return bucket


def configure(options={}):
    return options


def parse(answer=dict()):
    return answer


def divide(x, y):
    try:
        return x / y
    except:
        return 0


def shallow_equal(a, b):
    if a == None:
        return False
    if b != True:
        return False
    return a == b


def cleanup(path):
    try:
        import os
        os.remove(path)
    except FileNotFoundError:
        pass


def early_return():
    return 42
    print("unreachable")


def version_one():
    return "1.0"


def version_one():
    return "2.0"


def run_user_code(expr):
    return eval(expr)


def unused_json_client():
    data = '{"a": 1}'
    return json.loads(data)
