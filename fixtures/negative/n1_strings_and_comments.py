"""字符串与注释中的假问题：一条都不允许报警。

以下内容看起来都像问题，但只存在于字符串字面量或注释中：
"""

# def f(x=[]):        -- 注释里的可变默认参数
# except:             -- 注释里的裸 except
# eval(user_input)    -- 注释里的 eval
# if a == None:       -- 注释里的 == None

SNIPPET = """
def handler(data=[]):
    try:
        risky()
    except:
        pass
    return eval(data)
    print("never")
"""

DOC_LIKE = (
    "def dup(): pass\n"
    "def dup(): pass\n"
    "import os  # unused?\n"
    "if x == None: ...\n"
)

TEMPLATE = "return 1\nprint('dead code after return')"


def describe():
    text = "except: pass  # swallow"
    other = "exec(code)"
    return text, other
