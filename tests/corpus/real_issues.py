"""真实问题样例：每条规则各有一个真实报警，用于召回率统计。"""
import json  # 被使用，不应报警
import csv  # 未使用：应为 CR004；本行注释里提到 csv 不算“使用”


def make_user(name, roles=[]):
    """roles 默认值为可变对象：应为 CR001。"""
    roles.append(name)
    return {"name": name, "roles": roles, "meta": json.dumps({})}


def read_config(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except:
        return ""


def run_expr(expr):
    return eval(expr)


def find_user(cur, uid):
    cur.execute(f"SELECT * FROM users WHERE id = {uid}")
