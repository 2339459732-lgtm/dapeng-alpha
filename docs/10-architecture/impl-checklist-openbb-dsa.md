# OpenBB × DSA 接入 — 可派工实施清单（Impl Checklist）

- **用途**: 主理人逐单派活用。每单可直接粘贴进工程师开工说明。
- **上游设计**: [`openbb-dsa-integration-design.md`](./openbb-dsa-integration-design.md)（v1.2）
- **核准时间**: 2026-10-19 19:45（as-built 实测，非设计假设）
- **状态图例**: ✅ 已落盘待验证 / 🔧 待开发 / ⚠️ 已知陷阱

---

## 第 0 步：先解决三处「代码已落盘但线上无效」（**先做这个，否则后面全是白工**）

我 19:45 实测，发现 T01/T02/T03 的**代码在盘上**，但**线上完全没生效**：

| 探测 | 命令 | 实测结果 | 结论 |
|---|---|---|---|
| meta 端点 | `curl -s -o /dev/null -w "%{http_code}" 'http://127.0.0.1:8900/api/meta/capability?keys=finance'` | **404** | 运行进程早于 `main.py:248` 注册 |
| 指标端点 | `curl -s -o /dev/null -w "%{http_code}" 'http://127.0.0.1:8900/api/kline/indicators?code=sh600519&period=day&indicator=adx'` | **404** | 同上 |
| 财务端点 | `curl -s 'http://127.0.0.1:8900/api/finance?code=sh600519'` | `{"ok":false,...,"reason":"westock CLI 链路已废弃"}` | 旧链路 |

### 0.1 重启服务
```bash
# 按项目约定重启（勿手工改端口）
python start_server.py
# 或
./stop.bat && ./start.bat
```

### 0.2 ⚠️ **重启后 `/api/finance` 依然会是全 null —— 必须再打开开关**

这是我在核对时发现的**两个工作流的假设冲突**，不修则 T02 白做：

| 方 | 写的默认值 | 位置 |
|---|---|---|
| T01（寇豆码） | `"finance_v2_enabled": False` + 注释「默认全关，逐条打开验证」（fail-closed） | `server/config.py:440` |
| T02（eng-fundamental） | docstring 写「默认 **True**」，并期待用 `setdefault` 补键 | `server/fundamental.py:126-155` |

由于 T01 的 dict **已含该键且为 False**，T02 的 `setdefault` 永不生效 → `is_finance_v2_enabled()` 返回 **False** → `/api/finance` 走回退分支（已死的 westock）。

**二选一（推荐 A，不改代码、可回滚）**：
```bash
# A：环境变量覆盖（fundamental.py:139 优先读 env）
export FINANCE_V2_ENABLED=1
```
```python
# B：改 config.py:440 为 True（需重启）
"finance_v2_enabled": True,
```

### 0.3 三处开关标签与实际任务号错位（⚠️ 会让下一个人翻错开关）

`server/config.py:437-446` 的注释把开关映射到了错误的 T 号，**请以本表为准**：

| 开关名 | config.py 注释写的 | **实际对应** | 语义 |
|---|---|---|---|
| `finance_v2_enabled` | T02 | T02 ✅ 正确 | 财务新链路 |
| `valuation_percentile_enabled` | T02 | T02（分位部分） | PE/PB 分位 |
| `linkage_v2_enabled` | ~~T03~~ | **T14（L4/L5 联动）** | config 注释错：T03 是技术指标，与联动无关 |
| `risk_flags_enabled` | ~~T04~~ | **T07（6 旗标）** | config 注释错：T04 是风险指标计算 |
| `review_metrics_v2_enabled` | T04 | T04 ✅ 正确 | 复盘 7 指标 |
| `capability_badge_enabled` | T08 | T08 ✅ 正确 | 前端角标 |

> 建议由 T07/T14 的负责人顺手修正这 3 行注释（纯注释改动，无行为风险）。

### 0.4 第 0 步验收（三条必须全绿才进入 T04）
```bash
curl -s -o /dev/null -w "meta:%{http_code}\n" 'http://127.0.0.1:8900/api/meta/capability?keys=finance'
curl -s -o /dev/null -w "indicators:%{http_code}\n" 'http://127.0.0.1:8900/api/kline/indicators?code=sh600519&period=day&indicator=adx'
curl -s 'http://127.0.0.1:8900/api/finance?code=sh600519' | head -c 300
```
**期望**：`meta:200`、`indicators:200`、finance 返回 `"ok":true` 且 `data.cards` 至少 `roe_ttm` 有数值。

---

## T01 ✅ 缓存元信息 + 统一能力契约 — **已落盘，仅需核对**

