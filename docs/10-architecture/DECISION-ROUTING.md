# 决策分流：指标接入、映射规则与降级约定（decision-routing.v1）

> 落地日期 2026-11-03 ｜ 代码：`server/decision_metrics.py`、`server/decision_routing.py`、
> `data/decision_routing.json`、`server/brain_capabilities.py`（闸门）、`server/routers/decision.py`
>
> 一句话：**把趋势 / 资金流 / 买手率 / 情绪收敛成同一种尺度的指标，用可配置的规则算出「现在最多能承担多大风险」，
> 作为决策层的仓位闸门——只收紧，不产生方向。**

---

## 一、数据源接入方式与字段定义

| 指标 id | 数据源 / 接口 | 原始字段 | 单位 | 接入方式 | 更新频率 | 授权 |
|---|---|---|---|---|---|---|
| `trend.index` | `market-dashboard.index_trends` | `points[].close`（近 6 根 5 分钟线） | % | 进程内调用，失败退化到 `packet.market.indices[].chg` | 60s | 公开 |
| `trend.breadth` | `market-packet.market.breadth` | `up_ratio` | % | 全市场快照（5782 只） | 55s | 公开 |
| `trend.limit` | 同上 | `limit_up`、`limit_down` | 家 | 按板块阈值判定（主板 9.5% / 双创 19.5% / 北交所 29.5% / 主板 ST 4.8%） | 55s | 公开 |
| `flow.main` | `market-dashboard.capital_flow` | `flow_a`（主力净额，**上游口径未核实**） | 亿元 | 东财资金流接口聚合 | 300s | 公开 |
| `flow.north` | `market-packet.north` | `data.total`、`data.disclosed` | 亿元 | AkShare 沪深港通；**未披露时 `disclosed=false`** | 1800s | 公开 |
| `buyratio.market` | 腾讯 `qt.gtimg.cn` | `f7 外盘`、`f8 内盘` | % | 成交额前 120 只批量取样（60 只/批、4 并发） | 180s | 公开免 key |
| `buyratio.etf` | 同上 | 同上 | % | 固定 ETF 篮子：510300 / 510500 / 512100 / 588000 / 159915 | 180s | 公开免 key |
| `sentiment.score` | `sentiment.v1` | `score`、`coverage` | 分 | 六维加权（覆盖 <70% 不输出） | 45s | 公开 |

**买手率定义**：`外盘 / (外盘 + 内盘) × 100%`。外盘 = 主动买成交量，内盘 = 主动卖成交量，
单位「手」；这是逐笔口径的主动买占比，**不是**委托挂单簿（L2 十档）口径。
指数没有外盘/内盘字段，因此指数类不给买手率（`available=false`，不用成交量冒充）。

**多市场 / 多品种**：
A 股全市场（沪 2362 / 深 2944 / 京 476，有效报价 5668）、主要指数（上证/深成/创业板/科创）、
ETF 篮子（宽基 5 只）。港股/美股目前只有指数级数据（`global` 模块），未纳入买手率取样。

---

## 二、指标 → 分流条件：映射与权重

所有指标归一化到 `score ∈ [-1, 1]`，规则只认 `score`（或必要的原始 `value`），
这样阈值能跨指标比较：

| 指标 | 归一化公式 | 直观含义 | 权重 | 置信基线 |
|---|---|---|---|---|
| `trend.index` | `tanh(30min涨跌幅% / 0.6)` | ±0.6% 对应 ±0.76 | **0.20** | 0.75（退化 0.50） |
| `trend.breadth` | `(上涨占比 − 50) / 30`，截断 ±1 | 80% 上涨 = +1 | **0.16** | `0.55 + 0.4×报价覆盖` |
| `trend.limit` | `(涨停−跌停)/(涨+跌+5) × 2.2` | 涨跌停强弱 | **0.10** | 0.70 |
| `flow.main` | `tanh(主力净额亿元 / 40)` | ±40 亿 ≈ ±0.76 | **0.20** | 0.50（口径未核实，已折半） |
| `flow.north` | `tanh(净流入亿元 / 50)` | ±50 亿 ≈ ±0.76 | **0.08** | 0.80 |
| `buyratio.market` | `(买手率 − 50) / 15`，截断 ±1 | 65% = +1，35% = −1 | **0.16** | `0.45 + 0.45×取样覆盖` |
| `sentiment.score` | `(情绪分 − 50) / 30`，截断 ±1 | 80 分 = +1 | **0.10** | `0.4 + 0.5×六维覆盖` |

