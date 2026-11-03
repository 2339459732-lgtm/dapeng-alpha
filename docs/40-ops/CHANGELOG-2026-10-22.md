# 2026-10-22 系统修复与优化总账

> 本文件汇总 2026-10-22 三轮改造的全部改动，按「问题 → 根因 → 修复 → 验证」逐条登记。
> 逐行明细见仓库根目录 `CHANGELOG.md`；本文是**面向查阅的结构化索引**，供后续排查直接定位。

- 服务：FastAPI，`127.0.0.1:8900`，静态资源由 `app.mount("/", StaticFiles(..., html=True))` 直接托管
- 解释器：系统 Python 3.11（唯一装了 fastapi/uvicorn 的解释器）
- 进程模型：`scripts/server_watchdog.py` 常驻守护，uvicorn 子进程异常退出会自动拉起
- 影响页面：`index / dashboard / sentiment / quant / project-docs / api-reference / admin`（7 页）

---

## 一、总览

| # | 轮次 | 主题 | 关键改动 | 涉及文件 |
|---|---|---|---|---|
| R1 | 下午一 | 落地页优化 | 修 8 项确定性缺陷，请求 14 → 8 并行 | `static/index.html` |
| R2 | 下午二 | 站内互链 + 情绪页 | 7 页 6/6 出链，深链 `?code=` 生效，情绪页 14 板块全部有内容 | `js/app.js`、`js/sentiment.js`、6 个 HTML |
| R3 | 下午三 | 缓存回收 + 系统漏洞 | 缓存 5 缺陷重建、4 处系统漏洞 | `server/cache.py`、`routers/*`、`trading/monitor.py`、`static/admin.html` |
| R4 | 晚间 | 面板互联 + 图谱精进 | 板块体系映射、面板出口、图谱分组布局与标签 | `js/sentiment.js`、`js/app.js`、`js/docs-graph.js` |

---

## 二、R1 · 落地页（`static/index.html`）

| 缺陷 | 现象 | 根因 | 修复 |
|---|---|---|---|
| 涨跌配色反转 | 涨的标绿、跌的标红 | 用了欧美惯例 | 统一 A 股惯例：涨=`--red`、跌=`--green` |
| `[object Object]` | 信号列渲染成对象字面量 | `signals` 是对象数组 `[{type,label,score,desc}]`，被 `join()` 直接拼接 | 改为取 `label` 字段 |
| 硬编码 🟢 | 三处分支图标恒定 | 图标写死在模板里 | 按涨跌动态取色与图标 |
| 8 板块串行 | 首屏 3–6s 才出图 | `for` 循环里 `await` 逐个拉取 | 改 `Promise.all` 并行，统一快照层 `loadSnapshot()` |
| 随机假数据兜底 | 无数据时图上有随机波动 | `upData.fill(0).map(...)` 的返回值没赋回去，兜底逻辑从未生效 | 改为真实缺口 + 空态占位，不再伪造 |
| ECharts 阻塞首屏 | 白屏等待 1MB 脚本 | `echarts.min.js` 同步加载 | 改为延迟加载 |
| 移动端溢出 | `.stats` 被内联 `gap:48px` 顶破 | 内联样式优先级高于媒体查询 | 移除内联值，交给 CSS 变量 |
| `animation:spin` 缺失 | 加载圈不转 | 引用了从未定义的 keyframe | 补 `@keyframes spin` |

**验证**：0 页面错误、0 控制台错误、移动端无横向溢出、亮色主题正常、各板块图节点数与 API 返回一致。

---

## 三、R2 · 站内互链与情绪页

### 3.1 互链矩阵

| 页面 | 改前入链 | 改后出链 | 主要缺口 |
|---|---|---|---|
| `index.html` | 6 | 6/6 | 原 2 条，不指向量化/情绪/文档/接口 |
| `sentiment.html` | **0** | 6/6 | **完全孤立**，除返回箭头外无站内导航 |
| `quant.html` | 1 | 6/6 | 缺后台/文档/接口/情绪 |
| `api-reference.html` | 2 | 6/6 | 缺首页/量化/后台/情绪 |
| `project-docs.html` | 3 | 6/6 | 缺量化/情绪 |
| `admin.html` | 3 | 6/6 | 缺量化/情绪/文档/接口 |