| 项 | 内容 |
|---|---|
| **状态** | ✅ 已落盘（`server/providers/base.py` 11.5KB、`server/cache.py` `get_entry`:80 / `get_or_fetch_ex`:222、`server/routers/meta.py` 11.4KB、`server/config.py:419-451`） |
| **核对点** | ① `cache.get_or_fetch_ex` 三态：命中 / 未命中 / 过期但有旧值（`cache.py:246-264` 已实现 `stale_meta` 回落）；② `meta.py:117 _classify()` 四态推导；③ `meta.py:177 _resolve_cache_domain` / `:214 _resolve_quote_hub` 两条取数路径 |
| **断言** | `curl -s 'http://127.0.0.1:8900/api/meta/capability?keys=finance,positions' \| python -m json.tool` → 每个 key 含 `state/label/as_of/age/degraded/reason/source` 七字段，`state ∈ {realtime,cached,closed,stale,unavailable}` |
| **纪律断言** | 读缓存**不得**触发上游：连续调 3 次，`curl -s 'http://127.0.0.1:8900/api/stream/stats'`（或 QuoteHub stats）里 `upstream_calls` 不得增长 |
| **禁碰文件** | `server/routers/kline.py`、`server/trading/risk.py`、`static/js/chart.js`（其他单在用） |
| **已知陷阱** | `FEATURE_FLAGS` **全部默认 False 且 fail-closed**（`config.py:449-451`）。新增开关必须显式在 dict 里加键，`feature_enabled()` 对未定义键返回 False |

---

## T02 ✅ 财务基本面链路 — **已交付并实测，核对时按下表口径**

| 项 | 内容 |
|---|---|
| **状态** | ✅ 已交付（`server/providers/akshare_fundamental.py` 24KB、`server/fundamental.py` 953 行、`market_data.py:433 /finance`、`:485 /finance/valuation`） |
| **核对点** | ① `fundamental.py:126 is_finance_v2_enabled()` env 优先；② `:260 ttm_value()` 滚动 4 季；③ `:290 _pct_rank()` / `:332 _percentile_block()`；④ `:388 _unavailable_card()`；⑤ `:945 __all__` |
| **断言 1（主路径）** | `curl -s 'http://127.0.0.1:8900/api/finance?code=sh600519' \| python -m json.tool` → `ok:true`，`data.cards` **6 键齐全**，`data.availability.available_count==6` |
| **断言 2（部分缺数）** ★ | **用 `sz002456`（欧菲光），不要用 `sz000001`**：<br>`curl -s '.../api/finance?code=sz002456' \| python -c "import sys,json;d=json.load(sys.stdin);print(d['ok'],d['data']['availability'])"` → `ok:true`，`missing:["dividend_yield"]`，该卡 `reason` 非空 |
| **断言 3（全缺失，形状！）** ★ | `curl -s '.../api/finance?code=bj430047' \| python -c "import sys,json;d=json.load(sys.stdin);print(d['ok'], len(d['data']['cards']), d['data']['availability']['available_count'])"` → **`False 6 0`**（`ok:false` 但**仍有 6 张 null 卡**，不是 `data:null`） |
| **断言 4（ROE 口径）** ★★ | `curl -s '.../api/finance?code=sh600519' \| python -c "import sys,json;c=json.load(sys.stdin)['data']['cards']['roe_ttm'];print(c['value'], c['extra']['roe_reported'], c['formula'])"` → 期望 `32.3648 16.75 EPS_TTM（滚动 4 季）/ 最新每股净资产 × 100`。<br>**❌ 严禁用 `16.75` 断言 `roe_ttm`** —— 那是 H1 累计值 |
| **断言 5（分位）** | `curl -s '.../api/finance/valuation?code=sh600519&years=5' \| python -c "import sys,json;print(len(json.load(sys.stdin)['data']['series']['pe_ttm']))"` → **≥900** |
| **断言 6（缺数纪律 grep）** | `grep -rniE 'industry_avg\|行业均值\|interpolat\|插值\|fillna\(\|fill_value' server/fundamental.py server/providers/akshare_fundamental.py` → **无命中** |
| **断言 7（`_em` 零命中）** | `grep -n '_em(' server/providers/akshare_fundamental.py` → **无命中** |
| **断言 8（缓存落点）** | 缓存**不在**快照层：`grep -n 'akfund:v1:' server/providers/akshare_fundamental.py` → 见 `abstract` / `valuation:{symbol}:{period}:{key}` / `dividend` 三类键，负缓存 `NEGATIVE_TTL=120.0` |
| **禁碰文件** | `server/routers/kline.py`、`server/trading/*`、`static/js/app.js` |
| **⚠️ 陷阱（4 个）** | ① **东财 `*_em` 本机 403**，只用新浪/百度/巨潮；② 开关默认 False（第 0 步 0.2）；③ **`roe_ttm` 是 TTM，`revenue_yoy`/`profit_yoy` 是报告期原值（未做 TTM 重构）** —— 6 卡口径不统一，别假设一致；④ 样例数值随现价漂移，断言只校验**结构与非空**，不校验数值 |

---

## T02b 🔧 财务冷启动预热（**建议做，0.5 人天**）

