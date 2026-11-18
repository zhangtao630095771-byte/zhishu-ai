# 08 · Semantic Context Builder 接入 + 实体去重（P2 第二步 + 任务 3）

> 落地日期：2026-11-10 · 状态：已完成并验证通过

---

## 1. Semantic Context Builder（`semantic_builder.py`）

### 1.1 定位（增量，不重构）

现有组装逻辑已收敛在 `agent._build_system_prompt`（唯一位置）+ `semantic_layer.resolve`/`build_prompt_parts`，行为等价、有测试守护。**本模块不重写它们**，只做一件它们没做的事：用统一注册表 `semantic_registry` 对本次命中的语义上下文做**治理状态审计**——确认命中知识是否都 approved、是否混入 candidate / 停用对象。

### 1.2 提供能力

| 函数 | 职责 |
|---|---|
| `audit_context(ctx)` | 从 SemanticContext 提取命中对象的 stable_key，查注册表，统计 status/层级分布，识别「suspicious」（命中却非 approved / 已停用） |
| `audit_summary(audit)` | 把审计结果转成一段可拼进 info 的短字符串（无异常时精简，避免刷屏） |

### 1.3 接入点（`agent.py`）

- `agent.py:718` `resolve(ctx)` 之后 → `audit_context(ctx)`（try 包裹，只观测、不阻断）。
- `agent.py:758` info 组装时 → `audit_summary(_ctx_audit)` 追加到 info。

### 1.4 验证

- 语法 + import 26/26 通过（`test_imports.py`）。
- prompt 组装等价性 23 项全过（`test_prompt_assembly.py`）——证明审计接入未破坏组装字节序。
- `audit_context` 功能验证：正确识别 candidate 命中（`metric:小板完单量`、`doc:14`）为 suspicious，摘要正确。

### 1.5 边界

- 审计**只观测不阻断**：发现 candidate/停用混入时只进 info 与日志，绝不拦截取数（拦截仍由现有闸门负责）。
- 审计失败静默降级（打日志），不影响取数。

---

## 2. 实体别名去重（`_dedup_entities.py`）

### 2.1 合并规则（保守、可逆、可审计）

1. 只自动合并**无争议的后缀/简称冗余**，保留规范名，其余**停用**（`enabled=0`，不删除、可恢复），停用名并入保留实体的 `keywords` 保留检索能力。
2. 语义不同的冗余**不自动合并**，交人工确认。
3. 停用实体若被 `relationships` 引用则跳过（防 JOIN 铁律断链）。
4. 全程走 `config_store.upsert_entity`，自动写 `knowledge_history` 审计。

### 2.2 执行结果

- **停用 6 个冗余实体**：代驾订单表、用户登录日活表、流水记录、客户积分批次表、客户积分完单记录表、客户积分流水/完单记录表。
- **保留 5 个规范实体并加别名**：代驾订单、用户登录活跃、资金收支流水记录、客户积分批次、客户积分流水。
- 启用实体数 **52 → 46**；停用名正确并入保留实体 keywords；治理 status 未误改（approved 保持 approved、candidate 保持 candidate）。

### 2.3 需人工确认（未自动合并，4 组）

| 实体 A | 实体 B | 原因 |
|---|---|---|
| 保障订单 | 车辆运输保障订单 | 简称 vs 全称，选哪个保留取决于业务习惯 |
| 保障订单方案 | 担保订单方案 | guaranty 的「保障/担保」译法不一致，需统一口径 |
| 转卖单审核表 | 转卖单违规表 | 「审核」vs「违规」是同一表的不同用途视角，语义可能不同 |
| 线下单司机定位统计表 | 线下单位置统计表 / 线下接单违规记录 | 同表三个不同视角，需业务确认是否拆成独立实体 |

---

## 3. 本次改动文件清单

| 文件 | 改动 |
|---|---|
| `semantic_builder.py` | **新增**：语义上下文审计 + 摘要 |
| `agent.py` | import semantic_builder + 接入 audit_context/audit_summary（2 处） |
| `_dedup_entities.py` | **新增**：实体别名去重脚本（幂等） |
| `agent_config.db` | 停用 6 个冗余实体 + 5 个保留实体加别名（已备份） |

## 4. 回滚方式

- 实体去重：`cp agent_config.db.bak-dedup-20261110-135838 agent_config.db`
- semantic_builder 接入：撤销 `agent.py` 的两处 Edit 即可（semantic_builder.py 可保留，不调用即无副作用）
