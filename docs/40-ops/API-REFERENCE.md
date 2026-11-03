# DAPENG Alpha 接口参考

> 本文件由**运行中服务**的 `/openapi.json` 直接生成，不手工维护，因此不会与代码脱节。重新生成：
> ```bash
> curl -s --noproxy '*' http://127.0.0.1:8900/openapi.json -o artifacts/openapi.json
> python tools/gen_api_doc.py
> ```

**端点总数** 187 · **路径数** 179 · **服务版本** 4.0

## 一、通用约定

所有业务接口统一返回**三键信封**：

```json
{ "ok": true, "data": { ... }, "message": null }
```

| 约定 | 说明 |
|---|---|
| 失败形态 | `ok=false`、`data=null`、`message` 为可直接展示的中文原因 |
| 数据红线 | 取不到就是 `null` + `reason`，**绝不用 `0` 冒充真实值** |
| 降级自证 | 响应带 `degraded: true` 与 `reason` / `source`，前端显示来源徽章 |
| 真实数据优先 | 真实 → 缓存 → 多源降级 → 如实报不可用；不允许编造 |
| 本地调试 | `curl` 必加 `--noproxy '*'`，否则被系统代理劫持成 502 |
| 涨跌配色 | 涨=红、跌=绿（A 股惯例）|

## 二、端点总览

| 分组 | 端点数 |
|---|---:|
| 实时推送 (SSE) | 5 |
| 行情与 K 线 | 9 |
| 市场大盘与板块 | 17 |
| 选股与筛选 | 5 |
| 智能分析与全球市场 | 27 |
| 自动化交易 | 25 |
| 复盘与策略迭代 | 14 |
| AI 主脑 | 13 |
| 回测与量化任务 | 6 |
| 预警与监控 | 3 |
| 后台管理 | 24 |
| 支付 | 6 |
| 系统 | 1 |
| 其他 | 32 |


## 实时推送 (SSE)

| 方法 | 路径 | 说明 | 主要参数 |
|---|---|---|---|
| `GET` | `/api/stream` | 合并订阅：``?topics=market,signals,quotes&codes=sh600519,sz000858&period=day`` | topics, codes, period |
| `GET` | `/api/stream/market` | 市场级推送：指数快照 / 涨跌家数 / 板块排行前 N / 外部环境摘要 |  |
| `GET` | `/api/stream/quotes` | 个股实时报价推送（K 线最后一根动态更新用）：``?codes=sh600519&period=day`` | codes, period |
| `GET` | `/api/stream/signals` | 信号级推送：新信号 / 下单-成交-拒单 / 持仓变动 |  |
| `GET` | `/api/stream/stats` | 订阅数与上游取数计数（验收证据：证明连接数不放大上游请求） |  |


## 行情与 K 线

| 方法 | 路径 | 说明 | 主要参数 |
|---|---|---|---|
| `GET` | `/api/debug-kline` | 开发调试接口：仅 DEBUG=1 时启用，生产环境不暴露堆栈 | code |
| `GET` | `/api/kline` | 获取 K 线数据 + MA/MACD/策略指标 | code, period, limit, fresh |
| `GET` | `/api/kline/indicators` | **独立技术指标端点**（12 类 + 3 项补充） | code, period, limit, names, indicator, params… |
| `GET` | `/api/kline/indicators/catalog` | 指标目录：名字、overlay、panes、参数 schema（含 type/min/max/default/unit/desc） |  |
| `GET` | `/api/kline/quote` | 个股实时快照（K 线实时对接的轮询兜底接口，与 SSE `quotes.tick` 同结构） | code, period |
| `GET` | `/api/multi-period-resonance` | 多周期共振分析 — 同时拉取 6 个周期 K 线并计算共振状态 | code |
| `GET` | `/api/quotes` | 获取板块行情 + 策略评分（支持全量或分页） | sector, fast, page, limit |
| `GET` | `/api/quotes/multi` | 批量获取指定股票的实时行情 | codes |
| `GET` | `/api/realtime` | 腾讯实时行情接口 — 毫秒级返回 | codes |


