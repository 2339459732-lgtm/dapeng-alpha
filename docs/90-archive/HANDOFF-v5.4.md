> ⚠️ **已归档 · 版本已落后**
> 本文档对应 **v5.4（2026-07-07）**。项目当前已演进到 **v5.9+**（数据链路补全、自动化交易闭环、AI 主脑、板块轮动粒子图、数据真实性治理等）。
> 其中的版本号、文件清单、API 口径、启动路径均可能已过时。**当前状态以代码与
> [`docs/00-strategy/MASTER-PLAN-2026Q4.md`](../00-strategy/MASTER-PLAN-2026Q4.md) 为准。**
> 保留本文档仅因它是「早期架构快照」，对理解项目演进仍有价值。

---

# DAPENG Alpha v5.4 — AI 接驳手交包

> 给其他 AI 系统继续开发的完整源码上下文。包含项目架构、文件清单、API 列表、启动方式和当前版本状态。

---

## 1. 项目概况

| 项目 | 说明 |
|------|------|
| **名称** | DAPENG Alpha 策略选股量化系统 |
| **版本** | v5.4（刚完成的侧边栏 5 工具深度升级） |
| **路径** | `dapeng-water/` |
| **端口** | `8900`（HTTP Dashboard） |
| **数据源** | `westock-data` CLI（Node.js 脚本）+ DeepSeek API（LLM 决策） |
| **技术栈** | FastAPI (Python 3.13) + 原生 JS/HTML/CSS + ECharts + SQLite |
| **风格** | 暗色玻璃态（Dark Glassmorphism）单页 Dashboard |

---

## 2. 架构概览

```
dapeng-water/
├── server/                     # Python 后端（FastAPI）
│   ├── main.py                 # 入口 + 挂载路由 + 中间件
│   ├── config.py               # 板块配置（17板块, 200+只股票）+ 缓存TTL
│   ├── stock_pool.py           # 三层动态股票池（base/sector/dynamic）
│   ├── cache.py                # TTLCache 实现 + get_or_fetch
│   ├── strategies.py           # 策略评分引擎（动量/突破/背离/交叉/ATR）
│   ├── llm.py                  # DeepSeek API 封装（V3 模型）
│   ├── westock.py              # westock-data CLI 异步子进程封装
│   ├── tencent_kline.py        # 腾讯K线 HTTP 抓取（毫秒级）
│   ├── quant_core.py           # 量化核心（LightGBM 预测）
│   ├── api_tracking.py         # API 调用追踪中间件 + SQLite 异步写入
│   ├── routers/                # 路由模块
│   │   ├── quotes.py           # 板块行情 + 策略评分
│   │   ├── kline.py            # K线 + MA/MACD/RSI/ATR
│   │   ├── scan.py             # 全市场智能选股扫描（200+只）
│   │   ├── market_data.py      # 腾讯自选股 MCP 数据（龙虎榜/热门股/板块排行/资金流/研报等）
│   │   ├── analyze.py          # DeepSeek AI 实时分析
│   │   ├── search.py           # 跨板块搜索
│   │   ├── global_market.py    # 全球指数
│   │   ├── realtime.py         # 实时行情
│   │   ├── agents.py           # Agent 对话接口
│   │   ├── admin.py            # 后台管理 + API 调用监控
│   │   ├── quant.py            # 量化策略
│   │   ├── payment.py          # 支付
│   ├── agents/                 # Agent 模块
│   └── payment/                # 支付模块
├── static/                     # 前端（原生 JS + HTML + CSS）
│   ├── dashboard.html          # 主页面（单页应用，v9）
│   ├── admin.html              # 后台管理页
│   ├── css/dashboard.css       # 全局样式表（96048 bytes, v9）
│   └── js/
│       ├── api.js              # API 请求封装 + 去重/超时/重试（v6）
│       ├── app.js              # 核心应用逻辑（160K, v9, 4354行）
│       ├── market-data.js      # 市场数据模块（热门股/龙虎榜/资金流/板块分析, v7）
│       ├── strategies-ui.js    # 策略评分 UI（分析面板 + 技术指标卡片）
│       ├── chart.js            # ECharts 图表配置
│       ├── admin-monitor.js    # 后台 API 监控
│       ├── echarts.min.js      # ECharts 库（1MB）
│       └── (bundle.js 已迁出 static/js/ → docs/90-archive/bundle.js.deprecated)
│                            # 迁出原因：含 2026-10 修复前的过期代码副本 +
│                            #   showToast/enhancedToast 无限递归旧缺陷；切勿再引入
├── docs/                       # 设计文档
│   ├── incremental-prd-v54-sidebar-pro.md  # v5.4 PRD
│   ├── architecture-v54-sidebar-pro.md     # v5.4 架构设计
│   ├── architecture-sidebar-tools.md       # v5.3 架构设计
│   ├── architecture-tab-admin.md           # v5.1 架构设计
│   └── ...
├── start.bat                   # 启动脚本
├── stop.bat                    # 停止脚本
└── HANDOFF.md                  # ← 本文件
```

