# 情绪追踪 × 动态市场调查 × 主脑：三层一体化重构

> 版本 `sentiment-flow.v1` · `survey-pipeline.v1` · `brain-capability.v1` · `brain-memo.v1` · `brain-cycle.v1`
> 落地日期：2026-11-02 ｜ 对应代码：`server/sentiment_tracker.py`、`server/market_survey_engine.py`、
> `server/brain_capabilities.py`、`server/brain.py`、`server/data_bus.py`、`server/routers/{decision,brain}.py`
>
> **后续演进**：三层之间的口径对账与阈值集中配置见
> [`LAYER-CROSSCHECK.md`](LAYER-CROSSCHECK.md)（layer-crosscheck.v1 · brain-config.v1）——
> 本文的三层（情绪追踪 / 调查管线 / 主脑）各自输出，对证层负责判断它们说的是不是同一件事。

---

## 一、现状问题（都有代码/实测证据）

### P1 情绪只有「快照」，没有「追踪」

`/api/decision/sentiment` 返回的是**此刻**：总分 + 六维 + 覆盖。但交易真正需要的是：

| 问题 | 现状 |
|---|---|
| 在升温还是降温？ | 无。只能人肉对比两次截图 |
| 这个分数在高位还是低位？ | 无 z 分位、无历史分位 |
| 有没有拐点？ | 无方向翻转检测 |
| 这波变化谁在推？ | 六维只给值，不给「对总分的贡献」 |
| 持续性如何？ | 无连续同向计数 |

历史文件 `data/sentiment_history.json` 一直在写（`_persist_sentiment`），但**没有任何消费方**——只落盘不读，等于白存。

### P2 调查与情绪是两套口径，从不交叉

`market_survey_engine._diagnose_regime()` 用 `dashboard.judgment.score` 判「强势上行/区间震荡/…」，
`sentiment.v1` 用六维加权算 0–100 分。两者**同名不同义、各算各的**：

- 调查说「强势上行」，情绪可能已「高位回落」——这种背离往往比一致更有信息量，但系统里没有任何地方能发现它；
- 调查 payload 里没有情绪字段，情绪 payload 里没有市场状态，前端只能并排放。

### P3 板块数据三处各一份，关系不可见

| 数据 | 来源 | 时点 |
|---|---|---|
| 板块涨跌/成交 | `market-dashboard.sectors`（东财板块行情） | dashboard 缓存 |
| 轮动阶段/排名变化 | `market_scanner.build_rotation`（+`sector_history.json`） | scan 缓存 |
| 资金流 | `market_scanner.build_money_flow_proxy`（实测或注意力代理） | coverage 缓存 |

三者 TTL 不同、时点不同，但 payload 里**没有任何字段说明它们的对应关系**。消费方（前端/专家）无法判断「这个板块的轮动结论是不是基于同一批行情」。

### P4 主脑只有调度，没有能力契约

`server/brain.py` 有 6 个定时任务（调查/因子/专家/日结/蒸馏/情报），但：

- 「主脑能算什么、能决策什么」只存在于任务函数里，**前端无法展示**；
- 「数据缺失时哪项能力退化/阻断」没有声明，**测试无法守卫**；
- 「下一阶段要提升到什么水平」散落在注释里，没有可读目标。

### P5 缺一条贯穿的 trace

survey / sentiment / experts 各自生成 trace_id，一次推理链跨模块后无法回放「当时用的是哪份快照、哪个情绪值、哪版专家结论」。

---

## 二、整体改进思路

一句话：**情绪从「快照」升级为「状态」，调查从「黑盒」升级为「三段流水线」，主脑从「调度器」升级为「编排器 + 能力契约」，三者用资源图串联、单向依赖、共享 trace。**

```
采集（不算）        状态（只派生）      诊断（只判读）        研判（只投票）       决策（只编排）
market_coverage → packet → sentiment → sentiment_flow → survey → experts → brain.memo
                                ↘ scan（结构指标）↗
```