## 市场大盘与板块

| 方法 | 路径 | 说明 | 主要参数 |
|---|---|---|---|
| `GET` | `/api/fund-flow` | 个股资金流向（东财批量接口单只查询） | code |
| `GET` | `/api/fund-flow/sector` | 板块资金流向汇总（东财个股资金流批量聚合） | sector |
| `GET` | `/api/hot-stocks` | 热门股（真实活跃榜与换手率聚合 + 分页支持） | type, limit, page |
| `GET` | `/api/lhb` | 龙虎榜（真实数据源与日期解析 + 分页支持） | tab, date, page, limit |
| `GET` | `/api/market-dashboard` | 大盘纵览总接口：指数快照、涨跌家数、情绪评分与 regime、板块与资金、风险标签、机会提示 |  |
| `GET` | `/api/market-overview` | 市场涨跌分布 —— 复用 /api/market-dashboard 的多板块样本广度（同一实现，口径统一） |  |
| `GET` | `/api/market-sentiment` | 获取市场情绪评分 |  |
| `GET` | `/api/market/all` | 全市场 A 股实时排行（AkShare / 东方财富 数据源） | sort_by, limit |
| `GET` | `/api/market/north` | 沪深港通资金流向（北向沪股通/深股通 + 南向港股通） |  |
| `GET` | `/api/north-holding` | 沪深股通成份股 | market |
| `GET` | `/api/sector-clusters` | 同涨同跌簇（去市场因子相关 + 阈值 + topK 连通分量），每簇带成员/均相关/样本量/as_of |  |
| `GET` | `/api/sector-graph` | 单板块邻居：成分股 Jaccard 重叠 + 收益相关（去市场因子） | code, top |
| `GET` | `/api/sector-linkage` | 板块金融属性敏感度（板块日收益 × M2 外部因子日收益） | code, top |
| `GET` | `/api/sector-map/stock/{code}` | 反查某股票所属板块列表（读 data/sector_map.json，M3） |  |
| `GET` | `/api/sector-ranking` | 板块排行（全量宇宙 + 层级过滤 + 全量排序后分页） | page, limit, level |
| `GET` | `/api/sector-stocks` | 板块成分股（统一解析三套板块标识） | sector |
| `GET` | `/api/sectors` | 返回所有板块元数据，包含股票数量 |  |


## 选股与筛选

| 方法 | 路径 | 说明 | 主要参数 |
|---|---|---|---|
| `GET` | `/api/scan` | 智能选股 v3：腾讯行情 + DeepSeek AI 二阶段引擎 | price_min, price_max, sectors |
| `GET` | `/api/scan/status` | 智能扫描状态：按价格区间与板块筛选后的候选池概况（含过滤原因与样本量） | price_min, price_max, sectors |
| `GET` | `/api/screen` | 条件选股 — 按策略评分和涨跌幅筛选全市场股票 | min_score, min_chg, min_vol, min_turnover, max_pe, sector… |
| `GET` | `/api/search` | 搜索股票 — 支持全市场 5000+ A 股代码与名称跨板块极速模糊匹配 | keyword, limit |
| `GET` | `/api/strategy-lab/evaluate` | 对当前个股的三类规则策略做轻量级历史评估 | code, period |


## 智能分析与全球市场

