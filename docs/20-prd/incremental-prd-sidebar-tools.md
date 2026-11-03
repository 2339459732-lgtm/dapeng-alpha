# DAPENG Alpha 增量PRD — Sidebar 5 工具功能补全

> ⚠️ **状态更新（2026-10-16）** —— 文中基线所述「westock-mcp 接入」的 CLI 链路已废弃，相关端点现返回 `null`；本文其余内容（侧边栏 5 工具）为历史需求参考。

> **版本**: v5.3-incremental | **日期**: 2025-07-13 | **作者**: 产品经理 Alice
> **基线**: v5.2 (westock-mcp 接入完成) | **范围**: 左侧导航 5 个工具按钮的功能补全与 UI 重构
> **原始需求**: "sidebar 的 5 个工具按钮仍然有缺陷，并不能真正使用，要求排版设计并补全功能"

---

## 1. 背景与目标

### 1.1 现状分析（代码排查结论）

经逐文件排查 `static/dashboard.html` + `static/js/app.js` + `static/js/market-data.js` + `static/js/strategies-ui.js` + `static/js/api.js`，5 个工具按钮的真实状态如下：

| # | 按钮 | 入口函数 | 函数位置 | 状态 | 核心缺陷 |
|---|------|----------|----------|------|----------|
| 1 | 🔍 选股器 | `toggleScreenPanel()` | app.js:1137 | ⚠️ 半可用 | 有筛选输入框 + `doScreenStocks()` 可调用 `/api/screen`，但结果直接灌入普通 `#stock-list`，无独立结果表、无加载骨架、无"返回板块"入口 |
| 2 | 🤖 智能选股 | `showScanPanel()` | app.js:1370 | ⚠️ 半可用 | 全屏面板 + 轮询进度 + 推荐卡片列表基本完整，但卡片密度过高、无按评分排序、卡片点击不可选股 |
| 3 | 📊 板块分析 | `toggleSectorAnalysisPanel()` | app.js:3269 | ⚠️ 半可用 | 面板可打开，`renderRealSectorAnalysis()` 用**本地股池数据**计算统计（非 `/api/sector-ranking`），`updateSectorAnalysisPanel()` 调 MCP 数据但渲染目标容器与本地数据冲突 |
| 4 | 🔥 热门股 | `showHotStocksPanel()` | market-data.js:641 | ❌ 不可用 | 函数存在但**复用板块分析面板**，将 Top 10 热门股渲染到 `#top-stocks-list`（实为"龙头股 TOP 5"容器），无独立面板、仅 10 条（API 支持 50）、无 stock/fund 类型切换 |
| 5 | 📜 龙虎榜 | `showLHBPanel()` | market-data.js:659 | ❌ 不可用 | 函数存在但**复用板块分析面板**，仅渲染 Top 5 行到 `#top-stocks-list`，无 tab 切换（jg/yzb/yyb/gslmr/gslxw）、无日期选择、无完整明细表 |

**根本问题**：热门股和龙虎榜没有自己的独立面板，而是"寄生"在板块分析面板的 `#top-stocks-list` 容器中，导致打开后标题仍显示"龙头股 TOP 5"，数据量极少，用户体验极差。选股器和板块分析虽有逻辑但数据源/渲染目标存在问题。

### 1.2 产品目标

| # | 目标 | 衡量标准 |
|---|------|----------|
| G1 | **5 个工具全部可用** — 点击即可在 3 秒内加载并展示真实数据 | 5/5 面板点击后显示有效数据，无空壳、无报错 |
| G2 | **热门股/龙虎榜独立面板** — 不再寄生在板块分析面板 | 新增 2 个全屏覆盖式面板，各自有标题、数据区、关闭按钮 |
| G3 | **数据量达标** — 每个面板展示足够信息量供决策 | 热门股 ≥30 条、龙虎榜按 tab 全量展示、选股器结果 ≥20 条、板块排行全量 |

---

## 2. 用户故事

