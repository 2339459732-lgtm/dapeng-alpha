# DAPENG Alpha · 系统工作流与模块地图

> 面向「这套系统到底怎么跑起来的」：从数据进来到决策出去、再到复盘回流，每一环对应哪个文件、哪张表、哪个接口。
> 所有路径与函数名均可在仓库内复核；接口清单见 [`../40-ops/API-REFERENCE.md`](../40-ops/API-REFERENCE.md)（由运行中服务的 `/openapi.json` 自动生成）。
> 版本基准：服务 `v4.0`（`server/main.py`），141 个 HTTP 端点。

---

## 一、一句话架构

**FastAPI 单体 + 原生前端**：后端 `server/` 负责取数、计算、决策、模拟成交、复盘与迭代；前端 `static/` 负责展示，通过 **SSE 实时推送 + 轮询兜底** 与后端通信；状态落在 **SQLite（`data/trading.db`）+ 内存缓存 + JSON 产物**。

```mermaid
flowchart LR
  subgraph 数据层
    A1[腾讯行情<br/>realtime.py] --> C[缓存 cache.py<br/>分级 TTL]
    A2[腾讯/东财/Westock K线<br/>tencent_kline·eastmoney·westock] --> C
    A3[东财 push2/AkShare<br/>stable_market_provider.py] --> C
    A4[全球资产<br/>global_assets.py] --> C
    A5[板块体系<br/>sector_map/graph/linkage] --> C
  end
  subgraph 计算层
    C --> B1[quant_core.py<br/>指标/均线/MACD/ATR]
    C --> B2[qlib_factor_engine.py<br/>因子与 IC/IR]
    C --> B3[strategies.py<br/>5 套策略]
    C --> B4[chan_engine.py<br/>缠论]
  end
  subgraph 决策层
    B3 --> D1[SignalMonitor<br/>扫描/冷却/去重]
    D1 --> D2[RiskManager<br/>9 关风控]
    D2 --> D3[decision_gate.py<br/>四维决策闸]
  end
  subgraph 执行层
    D3 --> E1[OrderGateway<br/>paper / live]
    E1 --> E2[(trading.db<br/>signals/orders/positions)]
  end
  subgraph 复盘层
    E2 --> F1[review_engine.py<br/>逐笔复盘/周报/权益]
    F1 --> F2[iterator.py<br/>策略迭代提案]
    F2 --> F3[evolution 提案<br/>人工确认后应用]
  end
  subgraph 展示层
    E2 --> G1[SSE stream.py]
    F1 --> G2[/api/review/*]
    G1 --> H[static/ 前端]
    G2 --> H
  end
```

---

## 二、六条主链路

### 链路 1 · 行情数据接入（含降级链）

| 环节 | 实现 | 事实 |
|---|---|---|
| 个股实时报价 | `server/realtime.py` → `fetch_tencent_quotes` | 腾讯 `qt.gtimg.cn`，批量一次取多只；字段含 `price/open/high/low/prev_close/chg_pct/volume/amount/pe` |
| 日/周/月/分钟 K 线 | `GET /api/kline`（`server/routers/kline.py`） | 数据源优先 `tencent_kline` → `eastmoney` → `westock`；返回 `klines + MA/MACD/策略指标` |
| 缓存 | `server/cache.py` | **分级 TTL**：分钟级 15s、日/周/月 600s；`fresh=1` 绕过缓存直取 |
| 大盘/板块 | `server/stable_market_provider.py` | 东财 push2 → AkShare → **如实降级**（不再静默回填硬编码样本）；降级带 `degraded + reason` |
| 全球市场 | `server/global_assets.py` + `server/global_linkage.py` | 行情罗列 **+ 对 A 股的传导建模**（外部因子 → 影响权重） |
| 板块体系 | `sector_map.py` / `sector_graph.py` / `sector_linkage.py` | 落盘 `data/sector_map.json`、`sector_periods.json`、`sector_returns.json` |
| 全市场调查 | `server/market_survey_engine.py` | 宏观/新闻/预警/板块轮动/北向/龙虎榜多维度，历史落 `data/market_survey_history.json` |

**红线**：任一源失败 → 下一源；全部失败 → `null + reason`，**绝不返回 0**。前端以来源徽章自证。

---

### 链路 2 · 选股与信号生成

```
stock_pool.py（股票池/热度）
   └→ strategies.py（5 套策略，各自 entry/exit 规则）
        └→ SignalMonitor._scan_round()（server/trading/monitor.py:222）
             ├─ 交易时段判定 scheduler.session_phase()
             ├─ 预取 K 线 _prefetch_klines()（批量、并发受限）
             ├─ 冷却去重 _in_cooldown()（cooldown_minutes）
             └─ 产出 Signal → _dispatch()
```