**综合分**：

```
composite = Σ(weight_i × confidence_i × score_i) / Σ(weight_i × confidence_i)     （仅可用项）
```

缺失项自动重新归一，不会把总分拉向 0；`stale` 项的 confidence 再乘 0.6。
`composite ≥ 0.30` → `risk_on`；`≤ −0.30` → `risk_off`；其余 `neutral`。

---

## 三、分流优先级与触发阈值

规则在 `data/decision_routing.json`，按 `priority` 从高到低**先保护、后进攻**，命中即停：

| 优先级 | 路由 | 触发条件（全部满足） | 仓位上限 | 语义 |
|---|---|---|---|---|
| 100 | `risk_off` 系统性风险收缩 | `trend.breadth.score ≤ −0.45` **且** `flow.main.score < −0.20` | **10%** | 广度与主力资金同时转负 |
| 90 | `buy_squeeze` 买盘枯竭 | `buyratio.market.score ≤ −0.40` 且（可选）`flow.main.score < −0.10` | **20%** | 主动买盘明显弱于卖盘，反弹不做 |
| 80 | `risk_on` 风险偏好上行 | `breadth ≥ 0.45` **且** `buyratio.market ≥ 0.20` **且** `flow.main ≥ 0` | **60%** | 三者同向才放开进攻 |
| 70 | `trend_follow` 趋势跟随 | `trend.index ≥ 0.35` **且** `breadth ≥ 0.20` | **50%** | 指数与广度同步走强，顺势不追高 |
| 60 | `index_breadth_split` 指数与个股背离 | `trend.index ≤ −0.25` **且** `breadth ≥ 0.25` | **35%** | 指数被权重拖累、个股普涨，控节奏 |
| 10 | `neutral_range` 区间震荡 | `breadth.score > −1`（兜底命中） | **40%** | 无共振，按中性处理 |
| — | `observe` 数据不足·观察 | 可用指标 < 4 **或** 覆盖率 < 0.5，或全部未命中 | **0%** | 不做方向性暴露 |

阈值取值依据：`±0.45` 对应上涨占比约 36%/64%，`±0.40` 买手率对应约 44%/56%，
`40 亿` 主力净额约为日均成交额的 0.5%（2026-11-03 实测日均 8,300 亿）。
所有阈值都可改配置，不需要改代码。

**与决策层的关系（只收紧）**：

1. `cap = min(memo_cap, route.cap_max)`；
2. 路由判 `defend` 而专家判 `attack` → 立场降级为 `neutral`（10% 仓位的「进攻」自相矛盾）；
3. 路由**不会**提高仓位、不会新增方向、不会覆盖专家意见。

---

## 四、更新频率与时效要求

| 层级 | 频率 | 时效上限（TTL） | 超时后果 |
|---|---|---|---|
| 全市场快照 packet | 55s（闭环内钉住 300s） | 120s | `trend.breadth/limit` 记 `stale`，confidence ×0.6 |
| 情绪 sentiment | 45s | 120s | `sentiment.score` 记 `stale` |
| 指数分时 | 60s | 300s | 退化到当日涨跌幅（confidence 0.75 → 0.50） |
| 资金流 | 300s | 600s | 记 `stale`；仍参与但降权 |
| 北向 | 1800s | 3600s | 多为「未披露」→ 直接 unavailable |
| 买手率取样 | 180s | 360s | 记 `stale`；取样失败 → unavailable |
| **分流结论** | 缓存 20s | — | 页面轮询不会重复打腾讯接口 |

超过 `2×TTL` 直接判 `available=false`——**宁可承认没有数据，也不拿旧数据当现状**。

---

## 五、缺失 / 延迟 / 异常时的降级与兜底