四条约束：

1. **单向依赖**：上层可读下层，下层不得反向 import（`arch_report --check` 守卫，当前 0 违规）；
2. **不重复计算**：情绪追踪不重算六维，只做派生；调查不重算情绪，只引用；主脑不重算专家，只合成；
3. **缺失即 None + reason**：任何一层拿不到上游，都返回 `ok:false` 并写缺口，绝不补 0 或中性值；
4. **同 trace 可回放**：`brain-cycle.v1` 的 `trace_id` 取自专家/情绪/调查三者之一，memo 的 `inputs` 记录各输入的可用性与关键值。

---

## 三、模块划分

| 层 | 模块 | 职责 | 明确不做 |
|---|---|---|---|
| L0 采集 | `market_coverage` | 全 A / 全球 / 宏观抓取，只采不算 | 不判读、不打分 |
| L1 快照 | `decision_snapshot.get_market_packet` | 冻结同一时点事实，`trace_id / as_of / quality / evidence / chain` | 不做派生指标 |
| L2 状态 | `decision_snapshot.build_sentiment_snapshot` | 六维加权情绪分（sentiment.v1） | 不做时间序列分析 |
| L2 状态 | **`sentiment_tracker`（新增）** | 变化 / 斜率 / z 分位 / 拐点 / 持续性 / 驱动归因（sentiment-flow.v1） | 不重算六维、不抓数 |
| L3 结构 | `market_scanner` | 横截面 / 板块轮动 / 资金流 | 不判读市场状态 |
| L4 诊断 | **`market_survey_engine`（改造）** | 采集→诊断→结论三段；结构状态 + 情绪交叉校验 + 板块连接 | 不产出交易结论 |
| L5 研判 | `decision_experts`（含四个分片） | 六专家投票、匿名互评、共识与否决复核 | 不做仓位决策 |
| L6 决策 | **`brain_capabilities`（新增）** | 能力声明（算/决策/依赖/退化/目标）+ 备忘录合成 | 不做 IO、不抓数 |
| L6 决策 | **`brain`（扩展）** | 调度 + 按需闭环 `run_cycle()` + 能力矩阵 `capabilities()` | 不重算任何指标 |

---

## 四、数据连接关系

### 4.1 资源图（`data_bus` 注册表，v2）

| 资源 | 标签 | TTL | 依赖 | 失效下游 | 闸门 |
|---|---|---|---|---|---|
| `coverage` | 全市场覆盖 | 300s | — | `packet` | 60s |
| `packet` | 共享快照 | 55s | coverage | sentiment, scan | 40s |
| `sentiment` | 统一情绪 | 45s | packet | sentiment_flow | 30s |
| **`sentiment_flow`** | 情绪追踪 | 60s | sentiment | **survey** | 30s |
| `scan` | 全市场扫描 | 120s | coverage | survey, experts | 180s |
| **`survey`** | 动态市场调查 | 600s | packet, sentiment_flow, scan | — | — |
| `experts` | 六专家研判 | 300s | packet, sentiment, sentiment_flow, scan | — | — |

级联：`coverage → packet → {sentiment, scan} → sentiment_flow → survey`，`experts` 消费前四者。
闸门的作用是**挡住重算风暴**：专家团含两轮互评 + 账本落盘，是最重的一环，180s 内不重复失效。

### 4.2 一次闭环的时序

```
POST /api/brain/cycle
  ├─ get_sentiment_flow()      读 packet→sentiment，派生 flow（命中缓存则 0ms）
  ├─ get_market_survey()       四路并取（行情/宏观/新闻/情绪）→ 诊断 → 结论
  ├─ run_expert_panel()        六专家 + 互评 + 共识
  └─ compose_memo()            合成 brain-memo.v1（纯函数）
```

`stages[]` 记录每步耗时与成败；任一步失败不打断整链，缺口写进 `memo.gaps`，立场自动降为 `observe`。