> 设计与理由见设计文档 §6.5。核心：冷启动 10–18s，热缓存 1.5s；唯一解法是把成本移出用户关键路径。

| 项 | 内容 |
|---|---|
| **新建** | `_preload_fundamentals()`（放 `server/main.py`，与 `:66 _preload_data()` 同级） |
| **改动** | `server/main.py:136 lifespan()` 里追加一个 `asyncio.create_task(_preload_fundamentals())`；`server/config.py` 加 `FEATURE_FLAGS["fundamental_preheat_enabled"]=False` 与预算常量 |
| **五条硬约束（缺一条就会拖慢用户）** ★ | ① **排在 K 线预热之后**（`await asyncio.sleep(PRELOAD_DELAY + 60)`），否则抢 `MAX_CONCURRENT_UPSTREAM=2` 的槽把首屏挤超时；② **严格串行逐只 `await`**，禁止 `gather`（每只自身已占 2 槽）；③ 总预算 `asyncio.wait_for(..., 120s)`，超时即停，不追求跑完；④ **必须调用 `get_fundamental_snapshot(code)` 本身**，不要手拼缓存键（键在 Fetcher 层：`akfund:v1:abstract:{symbol}` 等，手写必错）；⑤ 受 `fundamental_preheat_enabled` 开关控制，默认 False |
| **预热标的** | `stock_pool` 自选池前 **5** 只（不是热门股：自选池是用户会点的） |
| **断言 1** | 重启后等 3 分钟，`curl -s '.../api/finance?code=<自选第1只>' -w '\n%{time_total}s\n'` → **<2s**（证明已预热） |
| **断言 2（不抢用户流量）** ★ | 重启后**立即**（预热未启动时）打 `/api/kline?code=...` 计时，对比预热期间同一请求耗时 → 差异 <50%（证明没抢槽） |
| **断言 3（开关）** | `fundamental_preheat_enabled=False` 时日志无「preheat fundamentals」字样 |
| **禁碰文件** | `server/providers/*`（**不要动超时值**）、`server/fundamental.py` |
| **⚠️ 陷阱（2 个，我都核过）** | ① **不要**把百度超时 8s→12s：实现已把最坏耗时钉在 8+8=16s（`akshare_fundamental.py:399`），调大只会把尾巴推到 24s；② **不要**把 `MAX_CONCURRENT_UPSTREAM` 提到 4：4 路并发会同时打穿 8s 超时，反而更慢 |

---

## T03 ✅ 12 类技术指标 — **已落盘，仅需核对**

| 项 | 内容 |
|---|---|
| **状态** | ✅ 已落盘（`server/indicators.py` 1517 行、`INDICATOR_REGISTRY`:1128、`INDICATOR_NAMES`:1330、`resolve_name`:1358、`kline.py:820` 端点） |
| **核对点** | ① 12 指标齐全；② `overlay` 标记（supertrend/ichimoku/donchian/kc/vwap → `true`）；③ `kline.py:878` 复用 `raw_kline` 不重复打上游；④ `INDICATOR_LIMIT_MIN = 1`（数据不足如实 `ok:false`，**不用 422 代替**，见 `kline.py:839`） |
| **断言 1** | `curl -s 'http://127.0.0.1:8900/api/kline/indicators?code=sh600519&period=day&names=all' \| python -c "import sys,json;d=json.load(sys.stdin);print(sorted(d['data'].keys()))"` → 12 个指标键 |
| **断言 2（overlay）** | 逐个请求 `indicator=supertrend/ichimoku/donchian/kc/vwap`，断言返回 `overlay:true`；`adx/aroon/choppiness/mfi/williams_r/di/obv` 为 `false` |
| **断言 3（不污染主缓存）** | 请求一次 `/api/kline?code=sh600519&period=day`（不带 limit，默认 120），断言 `len(klines)==120`；再请求 indicators，再查 `/api/kline` 仍为 120（证明**没写回** `raw_kline`） |
| **禁碰文件** | `server/fundamental.py`、`server/trading/*`、`static/js/*`（T10 才动前端） |
| **⚠️ 已知陷阱** | **0 填充**：序列头部不足周期时必须为 `null`（不是 0），否则副图 Y 轴与超买超卖判断全错。`indicators.py` 若已用 `None` 保持即可，**勿"优化"成 0** |

---

## T04 🔧 组合风险指标（β/R²/特质风险 + 夏普/索提诺/Omega/CVaR）

**前置**：第 0 步完成；T01 的 `SourceResult`/`cache.get_or_fetch_ex` 可用。