| 方法 | 路径 | 说明 | 主要参数 |
|---|---|---|---|
| `GET` | `/api/agents` | 列出所有 Agent 状态 |  |
| `GET` | `/api/agents/health` | Agent 系统健康检查 |  |
| `POST` | `/api/agents/{name}/start` | 启动 Agent |  |
| `GET` | `/api/agents/{name}/status` | 查询单个 Agent 状态 |  |
| `POST` | `/api/agents/{name}/stop` | 停止 Agent |  |
| `GET` | `/api/analyze` | 实时分析总结：基于K线+行情+策略数据，生成结构化分析 | code |
| `GET` | `/api/analyze/chart-summary` | K线 + 震荡指标自动分析总结（60s 缓存） | code, period |
| `GET` | `/api/consensus` | 机构评级 + 一致预期（合并返回） | code |
| `GET` | `/api/finance` | 财务基本面快照 —— 6 张指标卡（P0-1 主体） | code, type |
| `GET` | `/api/finance/valuation` | 估值日频序列 + 窗口分位（契约见架构设计 §3.3） | code, years, indicators |
| `GET` | `/api/global` | 获取全球市场指数 |  |
| `GET` | `/api/global/assets` | 全球资产分类快照（美股/港股/亚太/欧洲/商品/外汇，真实实时，60s 缓存） |  |
| `GET` | `/api/global/linkage` | 世界金融市场对 A 股传导建模：领先滞后相关 + 开盘缺口传导 + 每日影响打分卡 | force |
| `GET` | `/api/intelligent/ak/index` | 获取主要指数实时快照 |  |
| `GET` | `/api/intelligent/ak/macro` | 获取宏观经济数据 |  |
| `GET` | `/api/intelligent/ak/news` | 获取财经要闻与市场情报 | limit |
| `GET` | `/api/intelligent/ak/spot` | 获取 AkShare A 股全市场快照 |  |
| `GET` | `/api/intelligent/market/survey` | 获取市场全景调查、板块轮动与异动监控 |  |
| `POST` | `/api/intelligent/qlib/backtest` | 组合动态因子并执行真实逐期截面回测评估 |  |
| `GET` | `/api/intelligent/qlib/dynamic` | 动态因子缠论选股：真实行情+K线+缠论结构实时排名 | limit |
| `GET` | `/api/intelligent/qlib/factors` | 获取动态因子缠论因子库（含真实公式、可用性与不可用原因） |  |
| `GET` | `/api/intelligent/qlib/meta` | 获取因子回测面板元信息（因子/基准/区间/默认参数） |  |
| `GET` | `/api/news` | 个股新闻资讯（复用已有 get_news 方法） | code, limit |
| `GET` | `/api/news/market` | 市场资讯 —— 改用可达的 AkShare 要闻链路（东财，失效自动降级新浪） | limit |
| `GET` | `/api/news/{code}` | 获取个股新闻情绪分析 | limit |
| `GET` | `/api/profile` | 个股简况（复用已有 stock_profile 方法） | code |
| `GET` | `/api/report` | 研报列表 | code, limit |


## 自动化交易

