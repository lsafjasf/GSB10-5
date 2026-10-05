# 路径规范化规则表

规范化是**纯词法**操作（不访问文件系统、不解析符号链接），风格参数与宿主 OS 无关：
`style="windows"` 或 `"posix"`；大小写策略由文件系统决定，显式传入
`case_sensitive=True/False`，不做任何隐式猜测。

## 规则表

| 编号 | 规则 | Windows 风格 | POSIX 风格 | 违反时的处理 |
|---|---|---|---|---|
| R1 | 分隔符 | `/` 与 `\` 均视为分隔符，输出统一为 `\`；连续分隔符合并 | 仅 `/` 是分隔符，`\` 是合法文件名字符；连续 `/` 合并 | 不报错（归一化） |
| R2 | 相对段 `.`/`..` | 词法解析：`.` 删除；`..` 弹出上一级；绝对路径根处的 `..` 丢弃；相对路径开头的 `..` 保留 | 同左 | 不报错 |
| R3 | 大小写 | 默认保留；`case_sensitive=False` 时按 Unicode `casefold()` 折叠（盘符先统一为大写） | 默认保留；`case_sensitive=False` 时 `casefold()` | 不报错 |
| R4 | 保留名 | `CON PRN AUX NUL COM1–9 LPT1–9`（任意大小写、带任意扩展名如 `con.txt`）拒绝 | 无保留名 | `ReservedNameError`（默认）/ `strict=False` 放行 |
| R5 | 结尾点/空格 | 每个组件结尾的 `.` 与空格按 Win32 行为剥离（`file. `→`file`） | 保留，不剥离 | 不报错 |
| R6 | 长度限制 | 全路径 ≤ 260（`MAX_PATH`）；`\\?\` 前缀时 ≤ 32767 | 全路径 ≤ 4096（`PATH_MAX`）；单组件 ≤ 255（`NAME_MAX`） | `PathTooLongError` / `ComponentTooLongError`，**绝不截断** |
| R7 | UNC / 前缀 | `\\server\share\…` 保留为 UNC；`\\?\` 与 `\\?\UNC\…` 扩展前缀保留 | 不适用（`\\` 是普通目录名） | 不报错 |
| R8 | 盘符 | `C:` 等盘符统一为大写；`C:foo`（盘符相对路径）与 `C:\foo`（绝对路径）区分 | 不适用 | 不报错 |
| R9 | NUL 字节 | 拒绝 | 拒绝 | `PathNormError` |

等价性判定：两路径按相同 `(style, case_sensitive)` 规范化后字符串相等即等价。

## 关键跨平台陷阱

1. `a/b\c`：Windows 下 = `a\b\c`，POSIX 下 = 单个文件名 `a/b\c`（见 `reports/equivalence.csv`）。
2. 同样是 Windows 风格，`C:\A\B` 在大小写不敏感 FS（NTFS 默认）上等价于 `c:\a\b`，
   在大小写敏感设置下不等价——敏感性是 **FS 属性**，不能按操作系统假设。
3. 路径长度问题不会静默截断：超限时抛异常；Windows 下需要用 `\\?\` 扩展前缀显式放宽。
