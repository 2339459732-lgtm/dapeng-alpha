# DAPENG Alpha 增量PRD — 腾讯自选股MCP数据源接入

> ⚠️ **状态更新（2026-10-16）：部分过期** —— `westock-data` CLI 链路已废弃，依赖它的端点（`/api/finance` / `/api/report` / `/api/consensus` / `/api/profile`）现返回 `null`。本文仅作**历史需求参考**，详见 [`docs/30-data/DATA-DICTIONARY.md`](../30-data/DATA-DICTIONARY.md) §16。

> **版本**: v5.0-incremental | **日期**: 2025-07-11 | **作者**: 产品经理 Alice
> **原始需求**: "借助腾讯自选股接入到我的股票系统里，股票数量太少了，有一些功能也是欠缺的，无法实用和展示"

---

## 1. 项目信息

| 项目 | 说明 |
|------|------|
| **项目名称** | dapeng_alpha_mcp_integration |
| **技术栈** | 后端: FastAPI (Python 3.13) + 前端: HTML/CSS/JS (ECharts) |
| **数据源** | 腾讯HTTP行情(主力) + westock MCP连接器(新增) + westock-data CLI(备用) |
| **当前状态** | 24板块 356只股票，前端含多个空壳面板 |
| **目标状态** | 动态股票池(1000+) + 全量MCP数据源接入 + 空壳面板填充 |

### 原始需求复述

用户反馈当前系统存在两大核心问题：
1. **股票数量太少** — 仅356只硬编码股票，无法覆盖全市场，无法实用
2. **功能欠缺** — 资金流向、龙虎榜、热门股、研报、财务等核心功能缺失，前端部分面板是"空壳"（有UI无数据），无法展示

腾讯自选股MCP连接器(westock-mcp)提供了24+个数据工具，当前系统完全未接入。本次增量开发的目标是系统性地接入这些MCP能力，填充空壳面板，扩展股票覆盖范围，使系统达到可实用、可展示的水平。

---

## 2. 产品定义

### 2.1 产品目标

| # | 目标 | 衡量标准 |
|---|------|----------|
| G1 | **填充空壳面板** — 前端已有UI但无后端数据的功能全部接入真实MCP数据 | 资金流向/板块排行/龙头股TOP5 三个空壳面板100%填充真实数据 |
| G2 | **扩展股票覆盖** — 从356只静态股票扩展到1000+动态股票池 | 通过板块成分股+热门股+龙虎榜动态扩充，覆盖率达到A股市场20%以上 |
| G3 | **补齐核心功能** — 接入龙虎榜、市场总览、热门股、财务报表等缺失功能 | 新增8+个MCP数据API端点，前端新增对应展示面板 |

### 2.2 用户故事

| # | 角色 | 故事 |
|---|------|------|
| US1 | 作为A股投资者，我想看到个股的资金流向（主力净流入/大单/小单），以便判断主力资金意图 |
| US2 | 作为A股投资者，我想查看龙虎榜数据（机构席位/游资动向），以便跟踪聪明资金的交易方向 |
| US3 | 作为A股投资者，我想看到板块热度排行榜（涨跌幅/成交额/领涨股），以便发现市场热点切换 |
| US4 | 作为A股投资者，我想看到市场总览（涨跌家数/涨停跌停/总成交额），以便快速感知市场情绪 |
| US5 | 作为A股投资者，我想查看热门搜索股票榜，以便了解市场关注度最高的标的 |
| US6 | 作为A股投资者，我想查看个股的财务报表和研报评级，以便做基本面分析决策 |
| US7 | 作为A股投资者，我想看到北向资金持股变动，以便跟踪外资动向 |
| US8 | 作为系统使用者，我想股票池能动态扩展（不限于356只），以便发现更多投资机会 |

---

## 3. 需求池

### P0 — 必须有（填空壳 + 核心缺失功能）

> 判断依据：前端已有UI占位但无后端数据，或属于用户最核心的缺失功能

#### P0-1: 个股资金流向

