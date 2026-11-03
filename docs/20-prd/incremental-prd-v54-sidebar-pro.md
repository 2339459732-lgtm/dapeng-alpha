# DAPENG Alpha 增量PRD — Sidebar 5 工具深度升级（v5.4）

> 📎 **历史设计（v5.4）** — 本文描述的是**该版本当时的需求设计**；**现状以 [`MASTER-PLAN-2026Q4.md`](../00-strategy/MASTER-PLAN-2026Q4.md) 与 [`DATA-DICTIONARY.md`](../30-data/DATA-DICTIONARY.md) 为准**。正文保留作演进记录。

> **版本**: v5.4-incremental | **日期**: 2025-07-14 | **作者**: 产品经理 Alice
> **基线**: v5.3（sidebar 5 工具基础架构已完成，独立面板已建立）
> **范围**: 5 个工具的功能深度升级，**核心：板块分析完全重构**
> **原始需求**: "这几个的功能并不齐全和高级，特别是板块分析根本没点用"

---

## 1. 背景与目标

### 1.1 v5.3 现状回顾

v5.3 已完成 5 工具的基础架构建设：热门股/龙虎榜已从板块分析面板独立为全屏覆盖面板（`.full-overlay-panel`），选股器/板块分析/智能选股的基本功能已跑通。但用户反馈"功能并不齐全和高级"，经排查确认以下核心短板：

| 工具 | v5.3 达成 | v5.4 需补齐的短板 |
|------|-----------|-------------------|
| 🔍 选股器 | 评分+涨幅 2 条件，结果灌入 `#stock-list` | 缺换手率/量比/市值/PE/成交额筛选条件；无独立结果面板（结果与板块股池混用） |
| 🤖 智能选股 | 全市场扫描 + 推荐卡片排序 | 缺行业/概念过滤；无价格区间预填；结果卡片缺详情摘要 |
| 📊 板块分析 | 接入 `/api/sector-ranking`，31 板块展示 | **核心痛点**：只显示涨幅 1 列；缺多周期切换（5日/20日/60日/120日/250日）；缺板块成分股二级面板；缺板块资金流；缺板块走势对比；交互停留在"点击切换板块股池"层面，无深度分析能力 |
| 🔥 热门股 | 50 条 + 股票/基金 tab | 缺涨跌幅/换手率筛选；缺行业标签；行点击只能切 K 线无详情摘要 |
| 📜 龙虎榜 | 5 tab 切换可用 | 缺净买入额筛选；缺日期选择；缺个股关联跳转 |

### 1.2 产品目标

| # | 目标 | 衡量标准 |
|---|------|----------|
| G1 | **板块分析成为"板块投资决策面板"** — 从"展示涨幅"升级为"多周期+成分股+资金流+龙头"的完整决策链 | 5 周期切换 < 500ms；成分股列表 ≤ 20 只可点击切 K 线；资金流可视化展示 |
| G2 | **选股器支持 5 维度筛选** — 从 2 条件升级为评分/涨幅/换手率/量比/PE 多维筛选 | 5 个筛选条件全部生效，独立结果面板 3 秒内显示 |
| G3 | **智能选股支持行业过滤** — 从全市场扫描升级为可按行业/概念精准扫描 | 行业多选过滤生效，过滤后结果仅包含选中行业标的 |

---

## 2. 用户故事

| # | 角色 | 故事 |
|---|------|------|
| US1 | 个人投资者 | 作为个人投资者，我希望在板块分析面板**一键切换 5日/20日/60日/120日/250日 周期**，看到不同时段的板块涨跌排名，以便判断短中长期趋势 |
| US2 | 个人投资者 | 作为个人投资者，我希望点击**任一板块看到成分股列表**（最多 20 只），点击成分股切换 K 线，以便深入研究板块内个股 |
| US3 | 个人投资者 | 作为个人投资者，我希望看到**板块资金流向**（主力/超大单/大单/中单/小单），以便判断资金是在流入还是流出该板块 |
| US4 | 个人投资者 | 作为个人投资者，我希望选股器支持**换手率/量比/PE** 多维度筛选，以便筛选出活跃度合适、估值合理的标的 |
| US5 | 个人投资者 | 作为个人投资者，我希望选股结果在**独立面板**中展示（不替换板块股池），以便同时对比板块和筛选结果 |
| US6 | 个人投资者 | 作为个人投资者，我希望智能选股支持**行业/概念多选过滤**，以便精准扫描特定行业的投资机会 |
| US7 | 个人投资者 | 作为个人投资者，我希望板块分析表格**点击列头可排序**（涨跌幅升/降序），以便快速找到最强/最弱板块 |
| US8 | 个人投资者 | 作为个人投资者，我希望热门股行右侧有**"详情"按钮**，点击弹出股票简况卡（含行业/市值/PE/换手率），以便快速了解个股基本面 |
| US9 | 个人投资者 | 作为个人投资者，我希望龙虎榜支持**日期选择**，以便查看历史龙虎榜数据 |
| US10 | 个人投资者 | 作为个人投资者，我希望板块分析选中板块后看到**龙头股 TOP 5**（带涨跌幅），以便快速识别板块领涨标的 |