| 项 | 内容 |
|---|---|
| **新建** | `server/risk_metrics.py`、`server/routers/risk.py` |
| **改动** | `server/routers/automation.py:388 list_positions()` 追加 `risk` 块；同文件新增 `GET /review/metrics`；`server/main.py` 注册 `risk.router`（仿 `:248` 的写法，prefix `/api`） |
| **函数签名** | `compute_capm(port_ret, bench_ret) -> dict`（返回 `beta/alpha_annual/r2/idiosyncratic_risk/total_risk/systematic_pct/sample_days`）<br>`compute_sharpe(returns, rf=0.0, periods=252) -> Optional[float]`<br>`compute_sortino(...)` / `compute_omega(returns, threshold=0.0)` / `compute_cvar_95(returns)`<br>模块常量 `_MIN_SAMPLE = 60` |
| **端点** | `GET /api/risk/portfolio?benchmark=sh000300&window=180`<br>`GET /api/automation/review/metrics?days=90&benchmark=sh000300` |
| **断言 1（正常）** | `curl -s 'http://127.0.0.1:8900/api/risk/portfolio?window=180' \| python -c "import sys,json;d=json.load(sys.stdin)['data'];print(d['beta'],d['r2'],d['sample_days'])"` → β、r2 均为 float，`sample_days>=60` |
| **断言 2（样本不足）** | `curl -s 'http://127.0.0.1:8900/api/risk/portfolio?window=30'` → `beta/r2/idiosyncratic_risk` **全为 `null`** 且 `reason` 含「样本 30/60」 |
| **断言 3（回归：旧字段不丢）** | `curl -s 'http://127.0.0.1:8900/api/automation/positions' \| python -c "import sys,json;d=json.load(sys.stdin)['data'];need={'source','gateway','cash','initial_cash','trading_days','market_value','total_equity','items','paper_days_done','paper_days_required'};print('缺失',need-set(d))"` → **缺失 set() 为空** |
| **断言 4（口径标注）** | `/review/metrics` 返回体含 `risk_free_rate: 0.0` 与 `note` 明写「无风险利率取 0」 |
| **断言 5（无 0 冒充）** | `grep -nE 'sharpe.*or 0|beta.*or 0|sortino.*or 0' server/risk_metrics.py` → **期望无命中**（必须 `None`） |
| **禁碰文件** | `server/indicators.py`、`server/fundamental.py`、`server/trading/risk.py`、`static/js/*` |
| **⚠️ 陷阱** | ① 基准日 K 取数与组合收益序列**必须按日期对齐**（用日期交集，不能按下标），否则 β 会算错；② `_MIN_SAMPLE=60` 是硬门槛，少于 60 一律 `None`；③ **不许给 β 设默认值 1.0** —— 那是在编数据 |

---

## T05 🔧 策略参数 schema + `strategy_version`

| 项 | 内容 |
|---|---|
| **改动** | `server/trading/strategies/base.py`（加 `ParamSpec` dataclass、`params_schema: dict`、`version_fingerprint(params) -> str`）；`server/trading/strategies/*.py`（5 个策略各补 `params_schema`）；`server/trading/store.py`（`signals`/`orders` 表 `ADD COLUMN strategy_version`，幂等）；`server/routers/automation.py:162 get_config()` 返回 schema |
| **锚点** | `base.py` 现有 `Strategy` 类（`default_params` 已存在）→ 在 `default_params` 旁加 `params_schema`；`merged_params()` 之后加 `version_fingerprint()` |
| **断言 1（覆盖完整）** | `python -c "from server.trading.strategies import get_strategy, list_strategies; [print(s.id, set(s.params_schema)==set(s.default_params)) for s in [get_strategy(i) for i in list_strategies()]]"` → 全部 `True` |
| **断言 2（/config 带 schema）** | `curl -s 'http://127.0.0.1:8900/api/automation/config' \| python -c "import sys,json;d=json.load(sys.stdin)['data'];[print(s['strategy_id'], bool(s.get('params_schema'))) for s in d['strategies']]"` → 每个 `True` |
| **断言 3（schema 字段完备）** | 每个 param 的 schema 含 `type/min/max/unit/default/desc` 五键：`... print([set(v)>= {'type','min','max','unit','default','desc'} for s in d['strategies'] for v in s['params_schema'].values()])` → 全 `True` |
| **断言 4（版本号格式）** | 触发一个新信号后：`curl -s 'http://127.0.0.1:8900/api/automation/signals?limit=1'` → `strategy_version` 匹配 `^[a-z_]+@v\d+\+[0-9a-f]{8}$` |
| **断言 5（历史不回填）** | 迁移前的老信号 `strategy_version` 为 `null`，不得批量填默认值 |
| **断言 6（非法参数拒绝）** | `curl -s -X POST 'http://127.0.0.1:8900/api/automation/config' -H 'Content-Type: application/json' -d '{"strategies":[{"strategy_id":"ma_cross_atr","params":{"fast":30,"slow":10}}]}'` → `ok:false` 且 reason 指出 `fast>=slow` |
| **禁碰文件** | `server/trading/risk.py`（T06 的活）、`server/risk_metrics.py`（T04）、`static/js/automation.js`（T12） |
| **⚠️ 陷阱** | ① `ADD COLUMN` 必须幂等（`PRAGMA table_info` 先查），启动时重复执行不能报错；② `version_fingerprint` 要用**参数字典的稳定序列化**（`json.dumps(sort_keys=True)`）再 hash，否则键序变化会算出不同版本号，污染历史对比 |

