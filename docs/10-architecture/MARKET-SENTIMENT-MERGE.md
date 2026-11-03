# 大盘纵览 × 市场情绪追踪：合并设计

> **后续演进（2026-11-02 晚）**：本文所述「大盘纵览」已进一步并入**决策台 · 主脑中心**的**事实层**，
> 与「动态市场调查（判断层）」「决策台（决策层）」合成单一入口。见 `DECISION-HUB.md`。
> 本文的合并结论（统一情绪口径、六维 + 追踪、三锚点、6 档分布归位）全部保留，只是位置从"独立页"变成"事实层"。

> 版本 `market-merge.v1` · 落地日期 2026-11-02
> 涉及：`static/dashboard.html`、`static/js/market-dashboard.js`、`static/css/market-overview.css`、
> `static/sentiment.html`（降级为深链）、`server/sentiment_tracker.py`（分档对齐）

---

## 一、为什么合并：重合的不是样式，是口径

原系统里两个页面各自成体系：

| 维度 | 大盘纵览（dashboard.html#market） | 市场情绪追踪（sentiment.html） |
|---|---|---|
| 数据源 | `/api/market-dashboard`、`/api/global/*`、`/api/market/north` | `/api/market-overview`、`/api/market-sentiment`、`/api/sector-ranking`、`/api/global` |
| 情绪分 | `dashboard.judgment.score`（样本池广度评分） | `/api/market-sentiment`（另一套聚合） |
| 卡片 | 情绪 / 广度 / 资金面 / 外部环境 / 指数趋势 / 板块热力 / 涨跌榜 | 全球指数 / 情绪仪表 / 上证10日 / 涨跌分布 / 北向 / 资金流 / 板块热力 / 板块排行 / 涨幅TOP20 / 跌幅TOP20 / 三锚点 |

**重复清单**（逐项核对后的结论）：

| sentiment.html 面板 | 大盘纵览是否已有 | 处置 |
|---|---|---|
| 全球指数概览 | 有（全球市场条 + 外部环境） | 删除 |
| 市场情绪仪表 | 有（但口径不同） | **合并并统一口径** |
| 上证 10 日走势 | 有（指数趋势，12/24/48 点窗口） | 删除 |
| 涨跌分布 | 有（广度 3 档 + 可点击筛选） | 合并：6 档分布归入广度卡 |
| 北向资金趋势 | 有（资金面 · 沪深港通） | 删除（旧 `/api/north-holding` 链路已废弃） |
| 资金流向概览 | 有（资金面体征） | 删除 |
| 板块热力图 | 有（可排序 + 联动筛选） | 删除 |
| 板块涨跌排行 | 有（板块热力带排序切换） | 删除 |
| 涨幅 / 跌幅 TOP20 | 有（涨跌榜 + 排序） | 删除 |
| 三锚点信号塔 | 无 | **迁入**（量能 / 均线 / 情绪） |

更严重的问题不是重复，而是**同一个「情绪分」有三种说法**：

1. `dashboard.judgment.score`（大盘页仪表）
2. `/api/market-sentiment`（情绪页仪表）
3. `/api/decision/sentiment`（决策台 · sentiment.v1）

三者算法不同、时点不同。合并的第一目标是**只留一个口径**。

---

## 二、合并后的信息架构：事实 → 状态 → 结论

```
① 结论条（一句话：今天该进攻还是防守）
② 事实带   情绪体征｜市场广度｜资金面        ← 客观发生了什么
③ 状态带   三锚点信号（量能/均线/情绪）      ← 系统怎么判读
④ 事实带   指数与趋势 / 全球市场 / 板块热力 / 涨跌榜
⑤ 账户与风险
```

原来「情绪」只是事实带里的一张 gauge 卡；合并后它承担**状态层**：

- **分数** = sentiment.v1（六维加权，缺失维重归一，覆盖 < 70% 不出总分）
- **档位** = 与 `decision_snapshot._status_of` 完全一致的 20/40/60/80 分档
- **走向** = sentiment-flow.v1（斜率 / 分位 / 持续性 / 拐点）
- **归因** = 六维对偏离 50 的贡献度

这样「80 分」不再是一个孤立数字，而是：**档位（过热）→ 结论（追高性价比下降）→ 依据（宽度+9.8 / 动量+8.8 / 量能+6.8）→ 风险（连续 1 次下行）**。

---

## 三、模块与数据连接

### 3.1 前端职责

| 文件 | 职责 | 不再做 |
|---|---|---|
| `dashboard.html#market` | 唯一市场页：事实 + 状态 + 结论 | —— |
| `market-dashboard.js` | 渲染大盘事实；新增 `loadSentimentState()` 拉统一情绪与追踪 | 不再用 `judgment.score` 当情绪分（仅兜底） |
| `market-overview.css` | 六维 / 追踪 / 三锚点样式 | —— |
| `sentiment.html` | **兼容入口**：3 秒跳转 `dashboard.html?<原参数>#market` | 不再渲染任何面板 |
| `sentiment.js` | 不再被任何页面加载（保留文件以便回溯） | —— |

