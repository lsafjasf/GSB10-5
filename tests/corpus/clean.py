"""正确写法样例：不应产生任何报警。"""
import os


def make_user(name, roles=None):
    if roles is None:
        roles = []
    return {"name": name, "roles": roles}


def read_config(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def run_expr(expr):
    return compile(expr, "<expr>", "eval")


def find_user(cur, uid):
    cur.execute("SELECT * FROM users WHERE id = ?", (uid,))


def config_path():
    return os.path.join(os.path.dirname(__file__), "config.json")
