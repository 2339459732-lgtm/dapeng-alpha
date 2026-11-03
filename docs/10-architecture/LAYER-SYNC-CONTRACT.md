# 全 A 股分层实时分析流程 · 层间同步契约

> 版本 `layer-sync.v1` ｜ 落地日期 2026-11-02 ｜ 基线：`pytest 349 passed`
>
> 对应代码：`server/market_coverage_*.py`、`server/decision_snapshot.py`、`server/sentiment_tracker.py`、
> `server/market_survey_engine.py`、`server/decision_experts*.py`、`server/brain_capabilities.py`、
> `server/data_bus.py`、`server/lineage.py`
>
> 本文定义的是**契约**：谁在什么时候、把哪些字段、以什么方式交给下一层，以及
> 「结论能不能倒推回上游原始数据」。字段名与单位以运行中的接口为准，与代码不一致时以代码为准。

---

## 一、一页总览

```
        ┌───────────────────────────── 事实层（测量）─────────────────────────────┐
沪深京   │ market-coverage.v1  →  market-packet.v1  →  sentiment.v1  →  sentiment-flow.v1 │
全标的   │  报价/估值/基本面      广度/成交额/板块    六维情绪分        变化量/斜率/拐点  │
        └───────────────────────────────────┬──────────────────────────────────────┘
                                            │ 边界 A：快照 + 情绪 + 追踪（同 trace）
        ┌───────────────────────────────────▼──────────────────────────────────────┐
        │ 判断层（解释）survey-pipeline.v1：三阶段 + 结构状态 + 背离 + facts（实际输入）│
        └───────────────────────────────────┬──────────────────────────────────────┘
                                            │ 边界 B：判断结论 + 它依赖的原始 facts
        ┌───────────────────────────────────▼──────────────────────────────────────┐
        │ 决策层（裁决）expert-panel.v1 → brain-memo.v1：研判团六专家 → 立场/仓位/证伪│
        └───────────────────────────────────┬──────────────────────────────────────┘
                                            │ 边界 C：结论 + citations + lineage.v1
                                       留痕与回放（trace_id 反查）
```

| 层 | 产出契约 | 一句话职责 | 关键节拍 |
|---|---|---|---|
| 事实 | `market-coverage.v1` / `market-packet.v1` / `sentiment.v1` / `sentiment-flow.v1` | 只测量，不解释 | coverage 120s（盘中）· packet 55s · 情绪 45s · 追踪 60s |
| 判断 | `survey-pipeline.v1` | 只解释，不下单 | 600s（可按需强刷） |
| 决策 | `expert-panel.v1` / `brain-memo.v1` | 只裁决，不改上游数字 | 300s；闭环按需 |
| 留痕 | `lineage.v1` | 结论 → 依据的可解析链路 | 每轮闭环写一条，保留 60 条 |

---

## 二、事实层：全市场采集与动态更新

### 2.1 采集范围（沪深京全标的）

| 维度 | 来源 | 范围 | 更新 | 说明 |
|---|---|---|---|---|
| 行情（沪、深、京） | 新浪 `Market_Center hs_a` | 全池 5782；主源单次 5.0k–5.6k（分页/限流会漏抓） | **300s** 缓存，worker 120s 触发 | 分页 100 条/页，8 并发，失败重试 2 次 |
| 行情补缺（兜底） | 腾讯 `qt.gtimg.cn` 批量 | 仅补「池子里有、但主源无价格」的代码（单次 400–750 只） | **120s** | 60 只/批、4 并发；补不到保持 null 并列入 `unresolved` |
| 行情（京，备用源） | 东财 `clist m:0+t:81+s:2048` | 363 只 | **300s** | 补新浪未收录的 9 只，并提供第二来源交叉验证 |
| 估值/基本面 | 东财 `RPT_LICO_FN_CPD` | 5763 只（季度报告） | **6h** | ROE / 营收 / 净利 / EPS / 毛利率 |
| 核心样本快报 | 腾讯 `qt.gtimg.cn` | 核心池 | **60s** | 覆盖新浪 5 分钟快照的时延 |
| 全球指数/国家/行业 | 腾讯 + 公开源 | 17 指数 / 8 国家 / 11 行业 ETF | 60s | 只做背景，不参与个股判断 |
| 宏观 | 美财政部 / NY Fed / ECB / World Bank | 19 项全球 + 5 项中国 | 1800s | 时点以来源发布为准 |
| 公告与舆情 | 东财资讯/公告 | **抽样 10 条**（明确边界） | 随调查 600s | 见 §7 边界 1：不是全量公告流 |
| 资金流向 | 东财实测，频控期降级为注意力代理 | 板块/个股 | 与 packet 同步 | 降级时字段标注 `proxy`，不伪装成实测 |

