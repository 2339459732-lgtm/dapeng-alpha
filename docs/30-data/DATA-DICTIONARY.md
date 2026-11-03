# DAPENG Alpha · 数据口径字典

> 版本：v1.0 ｜ 日期：2026-10-14 ｜ 适用代码基线：v5.9
> 本文档登记 **数据源清单、可用性与降级行为、字段单位与来源标注约定**。
> 所有"现状"描述均可在源码中逐条回溯；与 [`docs/00-strategy/MASTER-PLAN-2026Q4.md`](../00-strategy/MASTER-PLAN-2026Q4.md) 第 4 章（数据真实性与数据源可用性治理）配套阅读。

---

## 1. 数据源清单与可用性

实测时间为 2026-10-14（详见 MASTER-PLAN §4.1，非推断）。

| 通道 | 端点 / 模块 | 状态 | 降级行为 |
|---|---|---|---|
| 腾讯行情 / K 线 | `server/tencent_kline.py`；`/api/global`、`/api/kline`、`/api/quotes` | ✅ 存活（实时主链路） | 失败 → 磁盘快照 / 本地样本（见 §3） |
| 新浪财经 | `/api/intelligent/ak/news` → `source = "Sina Finance Roll Stream (fallback)"` | ✅ 存活（新闻 fallback） | 作为新闻源备用通道 |
| 东方财富 push2 | `stable_market_provider.py` 板块排行 / 热门股 | ⚠️ **间歇可达**（`ReadTimeout` / `RemoteDisconnected`；19:12 全节点不可达，21:18 已恢复） | 多节点轮询 → 磁盘快照 → 本地样本（见 §3） |
| 东方财富 datacenter-web（HTTPS） | 龙虎榜 | ✅ 可达（有抖动：`ReadTimeout(6s)`、`getaddrinfo failed`） | 陈旧护栏 + 磁盘快照（见 §4） |
| 东方财富 ulist.np（批量） | `AkShareDataHub.get_stock_fund_flow()`、个股资金流 | ✅ 可用 | 失败返回 `ok:false`（**不再用空数组掩盖**） |
| AkShare 指数 | `/api/intelligent/ak/index` | ❌ 不可用（`ok=False`，0 条） | 前端降级为不可用提示 |
| AkShare 全市场快照 | `/api/intelligent/ak/spot` | ❌ 不可用（`ok=False`，0 条） | 同上 |
| AkShare 北向 | `/api/market/north` | ⚠️ 接口可用但**北向净买额自 2024-08 起停止实时披露** | 返回 `disclosed: false`，改呈现成分股涨跌分布 |

> **关键结论**：数据源可达性本身是波动的。因此**不能把"可达性"写进逻辑假设** —— 降级路径必须始终存在且正确；
> 且**恢复可达是最危险的时刻**：真数据回来时若响应里还混着旧的推算字段，绿标会让编造内容获得"实时"的可信外观。

---

## 2. 字段单位与来源标注约定

### 2.1 单位约定

| 字段 | 单位 | 说明 |
|---|---|---|
| `price` / `open` / `high` / `low` / `close` | 元（人民币） | A 股股价 |
| `chg` / `chg_pct` | 百分比数值 | 如 `1.85` 表示 +1.85% |
| `amount` | 视口径而定 | 接口返回原始金额；前端展示时按亿元换算并注明 `amount_yi` |
| `net_flow` | 亿元 | 资金净流入；**取不到时返回 `null`，不推算**（见 §5） |
| `turnover` | 百分比数值 | 换手率 |
| `up_count` / `down_count` / `flat_count` | 只 | 涨跌平家数 |
| `flow_5d` / `flow_20d` / `flow_60d` | 百分比数值 | 多周期涨跌幅（须来自日 K 聚合，禁止写死系数，见 §5） |

### 2.2 来源标注约定（三条硬规则）