---

## 3. 需求池

### P0 — 必须完成

#### P0-1: 📊 板块分析完全重构（核心需求，详见第 4 节）

| 属性 | 说明 |
|------|------|
| **需求描述** | 板块分析面板从"单列涨幅展示"完全重构为"多周期+排行+详情+成分股+资金流"的板块投资决策面板 |
| **优先级** | P0-最高 — 用户明确反馈"板块分析根本没点用" |
| **影响范围** | DOM 重写 + JS 重写 + CSS 新增 |

#### P0-2: 🔍 选股器升级 — 新增 3 筛选条件 + 独立结果面板

| 属性 | 说明 |
|------|------|
| **需求描述** | 选股器从 2 条件（评分+涨幅）升级为 5 条件（评分+涨幅+换手率+量比+PE），结果从灌入 `#stock-list` 改为独立全屏面板展示 |
| **新增条件** | ① 换手率 ≥ N%（`min_turnover` 参数）② 量比 ≥ N（`min_vol` 参数，v5.3 已有但未暴露）③ PE ≤ N（`max_pe` 参数） |
| **独立面板** | 沿用 `.full-overlay-panel` 模式新建 `#screen-result-panel`，标题"选股结果 (N只)"，表格列：代码/名称/评分/涨幅/现价/换手率/PE |
| **API** | `GET /api/screen?min_score=&min_chg=&min_vol=&min_turnover=&max_pe=&limit=30` |
| **交互** | 行点击 → `selectStock(code, name)` 切换 K 线 + 关闭面板；"重新筛选"按钮 → 返回条件区 |
| **默认值** | 所有新条件默认留空（不填=不过滤），用户按需填写 |

#### P0-3: 🤖 智能选股升级 — 行业多选过滤 + 价格区间预填

| 属性 | 说明 |
|------|------|
| **需求描述** | 智能选股扫描增加行业/概念多选 checkbox 过滤，价格区间默认预填合理值 |
| **行业过滤** | 在 `#scan-panel` 条件区新增行业 checkbox 组（从 `/api/sector-ranking` 的 31 板块动态生成），支持多选；选中后 `sectors` 参数传入对应板块 key（逗号分隔） |
| **价格区间** | 默认预填 `price_min=0, price_max=200`（可修改），避免扫出天价股 |
| **API** | `GET /api/scan?price_min=0&price_max=200&sectors=semiconductor,ai_compute&min_score=&min_chg=` |
| **结果增强** | 推荐卡片增加一行摘要文字："所属行业 · 换手率 X% · PE Y"，提升信息密度 |

### P1 — 应该完成（如时间允许）

| # | 需求 | 说明 |
|---|------|------|
| P1-1 | 🔥 热门股 — 详情按钮 | 每行右侧增加"详情"按钮，点击弹出股票简况卡（行业/市值/PE/换手率），非全屏弹窗，用 `.stock-detail-popover` 定位到行旁 |
| P1-2 | 📜 龙虎榜 — 日期选择器 | tab 旁增加日期选择器（`<input type="date">`，默认今日），选择后重新请求对应日期龙虎榜（如 API 支持 `date` 参数则传参，否则禁用并提示"仅支持今日"） |
| P1-3 | 📊 板块分析 — 龙头股展示 | 选中板块详情区显示 TOP 5 龙头股（带涨跌幅），点击龙头股切换 K 线 |
| P1-4 | 🔥 热门股 — 行业标签 | 每行增加行业标签列（从板块成分股反查或 API 返回字段） |
| P1-5 | 📜 龙虎榜 — 净买入额筛选 | tab 内容区顶部增加"净买入额 ≥ N 万"筛选框 |

