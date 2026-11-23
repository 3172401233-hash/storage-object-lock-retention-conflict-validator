# storage-object-lock-retention-conflict-validator

对象锁、保留期与版本删除的冲突语义校验器（Python 3.8.2+，仅标准库）。

给定对象版本、`Retention until`、保留模式、Legal Hold、带 Bypass 头的删除请求、
现有删除标记和策略版本，判定每个版本的删除是否允许，并输出最终状态与拒绝证据。

## 离线配置说明

**所有策略均为离线配置。** 本项目不发起任何网络请求，不依赖任何外部控制面或在线服务：

- 保留策略通过本地内存注册或本地 JSON 文件加载（`PolicyRegistry`）。
- 策略按版本号管理；每个对象版本绑定一个策略版本，策略更新只影响新绑定，
  旧版本绑定的策略语义保持不变。
- 策略配置中的 `compliance_bypass_allowed` / `legal_hold_bypass_allowed`
  即使被置为 `true` 也不会放松引擎语义：Compliance 保留与 Legal Hold 在代码层面恒不可绕过。

## 判定语义

判定按以下顺序执行，各约束分别检查、分别出证据：

1. **删除标记**：版本已存在删除标记时拒绝，且不生成新的可见标记。
2. **Legal Hold**：与时间保留独立检查；任何 Bypass 头或授权都不能突破。
3. **时间保留**：`now >= retention_until` 视为保留到期，时间保留不再拦截。
4. **Compliance 模式**：保留期内不可被普通 Bypass 突破，恒返回
   `COMPLIANCE_RETENTION` 证据。
5. **Governance 模式**：仅当 Bypass 请求头、调用方授权、策略开关
   （`governance_bypass_allowed`）三者同时满足时才允许绕过；否则返回
   `GOVERNANCE_RETENTION` 证据。
6. **批量原子性**：批量删除中只要有一个版本被拒绝，整批中止；
   本可通过的版本标记为 `BATCH_ABORTED`，**不会产生任何部分可见的删除标记**。

最终状态取值：

| 状态 | 含义 |
| --- | --- |
| `DELETE_MARKER_CREATED` | 删除允许，生成删除标记 |
| `RETAINED` | 删除被约束拦截，版本保留，无标记 |
| `BATCH_ABORTED` | 因批量中其他版本被拒而整批中止，无标记 |
| `DELETE_MARKER_PRESENT` | 版本已有删除标记，不再生成新标记 |

## 目录结构

```
object_lock/
  models.py    # 数据模型、证据码、最终状态枚举
  policy.py    # 离线策略注册表（内存 / 本地 JSON）
  engine.py    # 判定引擎（单版本与批量）
tests/
  test_retention_engine.py
validate_delete.py   # 离线 CLI：读本地 JSON，输出每版本状态与证据
examples/request.json
```

## 使用

```bash
python validate_delete.py examples/request.json
```

编程方式调用：

```python
from datetime import datetime, timezone
from object_lock import (
    DeleteRequest, ObjectVersion, PolicyRegistry,
    RetentionEngine, RetentionMode, RetentionPolicy,
)

registry = PolicyRegistry([
    RetentionPolicy(version="v1", governance_bypass_allowed=True),
])
engine = RetentionEngine(registry, clock=lambda: datetime.now(timezone.utc))

version = ObjectVersion(
    version_id="ver-1",
    retention_mode=RetentionMode.GOVERNANCE,
    retention_until=datetime(2027, 1, 1, tzinfo=timezone.utc),
    legal_hold=False,
    delete_marker=False,
    policy_version="v1",
)
request = DeleteRequest(bypass_header=True, bypass_authority=True)
decision = engine.evaluate_version(version, request)
print(decision.to_dict())
```

## 测试

覆盖 Governance、Compliance、Legal Hold（含与时间保留的独立检查）、
删除标记、批量删除原子性以及策略版本更新：

```bash
python -m unittest
```
