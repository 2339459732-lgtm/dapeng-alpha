# 自动化股票交易系统 — 架构设计蓝图

> 架构师：trading-arch · 基于 `docs/20-prd/auto-trading-prd.md`（P0-1 ~ P0-10）+ 团队已拍板决策
> 基线：FastAPI `server/` + 原生前端 `static/`（无构建），uvicorn 端口 8900
> **本文档为自动交易架构的唯一权威版本**，取代早期草案 `docs/90-archive/auto-trading-arch-v0-deprecated.md`（该草案的 JSON 存储 / pandas 依赖 / `/api/auto` 前缀方案已被团队决策推翻）。

---

## 0. 已确认决策（架构前提）

| # | 决策 | 架构影响 |
|---|------|---------|
| D1 | 范围 = 信号监控+自动提醒 **和** 实盘自动交易都要；**实盘默认 OFF**，券商通道未定前只有 PaperGateway 可启用 | OrderGateway 抽象 + 仅 PaperGateway 内置；实盘总开关持久化默认 false |
| D2 | 双轨策略并行：复用仪表盘现有信号 + 新的独立策略引擎 | Track A 适配层只读复用 `strategies.compute_strategy`；Track B 新策略注册表，两轨独立开关、独立冷却命名空间 |
| D3 | 推送 = QQ 邮箱 SMTP，授权码走环境变量 `QQ_SMTP_AUTH_CODE`，**禁止硬编码** | 覆盖 PRD P0-4 中的 `QQ_EMAIL_PASSWORD` 提法；发件账号用 `QQ_EMAIL_ACCOUNT` |
| D4 | **禁止引入 akshare/pandas 等重依赖** | 覆盖 PRD P0-6"依赖补齐 pandas"；回测器用纯 Python + numpy（numpy 已在 requirements.txt）实现，`quant_core.py` 保持不动 |
| D5 | 持久化用 **SQLite**（PRD P0-7 允许"SQLite 或 JSON"，团队指定 SQLite） | `data/trading.db`，5 张表：signals / orders / positions / config / audit_log |

---

## 1. 模块地图

### 1.1 新增包结构

```
server/
  trading/                      # 自动交易核心包（新增）
    __init__.py                 # 包门面：导出全局单例 trading_facade，供 main.py 与路由装配
    models.py                   # 全部数据模型（dataclass + 枚举）：Signal/OrderRequest/OrderResult/
                                #   Position/StrategySpec/AutomationConfig/GateState/RiskState
    store.py                    # SQLite 持久化层：建表、WAL、单写者连接 + asyncio.Lock、
                                #   signals/orders/positions/config/audit_log 全部读写唯一入口
    scheduler.py                # 交易时段判定（9:30-11:30/13:00-15:00、午休静默、工作日近似）
                                #   + 扫描节拍器（盘中 60s / 15:05 收盘扫描 / 可选 9:15 盘前摘要）
    monitor.py                  # 信号监控循环（asyncio 后台任务）：拉行情→评估→冷却去重→分发
    alerts.py                   # 邮件服务：QQ SMTP(465 SSL)、限流、指数退避重试、同窗聚合、摘要邮件
    risk.py                     # 风控层：限额/持仓上限/白名单/日亏熔断/连败熔断/门禁强制校验/审计落库
    gateway.py                  # OrderGateway 抽象基类 + 网关注册表（QMT/PTrade/easytrader P1 挂载点）
    paper.py                    # PaperGateway：模拟成交（腾讯现价）、持仓/资金落 positions 表、模拟日计数
    backtest.py                 # 轻量回测器（纯 numpy）：逐日事件循环，胜率/年化/最大回撤/盈亏比/净值曲线
    strategies/                 # 策略引擎子包（Track B）
      __init__.py               # 策略注册表 register/get/list_enabled
      base.py                   # Strategy 抽象基类 + StrategyContext + Signal 输出协议
      ma_cross_atr.py           # 内置策略1：MA5/MA20 金叉买入/死叉卖出 + ATR(14) 移动止损
      n_day_breakout.py         # 内置策略2：N 日新高突破买入（默认20）/ M 日低点退出（默认10）
      panel_adapter.py          # Track A 适配层：只读复用 server.strategies.compute_strategy
                                #   的输出，映射为统一 Signal（不改 strategies.py、不改 app.js）
  routers/
    automation.py               # REST API 路由（新增，prefix=/api/automation）：只做参数校验与编排
static/
  js/automation.js              # 前端：自动化配置卡 + 信号历史/订单台账/持仓浮层 + 状态灯轮询（新增）
data/
  trading.db                    # SQLite 数据库文件（store.py 首次启动自动建库建表）
```

### 1.2 每模块职责（一句话）

| 模块 | 职责 |
|------|------|
| `models.py` | 定义跨层传递的全部数据结构与状态机枚举，无任何业务逻辑 |
| `store.py` | SQLite 唯一读写入口：原子事务、建表迁移、分页查询、配置 KV 存取 |
| `scheduler.py` | 回答"现在该不该扫/该不该推"：交易时段、午休静默、休市近似、节拍对齐 |
| `monitor.py` | 后台扫描主循环：批量行情 → 双轨评估 → 冷却去重 → 邮件分发 + 门禁下单分发 |
| `alerts.py` | 把 Signal 变成邮件：模板渲染、双限流、同窗聚合、退避重试、失败告警标记 |
| `risk.py` | 所有下单动作的唯一安检口：限额/白名单/熔断/门禁状态机，过检才放行到网关，全量审计 |
| `gateway.py` | 定义 `buy/sell/cancel/query_position/query_account` 统一接口与注册表 |
| `paper.py` | 模拟盘网关：即时价成交、持仓台账、模拟交易日推进（门禁计数数据源） |
| `backtest.py` | 给定策略+历史日K，输出回测指标（不依赖 pandas/quant_core） |
| `strategies/*` | Track B 策略实现与注册；`panel_adapter` 把现有面板信号体系统一到同一 Signal 协议 |
| `routers/automation.py` | HTTP 层：配置/信号/订单/持仓/门禁/回测/开关/测试邮件共 12 个端点 |