### 4.3 板块 → 数据源的显式连接（`survey.sector_linkage[]`）

每个板块在 payload 里自带「我从哪来、什么时点」：

```json
{"sector": "半导体", "avg_chg": 2.1, "stage": "加速", "rank_change": 3,
 "sources": {
   "quotes":     {"provider": "market_coverage / 东财板块", "as_of": "..."},
   "rotation":   {"provider": "market_scanner.build_rotation + sector_history", "window": "1/5/20 日"},
   "money_flow": {"provider": "market_scanner.build_money_flow_proxy", "mode": "实测或注意力代理"}}}
```

这样消费方不必读代码就知道「这个 stage 是哪个窗口算的、资金流是不是代理」。

### 4.4 情绪与结构的交叉校验（`survey.divergence`）

| 结构状态 | 情绪走向 | 判定 |
|---|---|---|
| 强势/震荡上行 | 升温/修复/过热 | 同向（记录，不预警） |
| 强势上行 | 降温/高位回落 | **背离 → 预警**：结构偏强但推动力衰减 |
| 偏弱/弱势下行 | 修复/冰点修复 | **背离 → 预警**：结构偏弱但情绪先修复 |
| 任一为中性/缺失 | — | 不判定（避免噪声） |

背离时自动插入一条 `type="情绪-结构背离"` 的预警，并进 `synthesis` 文本。

---

## 五、关键实现步骤（已落地）

1. **`server/sentiment_tracker.py`（新增）**
   - `build_flow(snapshot, history, now)` 纯函数：`level / band / regime / delta_short / delta_day /
     slope_per_hour / z20 / percentile20 / persistence / turning_point / drivers / drags / flags / evidence`；
   - 贡献度 = `(维度值-50) × 可用维度重归一后的权重`，与 sentiment.v1 的缺失处理同源；
   - 样本 < 5 点：分位/斜率返回 None 并**降置信**，不编造方向；
   - `get_sentiment_flow()`：缓存按 `trace_id + level` 复用 → 落盘 `data/sentiment_flow.json`（240 点）
     → 发布总线事件 `sentiment_flow`。
2. **`market_survey_engine`（改造）**
   - 采集阶段四路并发（新增 `_sentiment_flow_safe()`，12s 超时，失败不拖垮调查）；
   - 诊断阶段新增 `_sentiment_brief()` / `_divergence()` / `_sector_linkage()`；
   - 结论阶段把情绪与拐点/背离写进 `synthesis`；
   - payload 新增 `sentiment / divergence / sector_linkage / pipeline / inputs` 五个键（**只增不改**）。
3. **`server/brain_capabilities.py`（新增）**
   - 15 项能力（8 计算 + 7 决策），每项含 `inputs（总线资源键）/ outputs / metrics[].target / fallback`；
   - `capability_graph()`：26 条边（数据边 + 派生边），可拓扑排序；
   - `report(resources)`：按总线新鲜度输出 `ok / degraded / blocked` 与 `available_ratio`；
   - `compose_memo(survey, flow, experts)`：纯函数，仓位规则显式（降温 ×0.7、背离 ×0.8、否决归零、硬顶 80%）。
4. **`server/brain.py`（扩展）**
   - `capabilities()`：能力矩阵 + 任务表；
   - `run_cycle(force)`：情绪 → 调查 → 专家 → 备忘录，共享 trace，结果写 KV 与洞察记忆；
   - `last_cycle()`。
5. **端点（5 个，只增不改）**
   - `GET /api/decision/sentiment/flow`、`GET /api/decision/sentiment/flow/history`
   - `GET /api/brain/capabilities`、`POST /api/brain/cycle`、`GET /api/brain/cycle/latest`