### 3.2 数据连接（一次刷新）

```
dashboard.html#market
 ├─ GET /api/market-dashboard        事实：指数 / 广度 / 成交 / 板块 / 涨跌榜
 ├─ GET /api/decision/sentiment      状态：六维 + 覆盖 + 风险旗标（dims 来源）
 ├─ GET /api/decision/sentiment/flow 状态：level/band/regime/斜率/分位/拐点/驱动
 └─ GET /api/kline?code=sh000001&period=day&limit=30   均线锚（真实日线）
```

**关键约定：分数与档位取自同一个响应（flow）。**
原因：`/sentiment` 与 `/flow` 是两次请求，跨秒取数会出现「80 分显示偏热、追踪说过热」。
现在 gauge 分数 = `flow.level`，档位 = `flow.band.label`，走向 = `flow.regime.label`；
`/sentiment` 只提供六维明细与覆盖（不参与分数显示），接口不可用时整块回退到 `judgment` 并标注「回退」。

### 3.3 服务端配合

`sentiment_tracker._BANDS` 的阈值与措辞原先为 30/45/58/72（冰点·偏冷·中性·偏暖·过热），
与 `decision_snapshot._status_of` 的 20/40/60/80（极冷·偏冷·中性·偏热·过热）不一致——
同一个 74 分在一个面板叫「偏暖」、另一个叫「偏热」。
现已对齐为 20/40/60/80 + 同一套措辞，并在 `tests/test_sentiment_flow.py`
里直接对 `_status_of` 做交叉断言，防止再次漂移。

---

## 四、关键实现

1. **情绪卡升级**（`dashboard.html` + `market-overview.css`）
   - 保留 gauge，新增 `#market-sent-dims`（六维，带比例条）、`#market-sent-flow`（走向 / 4 指标 / 归因条）。
   - 六维条宽度 = 维度值百分比（弹性 18–64px），不再是一段固定 14px 的"破折号"。
2. **三锚点**（`#market-anchors`）
   - 量能锚：两市成交额分档（≥9000 亿充足 / ≥6000 一般 / 其余萎缩）；
   - 均线锚：上证收盘 vs MA5（真实日线，`/api/kline` 30 根）；
   - 情绪锚：统一情绪分 ≥58 偏多 / ≤42 偏空 / 其余平衡。
   - 每项输出 `data-state = pass|warn|fail`，状态色与系统语义一致。
3. **涨跌分布归位**：6 档分布从情绪卡移入广度卡（它才是"分布"），情绪卡只保留状态信息，卡片高度 443 → 350px。
4. **旧入口收敛**：`sentiment.html` 改为跳转页并附「原面板去向」表；全站 9 处入口
   （页头 / 侧栏 / 量化页 / 管理后台 / 接口页 / 首页 / 图谱 / 文档站 / 决策台动作）统一指向 `dashboard.html#market`。
5. **深链与上下文**：`#market` 触发 `switchWorkspace('market')`；`?sector=` 由个股看板带入，
   落到大盘纵览后自动筛选涨跌榜，不再"跳过来还要手点一次"。

---

## 五、验证

脚本：`scripts/verify/verify_market_merge.mjs`（Playwright，**17/17 PASS**）

| 组 | 断言 | 实测 |
|---|---|---|
| A 口径 | 页面情绪分 = `/api/decision/sentiment` | 80 / 80 |
| A | 档位来自统一口径 | 过热 |
| A | 溯源标注 `sentiment.v1` | 通过 |
| B 六维 | 6 个芯片 | 宽度83 动量94 涨跌停78 量能95 全球52 事件50 |
| B 追踪 | 4 个指标 | 近端 -0.6 / 斜率 -0.11 / 分位 60% / 持续性 1 次 |
| B 归因 | ≥1 条 | 宽度+9.8 动量+8.8 量能+6.8 |
| C 锚点 | 3 项且状态合法 | 萎缩 / 跌破 / 偏多 |
| D 布局 | 1600 与 390 无横向溢出 | overflow 0 / 0 |
| E 旧入口 | 自动跳转且保留参数 | `/dashboard.html?sector=半导体#market` |
| E | 控制台零错误 | 通过 |

回归：`pytest tests -q` → **290 passed**；`scripts/ci_guard.py` 三道闸门全 PASS。

---

## 六、已知取舍

1. **三锚点的均线判定依赖日线接口**：`/api/kline` 失败时该锚显示「未知」而不是猜，
   符合全系统"缺失即 None"的约定。
2. **`sentiment.js` 保留但不再加载**：文件里仍有旧页面逻辑，属于历史资产；
   若确认无人引用可整体删除（当前保留以支持回溯）。
3. **6 档分布并入广度卡后，广度卡变高**，与情绪卡高度差由 `align-items: stretch` 吸收，
   在 1100px 以下宽度会换行——已验证无溢出。
4. **情绪分与锚点分数精度不同**（80 vs 79.9）：前者四舍五入用于仪表，后者保留一位小数用于锚点说明，
   同源不矛盾；若需完全一致可统一 `toFixed(0)`。