1. **实测优先**：能用真实接口取到的，绝不用推算。
2. **降级必须可见**：任何 fallback 数据必须带
   - 信封级：`source`（**必须反映真实来源名**）、`degraded: true`
   - 行级：`synthetic: true`（本地样本）/ `stale: true` + `stale_from`（历史快照）
   前端必须显示醒目横幅。
3. **取不到就说取不到**：返回 `null` + `reason`，前端显示"暂不可用"；
   **不允许用假数填充**，更不允许把假数标注成真实数据源。

### 2.3 正面范例（全项目模板）

- `server/global_assets.py` → `coverage={available:0,total:11}` + `degraded:true` + 逐项 `available:false` + `price:null`。
- `server/ak_market.py:113 get_north_flow` → 返回 `disclosed` 标记，如实说明"停止披露"。
- `server/stable_market_provider.py` 的**盘缓存降级链**（见 §3）。

---

## 3. 四级降级链（板块排行 / 热门股 / 龙虎榜共用）

| 级别 | 触发条件 | 返回内容 | 信封 | 行级标记 |
|---|---|---|---|---|
| ① 真实 | 网络正常 | 实测数据 | `degraded: false`，真实来源名 | `synthetic: false`, `stale: false` |
| ② 历史快照 | 网络失败，**有盘缓存** | 最近一次真实数据 | `degraded: true`，`来源（历史快照 YYYY-MM-DD HH:MM）` | `synthetic: false`, `stale: true`, `stale_from` |
| ③ 腾讯聚合 | 东财 push2 不可达，腾讯成分股可达 | 由腾讯成分股行情聚合的真实板块指标（成交额加权涨跌幅、成交额求和、涨跌家数） | `degraded: true`，来源标注腾讯聚合 | `synthetic: false`；`net_flow` 如实返回 `null`（不推算） |
| ④ 本地样本 | 网络失败且**无盘缓存** | 硬编码样本 | `degraded: true`，`系统快照（本地样本，非实时行情）` | `synthetic: true` |

**落盘为原子写**：写 `.tmp<pid>` → `flush + fsync` → `os.replace`；任一步失败只记 warning，不污染已有缓存。

**关键性质**：④ 只在"首次部署 / 缓存被删"时出现，而不是每次网络抖动都出现。

---

## 4. 陈旧护栏（龙虎榜 · 2015 数据伪装实时事件）

- **根因**：交易日探测失败 → 触发**无日期过滤的全历史查询** → 按净买额降序 → 早至 2015 年的救市日霸榜，被当成"今日龙虎榜"展示。
- **修法**：
  - 排序改为多列 `sortColumns=TRADE_DATE,BILLBOARD_NET_AMT` + `sortTypes=-1,-1`（先按交易日倒序，再按净买额倒序）。
  - 新增陈旧护栏 `_EM_LHB_STALE_DAYS = 10`：返回数据的最新交易日距今超过 10 天即判定为陈旧，降级并标注。
- **教训**：排序字段的选择会决定"最近的"还是"最大的"数据排在最前——分页取头部时二者混淆会静默返回历史极值。

---

## 5. 已被治理的伪造字段（必须保持为真实或 null）

以下字段此前为**推算值冒充实测值**，已治理，不得回退：

| 字段 | 治理前（伪造） | 治理后 |
|---|---|---|
| `net_flow`（板块） | `amt * (up/(up+dn+1) - 0.5) * 0.4` 涨跌家数比推算 | 来自实测接口；取不到 → `null` |
| `flow_5d` / `flow_20d` / `flow_60d` | `chg*1.85` / `chg*3.42` / `chg*5.80` 当日涨幅乘写死系数 | 从日 K 聚合出真实多周期收益 |
| 因子 `IC` | `动量分均值 × 0.15` | 真实截面相关系数时间序列 |
| 因子 `IR` | `IC × 18` | `mean(IC) / std(IC)` |
| 因子 `Q1~Q5` | 写死比例 | 真实分位分组 |
| 回测参数 `weights` / `selected_factor_ids` / `start_date` | 空壳（未使用） | 真实接线生效 |