| 属性 | 说明 |
|------|------|
| **需求描述** | 接入个股资金流向数据，包括主力净流入、超大单、大单、中单、小单的净额和净占比 |
| **MCP工具** | `data_fund_flow` |
| **前端位置** | **增强已有面板** — K线图下方新增"资金流向"标签页，同时在板块分析面板的`#sa-flow-grid`空壳填充资金流向概览 |
| **数据字段** | `main_net_inflow`(主力净流入), `super_large_net`(超大单净额), `large_net`(大单净额), `medium_net`(中单净额), `small_net`(小单净额), `main_net_pct`(主力净占比), `continuous_days`(连续净流入天数) |
| **API设计** | `GET /api/fund-flow?code={code}` — 单股资金流向；`GET /api/fund-flow/sector?sector={key}` — 板块资金流向汇总 |
| **展示形式** | 个股：柱状图(多空对比) + 数字面板；板块：资金流向概览网格 |
| **缓存TTL** | 60秒（盘中实时性要求高） |

#### P0-2: 板块热度排行

| 属性 | 说明 |
|------|------|
| **需求描述** | 接入板块排行数据，按涨跌幅/成交额排序展示全市场板块热度，含领涨股信息 |
| **MCP工具** | `data_sector` (mode=ranking) |
| **前端位置** | **填充空壳** — 板块分析面板的`#sa-hot-sectors`列表 |
| **数据字段** | `sector_name`(板块名), `sector_code`(板块代码), `chg_pct`(涨跌幅), `amount`(成交额), `leader_stock`(领涨股名称), `leader_chg`(领涨股涨幅), `up_count`(上涨家数), `dn_count`(下跌家数) |
| **API设计** | `GET /api/sector-ranking?type=industry` — 行业板块排行；`GET /api/sector-ranking?type=concept` — 概念板块排行 |
| **展示形式** | 热度排行列表，含涨跌幅条形图 + 领涨股标签 |
| **缓存TTL** | 60秒 |

#### P0-3: 龙头股TOP5（龙虎榜）

| 属性 | 说明 |
|------|------|
| **需求描述** | 接入龙虎榜数据，展示当日上榜股票的机构席位、游资、营业部买卖明细，填充龙头股TOP5空壳 |
| **MCP工具** | `data_lhb` |
| **前端位置** | **填充空壳** — 板块分析面板的`#top-stocks-list`龙头股TOP5；另新增龙虎榜独立面板（侧边栏入口） |
| **数据字段** | `code`(代码), `name`(名称), `reason`(上榜原因), `net_buy`(净买入额), `buy_list`(买入席位列表), `sell_list`(卖出席位列表), `institution_net`(机构净买入), `hot_money`(游资动向) |
| **API设计** | `GET /api/lhb?date={date}` — 当日龙虎榜；`GET /api/lhb/stock?code={code}` — 个股龙虎榜历史 |
| **展示形式** | TOP5卡片列表(净买入额 + 机构标识) + 龙虎榜明细表格(席位/买入额/卖出额/净额) |
| **缓存TTL** | 300秒（日内数据，变化频率低） |

#### P0-4: 市场总览

| 属性 | 说明 |
|------|------|
| **需求描述** | 接入市场总览数据，展示全市场涨跌家数、涨停/跌停数、总成交额、市场估值等宏观指标 |
| **MCP工具** | `data_market_overview` |
| **前端位置** | **新增面板** — 右侧面板顶部`#global-panel`区域扩展，或KPI行上方新增市场总览条 |
| **数据字段** | `up_count`(上涨家数), `dn_count`(下跌家数), `flat_count`(平盘家数), `limit_up`(涨停数), `limit_dn`(跌停数), `total_amount`(总成交额), `avg_pe`(平均市盈率), `avg_pb`(平均市净率) |
| **API设计** | `GET /api/market-overview` — 市场总览 |
| **展示形式** | 横向统计条(涨/跌/平/涨停/跌停) + 成交额大字 + 估值指标 |
| **缓存TTL** | 60秒 |

#### P0-5: 热门股/热搜榜

| 属性 | 说明 |
|------|------|
| **需求描述** | 接入热门搜索股票榜，展示市场关注度最高的股票，作为市场情绪指标和动态股票池来源 |
| **MCP工具** | `data_hot` |
| **前端位置** | **新增面板** — 侧边栏新增"热门股"入口，或板块分析面板新增热门股Tab |
| **数据字段** | `code`(代码), `name`(名称), `rank`(排名), `search_count`(搜索次数), `chg_pct`(涨跌幅), `price`(现价), `hot_tag`(热门标签) |
| **API设计** | `GET /api/hot-stocks?type=search` — 搜索热度榜；`GET /api/hot-stocks?type=trade` — 交易热度榜 |
| **展示形式** | 排行榜列表(排名 + 名称 + 涨跌幅 + 热度指标) |
| **缓存TTL** | 120秒 |

