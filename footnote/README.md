# 脚注编号与跨页合并

纯 Python 3（仅标准库）实现：编号分配、重复引用合并、删除重排、
脚注正文跨页拆分/续块、引用一致性校验。

## 文件

- `footnote_lib.py` —— 库：`assign_numbers()`、`paginate()`、`check_consistency()`
- `test_footnote.py` —— 自测（10 个用例，含跨页/顺延/删除重排）
- `demo.py` —— 生成 `REPORT.md`（对照表、跨页结果、重排数据、校验明细、性能）
- `RULES.md` —— 编号与排版规则说明
- `REPORT.md` —— 运行产出的数据报告

## 运行

```bash
cd footnote
python3 test_footnote.py   # 自测
python3 demo.py            # 生成/刷新 REPORT.md
```

## 快速示例

```python
from footnote_lib import FootnoteDef, Line, Ref, paginate, check_consistency

lines = [Line("引文", [Ref("src")]), Line("再引", [Ref("src")])]
defs  = {"src": FootnoteDef("src", ["出处说明。"])}
pages, numbers, points = paginate(lines, defs, body_cap=40, foot_cap=10)
assert check_consistency(pages, defs, points, numbers, foot_cap=10) == []
```