**接口信封谎报来源事件**：`market_data.py` 曾无条件返回 `"source": "东方财富行业板块实时行情"`，
而实际是 24 条硬编码样本（`chg` 全 = 1.85、`amount` 全 = 284 亿、`net_flow` 全 = 12.5 亿）。
现兜底数据强制自证身份 `synthetic` / `degraded`。

---

## 6. 其他口径说明

- **北向资金**：自 2024-08 起交易所停止实时披露，接口固定返回 0；现以 `disclosed` 标记区分，
  前端在 `disclosed=false` 时显示"已停止披露"，改以成分股涨跌分布 + 相关指数填充。
- **`/api/sector-ranking`**：`limit` 最小 5，传 1~4 会 422。返回 `chg` / `amount` / `turnover` /
  `net_flow` / `up_count` / `down_count` 与 5 日·20 日·60 日 %。
- **`/api/fund-flow/sector`**：2026-10-13 起改为东方财富个股资金流批量聚合，
  返回 `super_large`/`large`/`medium`/`small`/`main` + `summary` + `stocks`；取数失败返回 `ok:false`。
- **模拟成交成本（已建模）**：成交成本模型已落地于 `server/trading/costs.py`（纯函数、无 IO），
  并由 `server/trading/paper.py` 接线。口径（货币单位＝元）：
  - 买入成交价 = 现价 ×(1 + slippage_bps/10000)；卖出成交价 = 现价 ×(1 − slippage_bps/10000)
  - 佣金 = max(成交额 × commission_rate, commission_min)
  - 印花税 = 成交额 × stamp_tax_rate（**仅卖出**收取）
  - 过户费 = 成交额 × transfer_fee_rate（沪深均收）
  - 买入总成本 = 成交额 + 佣金 + 过户费；卖出净收入 = 成交额 − 佣金 − 印花税 − 过户费
  - 成交价保留 3 位小数，金额保留 2 位小数（便于手算对齐）
  即"纸面收益率"已按上述口径扣除交易成本，解读时与旧口径（不含成本）不可直接比较。
- **默认成本参数**：滑点 `slippage_bps` 默认 5.0（买 +、卖 −），其余见 `server/trading/models.py` 的配置默认。

---

## 7. 数据源陷阱清单（2026-10-16 实测）

> 本节记录本轮实测到的、会造成"请求成功但数据悄悄出错"的陷阱。**新增取数逻辑前务必先读本节。**

| # | 陷阱 | 事实 | 规避 |
|---|---|---|---|
| 1 | 东财 `clist` 单页上限 | `pz` 最大 **100**，`pz>100` 会被**静默截断到 100** | 分页拉取；显式断言页大小 ≤ 100 |
| 2 | 分页排序键 | **必须用稳定排序键 `fid=f12`（代码）**；用 `fid=f3`（按实时涨跌幅）会**翻页错位/重复** —— 实测 5 页去重后只剩 **481** 条（请求全部"成功"，数据却悄悄丢了） | 分页一律 `fid=f12` + `pn` 递增 |
| 3 | 东财板块级 PB | 板块级 `f23`(PB) **恒为 `'-'`**（只有成分股级才有值） | 判空 → `null` + `reason`，**不得填 0** |
| 4 | 申万分类 | **不可得**（新浪 `sh801010` / `sw801010` / `sz801010` 与腾讯同名为空） | **不得用东财行业板块冒充申万分类**；口径一律标注"东财行业板块" |
| 5 | 腾讯 `sh000037` | 是"**上证行业分类指数**"，与东财 BK 板块**不是同一口径** | **不可混用**；两者不得在同一榜单里比较 |
| 6 | `push2his` 可达性 | **间歇可达**（`ReadTimeout` / `RemoteDisconnected`） | 批量构建必须**容忍部分失败**并如实写 `coverage` |

---