### P2 — 可选（锦上添花）

| # | 需求 | 说明 |
|---|------|------|
| P2-1 | 自选股管理 | 表格行增加星标收藏，收藏后进入"自选"tab |
| P2-2 | 板块对比（多选对比） | 板块排行支持多选（checkbox），选择 2-5 个板块后弹出对比面板（涨跌幅/资金流并列对比） |
| P2-3 | 板块走势对比图 | 选中板块后显示该板块近 20 日走势折线图（与大盘指数叠加对比） |

---

## 4. 重点：板块分析完全重构方案

### 4.1 新面板结构（单页滚动，DOM 完全重写）

```
┌─ 📊 板块分析 ─────────────────────────────────────────────┐
│                                                             │
│  [周期 Tab]  5日 │ 20日 │ 60日 │ 120日 │ 250日      ✕      │  ← 默认 5日
│                                                             │
├─ 全市场板块涨幅榜 ──────────────────────────────────────────┤
│  #  │ 板块名称 │ 涨跌幅▼ │ 领涨股    │ 资金流            │  ← 31 行表格
│  1  │ 电子     │ +3.5%   │ 沪电股份  │ +12.3亿           │  ← 行可点击
│  2  │ 通信     │ +2.8%   │ 中际旭创  │ +8.1亿            │
│  3  │ 电力设备  │ -1.2%  │ 国电南瑞  │ +5.2亿            │
│  ...│ ...      │ ...     │ ...       │ ...               │
│  31 │ 房地产    │ -2.1%  │ 万科A     │ -3.1亿            │
│                                                             │
├─ 当前选中板块详情 ──────────────────────────────────────────┤
│  📌 电力设备                                                  │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ 均涨幅: -1.2%  │ 涨停: 2  │ 跌停: 0  │ 上涨: 8 / 下跌: 7│  │  ← 统计栏
│  ├───────────────────────────────────────────────────────┤  │
│  │ 龙头股 TOP 5:                                         │  │
│  │  国电南瑞(+5.2%)  特变电工(+3.1%)  ...                │  │  ← P1
│  ├───────────────────────────────────────────────────────┤  │
│  │ 资金流向:                                              │  │
│  │  超大单 +3.2亿 │ 大单 +2.0亿 │ 中单 -1.5亿 │ 小单 -3.7亿│  │  ← 横向柱状图
│  │  主力净流入: +5.2亿                                    │  │
│  ├───────────────────────────────────────────────────────┤  │
│  │ [📋 查看成分股(15只)] → 点击弹出成分股面板              │  │
│  └───────────────────────────────────────────────────────┘  │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**数据流**：
1. 面板打开 → `loadSectorRanking()` 获取 31 板块 → 渲染涨幅榜表格（默认 5日列）
2. 点击表格行 → 选中该板块 → 渲染"选中板块详情"区（统计栏 + 龙头 + 资金流 + 成分股按钮）
3. 点击"查看成分股" → 弹出成分股覆盖面板
4. 点击周期 tab → 从已缓存的 ranking 数据中取对应列重排行（无需重新请求 API）

### 4.2 成分股弹层（沿用 `.full-overlay-panel` 模式）

| 属性 | 说明 |
|------|------|
| **面板** | 新建 `#sector-stocks-panel`（`class="full-overlay-panel"`），与热门股/龙虎榜面板同级 |
| **标题** | "{板块名} 成分股 (N只)" + 关闭按钮 |
| **表格列** | 排名 / 代码 / 名称 / 涨跌幅 / 换手率 / 市值 / PE |
| **数据来源** | `GET /api/sector-stocks?sector={key}` → `{code, name, priority}`；涨跌幅/换手率等行情字段需从 `/api/quote` 或 `/api/screen` 补充（如 API 返回仅含 code/name，则展示已有字段，缺列显示 "--"） |
| **行点击** | `selectStock(code, name)` 切换 K 线 + 关闭成分股面板 |
| **API 参数** | `sector` 传板块 key（如 `power_equip`），**不传中文名** |
| **每次拉取** | 每次打开都从 API 重新拉取（不缓存），确保数据新鲜 |

### 4.3 板块资金流（横向柱状图 + 列表）