- **策略清单（运行时实测 `/api/automation/config`）**：`ma_cross_atr`（均线交叉+ATR止损）、`n_day_breakout`（N日突破）、`macd_trend`（MACD趋势）、`rsi_mean_reversion`（RSI均值回归）、`volume_breakout`（放量突破）。
- **门禁状态**：每条策略有 `gate_state`（`draft → backtested → paper_passed → live_enabled`），**未通过门禁不允许下单**（风控第 1 关）。
- **扫描节奏**：`scheduler.py` 按交易时段（集合竞价/连续竞价/午休/收盘扫描窗口）决定间隔，异常按 `backoff_sleep_seconds()` 指数退避。

---

### 链路 3 · 下单执行（9 关风控 → 四维决策闸）

```
Signal / 手动单 / 条件单
   └→ RiskManager.execute_order()（server/trading/risk.py）
        ① gate_state  策略门禁状态
        ② master_switch 纸面/实盘总开关
        ③ whitelist   标的白名单
        ④ max_amount  单笔额度上限
        ⑤ day_amount  当日累计额度
        ⑥ max_positions 最大持仓数
        ⑦ breaker     连续失败熔断
        ⑧ daily_loss  当日亏损阈值（触发后禁买）
        ⑨ order_cooldown 下单冷却
   └→ decision_gate.evaluate()（server/trading/decision_gate.py）四维打分
        sector 板块 / technical 技术 / sentiment 情绪 / timing 时机
        → 加权置信度 vs allow_threshold
   └→ OrderGateway（gateway.py 注册表：paper → PaperGateway）
   └→ store.py 落库 + audit 留痕
   └→ SSE 推送 order.new
```

**成交真实性（`server/trading/`）**：

| 模块 | 作用 |
|---|---|
| `costs.py` | 佣金/印花税/过户费/滑点模型 |
| `constraints.py` | 涨跌停、停牌、T+1、最小变动价位 |
| `paper.py` / `paper_drill.py` | 纸面成交 / 操练（drill）隔离 |
| `marks.py` | 操练标记（避免污染真实复盘） |
| `monitor.py` | 扫描 + 持仓守卫（止盈/止损/ATR） |
| `conditional.py` | 条件单触发、过期清扫 `sweep_expired()` |
| `alerts.py` | 邮件告警 |

**两类下单入口共用同一条风控链**：`POST /api/automation/orders`（手动）与信号自动单，**没有绕过风控的后门**。

---

### 链路 4 · 持仓与盘中守卫

- `monitor._position_guard()`（`monitor.py:312`）：逐仓判断止盈线、止损线、ATR 止损、时间止损。
- 条件单：`conditional.poll()` 每轮先 `sweep_expired()`（超过 `conditional_expire_days` 未触发 → `expired` + 留痕）。
- 盯市与权益：`current_equity()` 按最新价重估持仓，写权益快照供复盘曲线使用。

---

### 链路 5 · 复盘与策略迭代（**日终闭环**）

```
LoopScheduler.run_daily_routine()（server/trading/loop_scheduler.py:56）
  ① record_snapshot()          权益快照
  ② reviewer.review_new_trades()   逐笔复盘（TradeReviewer）
  ③ score_decisions()          决策质量评分（含被闸拒绝的决策）
  ④ iterator.run(full=…)       策略迭代 → 提案
  ⑤ flush_logs()
```

**复盘口径（`review_engine.py`）**

| 产物 | 内容 |
|---|---|
| `round_trips` | 一次买入→卖出配对成一笔完整交易，含 `mfe/mae/出场效率/归因标签` |
| `summary` | 胜率、盈亏比（PF）、期望、最大回撤、夏普、年化波动、成本合计、可回溯率 |
| `weekly` | 周维度盈亏/胜率/权益变化 + 叙述文本 |
| `equity` | 权益曲线（与基准沪深300 归一化对比） |
| `trade-reviews` | 单笔回放明细（供前端表格） |

**迭代（`iterator.py`）**：基于复盘指标生成参数/标签候选 → 写入提案 → **人工确认后 apply**（`POST /api/evolution/proposals/{proposal_id}/apply`，另有 `/reject`），不自动改生产参数。

---

### 链路 6 · 实时推送与前端消费

