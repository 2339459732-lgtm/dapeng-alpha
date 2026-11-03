# 决策台 → 主脑中心：三个工作区合一（decision-hub.v1）

> 落地日期 2026-11-02 ｜ 涉及 `static/dashboard.html`、`static/js/market-dashboard.js`、
> `static/js/intelligent-analysis.js`、`static/js/decision-desk.js`、`static/css/decision-desk.css`

---

## 一、为什么要合

合并前有 **三个并列工作区**，读的却是同一份数据：

| 工作区 | 主要接口 | 内容 |
|---|---|---|
| 大盘纵览 | `/api/market-dashboard`、`/api/global/*`、`/api/market/north`、`/api/decision/sentiment*` | 指数/广度/资金/板块/涨跌榜/情绪/三锚点 |
| 动态市场调查 | `/api/intelligent/market/survey`、`/api/decision/scan`、`/api/global/assets` | 调查结论/轮动/异动/散点/沪深港通 |
| 决策台 | `/api/decision/*`、`/api/market-dashboard`、`/api/global/*`、`/api/review/*` | 结论/专家/覆盖/扫描/轮动/资金流/持仓/复盘/情报 |

三者的数据源高度重叠（**决策台本来就同时调用 `/api/market-dashboard`、`/api/global/assets`、
`/api/global/linkage`、`/api/market/north`**），卡片也大量重复：

- 情绪：大盘纵览「市场情绪」、决策台「市场情绪 v1」；
- 广度：大盘纵览「市场广度」、决策台「市场温度」；
- 板块：大盘纵览「板块热力」、调查「板块轮动」、决策台「板块轮动 / 板块趋势」；
- 资金：大盘纵览「资金面」、决策台「资金流向 / 资金流向·主力净额」；
- 全球：大盘纵览「外部环境 / 全球资产」、决策台「世界局势 / 隔夜外盘」。

**问题不是"面板多"，而是同一件事在三个入口各说一遍，且口径会漂**（例如同一屏出现两个情绪分）。

---

## 二、目标形态：一个中心、三层递进

```
决策台 · 主脑中心
├─ 事实层（Fact）      客观发生了什么：指数 / 广度 / 资金 / 板块 / 情绪 / 三锚点
├─ 判断层（Survey）    系统怎么判读：市场调查 / 轮动 / 异动 / 散点 / 沪深港通
└─ 决策层（Decision）  最终做什么：结论 / 专家 / 覆盖 / 扫描 / 持仓风控 / 复盘
```

三层是**同一份市场快照的三种读法**，不是三个系统：

- 事实层用 `/api/market-dashboard` + `sentiment` + `sentiment-flow`；
- 判断层用 `/api/intelligent/market/survey`（内部消费 coverage / scan / 情绪）；
- 决策层用 `/api/decision/*`，其 `trace_id` 与事实层同源。

切换层不重新登录、不换页面、不丢当前板块上下文；hash 同步为 `#fact / #survey / #decision`。

---

## 三、模块划分（合并后）

| 位置 | 职责 | 关键约束 |
|---|---|---|
| `dashboard.html` · `#decision-dashboard-view` | 唯一市场中心容器：`dd-hub-bar` + 三个 `dd-hub-seg` | 三层 DOM 都在容器内，靠 `.is-active` 互斥 |
| `market-dashboard.js` · `switchHubSegment()` | 分层切换的唯一实现：切面板、切按钮、按需加载、写 hash | 事实层才拉大盘与情绪；判断层才初始化调查 |
| `market-dashboard.js` · `switchWorkspace()` | 工作区切换（策略选股 / Qlib / 决策台） | `'market'`、`'survey'` 变成别名 → 决策台 + 对应层 |
| `intelligent-analysis.js` · `initSurveyLayer()` | 判断层的初始化钩子（主脑模块 / 散点 / 沪深港通 / 倒计时） | **不再定义 `switchWorkspace`**（此前与 market-dashboard.js 重复导出，后者被覆盖） |
| `decision-desk.js` · 卡片动作 | `open-market` / `open-survey` → 切层，而非跳工作区 | 链接文案改为「事实层 / 判断层」 |

### 修掉的一个真实冲突