| 属性 | 说明 |
|------|------|
| **数据来源** | `GET /api/fund-flow/sector?sector={key}` → 资金流数据 |
| **5 个分类** | 超大单 / 大单 / 中单 / 小单 / 主力（主力 = 超大单 + 大单） |
| **可视化** | 横向柱状图：正值向右（红色，资金流入），负值向左（绿色，资金流出） |
| **API 缺数据时** | 本地估算：`estimated_amount = current.totalAmount * (upCount / totalCount) * factor`，其中 factor 按分类取经验值（超大单 0.3 / 大单 0.2 / 中单 0.15 / 小单 0.1），主力 = 超大单 + 大单。估算值标注"(估)"灰色小字 |
| **展示** | 柱状图下方显示数值列表：`超大单 +3.2亿 | 大单 +2.0亿 | 中单 -1.5亿 | 小单 -3.7亿`，主力净流入用大号字体高亮 |

### 4.4 周期切换

| 属性 | 说明 |
|------|------|
| **Tab 设计** | 5 个 tab：`5日` / `20日` / `60日` / `120日` / `250日`，复用现有 `.full-overlay-tab` 样式 |
| **默认激活** | `5日` |
| **切换逻辑** | API `/api/sector-ranking` 返回数据包含 5 列（`5日%` / `20日%` / `60日%` / `120日%` / `250日%`），切换 tab 时从已缓存数据中取对应列重新渲染表格排序，**无需重新请求 API** |
| **响应时间** | 切换后重排 < 500ms（纯前端排序） |
| **选中板块** | 切换周期后，已选中板块的详情区也同步更新为该周期数据 |

### 4.5 排序

| 属性 | 说明 |
|------|------|
| **默认排序** | 按当前周期涨跌幅降序（最强板块在最上） |
| **列头点击** | 点击"涨跌幅"列头 → 切换 升序 ↔ 降序，列头显示 ▼/▲ 箭头 |
| **排序范围** | 仅"涨跌幅"列可排序（P0），其他列排序为 P2 |

### 4.6 DOM 结构（新增/重写）

```html
<!-- 板块分析面板（重写 #sector-analysis-panel 内部结构） -->
<div id="sector-analysis-panel" class="full-overlay-panel" style="display:none;">
  <!-- 顶部：标题 + 周期 Tab + 关闭 -->
  <div class="full-overlay-header">
    <h2>📊 板块分析</h2>
    <div class="sector-period-tabs">
      <div class="period-tab active" data-period="5d" onclick="switchSectorPeriod('5d')">5日</div>
      <div class="period-tab" data-period="20d" onclick="switchSectorPeriod('20d')">20日</div>
      <div class="period-tab" data-period="60d" onclick="switchSectorPeriod('60d')">60日</div>
      <div class="period-tab" data-period="120d" onclick="switchSectorPeriod('120d')">120日</div>
      <div class="period-tab" data-period="250d" onclick="switchSectorPeriod('250d')">250日</div>
    </div>
    <button class="full-overlay-close" onclick="closeSectorPanel()">✕</button>
  </div>

  <!-- 主体：滚动区 -->
  <div class="full-overlay-body">
    <!-- 板块涨幅榜表格 -->
    <div id="sa-ranking-table-container">
      <table class="data-table sector-ranking-table">
        <thead>
          <tr>
            <th>#</th>
            <th>板块</th>
            <th class="sortable" onclick="toggleSectorSort()">涨跌幅 <span id="sort-arrow">▼</span></th>
            <th>领涨股</th>
            <th>资金流</th>
          </tr>
        </thead>
        <tbody id="sa-ranking-tbody">
          <!-- 动态渲染 31 行 -->
        </tbody>
      </table>
    </div>

    <!-- 选中板块详情区 -->
    <div id="sa-selected-detail" class="sa-detail-section" style="display:none;">
      <div class="sa-detail-header">
        <span class="sa-detail-name">📌 <span id="sa-selected-name"></span></span>
      </div>
      <div class="sa-stats-bar" id="sa-stats-bar">
        <!-- 均涨幅 / 涨停 / 跌停 / 上涨 / 下跌 -->
      </div>
      <div class="sa-leaders" id="sa-leaders">
        <!-- 龙头股 TOP 5 -->
      </div>
      <div class="sa-fundflow" id="sa-fundflow">
        <!-- 资金流横向柱状图 + 数值列表 -->
      </div>
      <div class="sa-actions">
        <button class="sa-view-stocks-btn" onclick="showSectorStocks()">📋 查看成分股</button>
      </div>
    </div>
  </div>
</div>

<!-- 成分股面板（新增） -->
<div id="sector-stocks-panel" class="full-overlay-panel" style="display:none;">
  <div class="full-overlay-header">
    <h2 id="sector-stocks-title">成分股</h2>
    <button class="full-overlay-close" onclick="closeSectorStocksPanel()">✕</button>
  </div>
  <div class="full-overlay-body">
    <table class="data-table">
      <thead>
        <tr><th>#</th><th>代码</th><th>名称</th><th>涨跌幅</th><th>换手率</th><th>市值</th><th>PE</th></tr>
      </thead>
      <tbody id="sector-stocks-tbody"></tbody>
    </table>
  </div>
</div>

<!-- 选股结果面板（新增，P0-2） -->
<div id="screen-result-panel" class="full-overlay-panel" style="display:none;">
  <div class="full-overlay-header">
    <h2>🔍 选股结果 <span id="screen-result-count">(0只)</span></h2>
    <button class="full-overlay-close" onclick="closeScreenResultPanel()">✕</button>
  </div>
  <div class="full-overlay-body">
    <table class="data-table">
      <thead>
        <tr><th>#</th><th>代码</th><th>名称</th><th>评分</th><th>涨跌幅</th><th>现价</th><th>换手率</th><th>PE</th></tr>
      </thead>
      <tbody id="screen-result-tbody"></tbody>
    </table>
  </div>
</div>
```

