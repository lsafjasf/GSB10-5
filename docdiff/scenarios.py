"""自测场景库：完全相同 / 仅改名 / 整章移动 / 层级调整 / 增删 / 组合。

每个场景是 (name, old_text, new_text)。断言见仓库根目录 selftest.py。
"""

_IDENTICAL_OLD = """# 第一章 概述
这是概述内容，保持不变。
# 第二章 安装
## 环境准备
准备内容第一行。
准备内容第二行。
## 安装步骤
步骤内容第一行。
步骤内容第二行。
"""

_RENAME_OLD = """# 第一章 概述
这是概述内容。
# 第二章 安装
## 环境准备
准备内容第一行。
准备内容第二行。
## 安装步骤
步骤内容第一行。
步骤内容第二行。
步骤内容第三行。
"""

_RENAME_NEW = """# 第一章 概述
这是概述内容。
# 第二章 安装
## 环境准备
准备内容第一行。
准备内容第二行。
## 安装流程
步骤内容第一行。
步骤内容第二行。
步骤内容第三行。
"""

_MOVE_OLD = """# 第一章 概述
概述正文。
# 第二章 安装
## 准备
准备内容。
## 步骤
步骤内容。
# 第三章 使用
使用正文。
"""

_MOVE_NEW = """# 第一章 概述
概述正文。
# 第三章 使用
使用正文。
# 第二章 安装
## 准备
准备内容。
## 步骤
步骤内容。
"""

_RELEVEL_OLD = """# 第一章
章内容一。
## 小节甲
甲内容。
## 小节乙
乙内容。
# 第二章
章内容二。
## 小节丙
丙内容。
"""

_RELEVEL_NEW = """# 第一章
章内容一。
## 小节甲
甲内容。
### 小节乙
乙内容。
# 第二章
章内容二。
### 小节丙
丙内容。
"""

_ADDDEL_OLD = """# 第一章 概述
概述正文。
# 第二章 安装
## 准备
准备内容。
## 步骤
步骤内容。
# 第三章 使用
使用正文。
"""

_ADDDEL_NEW = """# 第一章 概述
概述正文。
# 第三章 使用
使用正文。
# 第四章 维护
## 巡检
巡检内容。
## 升级
升级内容。
"""

_MODIFY_BODY_OLD = [f"使用章节正文第{i}行。" for i in range(10)]
_MODIFY_BODY_NEW = list(_MODIFY_BODY_OLD)
_MODIFY_BODY_NEW[4] = "使用章节正文第4行，已修订。"

_COMBINED_OLD = """# 第一章 概述
内容一。
# 第二章 安装
## 准备
准备内容。
# 第三章 使用
{use_body}
# 第四章 附录
附录内容。
""".format(use_body="\n".join(_MODIFY_BODY_OLD))

_COMBINED_NEW = """# 第一章 概述
内容一。
# 第三章 使用
{use_body}
# 第二章 安装手册
## 准备
准备内容。
# 第四章 附录
附录内容。
""".format(use_body="\n".join(_MODIFY_BODY_NEW))

SCENARIOS = [
    {"name": "identical", "desc": "完全相同",
     "old": _IDENTICAL_OLD, "new": _IDENTICAL_OLD},
    {"name": "rename_only", "desc": "仅改名（标题变、正文不变）",
     "old": _RENAME_OLD, "new": _RENAME_NEW},
    {"name": "chapter_move", "desc": "整章移动（第二章整章搬到第三章之后）",
     "old": _MOVE_OLD, "new": _MOVE_NEW},
    {"name": "relevel", "desc": "层级调整（小节乙并入小节甲降级；小节丙同父降级）",
     "old": _RELEVEL_OLD, "new": _RELEVEL_NEW},
    {"name": "add_delete", "desc": "整章删除 + 整章新增",
     "old": _ADDDEL_OLD, "new": _ADDDEL_NEW},
    {"name": "combined", "desc": "组合：改名+移动+内容修改",
     "old": _COMBINED_OLD, "new": _COMBINED_NEW},
]