---

## T06 🔧 `fail_code` 归一 + 熔断方向边界（**含统计口径修复**）

| 项 | 内容 |
|---|---|
| **新建** | `server/trading/fail_codes.py` |
| **改动** | `server/trading/risk.py`、`server/trading/constraints.py`、`server/trading/models.py`、**`server/trading/review_engine.py`**、`static/js/automation.js`（audit 筛选下拉，T12 可代做） |
| **核心改动 1（关卡 7）** | `risk.py:353-358`：`gate7_ok = not state.breaker_tripped` → `gate7_ok = not (breaker_hit and order.side == "buy")`；`checks["7_breaker"]` 增 `tripped/side/reduced_only` |
| **核心改动 2（审计）** | 熔断中放行减仓时写 `audit("risk", "breaker_reduce_allowed", {...}, result="allowed")`，**仅熔断期间写**，正常卖出不写 |
| **核心改动 3（口径修复）** | `review_engine.py:377` `score_decisions()` 循环内加：`if o.get("exit_reason") in {"fixed_stop","trailing_stop","take_profit","drill_forced","breaker_reduce"}: continue` —— 因为 decision_quality 度量**入场决策**，风控退出不算选股失误（详见设计 §2.6.5 P2） |
| **核心改动 4（模型）** | `models.py:220` 附近 `OrderResult` 加 `fail_code: Optional[str] = None`；`to_dict()`(:240) 与 `to_row()` 同步 |
| **断言 1（枚举完整）** | `python -c "from server.trading.fail_codes import FailCode, to_user_text; miss=[c.name for c in FailCode if not to_user_text(c,{})]; print('缺映射',miss)"` → `缺映射 []` |
| **断言 2（文案两段）** | 每条 `to_user_text()` 返回含非空 `title/reason/next_action` 三键 |
| **断言 3（熔断中买卖分流）** ★ | 手工触发熔断（`curl -X POST /api/automation/risk/reset-breaker` 前先制造连败，或用测试 store 直置 `breaker_tripped=True`）后：<br>· 买入 → `ok:false`，`fail_code:"BREAKER"`<br>· 卖出 → **`ok:true` 或被后续关卡拒**（但**不是** `7_breaker`） |
| **断言 4（审计分流）** ★ | `curl -s 'http://127.0.0.1:8900/api/automation/audit?action=breaker_reduce_allowed' \| python -c "import sys,json;print(json.load(sys.stdin)['data']['total'])"` → 熔断减仓后 ≥1；**且** `action=order_rejected` 里 `gate=7_breaker` 只对应买入 |
| **断言 5（正常卖出不写）** | 非熔断期做一笔卖出，断言 `breaker_reduce_allowed` 计数**不增** |
| **断言 6（关卡 2/3 仍拦双向）** | 白名单移除某持仓标的 → 买入**与卖出**均被 `3_whitelist` 拒 |
| **断言 7（口径隔离 ⑧-1）** ★ | `sqlite3 data/trading.db "SELECT COUNT(*) FROM orders WHERE exit_reason='breaker_reduce';"` → **0**（放行只写 audit，不 insert_order） |
| **断言 8（口径隔离 ⑧-2）** ★ | `curl -s 'http://127.0.0.1:8900/api/automation/decision-quality?limit=500' \| python -c "import sys,json;d=json.load(sys.stdin)['data']['items'];print('风控退出暴漏',sum(1 for x in d if x.get('verdict_reason','').startswith('breaker')))"` → **0** |
| **断言 9（回归：旧语义不变）** | 原 9 类拒绝场景的 `fail_reason` 文案语义不变（逐条人工比对 9 种） |
| **禁碰文件** | `server/trading/strategies/*`（T05）、`server/routers/kline.py`、`server/fundamental.py` |
| **⚠️ 陷阱（3 个）** | ① **`breaker_reduce_allowed` 只能写 audit，绝不 `insert_order`** —— 否则同一笔卖出现在 orders 两行，重复计数（`risk.py:219` 才是唯一落单点）；② **action 筛选下拉要注册**，否则 `/api/automation/audit?action=breaker_reduce_allowed` 在留痕页筛不到；③ **`score_decisions` 的既存缺陷必须一起修**，否则"风控止损做对了"会被记成"选股做错了"，指标朝反方向漂移 |

---

## T07 🔧 组合集中度第 10 关 + 6 项风险旗标