### 4.7 JS 函数清单

| 函数 | 新增/改造 | 说明 |
|------|-----------|------|
| `toggleSectorAnalysisPanel()` | 改造 | 打开面板时初始化：默认选中 5日 tab，加载 ranking 数据，渲染表格 |
| `loadSectorRankingData()` | 新增 | 调用 `/api/sector-ranking`，缓存返回的 31 行数据到 `window.appState.sectorRankingCache` |
| `renderSectorRankingTable(period)` | 新增 | 从缓存数据取 `period` 对应列，渲染 31 行表格到 `#sa-ranking-tbody` |
| `switchSectorPeriod(period)` | 新增 | 切换周期 tab：更新 active 状态 → 调 `renderSectorRankingTable(period)` 重排 → 如有选中板块则同步更新详情 |
| `toggleSectorSort()` | 新增 | 切换涨跌幅排序方向（升/降），更新箭头，重排表格 |
| `selectSector(sectorKey, sectorName)` | 新增 | 点击表格行：标记选中 → 渲染详情区（统计+龙头+资金流） |
| `renderSectorDetail(sectorKey, period)` | 新增 | 渲染选中板块详情：均涨幅/涨停数/龙头 TOP 5/资金流柱状图 |
| `loadSectorFundFlow(sectorKey)` | 新增 | 调用 `/api/fund-flow/sector?sector={key}`，空数据时本地估算 |
| `renderSectorFundFlow(data)` | 新增 | 渲染横向柱状图（SVG/CSS bar）+ 数值列表 |
| `showSectorStocks()` | 新增 | 打开 `#sector-stocks-panel`，调 `/api/sector-stocks?sector={key}` 渲染成分股表格 |
| `closeSectorStocksPanel()` | 新增 | 关闭成分股面板 |
| `closeSectorPanel()` | 新增 | 关闭板块分析面板（含清理选中状态） |
| `doScreenStocks()` | 改造 | 结果渲染到 `#screen-result-panel` 而非 `#stock-list` |
| `closeScreenResultPanel()` | 新增 | 关闭选股结果面板 |
| `loadScanIndustries()` | 新增 | 智能选股打开时从 ranking 数据生成行业 checkbox 组 |
| `startScan()` | 改造 | 收集选中的行业 checkbox → 拼接 `sectors` 参数 → 传入 `/api/scan` |

---

## 5. UI 设计原则

### 5.1 统一风格

- **暗色玻璃态**：所有面板 `background: rgba(15, 23, 42, 0.95)` + `backdrop-filter: blur(12px)`
- **复用 `.full-overlay-panel`**：板块分析/成分股/选股结果均使用已有全屏覆盖面板样式
- **复用 `.full-overlay-tab`**：周期 tab / 类型 tab / 龙虎榜 tab 统一样式
- **复用 `.data-table`**：所有表格统一表头/斑马纹/悬停高亮

### 5.2 板块分析特有样式