各页沿用自身既有导航组件，未引入新框架：`index`→`.nav-links`、`dashboard`→`.header-actions`、
`quant`→`.nav-link`、`admin`→侧栏 `.nav-item`、`docs`→`.docs-nav-btn`、
`api-reference`→新增 `.api-nav` 包裹、`sentiment`→新增 `.sh-nav`（≤900px 换行横滚）。

### 3.2 跨页深链 `?code=`

| 项 | 说明 |
|---|---|
| 根因 | `js/app.js` 全文无 `URLSearchParams` / `location.search`，所有 `?code=` 链接都是空操作 |
| 修复 | 新增 `_resolveDeepLink()`：解析参数 → `/api/search?keyword=` 反查板块 → 在 `init()` 最前写回 `appState` |
| 附带 | 板块标题只在 `switchSector()` 内赋值，深链改板块时显式同步（否则列表已是医药、标题仍写半导体） |
| 验证 | `?code=sh600276` → 板块 `pharma`、标题「医药」、选中 `sh600276`、K 线标题「恒瑞医药」；无效代码回落首只并 toast |

### 3.3 K 线头部三个从未赋值的字段

| 字段 | 根因 | 修复 |
|---|---|---|
| `#k-name` | 只在 `selectStock()` 内赋值，`init()` 不调用 → 首屏恒为 `--` | 移入 `updateKlineHeader()` |
| `#k-vol` | 全目录 0 次赋值 | 由 K 线末根 `v` 换算（手 → 万手） |
| `#k-turnover` | 全目录 0 次赋值 | 取行情 `turnover`，`toFixed(2)+'%'` |
| `#k-vol-ratio` | 全目录 0 次赋值；且 `data` 是对象非数组、字段名是 `v` 非 `volume` | 新增 `_calcVolRatio()`：当日量 ÷ 前 4 日均量，口径对齐 `server/strategies.py:249` |

### 3.4 情绪页四个板块

| 板块 | 现象 | 根因 | 修复 |
|---|---|---|---|
| 板块热力图 | 永久「加载中…」 | 代码判断 `data.sectors`，而 `/api/sector-ranking` 的 `data` **本身就是数组** | 改为数组/`.sectors` 双兼容 |
| 板块涨跌排行 | 只有涨幅榜 | 取 API 前 15 个，而 API 已按涨幅降序 | 按今日涨跌排序，取涨幅端 10 + 跌幅端 10 |
| 北向资金 / 资金流 | 空白无说明 | 数据源真缺失，前端吞掉了后端 `reason` | 显示后端 `reason`，角标改「未接入」，替换前先 `dispose` 图表 |
| `LIVE` 角标 | 常态是历史快照却标 LIVE | 未读信封的 `degraded` 字段 | 改用 `fetchSectorRankingEnvelope()`，降级时显示「快照」 |

---

## 四、R3 · 缓存自动回收与系统漏洞

### 4.1 缓存 5 项设计缺陷（`server/cache.py` 重写）

线上实测：**68 条缓存里 29 条已过期（43%）**，既读不到也删不掉。

| # | 缺陷 | 后果 | 修复 |
|---|---|---|---|
| 1 | `_maybe_cleanup()` 只在 `set()` 内调用 | 服务停止写入（夜间/周末/低峰）时过期条目永不回收 | 新增 `background_cleanup()` 后台任务，`lifespan` 启动，周期 30s（短于最短业务 TTL 60s），与写入解耦 |
| 2 | 容量满时直接淘汰 LRU | 淘汰仍有效的数据、留下过期垃圾 | `set()` 改为**先 `purge_expired(force=True)`，仍有缺口才淘汰 LRU** |
| 3 | `get_or_fetch` 等待者在 leader 失败后各自重试 | N 并发 = N 次上游调用，与「去重」语义相反 | 等待者改为 `await event.wait()` 后 `return self.get(key)`，失败共享 `None` |
| 4 | `quotes.py` / `admin.py` 直接读写私有 `cache._store` | 封装失效，改内部结构就会静默破坏调用方 | 补齐 `get_entry / iter_entries / purge_expired / stats / contains`，调用方全部迁移 |
| 5 | `clear()` 不重置清理游标；`max_size<=0` 时 `popitem` 抛 `KeyError` | 清空后立刻被节流挡住；容量 0 直接崩 | 重置 `_last_cleanup`；`max_size` 归一化 `max(1, …)`，淘汰循环包 `try/except` |