| 情形 | 识别方式 | 处理 | 决策后果 |
|---|---|---|---|
| 单指标缺失 | `available=false` + `reason` | 该指标退出加权，其余重新归一 | 仍可分流，但 `coverage` 下降 |
| 指标陈旧 | `age_s > ttl_s` → `stale=true` | 置信 ×0.6，仍参与 | 结论保留但更保守 |
| 超过 2×TTL | 自动置 `available=false` | 同「缺失」 | 视覆盖情况 |
| 关键条件缺指标 | 条件非 `optional` 且指标不可用 | **该路由不命中**，继续判下一条 | 不会误触发 |
| 指标大面积缺失 | 可用 < 4 或覆盖 < 0.5 | 走 `fallback=observe`，`degraded=true` | **仓位上限 0**，风险项写明原因 |
| 上游接口异常 | 采集抛异常 | 指标 `available=false` + 异常类型；分流不崩 | 同上 |
| 配置非法 | `validate()` 发现问题 | 接口返回 `config.issues`，规则按最后一份有效配置执行 | 不阻断主链路 |
| 腾讯买手率全批失败 | `rows` 为空 | `buyratio.*` unavailable，权重让给其余指标 | 分流仍可用 |

**不做的事**：不填 0、不给中性分、不用昨收冒充现价、不在缺数据时放开仓位。

---

## 六、交付物形态

### 6.1 配置清单（唯一可改点）

```bash
data/decision_routing.json     # 权重 / 阈值 / 6 条路由 / 降级参数（改完按 mtime 热生效）
```

字段：`weights[metric]`、`thresholds{risk_on,risk_off,min_coverage,min_available}`、
`routes[]{id,label,priority,match,conditions[],action{stance,cap_max,note}}`、`fallback`、`degrade`。

### 6.2 分流规则表

见 §三；接口 `GET /api/decision/routing` 返回的 `evaluated[]` 会逐条给出「为什么没命中」，
等价于把这张表实时展开。

### 6.3 接口

```bash
curl -s "localhost:8900/api/decision/metrics?refresh=1" | python -m json.tool   # 8 项指标明细
curl -s "localhost:8900/api/decision/routing?with_metrics=1" | python -m json.tool
curl -s -X POST localhost:8900/api/brain/cycle | python -m json.tool            # memo.routing + cycle.routing
```

### 6.4 校验与验证

```bash
# 1) 规则配置校验（未知指标 / 优先级重复 / 仓位越界 / 阈值倒挂）
python -c "from server.decision_routing import validate; print(validate())"

# 2) 规则单测（15 项：命中顺序、归一化、陈旧折减、降级、备忘录只在收紧方向生效）
pytest tests/test_decision_routing.py -q

# 3) 系统体检（含分流 5 项：配置 / 覆盖 / 当前路由 / 买手率 / 采集耗时）
python scripts/verify/system_audit.py --gate

# 4) 全量回归
pytest tests -q && python scripts/ci_guard.py
```

### 6.5 实测（2026-11-03 11:0x，盘中）

```
路由 index_breadth_split「指数与个股背离」 cap=35 · bias=neutral · composite=-0.162
coverage 87.5%（7/8）· 不可用 ['flow.north'（交易所未披露）]
买手率：全市场 47.97% · ETF 56.68% · 采集 662ms
memo：stance=attack cap=9（分流上限 35 未触顶，仅作闸门）
```

---

## 七、已知边界

1. **买手率是取样**：120 只按成交额取样 + 5 只 ETF，不是全市场逐笔。全量需 L2 行情源，
   当前 `.coverage` 字段如实给出取样比例（实测 1.0 = 120/120 成功）。
2. **`flow.main` 口径未核实**：上游未标注主力/大单类别，置信上限 0.5，权重 0.20 已属保守；
   接入券商 L2 资金流后可上调。
3. **北向常态不可用**：沪深港通改为按季披露，`flow.north` 大多 `available=false`，
   属真实数据状态，不是故障。
4. **港股/美股未纳入买手率**：只有指数级数据，缺逐笔口径。
5. **分流只做闸门**：不产生买卖点、不做仓位分配，方向仍由研判团与备忘录决定。