---

### P1 — 应该有（基本面数据 + 扩展股票覆盖）

> 判断依据：提升系统实用性的重要功能，但非阻塞性空壳填充

#### P1-1: 个股概览（基本面/估值/所属板块）

| 属性 | 说明 |
|------|------|
| **需求描述** | 接入个股基本面概览数据，包括市值、PE/PB/ROE、所属板块、上市日期、主营业务等 |
| **MCP工具** | `data_profile` |
| **前端位置** | **增强已有面板** — K线图区域新增"个股简况"标签页（与资金流向并列） |
| **数据字段** | `market_cap`(总市值), `float_cap`(流通市值), `pe`(市盈率), `pb`(市净率), `roe`(ROE), `industry`(所属行业), `list_date`(上市日期), `main_business`(主营业务), `total_shares`(总股本), `float_shares`(流通股本) |
| **API设计** | `GET /api/profile?code={code}` — 个股概览 |
| **缓存TTL** | 3600秒（基本面数据日内不变） |

#### P1-2: 财务报表

| 属性 | 说明 |
|------|------|
| **需求描述** | 接入财务报表数据，包括利润表、资产负债表、现金流量表的关键科目 |
| **MCP工具** | `data_finance` |
| **前端位置** | **新增面板** — K线图区域"个股简况"标签页下新增财务报表子Tab |
| **数据字段** | `revenue`(营业收入), `net_profit`(净利润), `eps`(每股收益), `bps`(每股净资产), `gross_margin`(毛利率), `net_margin`(净利率), `debt_ratio`(资产负债率), `current_ratio`(流动比率), `cash_flow_operating`(经营现金流), `yoy_revenue`(营收同比), `yoy_profit`(利润同比) |
| **API设计** | `GET /api/finance?code={code}&type=income` — 利润表；`GET /api/finance?code={code}&type=balance` — 资产负债表；`GET /api/finance?code={code}&type=cashflow` — 现金流量表 |
| **展示形式** | 财务数据表格(最新报告期 + 同比变动) + 趋势小图(近4季度) |
| **缓存TTL** | 3600秒 |

#### P1-3: 研究报告/评级

| 属性 | 说明 |
|------|------|
| **需求描述** | 接入机构研究报告和评级数据，展示分析师评级分布、目标价、研报摘要 |
| **MCP工具** | `data_report` + `data_consensus` + `data_rating` |
| **前端位置** | **新增面板** — K线图区域新增"研报"标签页 |
| **数据字段** | `rating_summary`(评级汇总: 买入/增持/中性/减持), `target_price`(目标价), `target_pct`(上涨空间), `analyst_count`(分析师数量), `report_title`(研报标题), `report_date`(发布日期), `institution`(机构名称), `rating`(评级), `summary`(摘要) |
| **API设计** | `GET /api/report?code={code}` — 研报列表；`GET /api/consensus?code={code}` — 分析师共识 |
| **展示形式** | 评级分布环形图 + 目标价区间 + 研报列表(标题/机构/日期/摘要) |
| **缓存TTL** | 1800秒 |

#### P1-4: 北向资金持股

| 属性 | 说明 |
|------|------|
| **需求描述** | 接入北向资金(沪股通/深股通)持股数据，展示外资持仓变动 |
| **MCP工具** | `data_north_holding` |
| **前端位置** | **新增面板** — K线图区域新增"北向资金"标签页；侧边栏板块分析面板新增北向资金汇总 |
| **数据字段** | `hold_shares`(持股数量), `hold_ratio`(持股比例), `hold_market_value`(持股市值), `net_buy_today`(今日净买入), `net_buy_5d`(5日净买入), `net_buy_20d`(20日净买入) |
| **API设计** | `GET /api/north-holding?code={code}` — 个股北向持股；`GET /api/north-holding/top` — 北向资金TOP增持 |
| **展示形式** | 持股变动柱状图(日/5日/20日) + 持股比例进度条 |
| **缓存TTL** | 300秒（收盘后更新） |

#### P1-5: 新闻资讯增强