6. **测试**
   - `tests/test_sentiment_flow.py`（15 项）：分档边界、增量、拐点、持续性、样本不足降置信、驱动归因、缺失语义；
   - `tests/test_brain_capabilities.py`（22 项）：id 唯一、输入必须是已注册资源、图边指向真实节点、
     阻断只沿下游传播、备忘录五类规则（立场/仓位/置信/否决/失效条件）。
   - 全库 **253 → 290 项通过**。

---

## 六、验证方式与实测结果

### 6.1 单测

```
pytest tests -q  →  290 passed
```

### 6.2 真实端点（2026-11-02 20:5x 实测）

`GET /api/decision/sentiment/flow`：

```json
{"ok": true, "version": "sentiment-flow.v1", "level": 80.5, "status": "过热",
 "band": {"key": "hot", "label": "过热"},
 "regime": {"key": "overheated", "label": "过热"},
 "delta_short": 0.0, "slope_per_hour": 0.183, "z20": 0.86, "percentile20": 85.0,
 "sample_n": 20, "persistence": {"direction": "flat", "ticks": 0},
 "drivers": [{"label": "宽度", "contribution": 10.53}, {"label": "动量", "contribution": 8.8},
             {"label": "量能", "contribution": 6.75}],
 "evidence": ["近端斜率 +0.18 分/小时", "相对近 20 采样 z=+0.86，分位 85.0", "..."]}
```

`GET /api/brain/capabilities`：15 能力 / 26 边 / `ok 11 · degraded 4 · blocked 0` / `available_ratio 0.867`。

`POST /api/brain/cycle`（缓存命中时 0.4ms，全冷约 40–90s）：

```json
{"version": "brain-cycle.v1", "ok": true,
 "sentiment": {"level": 80.5, "regime": "overheated", "turning": false},
 "survey": {"regime": "强势上行", "divergence": false, "alerts": 8},
 "experts": {"stance": "attack", "score": 0.846, "disagreement": 0.154},
 "memo": {"version": "brain-memo.v1", "stance": "attack", "stance_label": "进攻",
          "position_cap": 40, "confidence": 0.583,
          "thesis": ["市场结构", "情绪追踪", "六专家"],
          "risks": [{"code": "overheated", "text": "情绪过热，追高需减仓或等回踩"}],
          "invalidation": ["上证与创业板同时转负…", "情绪总分跌破 72（当前 80.5）", "情绪状态由「过热」切换"]}}
```

### 6.3 架构守卫

```
python tools/arch_report.py --check   → 退出码 0
依赖环 0 · 跨层反向依赖 0 · >900 行模块 0
python scripts/ci_guard.py            → 三道闸门全 PASS
```

---

## 七、下一阶段的提升目标（写进能力表，不是口号）

| 能力 | 目标 | 当前缺口 |
|---|---|---|
| 情绪追踪 | 拐点 T+1 命中率 ≥ 60%，20 点/日 | 线上刚接入，样本累积中 |
| 板块轮动 | 120+ 板块，1/5/20 日窗口齐备 | 20 日窗口依赖 `sector_history` 累积 |
| 资金流 | 实测通道 ≥ 50% | 东财常被频控，多为 proxy |
| 专家研判 | actionable ≥ 70% | 数据质量门槛会压低可执行率 |
| 备忘录 | 五要素齐备率 100% | 已满足（立场/理由/动作/风险/失效条件） |
| 账本校准 | T+1 结算 ≥ 95% | 需连续交易日累积 |

---

## 附录 A：星系浑天仪拖拽的「空间维度」根因与修复

> 独立于上面三层改造，同批完成；验收脚本 `scripts/verify/verify_armillary_drag.mjs`（17/17 PASS）。

### A.1 现象

用户反馈「仍有死角/拖不动」。用 Playwright 在**关闭自转与动效**的确定状态下测量（否则环境动画会污染结论）：

