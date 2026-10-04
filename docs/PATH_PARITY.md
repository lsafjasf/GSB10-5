# 演练与真实回滚路径对照

演练与真实回滚由同一个 `DrillRunner.run` 执行同一个冻结步骤表
（`rollback_drill.target.build_steps`），唯一差异是 `Plan` 绑定：
演练指向生产环境的逐字节克隆（`FileEnv.clone_to`），真实回滚指向生产目录本身。
下表为同一份种子数据上两次真实执行的对照数据（由 `tools/gen_samples.py` 生成）。

| # | step_id | 步骤 | 演练状态 | 真实状态 | 演练耗时ms | 真实耗时ms | 同步骤 | 同顺序 |
|---|---------|------|----------|----------|------------|------------|--------|--------|
| 1 | s01_precheck | 资源预检 | SUCCESS | SUCCESS | 1.167 | 1.158 | ✓ | ✓ |
| 2 | s02_verify_snapshot | 校验快照完整性 | SUCCESS | SUCCESS | 1.185 | 1.238 | ✓ | ✓ |
| 3 | s03_fence_writes | 写入围栏 | SUCCESS | SUCCESS | 0.088 | 0.097 | ✓ | ✓ |
| 4 | s04_capture_safety | 安全副本捕获 | SUCCESS | SUCCESS | 6.651 | 6.529 | ✓ | ✓ |
| 5 | s05_restore | 执行恢复 | SUCCESS | SUCCESS | 5.782 | 5.717 | ✓ | ✓ |
| 6 | s06_verify_restored | 恢复内容校验 | SUCCESS | SUCCESS | 2.533 | 2.305 | ✓ | ✓ |
| 7 | s07_drift_check | 数据漂移检测 | SUCCESS | SUCCESS | 0.023 | 0.021 | ✓ | ✓ |
| 8 | s08_unfence | 解除围栏 | SUCCESS | SUCCESS | 0.086 | 0.117 | ✓ | ✓ |
| 9 | s09_smoke | 冒烟测试 | SUCCESS | SUCCESS | 0.338 | 0.301 | ✓ | ✓ |
| 10 | s10_audit_commit | 审计落账 | SUCCESS | SUCCESS | 0.042 | 0.028 | ✓ | ✓ |

- 演练计划签名: `45bafeafa7235d95`，真实计划签名: `9a9f3d681bf04537`
  （签名含模式与目标绑定，二者必须不同；步骤序列必须相同）
- 步骤序列一致: `True`