| 主题 | 端点 | 事件 | 节拍 |
|---|---|---|---|
| 市场 | `GET /api/stream/market` | `market.snapshot` | 盘中轮询上游后广播 |
| 信号/订单 | `GET /api/stream/signals` | `signals.snapshot` / `signal.new` / `order.new` / `signals.unavailable` | 本地 SQLite，2s 级 |
| 个股报价 | `GET /api/stream/quotes?codes=sh600519,sz000858` | `quotes.tick` | **3s**；休市降频 30s 并标 `market_closed:true` |

- 合并订阅：`GET /api/stream?topics=quotes&codes=...`
- **共享取数**：多个客户端订阅同一批标的只打一次上游（`quote_polls == quote_upstream_calls`），连接数不放大压力。
- 前端客户端：`static/js/dashboard-stream.js`（`EVENT_NAMES` 白名单必须包含事件名，否则事件永远收不到）。
- **K 线实时对接**：`static/js/chart.js` 的 `_klineLive` 模块——`bar_ts` 与最后一根相同 → 更新；不同 → 追加；页面隐藏即暂停；SSE 不可用自动切 `GET /api/kline/quote` 5s 轮询；徽章显示 `实时/轮询/收盘/降级`。

---

## 三、存储与落盘

| 位置 | 内容 |
|---|---|
| `data/trading.db` | 表：`signals`、`orders`、`positions`、`config`(KV)、`audit_log`、`conditional_orders` |
| `data/api_calls.db` | 外部 API 调用记账 |
| `data/sector_map.json` / `sector_periods.json` / `sector_returns.json` | 板块体系离线产物 |
| `data/market_survey_history.json` | 市场调查历史 |
| `data/admin_logs.json` / `admin_notifications.json` | 后台操作与通知 |
| 内存 `server/cache.py` | 分级 TTL 缓存（K 线/行情/板块/因子） |

**配置 KV 段**：`risk`、`risk_state`、`paper_account`、`automation`、`strategies`、`decision_gate`、`conditional_expire_days`、`email_counters`。

---

## 四、接口 ↔ 链路映射（核心 20 个）

| 链路 | 端点 |
|---|---|
| 行情 | `GET /api/quotes`、`GET /api/quotes/multi`、`GET /api/kline`、`GET /api/kline/quote`、`GET /api/multi-period-resonance` |
| 大盘板块 | `GET /api/market-dashboard`、`/api/market-overview`、`/api/sector-ranking`、`/api/fund-flow`、`/api/lhb`、`/api/sector-linkage` |
| 选股 | `GET /api/screen`、`GET /api/scan`、`GET /api/strategy-lab` |
| 自动化 | `GET/POST /api/automation/config`、`GET /api/automation/gate`、`GET/POST /api/automation/orders`、`GET /api/automation/positions`、`GET /api/automation/signals`、`GET /api/automation/audit`、`POST /api/automation/run-cycle`、`GET /api/automation/decision-quality` |
| 复盘 | `GET /api/review/summary`、`/api/review/equity`、`/api/review/weekly`、`/api/review/trade-reviews` |
| 迭代 | `GET /api/evolution/status`、`/api/evolution/iterations`、`/api/evolution/proposals`、`POST /api/evolution/run` |
| 推送 | `GET /api/stream`、`/api/stream/market`、`/api/stream/signals`、`/api/stream/quotes` |

完整 141 条见 [`../40-ops/API-REFERENCE.md`](../40-ops/API-REFERENCE.md)。

---

## 五、自检命令（可直接复跑）

```bash
# 健康
curl -s --noproxy '*' http://127.0.0.1:8900/api/health

# 实时报价（单只）
curl -s --noproxy '*' "http://127.0.0.1:8900/api/kline/quote?code=sh600519"

# SSE 报价流（3s 一拍）
curl -s --noproxy '*' -N --max-time 6 \
  "http://127.0.0.1:8900/api/stream/quotes?codes=sh600519"

# 推送统计（确认共享取数：quote_polls 应等于 quote_upstream_calls）
curl -s --noproxy '*' http://127.0.0.1:8900/api/stream/stats

# K 线实时端到端（12 项断言）

  node artifacts/verify_kline_live.mjs
```

---

## 六、已知边界（如实记录）

1. **实盘通道未接入**：`gateway.broker_adapter()` 为空时，`live` 下单被第 2 关直接拒绝；当前全部为纸面交易。
2. **休市期间**：报价停更，K 线只显示最后一根真实 bar，徽章明确标注「已收盘」。
3. **部分端点的 OpenAPI 描述为空**（代码未写 docstring），文档中以 `—` 标注，不臆造说明。
4. **季节性数据源不稳定**：东财 push2 偶发 `RemoteDisconnected`，此时降级到 AkShare 或如实报不可用。