| 方法 | 路径 | 说明 | 主要参数 |
|---|---|---|---|
| `GET` | `/api/automation/audit` | 审计日志查询：决策闸判定/下单/拒单/撤单/配置变更/条件单触发等全量留痕 | action, actor, result, limit, offset |
| `GET` | `/api/automation/conditional-orders` | 条件单列表（可按 status 过滤：pending / triggered / expired / canceled） | status |
| `POST` | `/api/automation/conditional-orders` | 新建条件单（价格 >=/<= 或定时触发）；触发时仍走风控 9 关 + 决策闸 | body |
| `DELETE` | `/api/automation/conditional-orders/{conditional_id}` | 撤销未触发的条件单，并写审计留痕 |  |
| `POST` | `/api/automation/conditional-orders/{conditional_id}/check` | 按当前价即时评估一条条件单（便于验证/手动触发）；满足则走风控+决策闸下单 |  |
| `GET` | `/api/automation/config` | 读取自动化全量配置：总开关、触发条件、风控参数、策略门禁与显示名、决策闸参数、各配置段更新时间 |  |
| `POST` | `/api/automation/config` | 合并更新自动化配置（风控/触发/决策闸/条件单过期天数）；实盘开关不接受此端点，须走 /gate/live-switch 二次确认 | body |
| `GET` | `/api/automation/decision-quality` | 决策质量评分（含被闸拒绝的决策）：信号强度 vs 实际结果 | limit |
| `GET` | `/api/automation/gate` | 风控总览：总开关、各策略门禁状态、熔断与当日亏损状态、SMTP 状态、监控循环运行时状态 |  |
| `POST` | `/api/automation/gate/live-switch` | 实盘总开关（需 confirm_text 二次确认）；未接入券商通道时开启会被拒绝 | body |
| `POST` | `/api/automation/init-paper` | 开启 paper 总开关、白名单覆盖全池、为已回测/验证中策略启用默认标的、复位熔断 |  |
| `GET` | `/api/automation/market-sync` | 返回交易时段、市况状态与动态阈值建议 | index_pct, breadth |
| `POST` | `/api/automation/notify/test` | 发送测试邮件，验证 SMTP 配置与收件人是否可达 | body |
| `GET` | `/api/automation/orders` | 订单分页查询（可按标的过滤，含拒绝单与失败原因），返回 total + items | code, limit, offset |
| `POST` | `/api/automation/orders` | 手动下单：与信号单**共用**决策闸 + 风控 9 关（白名单/额度/涨跌停/停牌/T+1 一个都不绕） | body |
| `DELETE` | `/api/automation/orders/{order_id}` | 撤单：只能撤未成交单。已成交/已拒绝 → 明确拒绝（不静默成功） |  |
| `GET` | `/api/automation/positions` | 当前纸面持仓（成本/最新价/浮动盈亏）+ 账户权益 + 纸面运行天数进度 |  |
| `GET` | `/api/automation/review/metrics` | 复盘指标条 7 项：交易笔数 / 胜率 / 盈亏比 / 最大回撤 / 夏普 / 索提诺 / Omega（+ CVaR95） | days, benchmark |
| `GET` | `/api/automation/risk/health` | 把风控各道关卡、策略门禁、熔断、行情时段、近期拒单拼成一份可执行的体检报告 |  |
| `POST` | `/api/automation/risk/reset-breaker` | 复位熔断计数与当日亏损状态（人工确认后执行，留痕） |  |
| `POST` | `/api/automation/run-cycle` | 执行一轮完整闭环：策略信号→模拟成交→自动平仓→单笔复盘→迭代决策 | drill |
| `GET` | `/api/automation/signals` | 信号分页查询（可按标的过滤），返回 total + items | code, limit, offset |
| `POST` | `/api/automation/strategy/backtest` | 对指定策略跑一次历史回测，结果写入该策略门禁记录（backtested） | body |
| `POST` | `/api/automation/strategy/enable` | 启用/停用策略并设置标的；paper 目标要求门禁已达 backtested 及以上 | body |
| `POST` | `/api/automation/strategy/paper/confirm` | 人工确认纸面验证通过，把策略门禁从 paper_validating 推进到 paper_passed | body |


## 复盘与策略迭代

| 方法 | 路径 | 说明 | 主要参数 |
|---|---|---|---|
| `GET` | `/api/evolution/iterations` | 策略迭代日志（晋级/降级/调参记录），按时间倒序分页 | limit |
| `GET` | `/api/evolution/library` | 策略库：已验证/观察中的策略与参数沉淀 |  |
| `GET` | `/api/evolution/proposals` | 待人工确认的迭代提案列表（含提案依据与建议变更） | limit |
| `POST` | `/api/evolution/proposals/{proposal_id}/apply` | 人工立即应用某条迭代提案，走与自动应用相同的双轨验证后才落库 |  |
| `POST` | `/api/evolution/proposals/{proposal_id}/reject` | 人工否决某条迭代提案（留痕，不再自动应用） |  |
| `POST` | `/api/evolution/run` | 手动触发闭环日结例程（full=True 含调参提案与周报，默认开启便于验证） | full |
| `GET` | `/api/evolution/status` | 闭环总览：日结循环状态、单笔复盘/迭代/策略库/待处理提案数量，以及闭环口径说明 |  |
| `GET` | `/api/evolution/trade-reviews` | 单笔交易复盘记录（MFE/MAE/出场效率/归因标签），按时间倒序分页 | limit |
| `GET` | `/api/review/equity` | 净值曲线 vs 沪深300（窗口首日归一化为 1.0） | days |
| `GET` | `/api/review/round-trips` | 指定标的的闭合交易（FIFO 净口径）——每笔含 K 线买卖点标注字段 | code, include_drill, limit |
| `GET` | `/api/review/summary` | 北极星指标总览：超额收益 / 回撤 / 胜率盈亏比 / 可回溯率 + 逐项达标判定 | days, include_drill |
| `GET` | `/api/review/trade-reviews` | 单笔复盘列表（KV trade_reviews，review_engine.TradeReviewer 产出） | code, limit, offset |
| `GET` | `/api/review/weekly` | 本周复盘报告：自动生成 + 落 KV（同周重复调用覆盖，供连续周数统计） | include_drill |
| `GET` | `/api/review/weekly-list` | 历史周报列表（KV weekly_reports，每周由 /review/weekly 幂等落一份），新周在前 | limit |