| 姿态 / 手势 | 全部星点平均位移（修复前） | 修复后 |
|---|---|---|
| 基准·水平拖 100px | 73.7px | 73.7px |
| 极点(90°)·水平拖 | **22.9px** | **80.1px** |
| 超极点(180°)·水平拖 | **18.9px** | **63.2px** |
| 右侧说明栏起拖 | **3.3px** | **64.0px** |

### A.2 根因（三条，均有测量支撑）

1. **两环欧拉在极点退化（主因）**
   相机原本只有「航向 ry + 俯仰 rx」，且 ry 绕**世界 Y 轴**。当俯仰接近 ±90°，世界 Y 轴与视线轴重合，
   水平拖拽退化为**绕视线轴自转**——星系盘近似轴对称，自转看起来就是「没动」。
   实测水平拖拽的星点位移从 73.7px 掉到 22.9px（−69%）。

2. **拖拽增量被渲染循环二次累加**
   拖拽处理器把姿态设成「起点 + 位移」的**绝对值**，同时写入 `cam.vry/vrx`；
   `loop3D` 每帧又把这个增量加到 `base.ry/rx` 上并衰减 → 松手后继续自转。
   实测松手后 3.25s 仍漂移 8.6px。

3. **按钮区域被排除在拖拽之外**
   `DRAG_BLOCKERS` 里含 `button`，而右侧说明栏几乎全是 `button.panel-row`，
   从栏内起拖时事件根本不进入拖拽分支（实测位移 3.3px ≈ 无响应）。

### A.3 修复

| 根因 | 修复 | 位置 |
|---|---|---|
| 极点退化 | 增加**第三环滚转 rz**，姿态用 `R = Ry·Rx·Rz`；拖拽改为**屏幕轴旋转**（预乘 `Ry(ax)·Rx(ay)` 后分解回三环），任何姿态下「左右拖=绕屏幕竖轴、上下拖=绕屏幕横轴」 | `camMatYXZ / camDecomposeYXZ / camScreenRotateFrom`、`project3` |
| 二次累加 | 拖拽期间 `loop3D` 跳过惯性分支；拖拽本身只做绝对映射，不再写 `vry/vrx` | `loop3D`、pointermove |
| 按钮死角 | 从 `DRAG_BLOCKERS` 移除 `button`，只保留输入框/链接/标签/缩放条/面板；按下不动仍是 click，移动 >5px 才吞掉这一次 click | `DRAG_BLOCKERS` |

顺带清理：`COSMO / galaxyArms / galaxySpinAngle` 有一段**完全重复的定义**（历史合并残留），已删。

### A.4 验收

`scripts/verify/verify_armillary_drag.mjs` 用三个正确的指标（质心位移会误判：平坦盘绕屏幕竖轴转，几何上是「压扁」不是平移）：

- **运动能量**：全部星点平均位移，5 个姿态均 ≥ 63px；
- **主轴比变化**：点云协方差 `sqrt(λ1/λ2)`，用于排除「原地自转」这种伪响应（极点 1.8%、超极点 22.8%）；
- **近轴跟手**：离旋转轴 120px 以上的星点位移方向与指针同号（19.2px / 13.7px）。

结果 **17 PASS / 0 FAIL**，含「按钮仍可点击」「松手不再继续转（0.00px）」「控制台零错误」。

---

## 附录 B：兼容与回滚

| 变更 | 兼容性 | 回滚 |
|---|---|---|
| 新增 `sentiment_tracker` | 只读 sentiment.v1，不改其算法 | 删除模块 + 两处 import |
| survey payload 新增 5 键 | **只增不改**，旧消费方忽略即可 | 移除对应键的构造 |
| data_bus 新增 2 资源 | 老资源键与 TTL 未变 | 删除 `register` 两段 |
| `/api/brain/cycle` 等 5 端点 | 只增不改，不影响既有路由 | 删除路由函数 |
| 浑天仪三环 | `cam.rz` 缺省 0，非星系视角行为不变（仍走 clamp） | 还原 `project3` 的 rz 分支与拖拽分支 |