| # | 角色 | 故事 |
|---|------|------|
| US1 | 作为个人投资者，我希望在选股器输入最低评分和涨幅条件后，立即看到符合条件的股票结果列表（含代码、名称、评分、涨幅、现价），以便快速筛选标的 |
| US2 | 作为个人投资者，我希望选股结果在独立的结果表格中展示（而非替换板块股池），以便随时返回原板块查看 |
| US3 | 作为个人投资者，我希望点击"热门股"后看到全市场 Top 50 热门股票排行（含名称、代码、涨跌幅、现价、状态），以便了解市场关注度最高的标的 |
| US4 | 作为个人投资者，我希望在热门股面板中切换"股票/基金"类型，以便分别查看股票热榜和基金热榜 |
| US5 | 作为个人投资者，我希望点击"龙虎榜"后看到今日龙虎榜明细，支持切换"机构席位/一日榜/三日榜/五日榜/个股买卖明细"tab，以便跟踪聪明资金动向 |
| US6 | 作为个人投资者，我希望龙虎榜每行显示股票名称、净买入额、上榜原因、席位数，以便快速判断资金意图 |
| US7 | 作为个人投资者，我希望板块分析显示全市场板块排行（来自 API 真实数据，非本地股池），含涨跌幅、成交额、领涨股，以便发现市场热点 |
| US8 | 作为个人投资者，我希望点击板块排行中的某个板块后，能看到该板块的成分股列表，以便进一步研究 |
| US9 | 作为个人投资者，我希望智能选股扫描完成后，推荐卡片按评分从高到低排序，以便优先关注高分标的 |
| US10 | 作为个人投资者，我希望所有表格行点击后能切换到该股票的 K 线图，以便无缝衔接分析流程 |

---

## 3. 需求池

### P0 — 必须完成（功能补全 + 独立面板）

#### P0-1: 🔥 热门股 — 独立全屏面板 + Top 50 表格

| 属性 | 说明 |
|------|------|
| **需求描述** | 新增 `#hot-stocks-panel` 全屏覆盖面板，调用 `GET /api/hot-stocks?type=stock&limit=50` 渲染完整表格 |
| **入口函数** | 重写 `showHotStocksPanel()`：打开独立面板 `#hot-stocks-panel`（不再复用 sector-analysis-panel） |
| **面板结构** | 标题栏 "🔥 热门股排行" + 类型切换 tab（股票/基金）+ 关闭按钮；下方表格区 |
| **表格列** | 排名、股票名称、代码、涨跌幅(%)、现价、状态标签 |
| **API** | `GET /api/hot-stocks?type={stock\|fund}&limit=50` → `{ok, data:[{code, name, zdf, zxj, status, stock_type}]}` |
| **交互** | 行点击 → `selectStock(code, name)` 切换 K 线；类型 tab 切换 → 重新请求 |
| **三态** | 加载中：shimmer 骨架；空：空状态提示；错误：错误提示 + 重试按钮 |
| **新增 DOM** | `dashboard.html` 新增 `<div id="hot-stocks-panel" class="full-overlay-panel">` |
| **新增 CSS** | `.full-overlay-panel` 通用全屏面板样式（暗色玻璃态，可复用于龙虎榜） |

#### P0-2: 📜 龙虎榜 — 独立全屏面板 + Tab 切换 + 明细表

| 属性 | 说明 |
|------|------|
| **需求描述** | 新增 `#lhb-panel` 全屏覆盖面板，调用 `GET /api/lhb?tab={tab}` 渲染席位明细表 |
| **入口函数** | 重写 `showLHBPanel()`：打开独立面板 `#lhb-panel`（不再复用 sector-analysis-panel） |
| **面板结构** | 标题栏 "📜 龙虎榜" + 5 个 tab（机构席位/一日榜/三日榜/五日榜/个股买卖明细）+ 关闭按钮；下方表格区 |
| **Tab 映射** | 机构席位→`tab=jg`；一日榜→`tab=yzb`；三日榜→`tab=gsyb`（待确认）；五日榜→`tab=gslxw`；个股买卖明细→`tab=gslmr` |
| **表格列** | 排名、股票名称、代码、净买入额、上榜原因、席位数（字段根据 API 返回动态适配） |
| **API** | `GET /api/lhb?tab={tab}` → `{ok, data:{tab, rows:[...]}}` |
| **交互** | tab 切换 → 重新请求对应 tab 数据；行点击 → `selectStock(code, name)` 切换 K 线 |
| **三态** | 加载中：shimmer 骨架；空：空状态提示；错误：错误提示 + 重试按钮 |
| **新增 DOM** | `dashboard.html` 新增 `<div id="lhb-panel" class="full-overlay-panel">` |