## 8. "假零"红线（取不到 → null，绝不给 0）

- **规则**：任何字段取不到时一律返回 `null` + `reason`，**绝不给 `0`** —— 因为 `0` 会被前端当作真实值渲染（"0.00 亿"看起来像真实成交）。
- **本轮已修实例**：
  - 盘前腾讯成交额 `amount`（盘前无成交 ≠ 成交额为 0）
  - `north` 的 `total` / `hgt` / `sgt`（`disclosed=false` 时北向净买额已停止披露，返回 null 而非 0）
- 前端对 `null` 显示"暂不可用 / 已停止披露"，不显示 0。

---

## 9. 多周期收益口径约定

- **公式**：`d_n = close[-1] / close[-1-n] - 1`（n 为周期天数；用最新收盘价对 n 个交易日前收盘价）。
- **同一响应内不得混用口径**：要么统一 `eastmoney_kline`，要么统一聚合口径（并标 `aggregated: true`）。
- 多周期收益由**离线构建**（`data/sector_periods.json`），请求路径**只读缓存**；缓存缺失时返回 `period_returns_cache_missing`（不给推算值）。

---

## 10. 板块层级约定（`level` / Ⅱ Ⅲ 关系）

- **层级体系**：东财行业板块共 **496 个**，`level` 分布 **414（level=0，大类）/ 39（level=2）/ 43（level=3）**。
- **排行默认只在 `level=0`（414 大类）内比较**，避免"父子板块同上榜"。需要细分时用 `level=2|3` 参数；**跨层级（`level=all`）时结论不可直接比较**（接口已返回 `note` 提示）。
- ⚠️ **重要数据质量事实**：东财行业板块的 **Ⅱ / Ⅲ 两级在 37 对上成分股完全相同**，即这些"二级/三级板块"实为**同一集合的两个代号**，**不可当作两个独立板块**参与排行或关联分析。
  - 例：`白酒Ⅲ(BK1575)` 与 `白酒Ⅱ(BK1277)` 成分股完全相同（各 20 只，**Jaccard = 1.0**）
  - 全库统计：level=2 与 level=3 之间"成分股完全相同"的对数 = **37 对**
  - 其它例：`地面兵装Ⅱ/Ⅲ`、`航空装备Ⅱ/Ⅲ`、`航海装备Ⅱ/Ⅲ` …
  - 这正是 `sector-ranking` 默认只在 `level=0`（414 大类）内排名的原因
- **关联图谱**里这类关系会被标为 `same_lineage: true`（判定依据是**成分股包含/相同**关系），避免"父子板块天然高重叠"被误读成"两个板块真实联动"。
- `sector-graph` 端点**需要 `top=N` 参数才会返回邻居列表**（不带时默认列表为空）。文档/调用示例请带完整写法：`GET /api/sector-graph?code=BK1600&top=10`。

---

## 11. 涨跌配色铁律

- **铁律**：**红 = 涨 / 买 / 多 / 盈，绿 = 跌 / 卖 / 空 / 亏**（A 股惯例）。
- **非方向性指标不得用红绿**：置信度、仓位、中性评分等改用**中性色**，避免被读成涨跌。
- 风险语义可以用红，但**必须加注释说明**（其红是"风险"而非"上涨"）。
- 本轮修复清单（多路径一致）：`chart.js`（K 线两条渲染路径 + 像素级验证）、`dashboard.css`（`.lab-regime`）、`ui-refinement.css`（板块卡片 + `.ratio-*`）、`sentiment.css`、`sentiment.js`、`app.js`、`strategies-ui.js`。

---

## 12. 前端静态资源版本号规范

- **改了带 `?v=` 的资源必须 bump**（否则浏览器缓存用户仍加载旧文件，**等于修复无效**）。
- 本轮涉及：`chart.js?v=26`、`dashboard.css?v=62`、`ui-refinement.css?v=34`、`sentiment.css?v=3`、`sentiment.js?v=4`、`app.js?v=33`、`market-dashboard.js?v=19`。
- 约定同样适用于文档站自身：改 `static/js/project-docs-data.js` 后须递增 `project-docs.html` 中的 `?v=N`。