### 1.3 依赖方向（单向，禁止反向污染数据层）

```
static/js/automation.js
        │ HTTP
        ▼
routers/automation.py
        │
        ▼
trading: monitor ──▶ strategies(Track A panel_adapter / Track B 引擎)
   │  │                    │ Signal
   │  ├────▶ alerts（邮件）
   │  └────▶ risk ──▶ gateway ◀── paper（P1: qmt/ptrade/easytrader 注册同名接口）
   │           │          │
   └──────────▶ store ◀───┘（全量落库/审计）
        │
        ▼ 只读复用（单向，不修改、不被回调）
现有数据层: realtime.py / tencent_kline.py / cache.py / config.py / strategies.py
```

**铁律**：`server/trading/` 可以 import 数据层模块；数据层模块**绝不** import `trading/`。`strategies.py`、`realtime.py` 等现有文件零改动。

---

## 2. 运行模型

### 2.1 选择：asyncio 后台任务（不用 APScheduler）

**结论：在 FastAPI `lifespan` 中启动一个 asyncio 后台任务跑监控循环，不引入 APScheduler/Celery/Redis。**

理由：
1. **零新依赖**：团队红线是禁止重依赖；APScheduler 对"60 秒一轮 + 交易时段判定"的需求是杀鸡用牛刀。
2. **有成功先例**：现有 `server/agents/alert_agent.py` 的 `AlertAgent._run_loop`（`while running: try/except + asyncio.sleep`）已在生产稳定运行，monitor 完全复刻该模式。
3. **与 FastAPI 同生共死**：lifespan 启动/取消语义清晰，uvicorn 单进程模型下无需跨进程调度。
4. **扫描周期是连续轮询而非定时任务表**：盘中每 60s 一轮，`asyncio.sleep` 天然契合；每日 15:05 收盘扫描只需在循环内判断时间窗即可，不需要 cron 语义。

### 2.2 扫描频率（与现有缓存 TTL 对齐）

| 场景 | 频率 | 依据 |
|------|------|------|
| 盘中（9:30-11:30 / 13:00-15:00） | **60s/轮** | `CACHE_TTL_QUOTES=60`：比 60s 更密只会打缓存，无新数据；腾讯批量行情一次请求覆盖全部监控标的 |
| 收盘扫描 | **15:05 一轮全量** | 日线收定后跑日线级策略评估（PRD 开放问题 3 的默认：日内盘中 + 日线收盘各扫一次） |
| 盘前摘要（可选开关） | 9:15-9:30 一轮 | 汇总隔夜信号一封邮件，默认关闭 |
| 非交易时段/午休/休市 | 不扫描不推送 | scheduler 判定，循环空转 sleep 60s |

单轮性能预算：监控全股池 484 只（26 板块，实测 `get_all_stock_codes()`）→ 腾讯批量行情 1 次请求（5s 超时）；K 线全部走 `cache.get_or_fetch`（TTL 600s，盘中命中率 >90%）；`compute_strategy` 纯 numpy 单只 <5ms → 单轮 <30s 可完成，远小于 60s 间隔。整轮设 `asyncio.wait_for(90s)` 兜底，超时本轮作废。

### 2.3 交易时段判定（scheduler.py）

```
def session_phase(now: datetime) -> Literal["premarket","morning","lunch","afternoon","closed"]:
    # 仅按 本地时间(Asia/Shanghai) 判定：
    #   周一至周五 + 09:15-09:30 → premarket（可选摘要）
    #   09:30-11:30 → morning；11:30-13:00 → lunch（静默）
    #   13:00-15:00 → afternoon；其余 → closed
    # 法定节假日无日历库：按工作日近似，空扫描无害（行情不更新→无新信号→天然静默）
```

### 2.4 与 FastAPI 生命周期的挂载点

`server/main.py` 的 `lifespan` 中（仿 `_start_core_agents`）：

```python
# lifespan 启动段（新增两行）
trading_task = asyncio.create_task(trading_facade.start())   # monitor 循环（内部自行判定时段）
# lifespan 关闭段
trading_task.cancel()  # 优雅停止：flush 邮件摘要队列、关闭 store 连接
```

`routers/automation.py` 在路由注册区 `app.include_router(automation.router, prefix="/api/automation")`。**main.py 改动仅 3 行**，其余全部隔离在新包内。

### 2.5 崩溃自愈

monitor 循环体全量 `try/except`：异常 `logger.error(exc_info=True)` 后吞掉继续；连续失败计数 ≥5 → 该轮间隔指数退避（60s→120s→…→上限 600s），恢复后清零；`status` 端点暴露 `monitor.last_error / consecutive_errors`，前端状态灯亮红。

---

## 3. 数据流图

