# 译名一致性检查报告

## 概览

- 语料段数：9，术语条目：12，登记译名：20
- 源文命中：9，译文命中：8，耗时：0.0 s
- 冲突：4 条；待人工确认：3 条；术语库问题：0 条

## 冲突清单

1. **[forbidden_translation]** 条目 `E001` @ b01:1:2 — 使用了禁用的译名 '档'
2. **[wrong_sense]** 条目 `E003` @ b02:1:3 — 译名 '老鼠' 属于义项 mouse.animal，本段应为义项 mouse.computer
3. **[multiple_renderings]** 条目 `E005`（义项 E005 使用了 2 种译名且未声明 allow_multiple: API接口、应用程序接口）
   - `API接口`（synonym）首见于 b05:1:3
   - `应用程序接口`（canonical）首见于 b04:1:2
4. **[multiple_renderings]** 条目 `E008`（义项 E008 使用了 2 种译名且未声明 allow_multiple: 缓存、高速缓存）
   - `缓存`（canonical）首见于 b08:1:3
   - `高速缓存`（synonym）首见于 b09:1:1

## 待人工确认

1. **[ambiguous_sense]** 条目 `E003` @ b03:1:5 — 无法根据 cue 词确定义项，需人工确认
2. **[unlisted_translation]** 条目 `E007` @ b06 — 译文中未出现该条目的任何登记译名，可能漏译或使用了未登记译名
3. **[forbidden_unverified]** 条目 `E006` @ b07:1:3 — 纯中文文档中出现禁用译名，需人工确认