| 属性 | 说明 |
|------|------|
| **需求描述** | 增强当前有限的新闻功能，接入MCP新闻资讯接口，获取个股相关新闻和公告 |
| **MCP工具** | `data_news` |
| **前端位置** | **增强已有面板** — AI分析面板下方新增新闻流，或K线图区域新增"资讯"标签页 |
| **数据字段** | `title`(标题), `content`(内容摘要), `time`(发布时间), `source`(来源), `type`(类型: 新闻/公告/研报), `url`(链接) |
| **API设计** | `GET /api/news?code={code}&limit=20` — 个股新闻；`GET /api/news/market` — 市场要闻 |
| **展示形式** | 新闻流列表(时间 + 标题 + 来源标签) |
| **缓存TTL** | 300秒 |

#### P1-6: 股票覆盖范围扩展

| 属性 | 说明 |
|------|------|
| **需求描述** | 利用MCP动态扩充股票池，从356只扩展到1000+只 |
| **MCP工具** | `data_sector` (mode=constituent) + `data_hot` + `data_lhb` + `tool_strategy` + `tool_filter` |
| **前端位置** | 后端配置层变更 + 前端股票列表支持动态加载 |
| **实现方案** | 见下文"股票覆盖范围扩展方案"章节 |
| **API设计** | `GET /api/sector-stocks?sector_code={code}&dynamic=true` — 动态获取板块全部成分股 |
| **缓存TTL** | 86400秒（成分股日内不变） |

---

### P2 — 可以有（高级功能）

> 判断依据：锦上添花功能，优先级低于核心数据接入

#### P2-1: 宏观经济数据

| 属性 | 说明 |
|------|------|
| **需求描述** | 接入宏观经济数据(GDP/CPI/PMI/M2/社融等) |
| **MCP工具** | `data_macro` |
| **前端位置** | 新增独立的"宏观数据"页面或面板 |
| **缓存TTL** | 86400秒 |

#### P2-2: 筹码分布

| 属性 | 说明 |
|------|------|
| **需求描述** | 接入个股筹码分布数据，展示获利盘比例、平均持仓成本 |
| **MCP工具** | `data_chip` |
| **前端位置** | K线图区域新增"筹码"标签页 |
| **缓存TTL** | 600秒 |

#### P2-3: 融资融券数据

| 属性 | 说明 |
|------|------|
| **需求描述** | 接入融资融券数据，展示融资余额、融券余额、融资买入额 |
| **MCP工具** | `data_fund_margin` + `data_fund_short` |
| **前端位置** | K线图区域新增"两融"标签页 |
| **缓存TTL** | 300秒 |

#### P2-4: 分红送转

| 属性 | 说明 |
|------|------|
| **需求描述** | 接入分红送转历史数据 |
| **MCP工具** | `data_dividend` |
| **前端位置** | "个股简况"标签页下新增分红记录 |
| **缓存TTL** | 86400秒 |

#### P2-5: 股东数据

| 属性 | 说明 |
|------|------|
| **需求描述** | 接入十大股东/十大流通股东数据 |
| **MCP工具** | `data_shareholder` |
| **前端位置** | "个股简况"标签页下新增股东列表 |
| **缓存TTL** | 86400秒 |

#### P2-6: 事件驱动

| 属性 | 说明 |
|------|------|
| **需求描述** | 接入事件驱动数据(解禁/回购/增减持等) |
| **MCP工具** | `data_events` |
| **前端位置** | 新闻流中融合展示或独立事件日历 |
| **缓存TTL** | 3600秒 |

#### P2-7: 产业链数据

| 属性 | 说明 |
|------|------|
| **需求描述** | 接入产业链上下游关系数据 |
| **MCP工具** | `data_industry_chain` |
| **前端位置** | 板块分析面板新增产业链图谱 |
| **缓存TTL** | 86400秒 |

#### P2-8: MCP预置策略选股

| 属性 | 说明 |
|------|------|
| **需求描述** | 接入MCP预置策略(MACD金叉/大市值/低估值等)和条件选股(PE/PB/ROE/股息率过滤) |
| **MCP工具** | `tool_strategy` + `tool_filter` + `tool_ranking` |
| **前端位置** | 增强已有的智能选股面板`#scan-panel`，新增策略预设和条件过滤选项 |
| **缓存TTL** | 300秒 |

#### P2-9: 自选股管理（服务端持久化）

