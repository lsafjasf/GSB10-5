# 回滚演练报告 `drill-1e094a72`

- 模式: `DRILL`
- 计划: `plan-drill-sample-success` (签名 `28e42279ba7e01bb`)
- 总体判定: **SUCCESS**
- 总耗时: 17.969 ms
- 资源: {'bytes_copied': 50003.0, 'files_touched': 82.0, 'operations': 2.0, 'rss_peak_bytes': 20619264.0, 'rss_delta_bytes': 12288.0, 'wall_elapsed_ms': 19.648652999421756}

| # | step_id | 步骤 | 状态 | 耗时ms | 判定数据 | 补偿/标记 | 错误 |
|---|---------|------|------|--------|----------|-----------|------|
| 1 | s01_precheck | 资源预检 | SUCCESS | 1.098 | ✓ target_readable: 期望 `live tree readable` 实际 `40 files`<br>✓ non_empty: 期望 `size > 0` 实际 `24960 bytes` | - | - |
| 2 | s02_verify_snapshot | 校验快照完整性 | SUCCESS | 1.218 | ✓ snapshot_integrity: 期望 `343b8e00a2917c3dee6f3bf6e24e07a90de44e0643a650aab7cfc781792d544a` 实际 `343b8e00a2917c3dee6f3bf6e24e07a90de44e0643a650aab7cfc781792d544a` | - | - |
| 3 | s03_fence_writes | 写入围栏 | SUCCESS | 0.097 | ✓ fence_engaged: 期望 `fenced` 实际 `fenced` | - | - |
| 4 | s04_capture_safety | 安全副本捕获 | SUCCESS | 6.325 | ✓ safety_copy_matches: 期望 `ef95cf90793b7b0c83ef1173c7f2f502197ae83fd622e3163c41c2fa64d8fd1d` 实际 `ef95cf90793b7b0c83ef1173c7f2f502197ae83fd622e3163c41c2fa64d8fd1d` | - | - |
| 5 | s05_restore | 执行恢复 | SUCCESS | 5.742 | ✓ restore_changed: 期望 `fingerprint changed` 实际 `ef95cf90793b→343b8e00a291` | - | - |
| 6 | s06_verify_restored | 恢复内容校验 | SUCCESS | 2.899 | ✓ restored_matches_snapshot: 期望 `343b8e00a2917c3dee6f3bf6e24e07a90de44e0643a650aab7cfc781792d544a` 实际 `343b8e00a2917c3dee6f3bf6e24e07a90de44e0643a650aab7cfc781792d544a` | - | - |
| 7 | s07_drift_check | 数据漂移检测 | SUCCESS | 0.024 | ✓ no_drift: 期望 `watermark==0` 实际 `watermark==0` | - | - |
| 8 | s08_unfence | 解除围栏 | SUCCESS | 0.089 | ✓ fence_released: 期望 `open` 实际 `open` | - | - |
| 9 | s09_smoke | 冒烟测试 | SUCCESS | 0.303 | ✓ smoke_passed: 期望 `no empty files` 实际 `no empty files` | - | - |
| 10 | s10_audit_commit | 审计落账 | SUCCESS | 0.032 | ✓ audit_committed: 期望 `COMMITTED written` 实际 `COMMITTED written` | - | - |