#### P0-3: 🔍 选股器 — 补全结果展示 + 加载状态 + 返回入口

| 属性 | 说明 |
|------|------|
| **需求描述** | `doScreenStocks()` 已可调用 `/api/screen` 并将结果灌入 `#stock-list`，但需增强：加载骨架、结果计数标题、清除筛选返回板块 |
| **增强点 1** | 点击"选股"后立即在 `#stock-list` 区域显示加载骨架（非空白） |
| **增强点 2** | 结果加载后，`#sector-title-name` 显示"选股结果 (N只)"（已有此逻辑，确认生效） |
| **增强点 3** | 在 `#screen-panel` 区域增加"清除筛选"按钮，点击后恢复当前板块股池 |
| **增强点 4** | 确认 `/api/screen` 返回的 `results` 数组字段（code, name, score, chg, price）能正确映射到 `renderStockList()` 的数据结构 |
| **API** | `GET /api/screen?min_score=&min_chg=&limit=30` → `{ok, data:{results:[...], total}}` |
| **交互** | 结果行点击 → 已有 `selectStock()` 逻辑（确认生效） |

#### P0-4: 📊 板块分析 — 接入真实 API 数据 + 修复渲染冲突

| 属性 | 说明 |
|------|------|
| **需求描述** | 当前 `renderRealSectorAnalysis()` 用本地 `window.appState.stocks` 计算板块统计，`updateSectorAnalysisPanel()` 调 MCP API 但渲染目标容器冲突。需统一为 API 数据驱动 |
| **修复点 1** | `#sa-hot-sectors`（板块热度排行）改为从 `GET /api/sector-ranking` 获取真实全市场板块排行数据，不再用本地股池计算 |
| **修复点 2** | 确认 `loadSectorRanking()` → `renderSectorRanking()` 将 API 数据正确渲染到 `#sa-hot-sectors` |
| **修复点 3** | `#sa-flow-grid`（资金流向概览）确认有数据填充（来自 `loadSectorFundFlow` 或 fallback） |
| **修复点 4** | `#top-stocks-list`（龙头股 TOP 5）保留为板块分析自身的"当前板块领涨股"，不再被热门股/龙虎榜复用 |
| **修复点 5** | 涨跌分布 `#sa-distribution` 改为从 API sector-ranking 的 up_count/dn_count 字段渲染（如 API 提供），否则保留本地计算 |
| **交互** | 板块排行项点击 → `switchSector(key)` 切换到该板块股池 |
| **API** | `GET /api/sector-ranking` → `{ok, data:{sectors:[{name, chg_pct, amount, leader_stock, ...}]}}` |

#### P0-5: 🤖 智能选股 — UI/UX 微调

| 属性 | 说明 |
|------|------|
| **需求描述** | 智能选股面板基本可用，但需微调提升易用性 |
| **微调 1** | 推荐卡片列表默认按 `total_score` 降序排列（当前无序） |
| **微调 2** | 卡片点击 → `selectStock(code, name)` 切换 K 线 + 自动关闭扫描面板（或最小化） |
| **微调 3** | 卡片增加"评分"排序切换按钮（降序/升序） |
| **微调 4** | 扫描中禁用价格区间输入（防误改） |
| **微调 5** | 统计卡片区增加"平均评分"指标 |

### P1 — 应该完成（增强体验）

| # | 需求 | 说明 |
|---|------|------|
| P1-1 | 表格行点击 → 切换 K 线图 | 热门股/龙虎榜/选股器/板块分析 表格行统一支持点击切换 K 线 |
| P1-2 | 表格排序栏 | 热门股表格支持按涨跌幅/现价排序；选股器结果支持按评分/涨幅排序 |
| P1-3 | 智能选股结果排序 | 推荐卡片支持按评分/涨幅/换手率排序切换 |
| P1-4 | 龙虎榜日期选择 | 增加日期选择器，支持查看历史龙虎榜（如 API 支持 `date` 参数） |
| P1-5 | 热门股状态标签颜色化 | "涨停"红色高亮、"跌停"绿色高亮、"停牌"灰色 |
| P1-6 | 板块分析 — 板块成分股下钻 | 点击板块排行项 → 展开该板块成分股列表（调 `/api/sector-stocks?sector=xxx`） |