| 元素 | 样式 |
|------|------|
| 排名 `#` 列 | 圆形数字标签，TOP 3 金/银/铜色高亮 |
| 涨跌幅 | 正值 `var(--red)` #ff3d6a，负值 `var(--green)` #00e96a（A 股惯例：红涨绿跌） |
| 资金流 | 正值红色 `+N亿`，负值绿色 `-N亿`，估算值灰色小字 `(估)` |
| 选中板块行 | `background: rgba(59,130,246,0.15)` 蓝色高亮 |
| 资金流柱状图 | 正值红色右延伸，负值绿色左延伸，柱高 24px，间距 8px |
| 龙头股标签 | 行内 pill 样式，涨跌幅带颜色 |
| 周期 tab active | `background: var(--blue)` + 白色文字 |

### 5.3 选股器条件区布局

```
┌─ 选股条件 ──────────────────────────────┐
│  最低评分: [___]   最低涨幅%: [___]       │
│  换手率≥: [___]%   量比≥: [___]          │
│  PE≤: [___]                             │
│                    [清除] [🔍 选股]      │
└──────────────────────────────────────────┘
```

- 网格化布局，2 列 x 3 行（评分/涨幅、换手率/量比、PE 单行）
- 输入框 `width: 80px`，标签 `font-size: 13px`
- 所有新条件默认留空 = 不过滤

### 5.4 智能选股行业 checkbox 组

```
┌─ 行业过滤（可多选）──────────────────────┐
│ ☑ 电子  ☐ 通信  ☑ 电力设备  ☐ 医药     │
│ ☐ 食品饮料  ☐ 银行  ☐ 国防军工  ...     │
│                            [全选] [清空] │
└──────────────────────────────────────────┘
```

- 从 `/api/sector-ranking` 返回的 31 板块动态生成
- 每行 4 个 checkbox，`font-size: 13px`
- 底部"全选"/"清空"快捷按钮
- 默认全不选 = 全市场扫描

---

## 6. 待确认问题（已由主理人决策）

| # | 问题 | 主理人决策 |
|---|------|-----------|
| Q1 | 周期默认显示哪个？ | **5日** |
| Q2 | 成分股弹层是否每次都从 API 重新拉取？ | **是**（每次打开重新请求，不缓存） |
| Q3 | 选股器新增 3 个条件的默认值？ | **关闭默认**（留空=不过滤，用户按需填写） |
| Q4 | 智能选股行业下拉用 checkbox 多选还是下拉多选？ | **多选 checkbox**（直观可见，无需展开收起） |
| Q5 | 板块资金流 API 缺数据时是否本地估算？ | **本地估算**（标注"(估)"灰色小字区分） |

---

## 7. 验收标准

### 7.1 板块分析（P0 核心）

| # | 验收项 | 通过标准 |
|---|--------|----------|
| AC1 | 面板打开 | 点击"📊 板块分析"→ 1 秒内打开面板，默认显示 5日周期，31 行板块排行表格渲染完成 |
| AC2 | 周期切换 | 点击"20日"/"60日"/"120日"/"250日" tab → 表格按对应周期重排，响应 < 500ms |
| AC3 | 板块选中 | 点击表格任一行 → 该行高亮 → 下方"选中板块详情"区展开，显示统计栏（均涨幅/涨停/跌停/上涨/下跌） |
| AC4 | 龙头股展示 | 选中板块后，龙头股 TOP 5 显示（带涨跌幅），点击龙头股切换 K 线 |
| AC5 | 资金流展示 | 选中板块后，资金流横向柱状图 + 数值列表显示（5 分类）；API 有数据时显示真实值，无数据时显示估算值（标注"(估)"） |
| AC6 | 成分股弹层 | 点击"查看成分股" → 成分股面板弹出，标题显示"板块名 成分股(N只)"，表格含代码/名称/涨跌幅等列 |
| AC7 | 成分股点击 | 点击成分股表格行 → 面板关闭 + K 线切换到该股票 |
| AC8 | 排序 | 点击"涨跌幅"列头 → 升降序切换，箭头 ▼/▲ 更新 |
| AC9 | 三态 | API 异常时显示错误提示+重试按钮，无数据时显示空状态 |

### 7.2 选股器（P0）