两个脚本都定义了 `window.switchWorkspace`，`intelligent-analysis.js` 后加载并覆盖前者的实现——
意味着此前所有"工作区切换"实际跑的是它的版本。合并时必须先消除这个重复，否则分层逻辑会被悄悄替换。
现在只保留 `market-dashboard.js` 一份，调查侧只暴露 `initSurveyLayer()` / `leaveSurveyLayer()`。

---

## 四、去重清单（本轮）

| 重复项 | 处置 |
|---|---|
| 大盘纵览 / 动态市场调查 两个 tab | 删除，改为决策台内三层 |
| 事实层「大盘纵览」大标题 | 删除（主脑中心的分层标签已表明位置），只保留口径与刷新 |
| 决策层「市场情绪 v1 · 六维结构」 | 精简为「情绪摘要 · 决策口径」（4 项指标 + 风险旗标 + 跳事实层链接），六维明细只保留在事实层 |
| 决策层卡片「大盘纵览 → / 完整调查 →」 | 改为「事实层 → / 判断层 →」，点击在当前中心内切层 |
| 同屏两个情绪分（结论条 83 / 卡片 80） | 结论条改用统一口径，并在情绪返回后重刷 |

---

## 五、数据连接（一次进入决策台）

```
#decision
 ├─ 决策层：/api/decision/{snapshot,sentiment,experts,scan,coverage}、/api/review/*、/api/automation/*
 ├─ 事实层：/api/market-dashboard、/api/decision/sentiment(+flow)、/api/kline(sh000001)·均线锚
 └─ 判断层：/api/intelligent/market/survey（内部：coverage + scan + 情绪 + LLM 汇总）
```

- 三层共用同一次会话的 `trace_id` 家族；事实层与决策层的情绪分来自同一 `sentiment-flow.v1`。
- 轮询按层收敛：事实层 45s 拉大盘，决策层 60s 拉决策数据，判断层按用户开关的自动刷新倒计时；
  不在当前层不空转。

---

## 六、验证

脚本 `scripts/verify/verify_hub_merge.mjs` —— **21 PASS / 0 FAIL**（对证轮次补 1 项：
判断层面板必须是纵向排布且有实际宽度，防止「能数到 8 个 panel 但被挤成一列」的假通过）：

| 组 | 断言 | 实测 |
|---|---|---|
| A | 工作区只剩 3 个入口，无「大盘纵览 / 动态市场调查」 | 策略选股 / Qlib / 决策台 |
| A | 决策台内三层且默认决策层 | fact,survey,decision |
| B | 三层互斥，仅当前层可见 | 通过 |
| B | 事实层有情绪分且与结论条一致 | 卡片 80 / 结论条 80 |
| B | 判断层有调查面板且纵向排布 | 8 个 panel · 纵向 |
| C | 卡片内「事实层」链接切层而非跳页 | 通过 |
| D | `#market`→事实层、`#survey`→判断层 | #fact / #survey |
| D | 1600 / 390 无横向溢出 | 0 / 0 |
| — | 控制台零错误 | 通过 |

回归：`verify_market_merge` 19/19、`verify_split_round` 13/13、`verify_armillary_drag` 17/17、
`pytest` **290 passed**（该轮）、`ci_guard` 三道闸门全 PASS。

---

## 七、已知取舍

1. **判断层仍保留自己的调查结论卡**：它与决策层「今日决策结论」是不同抽象（市场诊断 vs 交易动作），
   属于"同一事实的两种读法"，不视为重复。
2. **决策层保留情绪摘要**（不删）：决策需要情绪输入，但六维明细只在事实层展开，避免两处维护。
3. **hash 采用 `#fact/#survey/#decision`**：`#market` 仍可用（旧书签兼容，自动落到事实层并改写为 `#fact`）。
4. **`market-workspace-active` 类在事实层时挂到 body**：移动端高度规则依赖它；离开事实层即移除。

---

## 八、后续演进

三层合到一处之后暴露出下一个问题：**三层各算各的，没有任何机制发现某一层算错了**
（判断层把结构分标成「情绪分」显示 100、涨停 145 vs 12 是两种口径却直接相减）。
对证引擎与集中配置见 [`LAYER-CROSSCHECK.md`](LAYER-CROSSCHECK.md)（layer-crosscheck.v1 ·
brain-config.v1）：决策层顶部常驻「三层对证」条，冲突时备忘录置信 ×0.85。
本次合并的结论（统一情绪口径、三层互斥、hash 兼容、轮询收敛）全部保留。