| 属性 | 说明 |
|------|------|
| **需求描述** | 接入MCP自选股管理，实现服务端持久化的自选股列表 |
| **MCP工具** | `portfolio_watchlist` + `portfolio_watchlist_add/remove/move/sort` |
| **前端位置** | 增强已有的自选股Tab(当前仅前端localStorage) |
| **缓存TTL** | 实时 |

#### P2-10: 模拟交易

| 属性 | 说明 |
|------|------|
| **需求描述** | 接入MCP模拟交易，实现纸上交易(paper trading)功能 |
| **MCP工具** | `portfolio_paper_trade` + `portfolio_paper_positions` + `portfolio_paper_portfolio` + `portfolio_paper_profit` + `portfolio_paper_history` |
| **前端位置** | 新增独立的"模拟交易"页面 |
| **缓存TTL** | 实时 |

---

## 4. 股票覆盖范围扩展方案

### 4.1 当前问题

- 硬编码24个板块、356只股票（`config.py` 中 `SECTORS` 字典）
- 部分板块股票数极少（如纺织4只、农业4只、交通4只）
- 无法动态发现市场新上市股票或热门概念股

### 4.2 扩展策略（三层动态扩充）

```
┌─────────────────────────────────────────────────────────┐
│              股票池动态扩充架构                           │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  Layer 1: 板块成分股全量加载 (P1-6)                      │
│  ├── data_sector mode=constituent                       │
│  ├── 对24个已有板块，拉取全部成分股                       │
│  ├── 预计扩充: 356 → 2000+ 只                           │
│  └── 缓存TTL: 86400秒 (日级更新)                        │
│                                                         │
│  Layer 2: 热门股/龙虎榜动态加入 (P0-5 + P0-3)            │
│  ├── data_hot 每日热门股自动加入股票池                    │
│  ├── data_lhb 龙虎榜上榜股票自动加入                      │
│  ├── 预计扩充: +50~100 只/日 (动态轮换)                  │
│  └── 缓存TTL: 3600秒 (小时级更新)                       │
│                                                         │
│  Layer 3: 策略选股结果动态加入 (P2-8)                    │
│  ├── tool_strategy 预置策略选股结果                      │
│  ├── tool_filter 条件选股结果                            │
│  ├── 用户主动触发选股时，结果股票自动加入池               │
│  └── 缓存TTL: 300秒 (分钟级更新)                        │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

### 4.3 实现方案

#### 后端架构变更

1. **新增动态股票池管理器** (`server/stock_pool.py`)
   - 维护三层股票池：`base_pool`(静态) + `sector_pool`(板块成分股) + `dynamic_pool`(热门/龙虎榜/选股结果)
   - 提供统一接口 `get_all_stock_codes()` 返回合并后的去重股票列表
   - 后台定时任务刷新板块成分股（每日开盘前）

2. **config.py 兼容性**
   - 保留 `SECTORS` 字典作为板块配置基础（板块名/图标/sector_code）
   - 新增 `sector_code` 字段已存在，直接用于 MCP `data_sector` 查询
   - `get_all_stock_codes()` 改为调用动态股票池管理器

3. **缓存策略**
   - 板块成分股：SQLite 持久化 + 内存缓存 (TTL 86400秒)
   - 热门股/龙虎榜：内存缓存 (TTL 3600秒)
   - 策略选股结果：内存缓存 (TTL 300秒)

#### 性能考量

- 全量扫描性能：当前356只需~15秒，扩展到2000只预计~60秒
- 建议分批扫描：按板块分批并行，每批40只
- 预热策略：仅预热核心板块(semiconductor/ai_compute/new_energy)的成分股

---

## 5. 前端面板变更汇总

### 5.1 填充空壳面板（P0）

| 面板 | DOM ID | 当前状态 | 变更内容 |
|------|--------|----------|----------|
| 资金流向概览 | `#sa-flow-grid` | 空div | 填充板块资金流向网格(主力/超大单/大单/中单/小单) |
| 板块热度排行 | `#sa-hot-sectors` | 空div | 填充板块排行列表(涨跌幅+领涨股) |
| 龙头股TOP5 | `#top-stocks-list` | 空div | 填充龙虎榜TOP5卡片(净买入+机构标识) |
| Mini资金流向 | `#mini-flow-val` | 显示"--" | 填充当前板块资金流向汇总值 |

### 5.2 新增面板