| 项 | 内容 |
|---|---|
| **新建** | `server/trading/portfolio_risk.py` |
| **改动** | `server/routers/automation.py:388 list_positions()` 追加 `flags/concentration/exposure/price_*`；`server/trading/risk.py` 在关卡 6 之后插 `_gate_10_concentration()` |
| **锚点** | `risk.py:418-455` 是关卡 6 与关卡 9 之间 → **第 10 关插在关卡 6 之后、关卡 7 之前**（金额与持仓上限之后、熔断之前） |
| **函数** | `build_flags(positions, quotes, cfg) -> dict`、`check_concentration(order, positions, cfg) -> tuple[bool, str|None, dict]`、`top_weight(positions)`、`industry_concentration(positions)`；常量 `_SINGLE_MAX_PCT=30.0`、`_INDUSTRY_MAX_PCT=50.0` |
| **断言 1（6 旗标常驻）** | `curl -s 'http://127.0.0.1:8900/api/automation/positions' \| python -c "import sys,json;f=json.load(sys.stdin)['data']['risk']['flags'];print(sorted(f));print({k:v['state'] for k,v in f.items()})"` → 6 键齐全，state ∈ `{ok,warn,breach,unavailable}` |
| **断言 2（缺价 → unavailable，不是 ok）** ★ | 造一只停牌/取不到价的持仓 → `price_quality.state == "unavailable"` 且 `reason` 非空 |
| **断言 3（权重自洽校验）** | `top_weight_pct` 与 `top_positions[0].weight_pct` 不一致时该旗标降级 `unavailable`（写单测） |
| **断言 4（尾部不截断）** | 造权重 0.05% 的持仓 → 仍出现在 `top_positions` 或 items 全量里，`top_weight_pct` 不被虚高 |
| **断言 5（第 10 关生效）** | 造单票 32%（上限 30%）→ 拒单，`fail_code` 为集中度类；**且平仓不受影响** |
| **断言 6（批量取价，不打爆上游）** ★ | 连续调 `/positions` 3 次，`QuoteHub.stats.upstream_calls` 每轮**至多 +1**（证明是批量 `snapshot(all_codes)`，不是循环单取） |
| **禁碰文件** | `server/trading/risk.py` 的关卡 1–9 与关卡 7/8 逻辑（T06 的活，**只在关卡 6 后插入**，不改既有判定）、`server/trading/review_engine.py` |
| **⚠️ 陷阱** | ① 旗标为 `unavailable` 时**必须带 reason**，不许静默变 `ok`；② 取价必须**批量一次**；③ 与 T04 共用 `/positions` 端点 → **必须 T04 先合并**，否则两人改同一函数冲突 |

---

## T08 🔧 能力角标组件 + 12 区块挂载

| 项 | 内容 |
|---|---|
| **新建** | `static/js/capability.js` |
| **改动** | `static/dashboard.html`（12 处角标容器 + `<script src="js/capability.js?v=1">` 插在 `api.js` 之后、`app.js` 之前，即 `:1328` 与 `:1340` 之间） |
| **API** | `Capability.preload(keys)`（**1 次批量请求**）、`Capability.attach(el, meta)`、`Capability.render(meta)`、`Capability.fromEnvelope(env)` |
| **断言 1（12 区块）** | 打开 dashboard，DevTools 里 `document.querySelectorAll('[data-capability]').length` → **12** |
| **断言 2（只 1 次请求）** ★ | Network 面板过滤 `meta/capability` → 仅 **1 条**（15s 刷新可再 1 条，但不得按区块数发） |
| **断言 3（四态视觉）** | 手动构造 4 种 meta 调 `Capability.render()`，断言输出含对应 class（`cap-realtime/cap-cached/cap-closed/cap-unavailable`） |
| **断言 4（unavailable 必带原因）** | `unavailable` 时文案含具体 reason，且可点击展开 |
| **断言 5（断网）** ★ | 断网后 12 区块全变 `unavailable`，**无一显示 0** |
| **禁碰文件** | `static/js/chart.js`、`static/js/app.js`（T10/T14 在用）、`server/*` |
| **⚠️ 陷阱** | ① **容器隐藏（Tab 不可见）时角标不渲染**，切 Tab 后要补渲染（用 `MutationObserver` 或 Tab 切换钩子），否则用户切回来看到空白；② 必须 `?v=1` 且 bump 后**跨页同步**（`grep -rn "capability.js?v=" static/*.html`） |

---

## T09 🔧 基本面 6 卡前端 + 估值分位条