```
┌──────────────────────────────────────────────────────────────────────┐
│ monitor 扫描循环（盘中 60s / 15:05 收盘轮，scheduler 判定时段）        │
└──────────────────────────────────────────────────────────────────────┘
   │
   ▼ ① 行情快照
realtime.fetch_tencent_quotes(监控标的)  ── 批量 1 次请求，5s 超时
   │  quotes[code → {price, chg, volume, ...}]
   ▼ ② K线（缓存优先）
cache.get_or_fetch("raw_kline:{code}:day", fetch_tencent_kline, TTL=600s)
   │
   ▼ ③ 双轨策略评估（各自独立开关）
┌─ Track A: panel_adapter ─ compute_strategy(klines) 只读复用
│    → 评分阈值(≥80)/信号类型(动量/突破/MACD金叉死叉)/涨跌幅异动(≥3%)
└─ Track B: strategies 引擎 ─ Strategy.evaluate(code, klines, ctx)
       → MA5/20+ATR / N日突破（仅对已"启用"的标的）
   │  Signal{id, ts, code, name, type, source=panel|strategy, price, chg_pct, score, detail}
   ▼ ④ 冷却去重（30min，同 code+signal_type+source 独立冷却）
cooldown: last_fired_at 由 signals 表 MAX(ts) 派生（重启自动恢复）
   │  冷却期内 → 丢弃（不落库不打搅）；过冷却 → 继续
   ▼ ⑤ 落库 + 双路分发
store.insert_signal(pushed=false)
   │
   ├─▶ ⑤a [邮件提醒]（notify_enabled 时）
   │      alerts: 同窗聚合(同 code 多信号并一封) → 限流(单标的5/日,全局20/时)
   │      → 超限进摘要队列 → smtplib SMTP_SSL smtp.qq.com:465
   │      → 失败退避重试×3(1s/4s/16s) → 仍败: signals.push_status='failed' + 状态灯告警
   │      → 成功: signals.pushed=true
   │
   └─▶ ⑤b [自动下单]（仅 strategy 源信号 + paper/live 开关打开）
          risk.check_order: 门禁状态 → 总开关 → 白名单 → 单笔≤1万 → 单日≤3万
          → 持仓数上限 → 连败熔断/日亏熔断 → 全量写 audit_log
          → 放行 → gateway.current().buy/sell（P0 只有 PaperGateway）
          → 成交: orders 表落台账 + positions 表更新 + paper 交易日计数
          → 失败: orders.status='failed' + 连败计数+1（≥3 → 熔断停机+邮件告警）
```

---

## 4. SQLite 表结构

数据库文件：`data/trading.db`。`store.py` 启动时执行 `PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;` 并建表（`CREATE TABLE IF NOT EXISTS`）。所有写操作走单写者连接 + 模块级 `asyncio.Lock`；读操作允许并发（WAL）。sqlite3 为标准库，无新依赖（项目已有 `api_tracking.py` 使用先例）。

> ⚠️ **启动顺序约束（2026-10-16 P0 修复）**：`store.py` 初始化顺序必须为 **`_SCHEMA`（只允许基列）→ `_migrate`（对老库 `ALTER TABLE ADD COLUMN` 补迁移列：`is_drill` / `exit_reason` / `peak_price` / 成本列等）→ `_ensure_indexes`（**最后**才建引用迁移列的索引）**。顺序倒置会让**老库首次启动**抛 `no such column: is_drill`，导致**服务反复崩溃重启**（本轮真实事故）。另：`init()` 全程持锁；启动 task 必须挂异常回调。详见 [`docs/30-data/DATA-DICTIONARY.md`](../30-data/DATA-DICTIONARY.md) §14。

### 4.1 `signals` — 信号历史

| 字段 | 类型 | 说明 |
|------|------|------|
| id | TEXT PRIMARY KEY | `sig_` + uuid4 hex(12) |
| ts | TEXT NOT NULL | 触发时间，本地 ISO 8601（`YYYY-MM-DDTHH:MM:SS`） |
| code | TEXT NOT NULL | 带市场前缀小写，如 `sh600519` |
| name | TEXT NOT NULL | 股票名称快照 |
| signal_type | TEXT NOT NULL | `score/momentum/breakout/macd_gold/macd_dead/chg_spike/ma_cross_gold/ma_cross_dead/strategy_buy/strategy_sell/atr_stop` |
| source | TEXT NOT NULL | `panel`（Track A）/ `strategy:<strategy_id>`（Track B） |
| price | REAL | 触发时价格 |
| chg_pct | REAL | 触发时涨跌幅 % |
| score | INTEGER | 评分（panel 源为 combined_score；strategy 源可空） |
| detail_json | TEXT | 信号描述/参数快照 JSON |
| pushed | INTEGER NOT NULL DEFAULT 0 | 是否已邮件推送（0/1） |
| push_status | TEXT NOT NULL DEFAULT 'pending' | `pending/sent/suppressed_cooldown/rate_limited/digest/failed/disabled` |
| created_at | TEXT NOT NULL DEFAULT (datetime('now','localtime')) | 落库时间 |

索引：`idx_signals_code_ts (code, ts DESC)`；`idx_signals_type_ts (signal_type, ts DESC)`；`idx_signals_ts (ts DESC)`（分页与冷却重建用）。

### 4.2 `orders` — 订单台账

| 字段 | 类型 | 说明 |
|------|------|------|
| id | TEXT PRIMARY KEY | `ord_` + uuid4 hex(12) |
| ts | TEXT NOT NULL | 下单时间（本地 ISO） |
| code | TEXT NOT NULL | 标的 |
| name | TEXT NOT NULL | 名称快照 |
| side | TEXT NOT NULL | `buy/sell` |
| qty | INTEGER NOT NULL | 股数（100 股整数倍，由风控按 amount/price 换算） |
| price | REAL NOT NULL | 成交/委托价格 |
| amount | REAL NOT NULL | 金额 = qty×price |
| gateway | TEXT NOT NULL | `paper`（P1 扩展 `qmt/ptrade/easytrader`） |
| status | TEXT NOT NULL | `filled/rejected/failed/cancelled` |
| fail_reason | TEXT | 失败原因（风控拒绝理由/网关异常） |
| signal_id | TEXT REFERENCES signals(id) | 触发信号溯源 |
| strategy_id | TEXT | 触发策略（panel 信号手动单可空） |
| created_at | TEXT NOT NULL DEFAULT (datetime('now','localtime')) | |