> **完整性以分市场口径衡量**，不用整体覆盖率掩盖缺口：整体 96.5% 看起来很好，
> 拆开才看到北交所只有 74.4%（476 只有 354 只有报价）。见 §6。

### 2.2 事实层内部派生（都是「算」不是「采」）

| 契约 | 输入 | 关键输出字段 | TTL |
|---|---|---|---|
| `market-packet.v1` | coverage + 核心样本 + 全球 + 情报 | `market.breadth{total,valid,up,down,flat,up_ratio,limit_up,limit_down,amount,median_chg}`、`market.turnover`、`market.sectors`、`market.judgment`、`quality{level,coverage_pct}`、`evidence[]`、`chain{build_ms,coverage_age_s}` | 55s（闭环期间钉住 300s） |
| `sentiment.v1` | packet | `score`、`status`、`coverage`、`dimensions[6]`（权重 30/20/15/15/10/10）、`risk_flags`、`inputs` | 45s |
| `sentiment-flow.v1` | sentiment + 历史 | `level`、`band`、`regime{key,label}`、`delta_short/delta_day`、`slope_per_hour`、`z20`、`percentile20`、`persistence`、`turning_point{detected,kind}`、`drivers/drags` | 60s |

### 2.3 完整性与时效的保证机制

1. **不编造**：取不到就是 `null` / `ok:false` + `reason`，禁止 0 或中性值填充（`quality.actionable` 明确表态）。
2. **口径可见**：每份 payload 带 `data_scope`（`full_a_share` / `core_sample`）与 `source`，
   跨层比对时口径不同判「不可比」而不是「冲突」。
3. **单飞重建**：同 key 并发只跑一次 loader，避免上游被自己打崩。
4. **失败退避**：连续失败计数 + 退避，不用重试把频控打成封禁。
5. **快照钉住**：闭环开始时 `pin_packet()`，整轮决策读同一份快照（`force` 也不会越过钉住）。

---

## 三、边界 A：事实层 → 判断层

### 3.1 同步内容范围

**给**：一份冻结快照（`packet`）+ 情绪分（`sentiment`）+ 情绪追踪（`flow`）+ 全市场扫描（`scan`）。
**不给**：全市场逐笔明细、原始分页数据、任何未聚合的中间态 —— 判断层要的是「事实的结论」，不是「事实的数据库」。