| 面板 | 位置 | 功能 |
|------|------|------|
| 市场总览条 | 右侧面板顶部 `#global-panel` | 涨跌家数/涨停跌停/总成交额 |
| 热门股榜 | 侧边栏新增入口 | 热搜股票排行 |
| 龙虎榜面板 | 侧边栏新增入口 | 完整龙虎榜明细 |
| K线图标签页组 | K线图下方 | 资金流向/个股简况/研报/北向资金/资讯 多Tab切换 |
| 财务报表 | 个股简况子Tab | 利润表/资产负债表/现金流量表 |
| 模拟交易 | 独立页面(P2) | 纸上交易 + 持仓管理 |

### 5.3 K线图区域Tab化改造

当前K线图下方是固定的策略评分/MACD/仓位建议等面板。建议改为Tab切换式：

```
┌──────────────────────────────────────────────┐
│  [策略评分] [资金流向] [个股简况] [研报]      │
│  [北向资金] [资讯] [筹码] [两融]              │
├──────────────────────────────────────────────┤
│                                              │
│  (Tab内容区域 — 默认显示策略评分)              │
│                                              │
└──────────────────────────────────────────────┘
```

- 默认Tab：策略评分（保持现有功能不变）
- 切换Tab时懒加载对应MCP数据
- 策略评分/MACD/仓位建议保持在默认Tab内

---

## 6. API端点汇总

### 新增API（按优先级）

| 优先级 | 端点 | MCP工具 | 说明 |
|--------|------|---------|------|
| P0 | `GET /api/fund-flow` | `data_fund_flow` | 个股资金流向 |
| P0 | `GET /api/fund-flow/sector` | `data_fund_flow` | 板块资金流向 |
| P0 | `GET /api/sector-ranking` | `data_sector` (ranking) | 板块热度排行 |
| P0 | `GET /api/lhb` | `data_lhb` | 龙虎榜 |
| P0 | `GET /api/lhb/stock` | `data_lhb` | 个股龙虎榜历史 |
| P0 | `GET /api/market-overview` | `data_market_overview` | 市场总览 |
| P0 | `GET /api/hot-stocks` | `data_hot` | 热门股/热搜榜 |
| P1 | `GET /api/profile` | `data_profile` | 个股概览 |
| P1 | `GET /api/finance` | `data_finance` | 财务报表 |
| P1 | `GET /api/report` | `data_report` | 研究报告 |
| P1 | `GET /api/consensus` | `data_consensus` | 分析师共识 |
| P1 | `GET /api/north-holding` | `data_north_holding` | 北向资金持股 |
| P1 | `GET /api/north-holding/top` | `data_north_holding` | 北向资金TOP增持 |
| P1 | `GET /api/news` | `data_news` | 个股新闻 |
| P1 | `GET /api/news/market` | `data_news` | 市场要闻 |
| P1 | `GET /api/sector-stocks` | `data_sector` (constituent) | 板块成分股(动态) |
| P2 | `GET /api/macro` | `data_macro` | 宏观经济数据 |
| P2 | `GET /api/chip` | `data_chip` | 筹码分布 |
| P2 | `GET /api/margin` | `data_fund_margin` | 融资融券 |
| P2 | `GET /api/dividend` | `data_dividend` | 分红送转 |
| P2 | `GET /api/shareholder` | `data_shareholder` | 股东数据 |
| P2 | `GET /api/events` | `data_events` | 事件驱动 |
| P2 | `GET /api/industry-chain` | `data_industry_chain` | 产业链 |
| P2 | `POST /api/strategy/mcp` | `tool_strategy` | MCP预置策略选股 |
| P2 | `POST /api/filter/mcp` | `tool_filter` | MCP条件选股 |
| P2 | `GET /api/ranking` | `tool_ranking` | 排行榜 |

### MCP调用层设计建议

新增 `server/mcp_client.py` 统一封装MCP工具调用：
- 通过 ToolSearch + DeferExecuteTool 机制调用MCP连接器
- 或通过 westock-mcp 的 HTTP/stdio 接口直接调用
- 统一错误处理 + 超时控制 + 缓存层
- 与现有 `westock.py` (CLI封装) 并存，逐步替代

---

## 7. 待确认问题

