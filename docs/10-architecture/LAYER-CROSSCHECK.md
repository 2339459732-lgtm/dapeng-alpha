# 三层对证与数据配置（layer-crosscheck.v1 · brain-config.v1）

> 落地日期 2026-11-02 ｜ 代码：`server/brain_config.py`、`server/layer_crosscheck.py`、
> `server/brain.py`、`server/brain_capabilities.py`、`server/routers/brain.py`、
> `static/js/decision-desk.js`、`data/brain_config.json`

---

## 一、问题：三层各算各的，没人对账

主脑中心分三层，但它们对**同一件事**往往各有一份数：

| 指标 | 事实层 | 判断层 | 决策层 |
|---|---|---|---|
| 情绪分 | `sentiment.score` 80.5 | 界面显示「情绪分 100」（实为**结构分**） | 沿用 80.5 |
| 上涨占比 | 83.9%（全 A） | 83.9%（同一批报价） | 不涉及 |
| 涨停家数 | 145 | 145 | 不涉及 |
| 数据口径 | full_a_share | core_sample（降级时） | 沿用事实层 |

问题不在「数字不同」（量纲不同本就不该相同），而在：

1. **同名不同义**：判断层把 `regime.score`（宽度+指数+涨跌停合成的结构分）标成「情绪分」；
2. **口径不一致却混着比**：事实层全市场 145 家涨停，判断层核心样本 12 家，肉眼无法判断谁错；
3. **没有对账机制**：三层快照之间的偏差、时点差、分类体系差异，全靠人看；
4. **阈值散落**：容差、分档、时点上限写在各个模块常量里，改一处漏一处。

---

## 二、做法：配置集中 + 逐项对证 + 结论降级

### 2.1 数据配置（`data/brain_config.json` → `brain_config.py`）

一份 JSON 管住所有可调参数，带默认值兜底、按 mtime 热重载、非法值回落并报告：

```json
{
  "crosscheck": {
    "number_tolerance": 0.5, "ratio_tolerance": 0.5, "amount_tolerance_pct": 0.5,
    "count_tolerance": 0, "sector_top_n": 3, "max_as_of_skew_s": 300,
    "warn_factor": 2.0, "min_comparable_ratio": 0.5,
    "sector_taxonomy": {"default": "em", "labels": {"em": "东财板块", "sw": "申万行业"}}
  },
  "sentiment": { "bands": {...}, "min_coverage": 0.7 },
  "layers": { "fact": {...}, "survey": {...}, "decision": {...} }
}
```

`validate()` 会把类型不对/越界的项回落到默认值并返回 `issues`，页面显示「配置有 N 项回落默认值」。

### 2.2 对证引擎（`layer_crosscheck.py`）

九项检查、八类判据（情绪同源项在事实↔判断、事实↔决策各查一次），
每项给出 `a / b / delta / tolerance / status / note / hint`：

| id | 比对 | 判据 |
|---|---|---|
| `sentiment_fact_survey` | 事实情绪分 ↔ 判断层情绪分 | 数值容差 ±0.5 |
| `sentiment_fact_decision` | 事实情绪分 ↔ 决策层情绪分 | 数值容差 ±0.5（证明同源传递） |
| `breadth_up_ratio` | 上涨占比 | 容差 ±0.5 个百分点 |
| `turnover` | 成交额 | 相对容差 0.5% |
| `limit_up` | 涨停家数 | 完全相等 |
| `sector_lead` | 领涨板块前 N | 有交集即通过；**分类体系不同则判不可比** |
| `scope` | 数据口径 | 相同通过；不同 warn |
| `freshness` | 三层时点最大偏差 | ≤300s 通过 |
| `score_axes` | 结构分 ↔ 情绪分 | **异量纲**：只校验方向（bullish/bearish），方向冲突才是 conflict |

状态语义：

- `pass` 一致；`warn` 超容差但在 2× 内；`conflict` 真冲突；
- `incomparable` **不可比**（异量纲、分类体系不同、口径不同）——不是错误，也不参与打分；
- `unavailable` 缺数据——不参与打分（降级不能伪装成不一致）。

结论：

```
ok = 无 conflict 且 可比率 ≥ 0.5 且 无 blocker
blockers: ["scope_mismatch"]   // 口径不同时显式阻塞，而不是报一堆假冲突
score = 可比项加权（pass 1.0 / warn 0.5 / conflict 0 / incomparable 1.0）
```

### 2.3 判断层补「实际输入」（`survey.facts`）

对证不能靠猜。判断层此前只在 `data_coverage.ashare` 里给汇总，降级时为空，
导致 4 项永久 `unavailable`。现在 payload 增加 `facts`：