| 字段 | 类型 | 单位 | 必填 | 含义 |
|---|---|---|---|---|
| `trace_id` | string | — | ✅ | 快照标识，全链路一致（`mk_<16hex>`） |
| `as_of` | ISO8601 | — | ✅ | **数据时点**（报价时点），不是构建时间 |
| `market.breadth.up_ratio` | float | % | ✅ | 上涨占比（分母为该口径的有效报价数） |
| `market.breadth.limit_up` / `limit_down` | int | 家 | ✅ | 涨跌停家数（按板块阈值：主板 9.5%、双创 19.5%、北交所 29.5%、主板 ST 4.8%） |
| `market.breadth.amount` | float | 元 | ✅ | 成交额（全市场） |
| `market.breadth.median_chg` | float | % | ✅ | 涨跌幅中位数（抗极值） |
| `market.sectors[].{name,pct,stage}` | array | % | ✅ | 板块阶段结构，判断层做轮动分析的输入 |
| `sentiment.score` / `band` | float / string | 分 / 档 | ✅ | 六维加权分与五档（极冷/偏冷/中性/偏热/过热） |
| `flow.delta_short` / `slope_per_hour` | float | 分 / 分·h⁻¹ | ✅ | 近端变化与斜率 |
| `flow.turning_point.detected` | bool | — | ✅ | 拐点标记（判断层用来升级预警） |
| `quality.level` / `coverage_pct` | string / float | — / % | ✅ | 数据质量，判断层据此降权 |

### 3.2 更新频率与传递方式

| 项 | 约定 |
|---|---|
| 频率 | packet 55s / sentiment 45s / flow 60s / scan 120s；判断层自己 600s |
| 传递方式 | **进程内缓存引用**（同一份 dict，不序列化、不拷贝大对象）+ **总线级联失效**（packet 刷新 → 失效 sentiment/scan，闸门 40s）+ **SSE 通知**（慢消费者丢帧不阻塞） |
| 顺序保证 | 判断层读的是「取到那一刻的缓存」；同一闭环内由 `pin_packet()` 保证一致 |
| 失败传递 | 上游 `ok:false` 时，判断层在 `inputs.{domain}.available=false` 如实标注，不静默跳过 |

---

## 四、边界 B：判断层 → 决策层

### 4.1 同步内容范围（判断结论 + 它依赖的原始数据）

这是「层层递进」的关键：**不能只给结论**。判断层必须把「我看到的原始输入」一并交出去，
否则决策层无法判断这个结论是不是建立在降级数据上。

| 字段 | 类型 | 必填 | 用途 |
|---|---|---|---|
| `version` | string | ✅ | `survey-pipeline.v1`（本轮补齐顶层可见） |
| `trace_id` / `as_of` | string / ISO8601 | ✅ | 与事实层同一条时间轴（本轮补齐） |
| `regime.{status,score,confidence,main_driver,liquidity_env,risk_assessment}` | object | ✅ | 结构判断结论 |
| `divergence.{detected,available,survey_tone,sentiment_tone,note}` | object | ✅ | 结构 ↔ 情绪背离（决策层据此降仓位） |
| `facts.{up,down,flat,up_ratio,limit_up,limit_down,median_chg,amount,source,scope,as_of}` | object | ✅ | **判断层真正用到的原始输入**，供对证与回溯 |
| `pipeline.{version,stages[],total_ms}` | object | ✅ | 三阶段耗时（collect/diagnose/conclude），可定位慢在哪 |
| `inputs.{dashboard,macro,news,sentiment,coverage}.available` | object | ✅ | 每个域是否可用、来源与 trace |
| `rotation` / `alerts` / `sector_linkage` | array | ✅ | 轮动、预警、板块联动 |
| `data_scope` / `data_coverage` | string / object | ✅ | 口径与覆盖，决策层据此判断可用性 |

### 4.2 更新频率与传递方式

| 项 | 约定 |
|---|---|
| 频率 | 600s（含 LLM 综合的判断成本高）；`POST /api/brain/survey/refresh` 可按需强刷 |
| 传递方式 | 判断层产物同时进入：① 决策层专家团的输入（进程内引用）；② 总线 `survey` 资源（供对证与链路视图）；③ 调查历史环形文件（重启可回溯） |
| 口径保证 | `facts` 与事实层同名字段**必须可比**：`up_ratio`/`limit_up`/`amount` 的容差在对证层定义（±0.5 / 0 家 / 0.5%），超差即 warn |
| 降级传递 | `regime.confidence` 随数据质量下降；`inputs.*.available=false` 会写进决策层 `memo.gaps` |

---

## 五、边界 C：决策层 → 结论与留痕