---

## 13. watchdog 日志计数陷阱

- `logs/watchdog.log` 里的"**异常退出 code=1**"**混入了"主动 kill 子进程以重载代码"**这一情况（`taskkill /F` 也会产生 exit code 1）。
- **不能把 `code=1` 计数当作崩溃次数**。判据应是：**`logs/server.log` 里是否还有真实 traceback**。
- 例：本轮"20 分钟连挂 5 次"中，多数是并行开发时的主动重载；只有带 `sqlite3.OperationalError` 栈的才是真实崩溃。

---

## 14. P0 稳定性约束（`server/trading/store.py` 迁移顺序）

- `_SCHEMA` **只允许定义基列**，**禁止包含引用迁移列（`is_drill` / `exit_reason` / `peak_price` …）的索引**。
- 初始化顺序必须严格为：**`_SCHEMA` → `_migrate` → `_ensure_indexes`**（先建基表，再 ALTER 补迁移列，最后建引用迁移列的索引）。
- `init()` **全程持锁**；启动 task **必须挂异常回调**。
- **历史教训**：老库首次启动时若顺序倒置（先建引用 `is_drill` 的索引再 ALTER 补列），会抛 `no such column: is_drill`，导致**服务反复崩溃重启**。

---

## 15. 板块相关端点与缓存 TTL（2026-10-16 补充）

### 15.1 板块端点清单（本轮新增 / 更新，全部前缀 `/api`，实测均 200）

| 端点 | 说明 | 失败 / 降级表现 |
|---|---|---|
| `GET /api/sector-ranking?level=0\|2\|3&limit=N` | 行业板块排行；**全量取回、在选定层级全量排序、`limit` 只控返回条数**；响应带 `total` / `universe_total` / `level`；默认 `level=0`（414 大类） | 见 §3 四级降级链 |
| `GET /api/sector-map/stock/{code}` | 反查某股票所属板块列表（读**离线产物** `data/sector_map.json`；请求路径**绝不现拉**） | 文件缺失 → 返回 `hint`「python scripts/build_sector_map.py」，**不伪造** |
| `GET /api/sector-linkage` | 板块 × 外部因子的**金融属性敏感度**（相关性） | n<30 → 降级（样本不足，不给系数） |
| `GET /api/sector-graph?code=BK1600&top=N` | 板块关联图谱（成分股重叠 Jaccard + 历史收益相关）；边带 `same_lineage`（父子板块）标记 | **必须带 `top=N` 才返回邻居列表**（不带则列表为空，非故障） |
| `GET /api/sector-clusters` | 板块关联簇（本轮为 168 簇） | 数据缺失 → 空列表 + 说明 |

### 15.2 缓存 TTL 清单（与 `server/config.py:396-414` 对齐，**以代码为准**）

| 常量 | TTL | 说明 |
|---|---|---|
| `CACHE_TTL_QUOTES` | 60s | 行情 |
| `CACHE_TTL_KLINE` | 600s | K 线 |
| `CACHE_TTL_GLOBAL` | 120s | 全球市场 |
| `CACHE_TTL_SCREEN` | 120s | 选股条件 |
| `CACHE_TTL_FUND_FLOW` | 60s | 资金流向 |
| `CACHE_TTL_SECTOR_RANK` | 60s | 板块排行 |
| `CACHE_TTL_LHB` | 300s | 龙虎榜 |
| `CACHE_TTL_MARKET_OVERVIEW` | 60s | 市场涨跌分布 |
| `CACHE_TTL_HOT` | 120s | 热门股 |
| `CACHE_TTL_PROFILE` | 3600s | 个股简况 |
| `CACHE_TTL_FINANCE` | 3600s | 财务报表（数据源已废弃，见 §16） |
| `CACHE_TTL_REPORT` | 1800s | 研报列表（数据源已废弃，见 §16） |
| `CACHE_TTL_NORTH` | 300s | 沪深股通 |
| `CACHE_TTL_NEWS` | 300s | 新闻资讯 |
| `CACHE_TTL_SECTOR_STOCKS` | 86400s | 板块成分股 |