| # | 验收项 | 通过标准 |
|---|--------|----------|
| AC10 | 5 条件筛选 | 输入换手率≥3% + PE≤50 + 评分≥70 → 点击"选股" → 独立面板 3 秒内显示结果 |
| AC11 | 独立结果面板 | 结果在 `#screen-result-panel` 全屏面板显示，不替换 `#stock-list` 板块股池 |
| AC12 | 结果行点击 | 点击结果行 → 面板关闭 + K 线切换 |
| AC13 | 清除筛选 | 点击"清除" → 所有条件清空 |

### 7.3 智能选股（P0）

| # | 验收项 | 通过标准 |
|---|--------|----------|
| AC14 | 行业过滤 | 勾选"电子"+"电力设备" → 扫描结果仅包含这两个行业的标的 |
| AC15 | 价格预填 | 打开面板时价格区间默认 `0-200`，可修改 |
| AC16 | 结果摘要 | 推荐卡片显示"所属行业 · 换手率 X% · PE Y"摘要行 |

### 7.4 热门股/龙虎榜（P1）

| # | 验收项 | 通过标准 |
|---|--------|----------|
| AC17 | 热门股详情按钮 | 点击行右侧"详情" → 弹出股票简况卡（行业/市值/PE/换手率） |
| AC18 | 龙虎榜日期选择 | 选择日期 → 重新请求对应日期龙虎榜数据 |
| AC19 | 龙虎榜净买入筛选 | 输入"净买入≥5000万" → 表格仅显示符合条件的行 |

---

## 8. 技术实现要点（供架构师参考）

### 8.1 数据缓存策略

```
window.appState.sectorRankingCache = null  // 缓存 /api/sector-ranking 返回的 31 行

// 首次打开面板时加载，后续周期切换/排序从缓存读取
loadSectorRankingData() → appState.sectorRankingCache = data
renderSectorRankingTable('5d') → 从 cache 中取 '5日%' 列排序渲染
```

### 8.2 板块 key 映射

API 返回的板块名称为中文（如"电力设备"），但 `/api/sector-stocks` 和 `/api/fund-flow/sector` 需传英文 key（如 `power_equip`）。需维护映射表：

```js
const SECTOR_NAME_TO_KEY = {
  '电子': 'semiconductor',
  '电力设备': 'power_equip',
  '通信': 'telecom',
  '医药生物': 'pharma',
  '食品饮料': 'consumer',
  '银行': 'finance',
  '国防军工': 'military',
  '计算机': 'ai_compute',
  '电力': 'photovoltaic',
  '传媒': 'internet',
  '房地产': 'real_estate',
  '钢铁': 'steel_material',
  '汽车': 'auto',
  // ... 其余从 stock_pool 配置补全
};
```

**注意**：API 返回的 31 板块与本地 17 股池可能不完全对应。对于 API 返回但本地无 key 的板块，需从 `/api/sector-stocks` 的返回结果中反查 key，或后端补充 key 字段。

### 8.3 资金流估算公式

```js
function estimateFundFlow(sectorData) {
  const { totalAmount, upCount, totalCount } = sectorData;
  const ratio = upCount / totalCount;
  const baseFlow = totalAmount * ratio;
  return {
    super_large: baseFlow * 0.30,  // 超大单
    large: baseFlow * 0.20,         // 大单
    medium: -baseFlow * 0.15,       // 中单
    small: -baseFlow * 0.10,        // 小单
    main: baseFlow * 0.50,          // 主力 = 超大单 + 大单
    isEstimated: true
  };
}
```

### 8.4 文件改动预估

| 文件 | 改动类型 | 改动量 |
|------|----------|--------|
| `static/dashboard.html` | 重写 `#sector-analysis-panel` 内部 + 新增 3 个面板 DOM | 中 |
| `static/css/dashboard.css` | 新增板块分析特有样式（柱状图/龙头标签/详情区） | 中 |
| `static/js/market-data.js` | 新增板块分析全部函数（~15 个函数） | 大 |
| `static/js/app.js` | 改造选股器 `doScreenStocks()` + 智能选股行业过滤 | 中 |
| 版本号 | dashboard.css `?v=8→v=9`，market-data.js `?v=6→v=7`，app.js `?v=8→v=9` | 小 |

---

## 9. 不在本次范围

- 后端 API 新增/修改（本次仅消费已有 API）
- dashboard 整体布局重构
- 板块走势折线图（P2-3）
- 板块多选对比面板（P2-2）
- 移动端适配
- 自选股后端持久化