## AI 主脑

| 方法 | 路径 | 说明 | 主要参数 |
|---|---|---|---|
| `GET` | `/api/brain/alignment` | 三层对证：数据配置是否合法 / 能力图 / 各层同一指标是否对得上 | refresh, light |
| `GET` | `/api/brain/capabilities` | 主脑能力矩阵：计算能力 / 决策能力 / 依赖资源 / 当前可用性 / 提升目标 |  |
| `GET` | `/api/brain/config` | 主脑配置身份与生效值：跑的是哪一版、有没有回落、最近改过什么 | history |
| `POST` | `/api/brain/cycle` | 按需跑一次完整闭环：情绪追踪 → 市场调查 → 六专家 → 决策备忘录 | force |
| `GET` | `/api/brain/cycle/latest` | 最近一次闭环结果（进程内 KV 缓存，重启后为空） |  |
| `GET` | `/api/brain/insights` | 主脑洞察记录（最近 N 条，按时间顺序返回，含来源任务与生成时间） | limit |
| `GET` | `/api/brain/intel` | 最新情报评估（世界局势/地缘 → 市场影响） |  |
| `POST` | `/api/brain/intel/scan` | 立即执行一轮情报扫描评估 |  |
| `GET` | `/api/brain/lineage/recent` | 最近 N 条结论溯源链路（摘要级，不含每节点 facts 明细） | limit |
| `GET` | `/api/brain/lineage/{trace_id}` | 按 trace_id 回放一条结论的完整依据链（含各节点版本、时点、字段指纹与取值） |  |
| `POST` | `/api/brain/run/{task_id}` | 手动触发某个调度任务（market_survey / daily_loop / memory_distill） |  |
| `GET` | `/api/brain/status` | 主脑全景状态：调度任务表 / 洞察 / 经验 / LLM 配置 |  |
| `POST` | `/api/brain/survey/refresh` | 强制刷新一次动态市场调查（绕过 10 分钟缓存） |  |


## 回测与量化任务

| 方法 | 路径 | 说明 | 主要参数 |
|---|---|---|---|
| `GET` | `/api/quant/config` | 读取回测默认参数（注意：该组接口为早期直返 JSON，未使用三键信封） |  |
| `POST` | `/api/quant/config` | 更新回测默认参数并落内存（同样为直返 JSON） |  |
| `GET` | `/api/quant/logs/{task_id}` | 回测任务日志流（SSE，边跑边推，避免长轮询） |  |
| `GET` | `/api/quant/result/{task_id}` | 取回回测结果（指标 + 净值曲线 + 交易明细） |  |
| `POST` | `/api/quant/run` | 提交一次回测任务，返回 task_id；后台线程执行，进度与结果另行轮询 |  |
| `GET` | `/api/quant/status/{task_id}` | 查询回测任务进度（pending/running/done/failed + progress） |  |