索引：`idx_orders_code_ts (code, ts DESC)`；`idx_orders_status (status)`；`idx_orders_ts (ts DESC)`。

### 4.3 `positions` — 模拟持仓

| 字段 | 类型 | 说明 |
|------|------|------|
| code | TEXT PRIMARY KEY | 标的（每标的每网关一行；P0 仅 paper） |
| name | TEXT NOT NULL | |
| qty | INTEGER NOT NULL | 持仓股数 |
| avg_cost | REAL NOT NULL | 平均成本 |
| last_price | REAL | 最近行情价（每轮扫描刷新，供浮盈展示） |
| gateway | TEXT NOT NULL DEFAULT 'paper' | |
| updated_at | TEXT NOT NULL | 最近变动时间 |

索引：主键即索引；另 `idx_positions_gateway (gateway)`。

### 4.4 `config` — 自动化配置（KV）

| 字段 | 类型 | 说明 |
|------|------|------|
| key | TEXT PRIMARY KEY | 见下方 key 清单 |
| value_json | TEXT NOT NULL | JSON 序列化值 |
| updated_at | TEXT NOT NULL | |

约定 key：
- `automation` — AutomationConfig 全量（三档开关、监控范围、触发条件、冷却分钟、静默开关、收件邮箱、邮件限流参数）
- `risk` — RiskConfig（单笔/单日限额、持仓数上限、白名单、熔断阈值、日亏熔断阈值、**live_master_switch 持久化默认 false**）
- `strategies` — 策略参数与门禁状态机（每策略：kind/params/gate_state/last_backtest/paper_days_done/paper_days_required=5）
- `risk_state` — 运行态计数器（连败数、当日累计金额、当日盈亏、熔断标志、熔断时间）
- `email_counters` — 邮件限流计数（按日/按小时窗口），重启可恢复

（选 KV 而非独立列：配置结构随迭代演进，KV+JSON 免迁移；读写频率极低，无查询需求。）

### 4.5 `audit_log` — 全量审计

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PRIMARY KEY AUTOINCREMENT | |
| ts | TEXT NOT NULL | 本地 ISO |
| actor | TEXT NOT NULL | `monitor/risk/gateway/paper/api/user` |
| action | TEXT NOT NULL | `signal_fired/order_submit/order_rejected/order_filled/order_failed/breaker_tripped/breaker_reset/live_enable_blocked/live_enabled/config_updated/backtest_run/gate_advanced/mail_sent/mail_failed` |
| payload_json | TEXT | 全量上下文（订单快照/风控判定明细/配置 diff） |
| result | TEXT | `ok/denied/error` + 简述 |
| created_at | TEXT NOT NULL DEFAULT (datetime('now','localtime')) | |

索引：`idx_audit_ts (ts DESC)`；`idx_audit_action_ts (action, ts DESC)`。

**冷却不建表**：冷却状态 = `SELECT code, signal_type, source, MAX(ts) FROM signals GROUP BY 1,2,3` 在启动时重建内存映射，天然解决重启丢失问题。

---

## 5. API 端点设计

全部挂 `/api/automation`，响应统一 `{"ok": bool, "data": ..., "message": str}`；业务拒绝（门禁拦截等）HTTP 400 + `ok:false`。路由层只做校验与编排。

### 5.1 `GET /api/automation/config` — 读取配置

响应：
```json
{
  "ok": true,
  "data": {
    "switches": {"notify": true, "paper": false, "live": false},
    "scope": {"mode": "pool", "watchlist": ["sh600519"]},
    "trigger": {"score_threshold": 80, "signal_types": ["momentum","breakout","macd_gold","macd_dead"], "chg_pct_threshold": 3.0},
    "cooldown_minutes": 30,
    "quiet": {"trading_only": true, "premarket_digest": false},
    "email": {"recipient": "user@qq.com", "per_stock_per_day": 5, "global_per_hour": 20},
    "risk": {"max_amount_per_order": 10000, "max_amount_per_day": 30000, "max_positions": 5, "whitelist": ["sh600519"], "breaker_fail_threshold": 3, "daily_loss_limit_pct": 5.0},
    "strategies": [
      {"strategy_id": "ma_cross_atr", "kind": "ma_cross_atr", "params": {"fast": 5, "slow": 20, "atr_period": 14, "atr_mult": 2.0},
       "gate_state": "backtested", "paper_days_done": 0, "paper_days_required": 5, "enabled_codes": []}
    ]
  }
}
```

### 5.2 `POST /api/automation/config` — 更新配置（部分字段合并）

请求体同 5.1 的子集。约束：
- `switches.live=true` 在此端点**一律拒绝**（实盘只能走 5.7 专用端点，带二次确认）；
- 修改某策略 `params` → 该策略 `gate_state` 自动回退 `draft`（参数变了，回测/模拟结论作废）；
- 全部配置变更写 `audit_log(action='config_updated', payload=diff)`。

响应：更新后的完整配置（同 5.1）。

### 5.3 `GET /api/automation/signals?code=&limit=100&offset=0`

```json
{"ok": true, "data": {"total": 132, "items": [
  {"id": "sig_a1b2", "ts": "2025-06-02T10:31:05", "code": "sh600519", "name": "贵州茅台",
   "signal_type": "macd_gold", "source": "panel", "price": 1688.0, "chg_pct": 1.2,
   "score": 86, "pushed": true, "push_status": "sent", "detail": {"label": "MACD金叉", "desc": "DIF上穿DEA"}}
]}}
```