### P2 — 可选（锦上添花）

| # | 需求 | 说明 |
|---|------|------|
| P2-1 | 收藏股票功能 | 表格行增加星标收藏按钮，收藏后进入"自选"tab |
| P2-2 | 表格列宽拖拽 | 全市场表格支持列宽拖拽调整 |
| P2-3 | 热门股/龙虎榜数据导出 | 支持 CSV 导出当前表格数据 |
| P2-4 | 板块分析热力图 | 板块排行以热力图形式展示（色块大小=成交额，颜色=涨跌幅） |

---

## 4. UI 设计规范

### 4.1 设计原则

- **暗色玻璃态**：与现有 dashboard 保持一致，背景 `rgba(15, 23, 42, 0.95)` + `backdrop-filter: blur(12px)`
- **全屏覆盖式面板**：热门股/龙虎榜使用 `position:fixed; inset:0; z-index:1000` 全屏覆盖，与 `#scan-panel` 风格一致
- **表格统一风格**：表头浅灰 `rgba(255,255,255,0.06)`、斑马纹 `nth-child(even)` 微亮、悬停高亮 `rgba(59,130,246,0.08)`、点击行选中态 `rgba(59,130,246,0.15)`
- **三态完整**：每个数据区都有加载(shimmer 骨架)、空(图标+提示文字)、错误(错误信息+重试按钮)三态

### 4.2 通用全屏面板结构（`.full-overlay-panel`）

```
┌─────────────────────────────────────────────────────┐
│  🔥 标题          [Tab1] [Tab2] [Tab3]         ✕   │  ← header (sticky)
├─────────────────────────────────────────────────────┤
│                                                     │
│  ┌────┬──────┬──────┬──────┬──────┬──────┐         │
│  │排名│ 名称 │ 代码 │ 涨幅 │ 现价 │ 状态 │  ...    │  ← table header
│  ├────┼──────┼──────┼──────┼──────┼──────┤         │
│  │ 1  │ xxx  │ xxx  │+5.2% │ 12.3 │ 涨停 │         │  ← row (clickable)
│  │ 2  │ xxx  │ xxx  │+3.1% │ 8.5  │  --  │         │
│  │... │ ...  │ ...  │ ...  │ ...  │ ...  │         │
│  └────┴──────┴──────┴──────┴──────┴──────┘         │
│                                                     │
└─────────────────────────────────────────────────────┘
```

### 4.3 配色映射

| 元素 | 颜色 |
|------|------|
| 涨幅 > 0 | `var(--green)` #00e96a |
| 涨幅 < 0 | `var(--red)` #ff3d6a |
| 涨停标签 | 背景 `rgba(255,61,106,0.15)` + 文字 `var(--red)` |
| 跌停标签 | 背景 `rgba(0,233,106,0.15)` + 文字 `var(--green)` |
| 停牌标签 | 背景 `rgba(148,163,184,0.15)` + 文字 `var(--muted)` |
| 机构席位标签 | 背景 `rgba(245,158,11,0.15)` + 文字 `var(--gold)` |
| 游资席位标签 | 背景 `rgba(6,182,212,0.15)` + 文字 `var(--cyan)` |

### 4.4 响应式

- 面板宽度：`max-width: 1200px; margin: 0 auto`（居中，两侧留暗色背景）
- 表格水平滚动：`overflow-x: auto`，列最小宽度 80px
- 移动端：表格简化为卡片列表布局（P2 可选）

---

## 5. 待确认问题

以下问题需主理人决策（如无特殊说明，按括号内默认值执行）：