### 5.1 研判团（六专家）与汇总

| 环节 | 输入 | 输出字段 |
|---|---|---|
| 独立研判 E1–E6 | packet + sentiment + flow + **survey** + scan | 每位专家 `{expert_id, stance, score, reasons[], confidence}` |
| 互评与分歧 | 六份独立意见 | `deliberation`、`minority[]`、`consensus.disagreement` |
| 汇总裁决 | 共识 + 风险否决 | `consensus.{score,label,stance,disagreement,confidence,position_cap,veto,veto_reasons}` |
| 备忘录 | 共识 + 情绪 + 背离 + 对证 | `brain-memo.v1`：`stance`、`position_cap`、`confidence`、`thesis[]`、`actions[]`、`risks[]`、`invalidation[]`、`gaps[]`、`citations[]` |

> **判断层如何真正进入决策**（不是"数据流过了"就算贯通）：
> E5 情绪专家读 `survey.divergence`，背离时禁止进攻并把仓位压到 ≤35%；
> E6 审计专家在 `survey` 不可用时直接否决可执行（三层缺一层 = 链断了）；
> 共识层输出 `consensus.survey_alignment`（aligned / mixed / divergent / unknown）并写入 `inputs.survey`，
> 备忘录再按 `divergence` 做 ×0.8 压仓。三处都落库，可回溯。

仓位规则（写死在 `compose_memo`，不靠人记）：共识仓位为基准 → 情绪降温 ×0.7 → 结构-情绪背离 ×0.8 →
存在 veto 归零 → `stance=observe` 归零 → **硬顶 80%**；三层对证有冲突时**置信 ×0.85** 并写入风险。

### 5.2 传递方式：结论自带「引用清单」

```json
{
  "stance": "attack", "position_cap": 40, "confidence": 0.58,
  "citations": [
    {"node": "coverage", "version": "market-coverage.v1", "as_of": "…", "digest": "d958ce1240ad", "same_trace": true},
    {"node": "packet",   "version": "market-packet.v1",   "as_of": "…", "digest": "f1ae99d5414f", "same_trace": true},
    {"node": "survey",   "version": "survey-pipeline.v1", "as_of": "…", "digest": "6e4a6fa70a41", "same_trace": true},
    {"node": "experts",  "version": "brain-panel.v3",     "as_of": "…", "digest": "313a4ce779a9", "same_trace": true}
  ],
  "lineage": {"version": "lineage.v1", "trace_id": "mk_…", "chain_ok": true, "consistent": true, "gaps": []}
}
```

`same_trace=false` 表示该条依据来自**另一份快照**（并发刷新/缓存错位），如实标注而不是隐藏。

---

## 六、字段规范（全链路统一）