```json
{"up":4680,"down":818,"flat":83,"up_ratio":83.9,"limit_up":145,"limit_down":23,
 "amount":1794899202976.0,"source":"market-coverage.v1","scope":"full_a_share","as_of":"..."}
```

对证优先读它，比的是「判断层真正用到的输入」而不是二手汇总。

### 2.4 接进闭环与页面

- `brain.run_cycle()` 增加 `crosscheck` 步骤，结果写入 `brain-cycle.v1.crosscheck`；
- `compose_memo(..., crosscheck=...)`：**有冲突则置信 ×0.85**，并把冲突与「可比率不足」写进风险；
- `GET /api/brain/alignment`：配置校验 + 能力图 + 对证结果；
  `ok` 表示**接口成功**，业务结论放 `healthy` / `crosscheck.ok`（避免消费方把「有冲突」当请求失败）；
- 决策层顶部常驻「三层对证」条：`三层对证 一致 6 · 不可比 2 · 一致性 100%` + 逐项 chip + 「重新对证」。
  刻意**不写「6/8」这种分数**：不可比项按 1.0 计分，分子分母一摆就会与一致率自相矛盾；
  改为逐类给绝对值，一致性另列；
- 两档缓存分开（`layer_crosscheck.json` / `layer_crosscheck_light.json`）：
  页面小条走 light（事实↔判断，不跑六专家），闭环走 full。

---

## 三、对证抓到的真实问题（都已修）

| # | 现象 | 根因 | 处置 |
|---|---|---|---|
| 1 | 判断层显示「情绪分 100」，事实层 80.5 | `regime.score` 是结构分，标签写成情绪分 | 标签改「结构分」并附「情绪分 79.9（过热）」 |
| 2 | 4 项长期 `unavailable` | 判断层降级时无 `ashare`，对证拿不到数 | payload 增 `facts`，对证改读实际输入 |
| 3 | 涨停 145 vs 12 被判「冲突」 | 两侧口径不同（全市场 vs 核心样本） | 口径不一致时宽度类改判 `incomparable` + `scope_mismatch` 阻塞 |
| 4 | 领涨板块永远对不上 | 事实层回退用申万行业，判断层用东财板块 | 登记 `sector_taxonomy`，不同体系判 `incomparable` |
| 5 | 页面小条显示「暂不可用」 | 接口 `ok:false` 被前端当成请求失败 | 接口 `ok:true` + `healthy`；前端状态细化 |

---

## 四、验证

`scripts/verify/verify_layer_crosscheck.mjs` —— **18 PASS / 0 FAIL**：

- 配置校验通过；对证 9 项（light 8 项）；`ok/score/counts/comparable_ratio` 字段齐全；
- 覆盖同源指标，异量纲项不判数值；完整档含决策层、轻量档不含；
- 页面 chip 数与轻量档一致、每项有状态与说明、无 `undefined/NaN`、有「重新对证」；
- 能力图仍可用；控制台零错误。

单测：`tests/test_brain_config.py`（7）+ `tests/test_layer_crosscheck.py`（16）+ 备忘录新增 3 项；
全库 **316 passed**；`ci_guard` 三道闸门全 PASS；依赖环 0、跨层反向依赖 0。

---

## 五、实测输出（2026-11-02 22:0x）

```
healthy true | score 1.0 | 9 项：7 一致 / 2 不可比 / 0 冲突
  pass          情绪分（事实 ↔ 判断）        Δ+0.000
  pass          情绪分（事实 ↔ 决策）        Δ+0.000
  pass          上涨占比（事实 ↔ 判断）       Δ+0.040%
  pass          成交额（事实 ↔ 判断）        相对 0.000%
  pass          涨停家数（事实 ↔ 判断）       Δ 0 家
  incomparable  领涨板块（事实 ↔ 判断）       申万行业 vs 东财板块
  pass          数据口径（事实 ↔ 判断）       同为 full_a_share
  pass          数据时点偏差（三层）          最大偏差 27s
  incomparable  结构分 ↔ 情绪分（方向一致性）   方向同为 bullish
```

---

## 六、已知取舍

1. **不追求「数字相等」**：结构分与情绪分、申万与东财，本质上不可比；强行对齐会制造假精度。
   对证只保证「同源项一致、异源项显式声明」。
2. **判断层缓存 600s**：情绪分可能滞后于事实层（实测 Δ0.6 触发 warn）。
   这是真实的时效差异，不掩盖——把 survey 的 TTL 调小会显著增加 LLM 调用成本。
3. **板块级对证依赖分类体系**：统一到同一体系（当前均为东财板块时）才会做名称比对，
   否则只报「不可比」。要真正打通需要板块映射表，属下一阶段。
4. **对证不自动改数**：冲突只降置信、加风险，不修改任何一层的输出——避免掩盖问题。