| 项 | 内容 |
|---|---|
| **新建** | `static/js/fundamental.js` |
| **改动** | `static/dashboard.html`：个股详情区加 `#fundamental-block`、`#valuation-bar`；`ktab` 加一个「基本面」按钮（仿 `:489` 的 `profile` 写法）；`static/js/app.js` 的 `switchKlineTab` 加分支；**bump `app.js?v=36 → 37`** |
| **断言 1** | 点开任一股票 → 基本面区 ≤5s 出现 6 卡（PE_TTM/PB/ROE/营收同比/净利同比/股息率） |
| **断言 2** | 每卡可见「数据源 + 报告期 + 是否最新期」三要素 |
| **断言 3** | 分位条显示当前 PE 在 5 年区间的百分位（与 `/api/finance/valuation` 的 `current_pct` 一致） |
| **断言 4** | 后端 `ok:false` 时卡片显示「不可用（原因）」，**不显示 0、不显示 `--`** 冒充 |
| **断言 5** | 报告期落后 >1 季度时加「数据可能过期」标注 |
| **禁碰文件** | `static/js/chart.js`（T10）、`static/js/automation.js`（T11/T12）、`server/*` |
| **⚠️ 陷阱** | ① 涨红跌绿：**不要**用红绿表达 PE 高低（PE 高不是"涨"）；② `?v=` bump 后跨页同步；③ 基本面是**季频数据**，角标应为 `closed`（灰）**不是** `realtime` |

---

## T10 🔧 副图 12 指标前端切换

| 项 | 内容 |
|---|---|
| **改动** | `static/js/chart.js`（扩展 `window._indicatorConfig.subIndicator` 枚举 `:18`、`setSubIndicator`:1413、副图渲染 `:1070-1090`、pane 分配 `:1867`、参数弹层 `:1440`）；`static/dashboard.html:462-464` 副图按钮 3→12；**bump `chart.js?v=33 → 34`** |
| **断言 1** | 副图下拉 12 项可切，切换 ≤1s 重绘 |
| **断言 2** | 每指标显示参数标签（如 `ADX(14)`） |
| **断言 3（overlay 不打乱主图）** ★ | 切 supertrend/ichimoku/donchian/kc/vwap → 叠主图且**主图 Y 轴范围仍被 K 线主导**（不被指标拉扁） |
| **断言 4（盘中不请求后端）** ★ | 盘中观察 Network：SSE tick 到来时**无** `kline/indicators` 新请求，最后一根本地重算 |
| **断言 5（涨红跌绿）** | `grep -nE "#ff|#00e|red|green" static/js/chart.js` 新增行**不得**出现硬编码色值，必须用 `_themeUp/_themeDown` |
| **禁碰文件** | `static/js/capability.js`（T08）、`static/js/fundamental.js`（T09）、`static/js/app.js`（T09/T14） |
| **⚠️ 陷阱** | ① **`proChart`（klinecharts，`chart.js:1756`）与 `klineChart`（ECharts，`:105`）是两套引擎**，overlay 指标要在**当前生效的那套**上做，别只改一套；② 序列头部 `null` 必须对齐 K 线索引，否则副图错位；③ `chart.js:1867` 有显式 pane 高度分配，新增副图别绕过它 |

---

## T11 🔧 持仓旗标条 + 复盘 7 指标条

| 项 | 内容 |
|---|---|
| **改动** | `static/js/automation.js`（`renderRiskFlags` / `renderReviewMetrics`；**bump `?v=8 → 9`**）；`static/dashboard.html`（`#risk-flag-bar`、复盘指标条扩位） |
| **前置** | **T04 + T07 都合并后**（共用 `/positions`） |
| **断言 1** | 持仓 Tab 顶部 6 旗标常驻；复盘指标条 4 → 7 |
| **断言 2** | 样本不足显示「样本 N/60」**而非比率** |
| **断言 3** | 旗标 `unavailable` 点击展开原因 |
| **断言 4** | `exposure.available=false` 时显示「暴露不可用 + reason」 |
| **禁碰文件** | `static/js/automation.js` 的**策略表单区**（T12 同文件不同区，**必须约定行段**，建议 T12 在 T11 合并后再改，或两人改前后半段） |
| **⚠️ 陷阱** | 与 T12 改同一文件 → **排班必须错开或划清行段**，否则必冲突 |

---

## T12 🔧 策略参数表单 + `fail_code` 文案前端

| 项 | 内容 |
|---|---|
| **改动** | `static/js/automation.js`（`renderStrategyForm`；**bump `?v=9 → 10`**，与 T11 顺序需协调）；`static/js/automation-trade.js`（`renderOrderFailure`；**bump `?v=3 → 4`**）；audit 筛选下拉注册 `breaker_reduce_allowed` |
| **前置** | T05 + T06 |
| **断言 1** | 5 个策略各出现表单，字段类型/范围/单位/默认值**来自 schema**，不硬编码 |
| **断言 2** | 非法值前端拦截（`fast>=slow`），提示原因 |
| **断言 3** | 保存后回显与 schema 一致 |
| **断言 4（双段文案）** ★ | 下单失败显示「原因 + 下一步」两段，17 个 `FailCode` 全覆盖（逐个造场景或单测） |
| **断言 5** | `strategy_version` 在信号表/订单表可见 |
| **断言 6** | 留痕页 action 下拉含 `breaker_reduce_allowed` 并可筛选 |
| **禁碰文件** | `static/js/automation.js` 的**旗标/复盘区**（T11） |
| **⚠️ 陷阱** | ① 与 T11 同文件，**必须错开或划行段**；② `automation.js` 已 1813 行，**不要**大改结构，只加渲染函数 |