| 规范 | 约定 | 反例（已治理） |
|---|---|---|
| 契约三件套 | 每份 payload 顶层必须有 `version` / `as_of` / `trace_id` | 调查层此前三者都缺，溯源表只能填 null（本轮补齐） |
| 时点语义 | `as_of` = **数据时点**；`generated_at` = **生产时间**；顺序校验只用后者 | 拿 `as_of` 比大小把正常链路判成「时间倒挂」 |
| 时间格式 | ISO8601；跨层比较前统一解析（带/不带时区都接受） | 混用 `T` 与空格、带/不带 `+08:00`，字符串直接比大小得出错误结论，甚至打成 500 |
| 单位 | 金额=元、量=股、市值=亿元、比例=百分点（不是小数） | 北交所源给「手」，未换算会与新浪口径差 100 倍 |
| 口径 | `data_scope` ∈ {`full_a_share`,`core_sample`}；不同口径**不做数值比对** | 145 vs 12 家涨停被判「冲突」，实为两种口径 |
| 来源 | `source` / `quote_source` 必填，降级标注 `proxy`/`fallback` | 代理数据冒充实测 |
| 缺失 | 用 `null` + `reason`，**禁止 0 填充** | 缺失写成 0 会让「中位数 0%」看起来像真数据 |
| 代码前缀 | `sh`/`sz`/`bj` 三段；北交所含 43/83/87/88/**920** 段 | `920xxx` 被误判为沪市，同一只股票被记成 `sh920xxx` 与 `bj920xxx` 两条 |

---

## 七、完整性与时效（实测）

### 7.1 全市场覆盖（`GET /api/decision/coverage`）

```
total_universe 5782 ｜ 有效报价 5668（98.0%）
沪 sh 2362 / 有报价 2362（100.0%）
深 sz 2944 / 有报价 2944（100.0%）
京 bj  476 / 有报价  362（76.1%）  ← 缺口集中在这里
本次补缺 586 只（请求 700+，未解析 114）
```

三层补缺链路：**新浪（主源）→ 东财北交所（分市场）→ 腾讯批量（兜底，只补无价格的代码）**。
补不到的 114 只集中在北交所，只在东财季度报告里出现（退市/长期停牌/代码失效），
保持 `null` 并列入 `sources.tencent_gapfill.unresolved` —— **不伪造缺口**，也不拿估值反推价格。

### 7.2 时效（`GET /api/decision/bus`）

| 资源 | TTL | 触发 | 闸门 |
|---|---|---|---|
| coverage | 300s | worker（盘中 120s / 非盘中 1800s） | 60s |
| packet | 55s（闭环钉住 300s） | request | 40s |
| sentiment | 45s | request | 180s |
| sentiment_flow | 60s | request | 30s |
| scan | 120s | request | 180s |
| survey | 600s | request | — |
| experts | 300s | request | — |

链路视图给出每个资源的 `age_s / cache_age_s / last_ms / fresh`；
新鲜度取「总线时间戳」与「缓存条目」中**较新的一个**（只信总线会把热缓存误判成 stale）。

---

## 八、可追溯（`lineage.v1`）

### 8.1 链路结构

```
coverage → packet → sentiment → sentiment_flow → survey → experts → crosscheck → memo
```

| 节点 | 记录内容 |
|---|---|
| 每节点 | `present` / `version` / `as_of` / `built_at` / `trace_id` / `ok` / `digest` / `facts` |
| `digest` | 关键字段的 sha1 前 12 位 —— 依据变了指纹就变，防止「结论引用了另一版数据」 |
| 检查项 | `complete`（节点齐）/ `time_ordered`（生产顺序递增）/ `trace_consistent`（同一份快照） |
| 结论 | `ok` = 结构可用；`consistent` = 依据同源（单独表达，不否证链路） |

### 8.2 回放命令

```bash
# 跑一轮闭环
curl -s -X POST localhost:8900/api/brain/cycle | python -m json.tool | head -40

# 取 trace_id 后回放整条依据链
curl -s "localhost:8900/api/brain/lineage/$(echo mk_xxx)" | python -m json.tool

# 最近 10 条链路（含 gaps 与 ok）
curl -s "localhost:8900/api/brain/lineage/recent?limit=10"
```

实测一条完整链路（`ok=true`、`consistent=true`、`gaps=[]`）：

| 节点 | 版本 | as_of | digest |
|---|---|---|---|
| coverage | market-coverage.v1 | 23:11:00 | d958ce1240ad |
| packet | market-packet.v1 | 23:10:26 | f1ae99d5414f |
| sentiment | sentiment.v1 | 23:10:26 | bf21a9e8fbc5 |
| sentiment_flow | sentiment-flow.v1 | 23:10:26 | 5f03c80ede08 |
| survey | survey-pipeline.v1 | 23:10:26 | 6e4a6fa70a41 |
| experts | brain-panel.v3 | 23:10:26 | 313a4ce779a9 |
| crosscheck | layer-crosscheck.v1 | 23:10:2x | 7f2d2174ac85 |
| memo | brain-memo.v1 | 23:10:26 | 19e063085897 |

### 8.3 为保证可追溯而修掉的真问题

| # | 现象 | 根因 | 处置 |
|---|---|---|---|
| 1 | 一条决策链挂两三代 `trace_id` | packet TTL 55s，而上游慢时构建就要 27s，下游任意 `force` 会重建 | 闭环 `pin_packet()` 钉住快照，`force` 不得越过 |
| 2 | 溯源顺序被误判「倒挂」 | `as_of` 语义各层不同（数据时点 vs 构建时间），且 `T`/空格、带/不带时区混用 | 顺序校验改用 `built_at` 并统一解析时间 |
| 3 | 一次比较失败把闭环打成 500 | 带时区与不带时区 datetime 直接比较抛 `TypeError` | 统一转本地朴素时间；校验异常降级为 `check_error` |
| 4 | 调查层没有 `version/as_of/trace_id` | 三件套只在代码里隐含 | 顶层补齐，溯源表不再填 null |
| 5 | 快照指纹是常量 | 取字段路径写成顶层 `breadth`，实际在 `market.breadth` 下 | 修正路径并加测试锁定 |
| 6 | `/api/decision/universe` 长期返回空 | `_LAST_ROWS` 被重新赋值，三处 `from-import` 拿到的是旧对象 | 改为 `clear()+extend()` 就地更新；排序不再丢弃缺字段行 |

---

## 九、验收标准

| # | 判据 | 命令 | 阈值 | 现状 |
|---|---|---|---|---|
| S1 | 沪深京全覆盖且在池 | `GET /api/decision/coverage` | `total_universe ≥ 5700`；`quality.exchanges.{sh,sz,bj}` 均存在且 `coverage ≥ 70` | ✅ 5782 / 98.2 / 98.7 / 74.4 |
| S2 | 分市场缺口不隐藏 | 同上 | `quotes.valid < total_universe` 时 `quality.level != good` 或 `actionable` 有附加说明 | ✅ partial |
| S3 | 契约三件套 | 各层接口 | `version`/`as_of`/`trace_id` 均非空 | ✅ |
| S4 | 时效达标 | `GET /api/decision/bus` | 盘中各资源 `fresh=true` 且 `age ≤ ttl` | ✅ |
| S5 | 链路完整 | `POST /api/brain/cycle` | `lineage.gaps=[]`、`lineage.ok=true` | ✅ |
| S6 | 依据同源 | 同上 | `lineage.consistent=true`、全部 `citations[].same_trace=true` | ✅ |
| S7 | 可回放 | `GET /api/brain/lineage/{trace_id}` | 返回 8 节点、`digest` 全非空、`facts` 可读 | ✅ |
| S8 | 降级不伪装 | 断开东财北交所源 | `sources.eastmoney_bj.status=error`、`bj.coverage` 下降、**不出现 conflict** | 单测覆盖（`test_layer_crosscheck`/`test_brain_capabilities`） |
| S9 | 回归闸门 | `pytest` / `ci_guard` / 5 个 verify 脚本 | 全绿 | ✅ 349 passed |

---

## 十、已知边界（不修饰）

1. **公告与舆情不是全量流**：当前是抽样 10 条 + 关键词匹配，覆盖不到全市场每只股票的公告。
   要真正「全标的公告」需要接交易所公告接口并按代码索引，属下一阶段。
2. **资金流向含代理**：东财频控期降级为注意力代理，字段标注 `proxy`，不参与强结论。
3. **北交所仍有 122 只无报价**：只在季度报告里存在，属上游未收录，不用估值反推价格。
4. **判断层 600s 会让情绪滞后**：实测 Δ0.6 会触发对证 warn，属真实时效差，不掩盖。
5. **单进程**：缓存与总线是进程内状态，扩容需拆采集/计算（见架构规范 §4.5）。
6. **溯源只留最近 60 条**：更早的结论只能靠 `brain_cycle_latest` 与洞察环形，不保留完整链路。