---

## 3. 核心 API 列表

### v3.0~v4.0 核心API
| 端点 | 说明 |
|------|------|
| `GET /api/health` | 健康检查 |
| `GET /api/quotes?sector=xxx` | 板块行情 + 策略评分 |
| `GET /api/kline?code=xxx&period=day` | K线 + MA/MACD/RSI/ATR |
| `GET /api/global` | 全球指数 |
| `GET /api/search?keyword=xxx` | 跨板块搜索 |
| `GET /api/screen?min_score=60&min_chg=0&min_turnover=1&max_pe=50&limit=30` | 条件选股（v5.4 新增 min_turnover/min_vol/max_pe） |
| `GET /api/analyze?code=xxx` | DeepSeek AI 实时分析 |
| `GET /api/scan?sectors=xx&price_min=10&price_max=200` | 全市场智能选股 |

### v5.0+ 腾讯自选股数据API
| 端点 | 说明 |
|------|------|
| `GET /api/sector-ranking` | 31 个申万一级板块排行（含5日/20日/60日/120日/250日%） |
| `GET /api/sector-stocks?sector=power_equip` | 板块成分股（sector 需传英文 key）|
| `GET /api/fund-flow/sector?sector=xxx` | 板块资金流向 |
| `GET /api/hot-stocks?type=stock|etf&limit=50` | 热门股/热门ETF排行 |
| `GET /api/lhb?tab=jg|yzb|yyb|gslmr|gslxw` | 龙虎榜（机构/一日榜/营业部/个股买卖/龙虎新闻）|
| `GET /api/market-overview` | 市场涨跌分布 |
| `GET /api/profile?code=xxx` | 个股概况 |
| `GET /api/finance?code=xxx&ftype=income` | 财务报表 |
| `GET /api/report?code=xxx` | 研报列表 |
| `GET /api/consensus?code=xxx` | 分析师评级 |

### v5.1 后台管理
| 端点 | 说明 |
|------|------|
| `GET /api/admin/stats` | API 调用统计 |
| `GET /api/admin/calls?page=1` | 调用明细分页 |
| `GET /api/admin/error-ranking` | 错误率排行 |

---

## 4. 板块配置（17 个板块，200+ 只股票）

| key | 名称 | 股票数 |
|-----|------|--------|
| semiconductor | 半导体 | 18 |
| new_energy | 新能源 | 16 |
| pharma | 医药 | 16 |
| consumer | 消费 | 16 |
| finance | 金融 | 15 |
| military | 军工 | 15 |
| ai_compute | AI算力 | 15 |
| photovoltaic | 光伏 | 10 |
| internet | 互联网 | 11 |
| real_estate | 房地产 | 15 |
| steel_material | 钢铁有色 | 15 |
| auto | 汽车 | 13 |
| power_equip | 电力设备 | 15 |
| telecom | 通信 | 14 |
| etf | ETF基金 | 13 |
| cyber | 网络安全 | 10 |
| consumer_electronics | 消费电子 | 9 |

---

## 5. 启动方式

```bash
cd dapeng-water/

# 方式1：start.bat（推荐）
start.bat

# 方式2：手动启动（解释器由启动脚本自动探测：优先项目 .venv，回退系统 Python 3.11）
# 将 <PY> 替换为本机可用解释器（需已装 fastapi/uvicorn）
<PY> -m uvicorn server.main:app --host 127.0.0.1 --port 8900
```