| # | 问题 | 默认决策 |
|---|------|----------|
| Q1 | 龙虎榜 tab 映射：`yzb`=一日榜，但"三日榜/五日榜"对应 API 的哪个 tab 值？team-lead 提到可选 `jg/yzb/yyb/gslmr/gslxw`，其中 `yyb` 是否=三日榜？`gslxw` 是否=五日榜？ | 默认按字面映射：jg=机构, yzb=一日, yyb=三日(假设), gslmr=个股买卖, gslxw=个股龙虎榜新闻；开发时按 API 实际返回适配 |
| Q2 | 热门股 `/api/hot-stocks` 的 `type` 参数除 `stock`/`fund` 外是否还有其他值？ | 默认只做 stock/fund 两个 tab |
| Q3 | 选股器是否需要增加更多筛选条件（如板块、换手率、市盈率）？ | P0 不增加，保持最低评分+最低涨幅两个条件；P1 可扩展 |
| Q4 | 板块分析的 `renderRealSectorAnalysis()` 中本地计算的板块统计（涨跌分布、资金流向概览）是否保留，还是全部替换为 API 数据？ | 保留本地计算作为 fallback，优先使用 API 数据；如果 API 返回了对应字段则覆盖本地值 |
| Q5 | 智能选股卡片点击后是否自动关闭扫描面板？ | 默认关闭（`closeScanPanel()`），让用户看到 K 线图 |
| Q6 | 全屏面板是否支持 ESC 键关闭？ | 默认支持，增加 `keydown` 监听 |

---

## 6. 验收标准

### 6.1 功能验收

| # | 验收项 | 通过标准 |
|---|--------|----------|
| AC1 | 🔥 热门股面板 | 点击侧边栏"🔥 热门股"→ 3 秒内打开独立全屏面板，显示 ≥30 条热门股数据表格 |
| AC2 | 🔥 热门股类型切换 | 点击"基金"tab → 表格刷新为基金热门榜；点击"股票"tab → 切回股票 |
| AC3 | 🔥 热门股行点击 | 点击表格行 → 面板关闭 + K 线图切换到该股票 |
| AC4 | 📜 龙虎榜面板 | 点击侧边栏"📜 龙虎榜"→ 3 秒内打开独立全屏面板，默认显示机构席位 tab 数据 |
| AC5 | 📜 龙虎榜 tab 切换 | 点击不同 tab → 表格刷新为对应数据；至少 3 个 tab 有有效数据 |
| AC6 | 📜 龙虎榜行点击 | 点击表格行 → 面板关闭 + K 线图切换到该股票 |
| AC7 | 🔍 选股器 | 输入条件 + 点击"选股"→ 3 秒内显示加载骨架 → 结果列表填充到 `#stock-list` |
| AC8 | 🔍 选股器清除 | 点击"清除筛选"→ 恢复当前板块股池 |
| AC9 | 📊 板块分析 | 点击"📊 板块分析"→ 面板打开，板块热度排行区显示 API 真实数据（非本地空壳） |
| AC10 | 📊 板块分析下钻 | 点击板块排行项 → 切换到该板块股池（P1 验收） |
| AC11 | 🤖 智能选股排序 | 扫描完成后卡片默认按评分降序排列 |
| AC12 | 🤖 智能选股卡片点击 | 点击推荐卡片 → 扫描面板关闭 + K 线切换 |
| AC13 | 三态完整性 | 所有 5 个面板在网络异常时显示错误提示+重试按钮，无数据时显示空状态 |
| AC14 | ESC 关闭面板 | 热门股/龙虎榜面板打开时按 ESC → 面板关闭 |

### 6.2 性能验收

| # | 验收项 | 通过标准 |
|---|--------|----------|
| P1 | 面板首次加载 | 点击到数据显示 ≤ 3 秒（API 正常情况下） |
| P2 | tab 切换响应 | tab 切换到新数据显示 ≤ 2 秒 |
| P3 | 表格渲染性能 | 50 行表格渲染 ≤ 200ms，无卡顿 |

---

## 7. 技术实现要点（供架构师参考）

### 7.1 新增 DOM 结构