### 5.4 `GET /api/automation/orders?code=&limit=100&offset=0`

```json
{"ok": true, "data": {"total": 7, "items": [
  {"id": "ord_x9y8", "ts": "2025-06-02T10:31:06", "code": "sh600519", "name": "贵州茅台",
   "side": "buy", "qty": 100, "price": 1688.0, "amount": 168800, "gateway": "paper",
   "status": "rejected", "fail_reason": "单笔金额超上限10000元", "signal_id": "sig_a1b2", "strategy_id": "ma_cross_atr"}
]}}
```

### 5.5 `GET /api/automation/positions`

```json
{"ok": true, "data": {"gateway": "paper", "cash": 1000000, "items": [
  {"code": "sz000858", "name": "五粮液", "qty": 100, "avg_cost": 128.5, "last_price": 131.2, "pnl_pct": 2.1}
], "paper_days_done": 3}}
```

### 5.6 `GET /api/automation/gate` — 门禁状态总览

```json
{"ok": true, "data": {
  "live_master_switch": false,
  "broker_adapter": null,
  "strategies": [
    {"strategy_id": "ma_cross_atr", "gate_state": "paper_validating",
     "backtest": {"passed_at": "2025-05-30T15:20:00", "win_rate": 0.57, "annual_return": 0.21, "max_drawdown": -0.12},
     "paper_days_done": 3, "paper_days_required": 5,
     "can_enable_live": false, "block_reason": "模拟验证未完成：3/5 交易日"}
  ],
  "breaker": {"tripped": false, "consecutive_failures": 0},
  "smtp": {"ok": true, "last_error": null},
  "monitor": {"running": true, "last_scan_at": "2025-06-02T13:22:00", "consecutive_errors": 0}
}}
```

### 5.7 `POST /api/automation/gate/live-switch` — 实盘总开关（二次确认）

请求：`{"on": true, "confirm_text": "确认开启实盘交易", "strategy_id": "ma_cross_atr"}`

服务端强制校验链（任一不过 → 400）：
1. 存在可用实盘网关适配器（当前 `broker_adapter=null` → **永远拒绝**，这是 D1 的硬约束）；
2. 目标策略 `gate_state == "paper_passed"`；
3. `confirm_text` 精确匹配（二次确认机制；前端输入框要求用户手打这 7 个字）；
4. 熔断未触发。

开启成功写 `audit_log(action='live_enabled')` + 告警邮件。关闭（`on:false`）无需确认、立即生效——**止损永远比开仓容易**。

### 5.8 `POST /api/automation/strategy/backtest` — 手动回测

请求：`{"strategy_id": "ma_cross_atr", "code": "sh600519", "params": {"fast":5,"slow":20,"atr_period":14,"atr_mult":2.0}, "days": 250}`

```json
{"ok": true, "data": {"win_rate": 0.57, "annual_return": 0.21, "max_drawdown": -0.12,
  "total_trades": 14, "profit_factor": 1.8, "equity_curve": [["2024-12-02",1.0],["2024-12-03",1.01]],
  "gate_state": "backtested", "passed_at": "2025-06-02T15:20:00"}}
```
回测完成 → 该策略 `gate_state: draft→backtested`，指标与时间戳存 config。

### 5.9 `POST /api/automation/strategy/enable` — 启用策略到模拟/实盘

请求：`{"strategy_id": "ma_cross_atr", "target": "paper"|"live", "codes": ["sh600519"]}`
- `target=paper`：须 `gate_state ∈ {backtested, paper_validating, paper_passed}` → 置 `paper_validating`，加入模拟监控；
- `target=live`：须 `paper_passed` 且 5.7 总开关已开 → 否则 400（**强制闸门 P0-8**）。

### 5.10 `POST /api/automation/strategy/paper/confirm` — 手动确认模拟通过

请求：`{"strategy_id": "ma_cross_atr"}` → `gate_state → paper_passed`（等满 5 个模拟交易日的替代路径，PRD P0-8 允许"用户手动确认模拟表现"）。

### 5.11 `POST /api/automation/risk/reset-breaker` — 熔断复位

用户手动点击后复位连败计数与熔断标志，写审计 + 邮件告知。

### 5.12 `POST /api/automation/notify/test` — 测试邮件

请求：`{"to": "user@qq.com"}`（缺省用配置收件人）→ 立即发一封测试邮件，返回 `{"sent": true}` 或具体 SMTP 错误。供用户验证授权码。

---

## 6. 风控框架

### 6.1 检查链（risk.check_order 顺序执行，任一不过即拒单）

| # | 关卡 | 规则 | 默认值 |
|---|------|------|--------|
| 1 | 门禁状态 | 信号源策略必须 `paper_passed`（live 单）或 `paper_validating+`（paper 单） | 强制 |
| 2 | 总开关 | paper_enabled / live_enabled（持久化，live 默认 false） | OFF |
| 3 | 白名单 | 仅 `risk.whitelist` 内标的可自动下单；白名单外标的只发提醒 | 空（= 全部只提醒，最保守起步） |
| 4 | 单笔金额上限 | `qty = floor(max_amount_per_order / price / 100) * 100`；换算后 qty=0 → 拒单 | 10,000 元 |
| 5 | 单日累计上限 | 当日已成交金额 + 本单 ≤ `max_amount_per_day`（按本地日切） | 30,000 元 |
| 6 | 持仓数上限 | 新买入时 `len(positions) < max_positions` | 5 |
| 7 | 连败熔断 | 连续下单失败 ≥3 → 熔断：停止一切自动交易 + 邮件告警 + 红横幅，须人工复位 | 3 次 |
| 8 | 日亏熔断 | 模拟账户当日浮动盈亏 ≤ `-daily_loss_limit_pct%` → 当日停止开新仓（只许平仓） | 5% |
| 9 | 冷却期 | 同标的同方向 30 分钟内不重复下单（复用信号冷却命名空间 + side 维度） | 30min |

