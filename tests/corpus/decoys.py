"""诱饵样例：字符串与注释里的“假问题”一律不得报警。"""

# def fake1(x=[]):        <- 注释里的可变默认参数，不得报 CR001
# except:                 <- 注释里的裸 except，不得报 CR002
# eval("1+1")             <- 注释里的 eval，不得报 CR003

CODE_SAMPLE = """
def fake2(x=[]):
    try:
        eval(x)
    except:
        pass
"""

SQL_TEXT = 'cur.execute(f"SELECT * FROM t WHERE id = {uid}")'  # 字符串里的 SQL 拼接
IMPORT_TEXT = "from pkg_a.mod import missing_symbol"  # 字符串里的假跨文件导入
NOTE = "import os"  # 字符串里的假导入语句