```html
<!-- 热门股全屏面板 -->
<div id="hot-stocks-panel" class="full-overlay-panel" style="display:none;">
  <div class="full-overlay-header">
    <h2>🔥 热门股排行</h2>
    <div class="hot-tabs">
      <div class="hot-tab active" data-type="stock" onclick="switchHotTab('stock')">股票</div>
      <div class="hot-tab" data-type="fund" onclick="switchHotTab('fund')">基金</div>
    </div>
    <button class="full-overlay-close" onclick="closeHotStocksPanel()">✕</button>
  </div>
  <div class="full-overlay-body">
    <div id="hot-stocks-table"></div>
  </div>
</div>

<!-- 龙虎榜全屏面板 -->
<div id="lhb-panel" class="full-overlay-panel" style="display:none;">
  <div class="full-overlay-header">
    <h2>📜 龙虎榜</h2>
    <div class="lhb-tabs">
      <div class="lhb-tab active" data-tab="jg" onclick="switchLHBTab('jg')">机构席位</div>
      <div class="lhb-tab" data-tab="yzb" onclick="switchLHBTab('yzb')">一日榜</div>
      <div class="lhb-tab" data-tab="yyb" onclick="switchLHBTab('yyb')">三日榜</div>
      <div class="lhb-tab" data-tab="gslmr" onclick="switchLHBTab('gslmr')">个股买卖</div>
      <div class="lhb-tab" data-tab="gslxw" onclick="switchLHBTab('gslxw')">龙虎新闻</div>
    </div>
    <button class="full-overlay-close" onclick="closeLHBPanel()">✕</button>
  </div>
  <div class="full-overlay-body">
    <div id="lhb-table"></div>
  </div>
</div>
```

### 7.2 函数改造清单

| 函数 | 文件 | 改造内容 |
|------|------|----------|
| `showHotStocksPanel()` | market-data.js | 重写：打开 `#hot-stocks-panel`，调用 `loadHotStocksFull('stock', 50)` |
| `showLHBPanel()` | market-data.js | 重写：打开 `#lhb-panel`，调用 `loadLHBFull('jg')` |
| `loadHotStocks()` | market-data.js | 改为 `loadHotStocksFull(type, limit)`，limit=50，渲染到 `#hot-stocks-table` |
| `renderHotStocks()` | market-data.js | 改为渲染完整表格（排名/名称/代码/涨幅/现价/状态），目标 `#hot-stocks-table` |
| `loadLHBTop5()` | market-data.js | 改为 `loadLHBFull(tab)`，渲染全量行到 `#lhb-table` |
| `renderLHBTop5()` | market-data.js | 改为 `renderLHBFull(data, tab)`，渲染完整表格 |
| `doScreenStocks()` | app.js:1110 | 增强：加载骨架 + 清除筛选按钮逻辑 |
| `toggleScreenPanel()` | app.js:1137 | 无需改，保持 toggle 行为 |
| `renderRealSectorAnalysis()` | app.js:3320 | 修改：`#sa-hot-sectors` 优先用 API 数据，本地计算作 fallback |
| `startScan()` / `renderScanResults()` | app.js:1394/1539 | 微调：卡片排序 + 点击选股 + 关闭面板 |
| **新增** `closeHotStocksPanel()` | market-data.js | 关闭热门股面板 |
| **新增** `closeLHBPanel()` | market-data.js | 关闭龙虎榜面板 |
| **新增** `switchHotTab(type)` | market-data.js | 切换热门股类型 |
| **新增** `switchLHBTab(tab)` | market-data.js | 切换龙虎榜 tab |

### 7.3 CSS 新增

```css
/* 通用全屏覆盖面板 */
.full-overlay-panel {
  position: fixed; inset: 0; z-index: 1000;
  background: rgba(15, 23, 42, 0.95);
  backdrop-filter: blur(12px);
  display: flex; flex-direction: column;
  padding: 20px;
}
.full-overlay-header {
  display: flex; align-items: center; gap: 16px;
  padding-bottom: 16px; border-bottom: 1px solid rgba(255,255,255,0.08);
}
.full-overlay-body { flex: 1; overflow-y: auto; margin-top: 16px; }
.full-overlay-close { margin-left: auto; /* ... */ }

/* 统一表格样式 */
.data-table { width: 100%; border-collapse: collapse; }
.data-table th { background: rgba(255,255,255,0.06); padding: 10px 12px; text-align: left;
  font-size: 12px; color: var(--text2); position: sticky; top: 0; }
.data-table td { padding: 10px 12px; border-bottom: 1px solid rgba(255,255,255,0.04); }
.data-table tr:nth-child(even) { background: rgba(255,255,255,0.02); }
.data-table tr:hover { background: rgba(59,130,246,0.08); cursor: pointer; }
.data-table tr.selected { background: rgba(59,130,246,0.15); }
```

---

## 8. 不在本次范围

- 后端 API 新增/修改（本次仅消费已有 API）
- dashboard 整体布局重构
- 板块分析面板的热力图可视化（P2）
- 收藏功能的后端持久化（P2）
- 移动端适配（P2）
