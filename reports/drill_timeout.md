# 回滚演练报告 `drill-b9820ea5`

- 模式: `DRILL`
- 计划: `plan-drill-sample-timeout` (签名 `0c5b34c3b5ec1de6`)
- 总体判定: **FAILED**
- 总耗时: 10000.000 ms
- 资源: {'bytes_copied': 24960.0, 'files_touched': 40.0, 'operations': 2.0, 'rss_peak_bytes': 20635648.0, 'rss_delta_bytes': 4096.0, 'wall_elapsed_ms': 15.940933999445406}

| # | step_id | 步骤 | 状态 | 耗时ms | 判定数据 | 补偿/标记 | 错误 |
|---|---------|------|------|--------|----------|-----------|------|
| 1 | s01_precheck | 资源预检 | SUCCESS | 0.000 | ✓ target_readable: 期望 `live tree readable` 实际 `40 files`<br>✓ non_empty: 期望 `size > 0` 实际 `24960 bytes` | - | - |
| 2 | s02_verify_snapshot | 校验快照完整性 | SUCCESS | 0.000 | ✓ snapshot_integrity: 期望 `343b8e00a2917c3dee6f3bf6e24e07a90de44e0643a650aab7cfc781792d544a` 实际 `343b8e00a2917c3dee6f3bf6e24e07a90de44e0643a650aab7cfc781792d544a` | - | - |
| 3 | s03_fence_writes | 写入围栏 | SUCCESS | 0.000 | ✓ fence_engaged: 期望 `fenced` 实际 `fenced` | - | - |
| 4 | s04_capture_safety | 安全副本捕获 | SUCCESS | 0.000 | ✓ safety_copy_matches: 期望 `ef95cf90793b7b0c83ef1173c7f2f502197ae83fd622e3163c41c2fa64d8fd1d` 实际 `ef95cf90793b7b0c83ef1173c7f2f502197ae83fd622e3163c41c2fa64d8fd1d` | - | - |
| 5 | s05_restore | 执行恢复 | FAILED | 10000.000 | - | 已回退: `{'restored_from': '/tmp/rollback-safety-idem3xka', 'fp': 'ef95cf90793b7b0c83ef1173c7f2f502197ae83fd622e3163c41c2fa64d8fd1d'}` | timeout(>5.0s) |
| 6 | s06_verify_restored | 恢复内容校验 | SKIPPED | 0.000 | - | - | - |
| 7 | s07_drift_check | 数据漂移检测 | SKIPPED | 0.000 | - | - | - |
| 8 | s08_unfence | 解除围栏 | SKIPPED | 0.000 | - | - | - |
| 9 | s09_smoke | 冒烟测试 | SKIPPED | 0.000 | - | - | - |
| 10 | s10_audit_commit | 审计落账 | SKIPPED | 0.000 | - | - | - |

> 触发超时，阈值 5000.0 ms