## 预警与监控

| 方法 | 路径 | 说明 | 主要参数 |
|---|---|---|---|
| `GET` | `/api/alerts` | 获取最近告警 | limit |
| `POST` | `/api/alerts/watch/{code}` | 添加监控股票 |  |
| `DELETE` | `/api/alerts/watch/{code}` | 移除监控股票 |  |


## 后台管理

| 方法 | 路径 | 说明 | 主要参数 |
|---|---|---|---|
| `GET` | `/api/admin/api-health` | API 健康检查：基于真实调用日志聚合，不生成估算或随机数据 |  |
| `GET` | `/api/admin/cache` | 获取缓存列表（key, 大小估算, 创建时间, TTL剩余） |  |
| `DELETE` | `/api/admin/cache` | 清空所有缓存 |  |
| `POST` | `/api/admin/cache/purge-expired` | 手动回收过期缓存（后台任务每 30s 自动执行，此处供运维即时清理） |  |
| `DELETE` | `/api/admin/cache/{key}` | 删除单个缓存 |  |
| `GET` | `/api/admin/calls` | API调用明细分页查询 | page, size, endpoint, start, end |
| `GET` | `/api/admin/dashboard` | 后台仪表盘概览数据 |  |
| `GET` | `/api/admin/error-ranking` | API调用错误率排行 TOP 10 — 按端点聚合 | start, end |
| `GET` | `/api/admin/export` | 导出CSV文件 | type |
| `GET` | `/api/admin/logs` | 操作日志列表（分页+筛选） | page, size, action |
| `POST` | `/api/admin/logs` | 手动记录操作日志 | action, detail, operator |
| `GET` | `/api/admin/notifications` | 获取通知列表 |  |
| `DELETE` | `/api/admin/notifications` | 清空所有通知 |  |
| `POST` | `/api/admin/notifications/read` | 标记通知已读（指定ID或全部） | notification_id |
| `GET` | `/api/admin/orders` | 订单列表（分页+筛选） | page, size, status, gateway |
| `POST` | `/api/admin/orders/{order_id}/refund` | 退款订单 |  |
| `GET` | `/api/admin/revenue-trend` | 近N天收入趋势 | days |
| `GET` | `/api/admin/sector-heatmap` | 板块访问热力数据 |  |
| `GET` | `/api/admin/stats` | API调用统计汇总 — 总调用/成功率/平均响应/错误数 | start, end |
| `GET` | `/api/admin/system` | 系统运行状态 |  |
| `GET` | `/api/admin/system-health` | 系统健康总览：把本轮升级新增模块的**真实状态**汇总给后台 |  |
| `GET` | `/api/admin/users` | 用户列表（分页+筛选） | page, size, status, plan |
| `GET` | `/api/admin/users/{user_id}` | 获取单个用户详情 |  |
| `POST` | `/api/admin/users/{user_id}/toggle` | 启用/禁用用户 |  |


## 支付

| 方法 | 路径 | 说明 | 主要参数 |
|---|---|---|---|
| `POST` | `/api/payment/alipay/notify` | 支付宝异步通知（正式验签接入前拒绝处理，避免伪造支付成功） |  |
| `GET` | `/api/payment/alipay/return` | 支付宝同步跳转（用户支付完成后跳回） | out_trade_no, total_amount, sandbox |
| `POST` | `/api/payment/create` | 创建支付订单 → 返回支付参数 | body |
| `POST` | `/api/payment/simulate-pay` | 沙箱模式：模拟支付成功（仅开发测试用） | body |
| `GET` | `/api/payment/status/{order_id}` | 轮询订单支付状态 |  |
| `POST` | `/api/payment/wechat/notify` | 微信支付异步通知（正式验签接入前拒绝处理，避免伪造支付成功） |  |


## 系统

| 方法 | 路径 | 说明 | 主要参数 |
|---|---|---|---|
| `GET` | `/api/health` | 健康检查接口 |  |


## 其他