| # | 问题 | 影响范围 | 建议 |
|---|------|----------|------|
| Q1 | MCP连接器的调用方式？是通过ToolSearch/DeferExecuteTool机制在后端Python中调用，还是通过westock-mcp的HTTP接口直接调用？ | 架构设计 | 建议架构师确认MCP连接器的后端调用路径，Python后端是否能直接调用MCP工具 |
| Q2 | 板块成分股全量加载后（2000+只），全市场扫描性能是否可接受？是否需要改为按需加载？ | 性能 | 建议分批扫描+按板块预热，核心板块全量加载，非核心板块按需加载 |
| Q3 | 当前前端使用纯HTML/CSS/JS，新增大量Tab和面板后是否需要引入前端框架(如Vue/React)？还是继续原生JS？ | 前端架构 | 建议继续原生JS + 组件化封装，避免引入框架增加复杂度 |
| Q4 | 自选股服务端持久化(P2-9)需要用户认证体系，当前系统是否有用户登录功能？ | 用户系统 | 当前系统有支付/管理后台模块，可能已有用户体系，需确认 |
| Q5 | MCP数据源的调用频率限制？westock-mcp是否有QPS限制？ | 数据源 | 需确认MCP连接器的速率限制，设计合理的缓存TTL |
| Q6 | 龙虎榜数据是盘后数据还是盘中实时？展示时是否需要标注数据日期？ | 数据展示 | 龙虎榜通常盘后更新，需在UI上明确标注日期 |
| Q7 | 股票池动态扩展后，现有的策略评分/智能选股是否需要适配？评分算法是否需要调整？ | 算法适配 | 建议保持现有评分算法不变，动态扩展的股票使用相同评分逻辑 |
| Q8 | 是否需要新增独立的"数据中心"页面来集中展示市场总览/板块排行/龙虎榜/热门股？还是全部嵌入dashboard？ | UI布局 | P0功能建议嵌入dashboard，P2可考虑独立页面 |

---

## 8. 优先级总览

```
P0 (必须有 — 填空壳 + 核心功能)
├── P0-1: 个股资金流向        → data_fund_flow       → #sa-flow-grid 空壳填充
├── P0-2: 板块热度排行        → data_sector          → #sa-hot-sectors 空壳填充
├── P0-3: 龙头股TOP5/龙虎榜   → data_lhb             → #top-stocks-list 空壳填充
├── P0-4: 市场总览            → data_market_overview  → 新增面板
└── P0-5: 热门股/热搜榜       → data_hot              → 新增面板

P1 (应该有 — 基本面 + 扩展覆盖)
├── P1-1: 个股概览            → data_profile
├── P1-2: 财务报表            → data_finance
├── P1-3: 研报/评级           → data_report + data_consensus + data_rating
├── P1-4: 北向资金            → data_north_holding
├── P1-5: 新闻资讯增强        → data_news
└── P1-6: 股票覆盖扩展        → data_sector(constituent) + data_hot + data_lhb

P2 (可以有 — 高级功能)
├── P2-1: 宏观数据            → data_macro
├── P2-2: 筹码分布            → data_chip
├── P2-3: 融资融券            → data_fund_margin + data_fund_short
├── P2-4: 分红送转            → data_dividend
├── P2-5: 股东数据            → data_shareholder
├── P2-6: 事件驱动            → data_events
├── P2-7: 产业链              → data_industry_chain
├── P2-8: MCP策略选股         → tool_strategy + tool_filter + tool_ranking
├── P2-9: 自选股持久化        → portfolio_watchlist
└── P2-10: 模拟交易           → portfolio_paper_*
```

---

## 附录: 现有系统技术栈参考

| 组件 | 技术 | 文件位置 |
|------|------|----------|
| 后端框架 | FastAPI (Python 3.13) | `server/main.py` |
| 行情数据源 | 腾讯HTTP行情 | `server/tencent_kline.py`, `server/realtime.py` |
| CLI数据源 | westock-data CLI (Node.js) | `server/westock.py` |
| 缓存 | 自研内存缓存 | `server/cache.py` |
| AI分析 | DeepSeek V4 Pro | `server/llm.py` |
| 前端 | 原生HTML/CSS/JS + ECharts | `static/dashboard.html`, `static/js/` |
| 板块配置 | 硬编码24板块356股 | `server/config.py` |
| 量化回测 | 自研quant_core | `server/quant_core.py` |
| 支付系统 | 支付宝/微信 | `server/payment/` |