每次判定无论通过与否都写 `audit_log`（payload 含全部 9 项判定明细），拒单同时落 `orders(status='rejected', fail_reason=...)` —— **台账里看得见每一次"想下没下成"的单**。

### 6.2 门禁状态机（每策略独立）

```
                    回测通过(用户跑回测并看到指标)
        draft  ──────────────────────────────▶  backtested
          ▲                                          │ 启用模拟(target=paper)
          │                                          ▼
          │                                    paper_validating
          │                                          │  ① 模拟满 paper_days_required(默认5)个
          │                                          │     交易日（收盘扫描自动+1）
          │                                          │  ② 或用户手动 confirm
          │                                          ▼
          │                                     paper_passed
          │                                          │ 实盘总开关二次确认 + broker 适配器存在
          │                                          ▼
          └────────  参数任何修改（回退）  ◀──  live_enabled
```

**解锁条件**：
- `draft→backtested`：回测接口实跑成功并落指标；
- `backtested→paper_validating`：用户显式 enable；
- `paper_validating→paper_passed`：`paper_days_done ≥ 5`（收盘扫描时 PaperGateway 推进计数）**或** 手动 confirm；
- `paper_passed→live_enabled`：5.7 端点四重校验全过。

**回退条件**：
- 策略 `params` 变更 → 强制回 `draft`（回测/模拟结论全部作废，重走全链）；
- 熔断触发 → `live_enabled` 策略暂停（state 保留但下单被关卡 7 拦截）；
- 用户随时可 disable 任意策略 → 回 `draft`。

### 6.3 实盘二次确认机制

三重防线：① 前端实盘开关置灰 + 风险文案 + 手打"确认开启实盘交易"7 字；② 服务端 5.7 端点强制校验链（broker 适配器存在 / paper_passed / confirm_text 精确匹配 / 无熔断）；③ `live_master_switch` 持久化默认 false，进程重启不漂移。**当前 broker_adapter=null，实盘路径物理不可达**——这是 D1 的架构级保证，而非 UI 层遮挡。

---

## 7. 策略引擎接口

### 7.1 Strategy 基类（strategies/base.py）

```python
class StrategyContext:
    quote: dict | None        # 当日实时快照（盘中轮有，收盘轮可无）
    position: Position | None # 当前模拟持仓（供止损/退出判定）
    params: dict              # 该策略实例参数
    now: datetime

class Strategy(ABC):
    id: str            # "ma_cross_atr" / "n_day_breakout"
    name: str          # 中文名
    default_params: dict

    @abstractmethod
    def evaluate(self, code: str, klines: list[dict], ctx: StrategyContext) -> Signal | None:
        """纯函数式评估：输入日K(含盘中未成型的当日bar)，输出信号或 None。
        不得自行拉数据/写库——可单测、可回测复用。"""
```

### 7.2 双轨注册与隔离

| | Track A（面板轨） | Track B（引擎轨） |
|---|---|---|
| 实现 | `panel_adapter.PanelSignalAdapter`（实现同一 `evaluate` 协议） | `MaCrossAtrStrategy` / `NDayBreakoutStrategy` |
| 信号来源 | 只读调用 `server.strategies.compute_strategy(klines)`，把 StrategyResult 的评分/信号映射为统一 Signal；**不改 strategies.py 一行代码，不改 app.js** | 基类协议自实现，指标复用 `strategies.py` 的 `compute_ma/compute_atr` 等纯函数 |
| 开关 | `switches.notify` 下随监控引擎走 | 每策略独立 enable + 标的清单 `enabled_codes` |
| 冷却命名空间 | source=`panel` | source=`strategy:<id>`（同标的同类型两轨互不干扰） |
| 下单权限 | **永不下单**，只发邮件提醒 | 过风控链后可下单（paper/live） |
| 注册方式 | 启动时固定挂载 | `strategies/__init__.py` 注册表 `register(cls)`；P1+ 新策略放子包即插即用 |

**Track A 复用说明（只读适配）**：面板信号的唯一权威计算在后端 `strategies.compute_strategy`（`routers/kline.py::_build_kline_response` 同款）。adapter 直接以缓存日K调用该函数，与面板看到的结果**同源同值**，不存在"前端算一套、监控算一套"的口径分裂。盘中调用时腾讯日K末根 bar 为当日未成型 bar，与面板盘中刷新行为一致。

### 7.3 内置双策略

- **ma_cross_atr**：MA(fast=5) 上穿 MA(slow=20) → `strategy_buy`；下穿 → `strategy_sell`；持仓中收盘价 < 最高收盘 − atr_mult(2.0)×ATR(14) → `atr_stop`（移动止损，卖出）。参数均可配。
- **n_day_breakout**：收盘价创 entry_days(20) 日新高 → `strategy_buy`；跌破 exit_days(10) 日最低 → `strategy_sell`。

### 7.4 回测器（backtest.py，纯 numpy）