| 方法 | 路径 | 说明 | 主要参数 |
|---|---|---|---|
| `GET` | `/api/data-sources/health` | — |  |
| `GET` | `/api/decision/bus` | 全局数据总线：资源图、依赖、刷新节拍、链路年龄、最近事件、订阅者数量 |  |
| `GET` | `/api/decision/cache` | 缓存治理状态：条目数/字节占用/命中率/淘汰与拒写计数/最大条目 |  |
| `GET` | `/api/decision/coverage` | 全 A 股行情/估值/基本面 + 全球指数/行业代理 + 宏观覆盖 | refresh |
| `GET` | `/api/decision/coverage/stream` | SSE：持续推送同一份覆盖快照。前端只读缓存，不触发额外上游抓取 |  |
| `GET` | `/api/decision/experts` | 六专家独立意见和主脑聚合结果；相同 trace 在缓存窗内复用 | refresh |
| `GET` | `/api/decision/scan` | market-scanner.v1：横截面扫描 / 板块轮动 / 资金流向（实测或显式代理） | refresh |
| `GET` | `/api/decision/sentiment` | sentiment.v1 六维情绪分；缺失维度重归一，覆盖不足不输出总分 | refresh |
| `GET` | `/api/decision/sentiment/flow` | sentiment-flow.v1 情绪追踪：变化 / 分位 / 拐点 / 持续性 / 驱动归因 | refresh |
| `GET` | `/api/decision/sentiment/flow/history` | — | limit |
| `GET` | `/api/decision/sentiment/history` | — | limit |
| `GET` | `/api/decision/snapshot` | 一次决策周期的统一 MarketPacket（trace_id / as_of / coverage / quality） | refresh |
| `GET` | `/api/decision/stream` | 统一 SSE（事件驱动）：数据总线刷新即推送，无需等待轮询周期 | interval |
| `GET` | `/api/decision/system` | 决策系统总览：链路（bus）+ 缓存（cache）联合视图，供运维与页面角标使用 |  |
| `GET` | `/api/decision/universe` | 全市场分页查询；仅返回已加载快照，不在请求线程里打上游 | limit, offset, sort |
| `GET` | `/api/etf/rotation` | ETF 双动量轮动：当前/目标持仓 + 动量排名 + 参数平原 | pool, lookback, top, cost_bps, rebalance_days, refresh… |
| `GET` | `/api/ict/history` | ICT 滚动回测统计：逐日滚动扫描 → 事件去重 → 收益分布 + 随机持有基准 | code, period, limit, horizons, swing_width, minimum_rr |
| `GET` | `/api/ict/scan` | ICT 九阶段证据链 + 六道机械门扫描（日线/分钟级） | code, period, direction, limit, swing_width, minimum_rr |
| `GET` | `/api/ict/structure-stats` | ICT 结构级统计：FVG 填充率 / sweep 反转率 / OB 回踩率 + 波动率匹配基准 | code, period, limit, horizon, reversal_atr_mult, swing_width… |
| `GET` | `/api/meta/capability` | 统一能力元信息（**只读缓存，不触发上游**） | keys |
| `GET` | `/api/risk/portfolio` | 组合风险视图：β / R² / 特质风险 / 夏普 / 索提诺 / Omega / CVaR95 | benchmark, window, days, codes |
| `POST` | `/api/system/backup` | — |  |
| `GET` | `/api/system/integrity` | — |  |
| `GET` | `/api/system/status` | — |  |
| `GET` | `/api/tdx/kline` | — | code, period, limit |
| `GET` | `/api/tdx/status` | — |  |
| `GET` | `/api/themes/board/{code}` | — | limit |
| `GET` | `/api/themes/char/{char}` | — | limit |
| `GET` | `/api/themes/overview` | — |  |
| `GET` | `/api/timeshare` | 当日分时走势（1 分钟粒度） | code |
| `GET` | `/healthz` | — |  |
| `GET` | `/readyz` | — |  |