---

## 16. 已废弃数据源：westock CLI（重要 · 运维勿再配置）

- **`westock-data` CLI（Node.js）链路已废弃**（该 skill 已迁移到 MCP 后 CLI 永久不可用），但 `server/westock.py` 的 `WestockClient` 封装仍在（CLI 不可用告警只打一次，属预期）。
- **后果**：依赖它的端点现在**返回 `null` / 空**，而不是"功能正常但没数据"。**实测（2026-10-16 夜，已更新）**：
  这些端点现已按 §17 的规范改为 **`ok:false` + `source:"unavailable"` + 明确 `reason`**（不再是 `ok:true`）：
  - `GET /api/finance?code=sh600036` → `{"ok":false,"data":null,"source":"unavailable","reason":"westock CLI 链路已废弃（已迁移 MCP），数据源未接入"}`
  - `GET /api/report?code=sh600036`   → 同上
  - `GET /api/consensus?code=sh600036` → 同上（**注意**：此前是 `{"ok":true,"data":{"rating":null,"consensus":null}}`，
    空对象形态会让前端 `if(!consensus)` 判不出空、渲染出一张全是 `--` 的**假卡片**；现已改为 `ok:false`）
  - `GET /api/profile?code=sh600036` → 同上
  - `GET /api/north-holding?code=sh600036` → 同上
- **运维提示**：**不要再去配置 westock CLI**。以下早期文档描述的 westock 集成**已过时，仅作历史参考**：
  `docs/10-architecture/architecture-westock-mcp.md`、`docs/10-architecture/system_design.md`、
  `docs/20-prd/incremental-prd-westock-mcp.md`。
- **替代路径**：行情 / 板块用腾讯 + 东财；个股资金流用东财 `ulist.np` 批量接口；沪深港通用 AkShare。

---

## 17. 数据完整性约定（2026-10-16 夜新增，强制）

### 17.1 订单「请求数量」必须留存（`req_qty` / `req_amount`）

- `orders` 表新增两列：`req_qty INTEGER NOT NULL DEFAULT 0`、`req_amount REAL NOT NULL DEFAULT 0`（属**迁移列**，
  只能写在 `store._MIGRATIONS` 里，不能进 `_SCHEMA`——见 §14）。
- **语义**：`req_qty` = 用户/信号**换算前**请求的数量；`req_amount` = `req_qty × 参考价`。
  `qty` 则是风控换算后的数量。二者在成功单上通常相等，**在被拦下单上可能不同**。
- **为什么必须有**：`check_order()` 会把 `order.qty` 改写为风控换算后的数量。若拒绝分支不在此刻留痕，
  「用户当时想买多少」就永久丢失，复盘无法回放「如果当时买了会怎样」。
- **约定**：0 = 调用方未指定数量（买入按额度自动换算 / 卖出全部平仓），**不是**"请求买 0 股"。
- 前端展示规则：当 `qty==0 且 req_qty>0` 时，必须显示 `req_qty`，**不得显示 0**。

### 17.2 演练单标记（`is_drill`）

- `orders.is_drill = 1` 表示演练/测试单，**必须排除出一切真实统计**：策略指标与晋级降级、复盘胜率、决策质量评分。
- 写入优先级（`store.insert_order`）：调用方显式给 `is_drill` → 否则读 `marks.drill_active()` 演练上下文。
- **入口约定**：`POST /api/automation/orders` 的 body 支持 `is_drill`（`null` = 交由服务端上下文判定；
  `true` = 显式演练单）。**任何联调/测试下单都必须带 `is_drill: true`**，否则会污染真实统计。