逐日事件循环：`for i in range(max_warmup, len(klines)): sig = strategy.evaluate(code, klines[:i+1], ctx)` → 次日开盘价成交（T+1，贴近 A 股现实）→ 维护现金/持仓/净值曲线。输出：`win_rate / annual_return / max_drawdown / total_trades / profit_factor / equity_curve`。**策略的 evaluate 在实盘与回测中是同一份代码**，杜绝"回测一套实盘一套"。数据源：`fetch_tencent_kline(code, "day", limit=250)` + cache，不碰 quant_core。

---

## 8. 邮件提醒（alerts.py）

| 项 | 设计 |
|---|---|
| 通道 | 标准库 `smtplib.SMTP_SSL("smtp.qq.com", 465)` + `email.mime`；发送放 `asyncio.to_thread` 避免阻塞事件循环 |
| 凭证 | `QQ_EMAIL_ACCOUNT`（发件人）+ `QQ_SMTP_AUTH_CODE`（授权码）只从 `os.environ` 读（main.py `_load_dotenv` 已统一加载 .env）；**代码/配置/数据库中绝不出现授权码**；缺失时 notify 自动降级为"仅记录不发送"+ 状态灯黄色提示 |
| 模板 | 标的名称/代码、触发信号、当前价、涨跌幅、触发时间、"信号提示，供参考"措辞、面板链接 `http://localhost:8900/dashboard.html`；页脚合规声明 |
| 同窗聚合 | 同一冷却窗内同标的多信号合并一封（主题：`【信号提示】贵州茅台 触发3个信号`），正文逐条列 |
| 限流 | 单标的 ≤5 封/日；全局 ≤20 封/小时；超限进摘要队列，整点/窗口结束后合并发摘要邮件；计数器存 config 表 `email_counters`，重启不丢失 |
| 重试 | 指数退避 1s→4s→16s 共 3 次；仍失败：`signals.push_status='failed'` + `audit_log(action='mail_failed')` + 状态灯红色告警 + risk_state.smtp_alert |
| 时段 | 仅交易时段即时推送；9:15-9:30 可选盘前摘要；其余时段产生的信号落库不推送（面板可见） |

---

## 9. 前端设计

### 9.1 入口与挂载点（不写实现，只定位置）

1. **入口按钮**：`static/dashboard.html` 左侧 `#sidebar-nav` 底部工具组（现"选股器/智能选股/AI每日筛股/板块分析/热门股/龙虎榜"之后）追加：
   `<div class="sidebar-tool" id="automation-entry" onclick="toggleAutomationPanel()">⚙️ 自动化</div>`
   ——与现有工具同排同风格，零布局侵入。
2. **自动化中心浮层**：复用现有 `full-overlay-panel` 模式（参照 `#sector-analysis-panel`，dashboard.html L874），新增 `#automation-panel`，顶部 4 个 Tab：
   - **配置**（PRD P0-9 配置卡）：三档开关行 `[邮件提醒][模拟交易][实盘交易]`（实盘置灰直至 paper_passed）、网关状态灯（●绿模拟运行/●黄仅提醒/●红熔断或异常/●灰关闭）+ 一行模式说明、监控范围/触发条件/冷却下拉/收件邮箱/测试邮件按钮、策略区（下拉+参数表单+运行回测+结果摘要条+启用按钮）、熔断红色横幅（含 [重置并恢复] 按钮）；
   - **信号历史**（P0-10）：表格列 = 时间/标的/信号/价格/涨跌幅/评分/来源/推送状态，顶部按标的筛选输入框；
   - **订单流水**（P0-10）：时间/标的/方向/数量/价格/金额/网关/状态/失败原因，失败行标红 + title 悬浮原因；
   - **模拟持仓**：持仓表 + 模拟验证进度条（`paper_days_done/5`）。
3. **状态灯常态化**：配置 Tab 关闭期间，入口按钮 `.sidebar-tool` 左侧带迷你状态点（轮询 `GET /api/automation/gate`，30s/次），异常（熔断/SMTP 失败）红点呼吸闪烁，用户不进浮层也能感知。
4. **合规**：浮层底部固定一行"本系统仅为个人量化工具，信号不构成投资建议，据此操作风险自负"。
5. **资源挂载**：dashboard.html 尾部加 `<script src="js/automation.js?v=N">`（按既有 `?v=N` 约定递增）；样式追加到 `static/css/dashboard.css` 末尾，全部沿用现有 CSS 变量（`--green/--gold/--red/--muted/--card-bg` 等），暗色风格一致。
6. **注意**：只改 `static/dashboard.html`；项目根目录的 `dashboard.html` 是不被 uvicorn 服务的冗余副本（StaticFiles 挂载的是 `static/`），不要动它以免双份漂移。

### 9.2 交互约束

- app.js（5559 行）**零改动**：自动化前端全部在新文件 `automation.js`，通过 `window.appState` 只读获取当前标的等上下文；
- 信号历史/订单表格分页 `limit=100` + 滚动加载；
- 所有按钮操作后立即 `GET /api/automation/gate` 刷新状态灯，保证"灯=服务端真实状态"。

---

## 10. 实施里程碑（PRD P0 → 4 个可独立验收批次）