---

## T13 🔧 数据能力矩阵文档

| 项 | 内容 |
|---|---|
| **新建** | `docs/30-data/DATA-CAPABILITY-MATRIX.md` |
| **断言** | 覆盖 D1–D9 + A1–A8 共 17 行 × 6 数据源（腾讯/东财/AkShare新浪/AkShare百度/巨潮/Westock）；每个「不可得」格写明阻塞点与最后核验日期；与 `DATA-DICTIONARY.md` 交叉链接 |
| **⚠️ 陷阱** | 必须写明**东财 push2 本机 403**（实测），不要把「东财可查」写成理论可达 |

---

## T14 🔧 L4 钻取 + L5 `connect`（**并行轨 W3.5，与 W3 后半段同时进行**）

> 拆三小单，可由 2 人并行。**依赖**：仅 `capability.js` 的 `meta` 对象结构（T08 落 1 天后即可开工）。

### T14a 事件总线 + 现有 2 处钻取归并
| 项 | 内容 |
|---|---|
| **新建** | `static/js/linkage.js` |
| **改动** | `static/js/app.js:3888`（热力图 click）、`app.js:993` 附近 `showStockContextMenu` 的调用链 → 改为 `DrillBus.emit`；**bump `app.js?37 → 38`** |
| **断言** | 点热力图格子 → `DrillBus` 收到 `{type:'sector'}`；点右键菜单「查看K线」→ 收到 `{type:'stock'}`；`DrillBus.extract()` 无法识别时 **toast 明示**，不静默 |
| **禁碰** | `static/js/chart.js`（T10）、`server/*` |

### T14b ECharts `connect` 分组
| 项 | 内容 |
|---|---|
| **改动** | `static/js/app.js`（`sector-group`：`heatmapChart`+`sectorRankChart`+资金流柱图）、`static/js/market-dashboard.js`（`review-group`） |
| **断言 1** | `sector-group` 三图十字光标同步（`axisPointer` 联动） |
| **断言 2（隐藏容器不抛错）** ★ | 切到别的 workspace 再回来，Console **无报错**（隐藏容器宽高为 0，`connect` 前要 `resize` 守卫） |
| **断言 3（不共享 dataZoom）** | 热力图无时间轴，**不得**被纳入 dataZoom 同步（否则错位） |
| **⚠️ 陷阱** | `proChart`（klinecharts）**不参与** `connect`；只有 ECharts 实例可 `connect` |

### T14c hash 路由 + 面包屑
| 项 | 内容 |
|---|---|
| **改动** | `static/js/linkage.js`（`DrillBus.route`）、`static/js/app.js`（面包屑渲染）、`static/js/automation.js`（`#/auto?tab=` 解析） |
| **断言** | J1–J8 八条路径全通；每次跳转带「来自 XX」面包屑；URL **可粘贴复现**（新窗口打开还原同样视图） |
| **⚠️ 陷阱** | hash 变更要防循环触发（`emit` → 改 hash → `hashchange` → 再 emit），需 `_routing` 标志位 |

---

## 附：并发排班防撞车矩阵（**派活前必看**）

| 文件 | 谁在用 | 冲突处置 |
|---|---|---|
| `server/routers/automation.py` | **T04**（`/positions` risk 块 + `/review/metrics`）、**T07**（`/positions` flags）、T05（`/config` schema） | **T04 → T07 串行**（同一函数 `list_positions`）；T05 改 `/config` 不冲突，可并行 |
| `server/trading/risk.py` | **T06**（关卡 7 + 审计 + 关卡 10 插入点）、**T07**（关卡 10 实现） | **同一人**或 T06 先合并 T07 再动 |
| `server/trading/review_engine.py` | **T06** 独用 | 无冲突 |
| `static/js/automation.js` | **T11**（旗标/复盘区）、**T12**（表单/fail_code 区）、T14c（hash 解析） | **三人同文件** → 必须划行段或串行；建议 T11 → T12 → T14c |
| `static/js/app.js` | **T09**（switchKlineTab）、**T14a**（click 归并）、**T14b**（connect） | T09 先合并；T14a/T14b 同人做 |
| `static/js/chart.js` | **T10** 独用 | 无冲突 |
| `server/routers/kline.py` | **T03**（已落盘） | 后续勿动 |
| `server/fundamental.py` / `providers/*` | **T02**（已落盘） | 后续勿动 |

**建议并行波次**：
1. **第 1 天**：T04（1人）+ T08（1人）+ T14a（1人）← 三者文件不重叠
2. **T04 合并后**：T07（同 T04 的人）+ T09（1人）+ T10（1人）
3. **T05 完成后**：T06 → T12（同人）
4. **T07 合并后**：T11
5. **T08+T14a 后**：T14b、T14c
6. **最后**：T13（文档，任意人）