- 历史修复入口：`store.mark_orders_as_drill(ts_from, ts_to)`（一次性数据修复，属 store 唯一写入口）。

### 17.3 行情字段禁止假零（`_opt_float` vs `_safe_float`）

- `server/ak_market.py` 有两个转换器，**用途不可混**：
  - `_safe_float(val, fallback=0.0)` —— **仅**用于「0 有意义且已被 `<=0` 守卫」的位置（如宏观指标取值）。
  - `_opt_float(val)` —— 用于**一切行情字段**（价格 / 涨跌幅 / 成交额 / 净流入 / 成分股资金流）。
    缺失、`-`、`--`、NaN、±inf **一律返回 `None`**，由上层写 `null` + `reason`。
- **理由**：`0` 会被前端当真实值渲染成「价格 0」「净流入 0」。**宁可 `null` + 原因，不可 0**。

### 17.4 初始本金单一真相源

- 唯一真源：`server/trading/models.py:DEFAULT_INITIAL_CASH`。
  **禁止**在 `backtest.py` / `iterator.py` / `paper.py` 或任何别处再写字面量 `1_000_000`。
- 运行时以虚拟盘账户 `paper_account.initial_cash` 为准；`strategy_metrics(trips, initial_cash=…)` 的
  **最大回撤分母必须传真实本金**，并在返回值里回带 `maxdd_base_cash` 供审计。

### 17.5 后台 CPU 采样：非阻塞 + 不许假零

- `server/routers/admin.py` 的 `sample_process_cpu_percent()` / `sample_system_cpu_percent()`：
  **禁止在 `async def` 里用 `cpu_percent(interval=…)`**（会阻塞事件循环，`interval=0.5` 即卡 500ms，同时会拖垮 SSE 推送）。
- 采用 `interval=None` + 5s 时间闸；**首帧（基线未建立）返回 `None`**，UI 显示 `--`，
  **绝不用 0 冒充真实占用**。字段 `cpu_scope` 标明口径（`process` = 本进程，非整机）。

### 17.6 后台系统健康端点

- `GET /api/admin/system-health` 汇总五块（外部因子新鲜度 / 离线产物 / 交易引擎 / 实时推送 / 功耗与缓存），
  **每块带 `source`；每条带时间戳；取不到一律 `null` + `reason`**。
- 前端 `static/js/admin-health.js`：**单一可暂停定时器**（45s，`document.hidden` 时停止轮询），
  非方向性指标（CPU/内存/缓存/命中率）**用中性色 + 文案，不使用红绿**（见 §11）。

---

## 18. SSE 实时推送（`/api/stream/*`）运行特性与判据

- **发布模型**：market / signals 各一个**共享发布协程**，所有订阅者共享同一份快照。
  **判据（3 客户端 60s 压测）**：`market_refresh_invocations` 增量 **≈1**（而非 3×），`market_cache_hits` 显著增长。
  → 连接数翻倍，**上游取数不翻倍**（对 push2 限流至关重要）。
- **断连回收**：`request.is_disconnected()` 以 `POLL_SECONDS=1.0` 轮询，**不依赖是否有事件可发**（空闲订阅者也能在 ≤1s 内被发现）；
  `finally` 注销队列。**判据**：连上 `active=1`，`kill -9` 后 5s 内回落 `0`。
- **背压**：单连接队列 `MAX_QUEUE=100`，满时**丢最旧保最新**并累加 `events_dropped` + 打日志。
  **判据**：灌入 125 条 → 队内保持 100、`dropped=25`、队首为第 25 条。
- **心跳**：空闲 `HEARTBEAT_SECONDS=15.0` 发 SSE 注释行 `: ping`（**判据**：40s 窗口内应见 2 次）。
- **计数语义**：`clients_total` / `clients_total_ever` 是**历史累计连接数**（单调递增，**不是在线数**）；
  在线数以 `active_clients`（每次快照动态计算）为准。勿混用。