| 批次 | 覆盖 PRD | 涉及文件 | 验收标准 |
|------|---------|---------|---------|
| **M1 监控底座** | P0-1, P0-2, P0-3 | `trading/models.py`、`trading/store.py`、`trading/scheduler.py`、`trading/monitor.py`、`trading/strategies/{__init__,base,panel_adapter}.py`、`routers/automation.py`（config/signals 端点）、`main.py`（3行挂载） | uvicorn 重启后 monitor 随 lifespan 启动；盘中 60s 轮扫描全股池；`GET /api/automation/signals` 能查到落库信号；同 code+signal_type 30min 冷却生效；非交易时段无新信号；config GET/POST 可用且改参数写审计 |
| **M2 邮件通道** | P0-4, P0-5 | `trading/alerts.py`、`routers/automation.py`（notify/test 端点） | 测试邮件送达（授权码到位后）；同窗多信号合并一封；单标的5/日、全局20/时限流与摘要合并生效；断网重试3次后 push_status=failed 且 gate 端点 smtp.ok=false |
| **M3 策略+模拟+风控** | P0-6, P0-7, P0-8 | `trading/strategies/{ma_cross_atr,n_day_breakout}.py`、`trading/backtest.py`、`trading/gateway.py`、`trading/paper.py`、`trading/risk.py`、`routers/automation.py`（backtest/enable/gate/orders/positions/reset-breaker 端点） | 两策略对 sh600519 回测返回五项指标且置 backtested；enable paper 后模拟信号自动下单、positions 更新、订单台账可查；`paper_days_done` 每收盘+1；未 paper_passed 时 enable live 返回 400；连续3败熔断停机；白名单外标的只提醒不下单；单笔>1万被拒且台账可见 |
| **M4 前端集成** | P0-9, P0-10 | `static/js/automation.js`、`static/dashboard.html`、`static/css/dashboard.css` | 侧边栏入口+四Tab浮层；配置卡三档开关/状态灯/策略参数/回测按钮/启用置灰逻辑；熔断红横幅+复位按钮；信号/订单双表按标的筛选、失败行标红悬浮；合规页脚；js/css 版本号递增 |

依赖关系：M1 → M2、M1 → M3、M3 → M4（M2/M3 可并行）。每批交付后服务可独立验收、不出现半成品状态。

---

## 11. 风险与开放问题

| # | 风险/开放问题 | 临时默认方案（架构已兼容） |
|---|--------------|---------------------------|
| 1 | **券商通道未定**（QMT/PTrade/easytrader 技术栈差异大） | `gateway.py` 注册表只挂 PaperGateway；`broker_adapter=null` 时实盘开关物理不可达；P1 拿到通道信息后新增一个适配器文件注册同名接口，上层零改动 |
| 2 | **SMTP 授权码待提供**（当前 .env 无 QQ_ 变量） | alerts 启动检测 `QQ_EMAIL_ACCOUNT/QQ_SMTP_AUTH_CODE` 缺失 → notify 降级"仅落库不发送" + 状态灯黄 + gate 端点 `smtp.ok=false`；测试邮件端点供用户到位后即时验证 |
| 3 | **回测数据源** | 默认复用现有 `tencent_kline.fetch_tencent_kline` 日K（limit=250）+ cache；不接本地历史库、不装 pandas/tushare |
| 4 | **交易日历缺失** | 周一至周五近似；法定节假日会空扫（行情不更新→无新信号），无害；P1 可接交易所日历，scheduler 单点替换 |
| 5 | **PRD P0-6 要求"补齐 pandas 复用 quant_core"与 D4 冲突** | 按 D4 执行：自研纯 numpy 回测器（约 200 行）；quant_core 保持懒加载原状。已在 §0 决策表登记，需 PM 知悉 |
| 6 | **PRD P0-4 环境变量名 `QQ_EMAIL_PASSWORD` 与 D3 `QQ_SMTP_AUTH_CODE` 冲突** | 按 D3 执行：`QQ_EMAIL_ACCOUNT`（账号）+ `QQ_SMTP_AUTH_CODE`（授权码） |
| 7 | **白名单初始清单为空** | 最保守默认：空白名单 = 全部标的只提醒不下单；用户在配置卡显式添加后才进入可交易集合 |
| 8 | **盘中信号基于未成型日K bar** | 与面板现有行为一致（同源同值）；收盘 15:05 轮用定型日K复核，可能产生"盘中触发、收盘不成立"的信号，属预期行为，邮件文案含"信号提示，供参考" |
| 9 | **早期草案 `docs/90-archive/auto-trading-arch-v0-deprecated.md` 与本文档并存** | 本文档为唯一权威；旧草案已归档至 `docs/90-archive/`，避免工程师拿错蓝图 |
| 10 | **单进程单用户假设** | 现架构为个人工具单机运行（uvicorn 单进程 + SQLite WAL 单写者），不解决多实例并发写；若未来多开需改文件锁/外置 DB |

---

## 12. 工程师共享约定（Shared Knowledge）

- **响应格式**：所有新端点 `{"ok": bool, "data": ..., "message": str}`；业务拒绝 HTTP 400 + ok:false。
- **股票代码**：一律带市场前缀小写（`sh600519`），与 config.py/腾讯接口一致。
- **缓存**：K线/行情读取一律 `cache.get_or_fetch`，key 沿用 `raw_kline:{code}:day`，TTL 常量在 `server/config.py`，不私自定义。
- **环境变量**：只读 `os.environ`（main.py `_load_dotenv` 统一加载）；**授权码不入库、不入配置 JSON、不入日志**。
- **日志**：`logger = logging.getLogger(__name__)`；后台循环异常 `logger.error(..., exc_info=True)` 后吞掉，循环不退出。
- **持久化**：一律经 `store.py`，禁止各模块自行 `sqlite3.connect` 或 open 写 data/ 文件。
- **时间**：落库用本地时间 ISO 8601 字符串，与面板同源展示。
- **合规措辞**：邮件与 UI 一律"信号提示/供参考"，禁止"建议买入/卖出/稳赚/收益保证"。
- **服务重启**：改动后端需重启 `start_server.py`（端口 8900）生效；前端静态文件改 `?v=N`。
- **零新增第三方依赖**：sqlite3/smtplib/asyncio 全标准库；指标计算用已有 numpy；requirements.txt **不需要任何改动**。