访问：
- Dashboard: `http://localhost:8900/dashboard.html`
- 后台管理: `http://localhost:8900/admin.html`

---

## 6. 当前版本状态（v5.4 — 2026-07-07）

### 最新改动（v5.4）
| 文件 | 版本号 | 改动 |
|------|--------|------|
| `dashboard.html` | v9 | 板块分析面板重写（周期Tab + 涨幅榜 + 详情 + 成分股弹层）；选股器5条件；行业checkbox |
| `dashboard.css` | v9 | 新增 ~150 行样式（sector-period-tabs / sa-fundflow / screen-input-group / sector-checkbox-group / stock-detail-popover 等）|
| `app.js` | v9 | 重写 toggleSectorAnalysisPanel + loadSectorRankingData + renderSectorRankingTable + renderSectorDetail + showSectorStocks + switchSectorPeriod；改造 doScreenStocks；新增 loadScanIndustries |
| `api.js` | v6 | fetchScreen 多参数（minVol/minTurnover/maxPe）；fetchScan opts 模式；新增 fetchSectorStocks/fetchSectorFundFlow |
| `market-data.js` | v7 | showStockDetailPopover 弹卡；getLHBSelectedDate 日期选择；扩展 ESC 监听器 |

### 侧边栏 5 工具
| 工具 | 功能状态 |
|------|----------|
| 🔍 选股器 | 5条件筛选（评分/涨幅/换手率/量比/PE）+ 独立全屏结果面板 |
| 🤖 智能选股 | 全市场扫描 + 行业 checkbox 过滤 + 评分排序 |
| 📊 板块分析 | **完全重构**：31板块排行 + 5周期切换 + 详情展开 + 资金流 + 成分股弹层 |
| 🔥 热门股 | 50条排行 + 股票/ETF tab + 详情弹卡 |
| 📜 龙虎榜 | 5个tab + 日期选择器 |

### 已知限制
- `GET /api/screen` 的 `min_turnover/min_vol/max_pe` 参数后端可能未完全实现，需验证
- 板块资金流 API 常返回空，前端有本地估算 fallback
- `/api/sector-stocks` 仅返回 `{code, name, priority}`，缺涨跌幅/换手率等字段

---

## 7. 关键技术约定

- **sector key**：API 中 `sector-stocks` 和 `fund-flow/sector` 需传英文 key（如 `power_equip`），而非中文名
- **版本号管理**：静态资源用 `?v=N` 参数控制缓存，dashboard.html 中统一声明
- **ESC 关闭**：market-data.js:1016 处有统一的 ESC 监听器，按优先级关闭弹层
- **加载/空/错误三态**：`_renderTableSkeleton()` / `_renderTableEmpty()` / `_renderTableError()` 三个通用函数
- **全屏覆盖面板**：`.full-overlay-panel` class 统一样式，5 个面板复用
- **DeepSeek API**：config.py 中 `AGENT_CONFIG.deepseek_api_key`，模型 `deepseek-chat`
- **CLI 超时**：westock-data 调用超时 25 秒，并发信号量 Semaphore(3)
- **Python 解释器**：由启动脚本自动探测（优先项目 `.venv`，回退系统 Python 3.11），不再写死 venv 绝对路径
- **Node 路径**：`~/.workbuddy\binaries\node\versions\22.22.2\node.exe`

---

## 8. 给接续 AI 的提示

1. **先读本文件**理解项目架构
2. **核心入口**：`server/main.py`（后端路由挂载）+ `static/dashboard.html`（前端单页）
3. **最常改动的文件**：`app.js`（核心逻辑）、`market-data.js`（市场数据）、`api.js`（请求封装）、`dashboard.css`（样式）
4. **服务已在运行**：先 `curl http://localhost:8900/api/health` 确认
5. **静改文件不需要重启**：HTML/CSS/JS 修改后浏览器刷新即可，Python 修改需重启 uvicorn
6. **版本号务必递增**：避免浏览器缓存旧代码