新增运行态指标（`stats()`）：`size / live / expired_now / hits / misses / hit_rate / sets / evictions / expired_purged / pending_fetches / cleanup_interval / seconds_since_cleanup / cleanup_task_running`。

**自测**：`_test_cache.py` 9 组 **31/31 通过**，含两条关键回归 ——
「零写入下后台任务把 4 条过期清成 0」（旧实现恒为 4）、「8 并发失败只打 1 次上游」（旧实现为 8）。

> 测试期间发现两条**断言本身写错**，已修正断言而非放宽实现：
> ① 原用例给 `max_size=3` 塞 4 条，未过期时就已满容，淘汰 LRU 是正确行为；
> ② 原用例用 `cleanup_interval=0.15`，但实现有 1.0s 下界（防热循环），0.6s 等待等不到第一次 tick。

### 4.2 四处系统漏洞

| # | 位置 | 现象 | 根因 | 修复 |
|---|---|---|---|---|
| 1 | `routers/quotes.py:102` | fast 模式超时后不回落旧行情 | `cache.get()` 遇过期**立即删除**，紧接着读 `_store` 必然为空 —— 死代码 | 改用 `get_entry()`（保留过期条目），日志打出 `expired/age` |
| 2 | `static/admin.html` | 仪表盘板块卡片永久空白、两个图表永不渲染 | `startEventStream()` **全项目未定义**，抛 `ReferenceError` 被 catch 吞成 toast，后续渲染全部跳过 | 删除死调用；实时刷新本就由 `_liveEngine`（30s 轮询、`document.hidden` 暂停）负责 |
| 3 | `trading/monitor.py` | 一封邮件网络抖动导致整轮扫描漏扫 + 退避 | SMTP 走 `to_thread` + 重试无上限，嵌在 `SCAN_ROUND_TIMEOUT_SECONDS=90s` 预算内 | 加 `asyncio.wait_for(..., timeout=NOTIFY_TIMEOUT_SECONDS=8.0)`，超时落 `PushStatus.FAILED` 不冒泡 |
| 4 | `routers/chart_analysis.py` | 过期分析结果长期驻留 | 私有 `_cache` 满 200 条才裁，从不检查过期 | 补「先回收过期，仍超 200 再裁最旧 50」 |

附带修复：`loadOrdersPieChart()` 每 30s 重复请求一次 `/api/admin/dashboard`，改为复用 `loadDashboard()` 已取到的 `d.orders`。

### 4.3 管理端缓存页

- `/api/admin/cache` 增加 `stats` 块，行内增加 `age` 与过期标记
- 新增 `POST /api/admin/cache/purge-expired`（手动即时回收，后台每 30s 也会自动跑）
- 新增三张卡片：**过期未回收**（>0 标红）/ **缓存命中率** / **自动回收**（运行中·周期·淘汰数）
- 过期行降透明度 + 红色「过期」角标，TTL 列显示「已过期」
- 日志动作新增 `CACHE_PURGE`（筛选下拉 + 文案映射同步）
- `/api/health` 增加 `cache` 块（`expired_now` 长期 >0 说明后台任务没跑）

---

## 五、R4 · 面板信息互联

### 5.1 两套板块体系此前无法互通

| 体系 | 数据源 | 口径 |
|---|---|---|
| 情绪页 | `/api/sector-ranking` | 东财行业板块，**50 个**，代码形如 `BK1500 医美耗材` |
| 仪表盘 | `/api/quotes?sector=` | `server/config.py` 自选板块，**26 个**，key 形如 `semiconductor / pharma` |

两边名字与代码都对不上，于是「看到某板块走强 → 去仪表盘看成分股」这条最自然的动作无法完成。

**修复**：`static/js/sentiment.js` 新增 `SECTOR_ALIAS`（26 组关键词别名）+ `mapToSector(name)`：

- 命中 → 热力格可点击，跳 `/dashboard.html?sector=<key>`
- 未命中 → **不可点**（不做假跳转），`title` 注明「未纳入仪表盘自选板块」
- 顺序敏感：越具体的别名越靠前，避免「消费电子」被「消费」先截走

### 5.2 上下文传递链路

| 方向 | 形式 | 落地行为 |
|---|---|---|
| 情绪 → 仪表盘 | 热力格点击 | `dashboard.html?sector=<key>`，由 `_resolveDeepLink()` 选中该板块 |
| 仪表盘 → 情绪 | header「情绪」按钮 | 自动带 `?sector=<当前板块>` |
| 情绪页内聚焦 | `?sector=` | 顶部聚焦条（板块名 + 东财口径命中数 + 一键清除），对应格子描边高亮 |
| 仪表盘 → 回测 | K 线浮层「相关信息」条 | 带 `?code=&sector=`，回测页显示来源说明条（池化回测不预填标的） |

### 5.3 面板出口

`dashboard.html` 的 K 线浮层底部新增 `.kline-overlay-links`「相关信息」条，
9 个 Tab（策略评分/资金流向/个股简况/基本面/研报/北向资金/资讯/策略实验室/模拟持仓）共用，
href 由 `app.js` 的 `_syncContextLinks()` 在 `init / switchSector / selectStock` 三处刷新。

---

## 六、R4 · 文档图谱精进

| 项 | 改前 | 改后 |
|---|---|---|
| 数据新鲜度 | `static/docs-graph.json` 生成于 2026-10-21，`CHANGELOG.md` 节点仍是 566 行 | 重新生成，节点/连线/字节数同步到当前仓库 |
| 分组可读性 | 力导向把分组打散，颜色混在一起，看不出结构 | 布局加入**分组凝聚力**（同组向组质心收拢），板块成簇 |
| 标签覆盖 | 仅核心节点 + 度数 Top5 常显，30 个节点里只看到约 8 个名字 | 覆盖率提升，仍由 `labelLayout.hideOverlap` 兜重叠 |
| 连线可读性 | 78 条线在中心叠成灰雾 | 边的存在感随两端重要度衰减，叶片连线退到背景 |

---

## 七、验证记录

| 项目 | 结果 |
|---|---|
| `_test_cache.py` | 31/31 PASS |
| `py_compile`（cache/main/admin/quotes/monitor/chart_analysis） | 全部通过 |
| `/api/health` | `expired_now=0`、`cleanup_task_running=true`、`expired_purged` 递增 |
| `/api/admin/cache` | `stats` 完整、`purge-expired` 返回 `{purged:N}` |
| `/api/quotes?sector=pharma&fast=1` | 21/21 有效，`degraded=false` |
| 管理端缓存页（浏览器） | 8 张卡片、120+ 行、副标题与按钮正常，0 控制台错误 |
| 全站 7 页扫描 | 0 横向溢出、0 4xx/5xx、0 脚本错误 |
| `dashboard.html` 专项 | 28 行、`k-name`=华微电子、`k-vol`=64.81万手、`k-turnover`=6.75%、`k-vol-ratio`=2.74 |

---

## 八、复现命令

```bash
# 缓存自测（纯 stdlib，不依赖运行中的服务）
python _test_cache.py

# 未定义函数静态审计（HTML 属性 + JS 调用点）
python _audit_undefined.py

# 全站七页扫描 / 仪表盘专项 / 管理端缓存页 / 图谱截图
NODE_PATH="<playwright node_modules>" node _verify_pages.mjs
node _verify_dashboard.mjs
node _verify_admin_cache.mjs
node _shot_graph.mjs

# 重新生成图谱数据（改动文档后必跑）
python tools/gen_docs_graph.py

# 重启后端（有看门狗，杀子进程即可，约 15–20s 恢复）
# PowerShell: Stop-Process -Id (Get-NetTCPConnection -LocalPort 8900).OwningProcess -Force
```

---

## 九、遗留项

| 项 | 说明 | 影响 |
|---|---|---|
| `/api/meta/capability` | 文档称「前端角标唯一事实源」，实际仅 `js/fundamental.js` 消费，且多数域返回 `unavailable`（反映 L2 缓存冷启动为空） | 要接入各页需先解决冷启动预热 |
| `updateActiveKlinePrice` / `initQlibDashboard` | 两处调用被 `typeof === 'function'` 保护，函数从未定义 —— 死分支 | 无功能影响，可清理 |
| 仓库根目录残留 | 文件 `nul`、目录 `server" && cp ...`（历史误操作产物，内含误拷的 `payment.py`） | 仅影响观感，需确认后清理 |
| 情绪页板块映射 | 26 组别名覆盖主流行业，个别细分板块（如「医美耗材」）会映射到父类「医药」 | 属有意的父类归并，非缺陷 |
