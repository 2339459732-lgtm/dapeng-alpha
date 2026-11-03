# DAPENG Alpha 更新日志

> 2026-10-22 三轮改造的结构化总账（含逐条根因与复现命令）见
> [docs/40-ops/CHANGELOG-2026-10-22.md](docs/40-ops/CHANGELOG-2026-10-22.md)。

## 2026-11-03（21:55）— 数据口径修复 + 8 分制候选池 + 分层面板 + 口令边界

**背景**：接入《交易策略配置文件》的 8 分制规则时，发现决策台存在
「全市场广度被静默降级成 447 只核心样本」的准确性问题；同时补齐候选池、
分层阅读与弱口令的部署边界。

**数据链路**
1. **修复覆盖快照只读内存缓存**（`server/market_coverage_payload.py`）：
   服务重启后 `get_cached_coverage()` 返回 None，决策快照静默退回核心样本——
   广度从「涨 2152 / 跌 3329、涨停 61」变成「涨 256 / 跌 180、涨停 3」，方向相反。
   现在内存未命中时回落 `data/market_coverage_latest.json`（受 `max_age` 约束，过期不用）。
2. 修复后快照：`full_market=true`、覆盖 95.6%、`scope=沪深北 A 股（含 ST）`、
   广度 1984/2894、涨停 54 / 跌停 29。
3. 新增 `/api/decision/strategy-profile`：8 分制候选池，数据装配补齐
   MA20 / 平台高点 / 量比 / 行业排名 / 板块龙头涨停 / 风险代理。

**8 分制候选池（`server/strategy_profile.py` / strategy-profile.v1）**
- 完全对齐文档：市场/板块/个股/量价/风险各 0~2 分，总分 ≥8 且无雷、盈亏比 ≥2 才入池；
- 仓位按 `min(首笔意图, 单票 60%, 现金下限 40%, 风险预算 64/6%)` 取最小项，100 股整手；
- 独立纸面档案：不覆盖现有账户/风控，`auto_execute=false`，不接券商；
- 配置落 `data/strategy_profile.json`（与文档 config.json 一一对应）；
- 无数据时返回 `available:false` + reason，不用空列表冒充「今天没机会」。

**面板（决策台）**
- 17 张卡按 **决策结论 → 事实层 → 判断层 → 决策层 → 数据与溯源** 分组，补分组标题；
- 新增「8 分制候选池」卡片：市场分 / 涨停跌停 / 入围通过 / 指数 MA20 + 候选表（五维、量比、止损目标、首笔金额），
  无候选时明确显示「空仓不做」；
- 候选池 **不进主批次**：主批次结束再单独拉（浏览器同域并发限制会把慢接口堵在队尾）。

**口令边界（保留 071215，好记 + 加固）**
- 新增 `ACCESS_IP_ALLOWLIST`（支持 CIDR）：配置后所有请求（含登录页）必须命中白名单；
- 新增单 IP 渐进锁定：15 分钟错 5 次 → 锁 15 分钟（挡「慢速穷举」，与 10 次/分钟的频率限制互补）；
- 弱口令自动缩短会话：`ACCESS_KEY` < 12 字符时会话上限从 72 小时压到 8 小时；
- 设置项拆分为 `issues`（硬故障）与 `warnings`（配置风险），弱口令不再让体检长期 FAIL。

**验证**
- `pytest` **524 passed**（+24：评分口径、仓位约束、IP 白名单、渐进锁定、弱口令 TTL）。
- `ci_guard` 三 PASS；`system_audit` FAIL 0。
- 线上实例：`071215` 登录 200、旧密钥 401、未登录 302/401；
  候选池实测 universe 5527 → 预筛 31 → 评分 30 → 通过 0（当日指数在 MA20 下方，规则判定空仓）。

## 2026-11-03（20:35）— 开源交付 + 访问密钥鉴权（access-key.v1）

**背景**：代码要开源，但运行实例不能公开——系统此前没有任何用户体系，只靠
`HOST=127.0.0.1` 本机绑定。一旦对外监听，任何拿到地址的人都可直接看行情、调接口、改配置。

**开源前的两处阻断（已修）**
1. **仓库根错位**：原 git 仓库根是整个 `~/.openclaw/workspace`（含个人笔记与其他项目），
   在其中提交会把私人工作区一并公开 → 在 `dapeng-water/` 内新建独立仓库。
2. **`data/` 未隔离**：`users.json`（6 位用户的姓名/手机号/订单号）、`trading.db`（11MB 真实交易）、
   `payment_orders.json`、`admin_logs.json`、`data/review/` 都会被提交 → 改为**白名单式**
   `.gitignore`（`data/*` 全忽略，只放行 4 个纯配置），并核查确认无漏网。

**新增鉴权（`server/infra/auth.py` + `server/routers/auth.py` + `static/login.html`）**
- 单口令 → HMAC 签名 Cookie（`v1.<exp>.<sig>`），HttpOnly / SameSite=Lax / 可选 Secure；
- 签名密钥由 `ACCESS_KEY` 派生（可用 `SESSION_SECRET` 覆盖）：**换 key = 全员下线**；
- 未配置 `ACCESS_KEY` 时整层关闭，本地开发与测试无感；
- 页面 302 `/login?next=`、API 401 JSON；登录页自包含、无外部依赖。

**防护墙（五层）**
1. `hmac.compare_digest` 常量时间比较，防时序侧信道；
2. 登录 10 次/分钟/IP；
3. 全局失败闸门 60 次/10 分钟（挡分布式爆破，换 IP 也暂停）；
4. 写操作 Origin 校验 + SameSite=Lax（CSRF 纵深）；
5. **非回环监听且无 `ACCESS_KEY` → 拒绝启动**（`ALLOW_INSECURE_BIND=1` 可跳过）。

辅助：`TRUST_PROXY`/`TRUSTED_PROXIES` 控制是否采信 `X-Forwarded-For`（默认不采信，
防伪造限流键）；口令 <16 字符启动告警；`logs/auth.log` 审计登录成功/失败/限流
（JSONL、5MB 轮转、**绝不记录密钥**）。

**开源交付件**
- `LICENSE`（Apache-2.0）、`SECURITY.md`（威胁模型/部署清单/轮换步骤/已知限制）、
  `CONTRIBUTING.md`（环境/约束/PR 清单）、`.env.example`、`.github/workflows/ci.yml`（pytest + gitleaks）；
- `docs/10-architecture/AUTH.md`：会话格式、中间件顺序、五层防护、失败模式排查；
- 清理 55 处个人机器路径：启动脚本改 `%~dp0`/`%USERPROFILE%`、Python 改 `Path.home()`、
  文档与前端数据改相对路径；`.vbs` 自启脚本与临时产物移出仓库。

**验证**
- `pytest` **500 passed**（+29 鉴权：签名伪造/过期/轮换/限流/来源校验/审计脱敏/全局闸门）。
- `ci_guard` 三 PASS；路由基线 +5 条（`/api/auth/*`、`/login`）。
- 真实实例端到端：未登录 302/401、跨站 POST 403、正确密钥 200、退出后 401；
  浏览器 8 项全过（含刷新保持与错误提示）；审计日志 0 处密钥明文。
- 非回环 + 无密钥启动：进程拒绝启动并给出配置指引。

**维护能力**
- 新增 `scripts/verify/checkup.py`：一条命令跑「pytest + 架构守卫 + 运行态体检 + 性能预算」，
  结束给 4 行结论，避免接手时「只跑 pytest 就发版」。
- 验证脚本全部支持登录态：`_auth.py`（Python）与 `_login.mjs`（Playwright）自动读
  `ACCESS_KEY` 登录，鉴权开启后审计/性能/页面验收不再被 401 挡死。
- `docs/10-architecture/AUTH.md` 增「自检工具怎么跑」与「维护手册」（换密钥/查登录/疑爆破/忘记密钥）。
- 线上实例已启用：生成 43 位 `ACCESS_KEY` 与 32 位 `MOAT_ADMIN_TOKEN` 写入 `.env`
  （备份在 `backups/`），服务重启后 `/dashboard.html` 已 302 到登录页。

## 2026-11-03（12:30）— 龙虎榜复盘：买卖因素库 + 因素命中展示（lhb-factors.v1）

**背景**：「为什么买/为什么卖」此前是写死在 `attribute()` 里的 if 拼接——不可调（改文案要发版）、
不完整（尾盘偷袭封板 / 主线追涨 / 均价下方低吸描述不出来）、不可统计（字符串没法回答
「哪类买入理由次日更容易被打脸」）。

**新增因素规则库（`server/lhb_factors.py`，304 行）**
1. 每条因素 = `id + label + weight + when`：`when` 的键是「事实名 + 比较符后缀」
   （`_gte/_lte/_gt/_lt/_eq/_ne`，无后缀=相等），多键 AND；时间字段零填充 `HH:MM`，
   字符串比较等价时间比较。
2. **严格 fail-closed**：事实缺失（None）不命中——「不知道」≠「不满足」；配置引用不存在的
   事实名也不命中，绝不静默通过。
3. 默认 7 买 + 6 卖因素：早盘封板抢筹 / 主线板块追涨 / 连板梯队接力 / 实质消息驱动 /
   炸板回封承接 / 均价下方低吸 / 高换手活跃票；开板兑现窗口 / 板上兑现 / 换手过高出货 /
   题材发酵高点兑现 / 博弈激烈触发 / 大幅偏离均价。阈值、文案、权重全在
   `data/lhb_config.json` 的 `factors` 段可调。
4. **对标的评估，不是对记录判断**：同一只票同时评估买卖两侧（这正是一票上多空分歧的来源），
   输出 `balance = 最强买入权重 − 最强卖出权重`。

**接线与展示**
- `attribute()`：买入时机文案追加「买入侧因素链（按权重）」；卖出触发从一句笼统总结改为
  逐条因素 + 各自证据（哪个条件、什么阈值）；`logic` 追加 `summarize()`；记录新增 `factors` 字段。
- `lhb_insight.factor_stats()`：因素命中统计（次数 + |净额| 合计），**口径按记录方向过滤**——
  买入记录的兑现条件不混进「为什么卖」。
- 展示页新增「买卖因素命中统计 FACTOR HITS」面板（两栏条形图）；明细展开新增「买卖因素命中」
  区块（两侧因素 + 权重 + 证据链 + balance 提示）；KPI 增至七格（新增「因素命中覆盖」32/35）；
  决策台摘要卡新增「热门因素」行。
- `data/lhb_config.json` 新增 `factors` 段（含 `_usage` 说明可用事实与语法）。

**修掉的问题**
1. 因素统计初版把买入记录上命中的卖出因素计入「卖出侧命中」（板上兑现 23 次 > 卖出记录数），
   语义混乱 → 按记录方向过滤。
2. 因素名「大幅偏离均价获利了结」过长被截断 → 缩短为「大幅偏离均价」+ 加宽标签列。
3. `lhb_factors` 未登记 `arch_report.LAYER_OF` → 补登记（engine 层）。

**验证**
- `pytest` **471 passed**（+24 因素库：判定式后缀 / AND 语义 / 缺失不命中 / 未知事实 fail-closed /
  权重排序 / 双侧同时命中 / facts 组装 / 接线契约 / 统计口径按方向过滤）。
- `ci_guard` 三 PASS；`system_audit` 39 项 FAIL 0（4 WARN 为原有环境项）；`perf_budget` 11 端点 OK。
- 页面验收：8 个面板、因素面板买卖各 6 行、明细因素区块两侧渲染、0 console 错误、
  可滚动（5649px）、移动端 430px 无横向溢出；决策台卡片「热门因素」行渲染正常。

## 2026-11-03（11:58）— 龙虎榜复盘：聚合层 + 展示页 + 三处入口（lhb-insight.v1）

**背景**：上一轮只做了归因与接口，35 条记录是一张平铺清单——看不出「一只票上多路资金是合力还是对峙」，
也没有任何界面，用户问「这个模块在哪打开」。

**新增聚合层（`server/lhb_insight.py`，421 行）**
1. `by_stock`：一只票上所有席位拼图，核心派生量 `alignment`（合力做多 ≥80% / 合力出货 ≤20% / 多空对峙）、
   封板质量标签（几连板 · 炸板几次 · 首封时间）、按 |净额| 加权的主因。
2. `by_seat`：席位当日集中度。**机构专用 / 北向资金标为「通道汇总」且不算集中度**——
   那是几十上百个账户的汇总，当成一个人算「集中狙击」是误读。沪股通与深股通按类型合并成一行。
3. `by_industry`：按行业汇总净额；未涨停的归「未归入涨停池」（事实，非缺失）。
4. `motive_matrix`、`motive_by_seat_type`、`highlights`（自动要点）、`totals`。

**新增展示页（`static/lhb-review.html` + `js/lhb-review.js` + `css/lhb-review.css`）**
- 阅读链：一句话结论 → KPI 六格 → 市场底座 → 关键发现 → 动因分布 → 标的视角 → 逐笔明细 → 席位视角 → 题材视角。
- 逐笔明细支持按席位/方向/主因/关键词筛选，点行展开五维分数条、各维证据、价位与时机、卖出触发、完整推演逻辑。
- 筛选即钻取：点主因条、标的行或要点卡片，都把明细收敛到子集，不跳页。
- 三处入口：左侧栏 `📊 龙虎榜复盘`、决策层「龙虎榜复盘」卡（显示当日摘要 + 查看完整复盘）、直接 `/lhb-review.html`。
  决策层卡片**不进 60 秒轮询**（报告近 100KB），只在首次进入时拉一次。

**修掉的三个缺陷**
1. **阻断级：页面完全无法滚动**。`dashboard.css` 给 `html,body` 设了 `height:100%;overflow:hidden`
   （主控台的整屏布局前提），新页直接复用就变成 scrollHeight==clientHeight，首屏以下 3 个面板永远看不到。
   Playwright 点击会自动滚元素进视野，所以交互测试全过、真实用户卡死——这类问题只靠功能断言发现不了。
2. 席位列显示内部枚举 `institution` / `northbound`，改为中文标签。
3. 动因分布条**没有主因名称**，只有条数；且归因文案里出现 `northbound 净…万` 的英文泄漏。

**设计上的两个取舍**
- 标的表把「买盘占比」换成「参与席位构成」：前者与「资金合力」是同一个量的两种说法，并排是重复信息。
- 通道类席位的 `top_share` 返回 `null` 而不是 0——不给假的集中度。

**验证**
- `pytest` **447 passed**（+17 聚合：合力边界、通道合并、加权主因、封板质量、行业归属、空数据）。
- 页面验收：7 个面板、25 只标的 / 35 条明细、展开与筛选与排序全部生效、**console 错误 0**、可滚动（5260px）。
- 决策层卡片与侧边栏两个入口均已验证跳转正确；`arch_report` 环 0 / 反向 0 / 超大模块 0。

## 2026-11-03（11:35）— 龙虎榜复盘：席位筛选 + 五维归因 + 封板时刻级推断（lhb-review.v1）

**背景**：需要每天收盘后自动回答「优质席位在哪些票上做了什么、为什么」。

**新增**
1. `server/lhb_fetch.py`（抓取层）：东财营业部买卖明细（全页）、个股级龙虎榜、公告、
   复盘日涨停/炸板/跌停池（akshare 东财同口径）。
2. `server/lhb_review.py`（归因层）：席位五分类筛选 → 五维打分 → 主因 + 证据链 + 推演逻辑；
   `attribute()` 为纯函数，可脱网回放。
3. `data/lhb_config.json`：唯一可调配置（席位门槛、五个维度的阈值、游资别名表）。
4. 三个读接口：`/api/decision/lhb-review`、`/lhb-review/{date}`、`/lhb-review/recent`。
5. `server/brain.py` 新增 `lhb_review` 调度任务（15:35–23:00，每 30 分钟，幂等覆盖）。
6. `docs/10-architecture/LHB-REVIEW.md`：口径、算法、推断等级、配置、验收基线。

**归因设计上的四个决定（都踩过坑）**
- **情绪打折**：全市场共用的环境分乘 `mood.discount=0.55`。不打折时 125 家涨停的一天
  会把 35 条记录全部归成「市场情绪」，归因失去意义。真正提供区分度的是龙头效应
  （最高板 / 同行业最高板 / 炸板回封），只对个别标的成立。
- **一个事实只算一次**：连板高度只进情绪维度，题材维度只用板块效应，否则同一件事被算两遍。
- **同分优先个股证据**：`sorted` 是稳定排序，不显式定 tie-break 的话同分记录会按字典顺序
  全落到「市场情绪」。
- **对倒检测不能用分侧**：席位已合并成一行，按 `BUY>=SELL` 判侧永远只有一侧；
  必须 `BUY>0 and SELL>0`。

**修掉的五个真实缺陷**
1. `config()` 浅合并 → 配置文件里一旦有 `motive`，新增的 `mood.discount` 默认值永远读不到，
   规则静默退回旧行为。改为 `_deep_merge()` 递归合并。
2. 公告窗口锚在 `datetime.now()` → 复盘历史日期时把复盘日之后的公告算进动因（未来函数）。
   改为 `as_of=复盘日`，并显式丢弃复盘日之后的公告。
3. 涨停池换成按复盘日取（原用 T+1 盘中广度解释 T 日行为，时间轴错一天）。
4. `TOTAL_BUYRIO` 是小数（0.0211=2.11%），原来当百分数输出，差 100 倍。
5. `920xxx` 被「9 开头 → 沪市」判成 sh（北交所新号段）；拉萨席位因名单先于关键词被当成优质游资。

**推断等级（不假装精确）**
- `inference: "seal-time"`：能拿到涨停封板时间 → 「10:37 才封板 → 买入更可能发生在盘中拉升段」；
- `inference: "day-level"`：拿不到 → 只定性到「盘中/板上/尾盘」；
- 个股无任何特异证据时 `mood_only=true`，`confidence` 压到 ≤0.45 并在 logic 里注明属环境驱动。

**验证**
- `pytest` **430 passed**（+43 龙虎榜：深合并、席位分类、五维区分度、双向合并、缺失降级、
  公告时效、未来函数拦截、代码前缀、涨跌停阈值）。
- 2026-11-02 实测：35 条 / 25 只票，主因 情绪 15、题材 9、技术 8、博弈 3，
  封板时刻级推断 23 条，重复 0，耗时 4.7s；三个接口与调度任务均已验证。

## 2026-11-03（11:15）— 打板复盘工具链：交割单 → 逐笔回合 → 日报/台账/看板

**背景**：需要按交易日复盘「某个交易者的打板操作」。系统里只有 paper 委托（多为 rejected、无卖出成交、无打板策略），
不具备可复盘的成交流水；因此先把**解析 + 复盘 + 三形态输出**的工具链建好，拿到交割单即可一键出报告。

**改动**
1. `scripts/review/parse_statement.py`：券商/同花顺导出的成交明细 → 统一 18 字段。
   自动识别表头行与列名别名（买卖标志/操作/业务名称、成交金额/发生金额…），代码补 `sh/sz/bj` 前缀，
   缺费用列时按「佣金万 2.5（最低 5）+ 印花税千 0.5（卖出）+ 过户费十万分之一」估算并标注。
2. `scripts/review/build_review.py`：
   - 按标的 **FIFO** 配对回合，买入费用按数量摊到各批次，未平仓单列不计盈亏；
   - 离场判定：有计划先比计划（止损触发/止盈触发/**区间离场**），完全没计划才按 ≤−5% / ≥+7% 归类；
   - 风格统计：板型、入场时段、单笔金额分位、同标的加仓次数、离场类型、胜率/盈亏比/单笔期望/计划执行率；
   - 输出 Markdown 日报（逐日）+ 总览 + Excel 5 sheet + 自包含 HTML 看板（无外链、离线可开）。
3. 新增 `docs/40-ops/BOARD-REVIEW.md`：需要哪些列、两条命令、计算口径、已知边界。

**修掉的一个判定错误**
初版把「有计划但从区间内离场」误标成「止盈/止损（未设计划）」——会让「计划覆盖率 100%」与
「全部未设计划」同时出现在一份报告里。现在改为「区间离场（计划未触发）」，并把「按计划止损率」
换成可解释的「计划执行率 + 触发分布（止损/止盈/区间/无计划）」。

**验证**
- `pytest` **387 passed**（+6 复盘引擎：FIFO 顺序、费用分摊、持仓不冒盈亏、四类离场判定）；
  `ci_guard` 三 PASS。
- 示例数据跑通：4 笔回合、胜率 75%、净盈亏 +96,947.11；HTML 看板 0 控制台错误、无横向溢出；Excel 5 sheet 结构正确。

## 2026-11-03（11:05）— 决策分流：多市场指标统一接入 + 规则化闸门（decision-metrics.v1 · decision-routing.v1）

**背景**：趋势、资金流、买手率、情绪四类信号散在各模块，口径与量纲各不相同，决策层只能各看各的；
「买手率」此前根本没有；分流逻辑也不存在——只有专家共识给的一个仓位值。

**改动：统一指标层（`server/decision_metrics.py`）**
1. 8 项指标收敛为同一结构 `{id,label,market,kind,value,unit,score,confidence,source,fields,as_of,age_s,ttl_s,stale,coverage,fallback}`。
2. 全部归一化到 `score∈[-1,1]`：`tanh(涨跌幅/0.6)`、`(上涨占比−50)/30`、`tanh(主力净额/40亿)`、`(买手率−50)/15` 等，
   阈值因此可以跨指标比较。
3. **新增买手率**：腾讯 `f7 外盘 / f8 内盘`，按成交额取前 120 只 + ETF 篮子（510300/510500/512100/588000/159915），
   60 只/批、4 并发、GBK 解码；指数明确不给（不拿成交量冒充）。
4. `confidence` 与 `score` 分离：覆盖低、口径未核实（`flow.main`）、数据陈旧各自降权，缺失项不参与加权。

**改动：分流引擎（`server/decision_routing.py` + `data/decision_routing.json`）**
5. 6 条路由 + 观察兜底，按优先级**先保护后进攻**：`risk_off(100) > buy_squeeze(90) > risk_on(80) > trend_follow(70) > index_breadth_split(60) > neutral_range(10)`。
6. 条件支持 `all/any`、`score/value`、`optional`；必需指标不可用则该路由不命中，继续判下一条。
7. 综合分按 `weight×confidence` 加权并重新归一；`stale` 项置信 ×0.6。
8. 降级：可用 <4 或覆盖 <0.5 → `observe`，仓位上限 0，`degraded_reasons` 写明原因。
9. 配置校验：未知指标、优先级重复、仓位越界（>80）、`risk_on≤risk_off` 全部拦下。

**改动：接进决策**
10. 闭环新增 `routing` 步骤；`memo` 增加 `routing` 块与闸门：`cap = min(cap, route.cap_max)`；
    路由判 `defend` 而专家判 `attack` → 立场降级 `neutral`；降级时写入 `risks`。**只收紧，不放大**。
11. 新接口 `GET /api/decision/metrics`、`GET /api/decision/routing`（含 `evaluated[]` 逐条未命中原因）。

**实测（盘中 11:0x）**
- 指标 7/8 可用（北向未披露），采集 662ms；买手率 全市场 47.97% / ETF 56.68%。
- 路由 `index_breadth_split`（指数与个股背离）`cap=35`、`composite=-0.162`、`coverage=87.5%`。
- 闭环 `memo` 记 `routing` 块；分流上限未触顶时不改仓位（攻击 cap 9 < 35）。

**验证**
- `pytest` **381 passed**（+15 分流规则用例）；`ci_guard` 三 PASS；
  `system_audit --gate` 新增分流 5 项全 PASS（39 项 0 FAIL）。

## 2026-11-03（10:45）— 事实层补齐全 A 股 + 三层真正贯通（gapfill v1 · expert-survey-link v1）

**背景**：两个实打实的缺口 ——
① 事实层报价靠新浪单源，分页/限流会漏抓（实测同一天两次抓取 5581 → 5158，覆盖率从 96.5% 掉到 90%）；
② 研判团只吃事实层（`packet` + `sentiment`），判断层的结构/背离结论从未进入专家意见，
「三层贯通」只到数据管道，没进决策。

**改动：事实层补全（三层数据源兜底）**
1. 新增 `server/market_coverage_gapfill.py`：腾讯 `qt.gtimg.cn` 批量补缺——
   只对「池子里有、但主源无价格」的代码补，60 只/批、4 并发、TTL 120s，GBK 解码（否则中文名乱码）。
2. 合并策略：新浪（主源）> 东财北交所（分市场）> 腾讯（兜底），**只补缺不覆盖**，避免源间口径漂移。
3. 补不到的保持 `null` 并写入 `sources.tencent_gapfill.unresolved`（退市/停牌/代码失效），不拿估值反推价格。
4. 实测：**5668 / 5782 = 98.0%**（沪 100%、深 100%、京 76.1%），单次补缺 586 只、763ms。

**改动：三层贯通（判断层进入研判团）**
5. `build_expert_opinions(packet, sentiment, survey)`：E5 情绪专家读 `survey.divergence`，
   背离时禁止进攻、仓位压到 ≤35%，并写入 `structure_sentiment_divergence` 证据。
6. `_audit_expert(..., survey)`：判断层不可用直接否决可执行（三层缺一层 = 链断了），写入 `survey_available` 证据。
7. `aggregate_expert_opinions(..., survey)`：输出 `consensus.survey_alignment`（aligned/mixed/divergent/unknown）
   与 `inputs.survey`（版本/时点/trace/regime/背离/口径），summary 里直接写「判断层：强势上行（背离）」。
8. `run_expert_panel(force, survey=None)`：闭环传入已算好的 survey；单独调用时按缓存取一次，取不到如实标注。
   专家团缓存键补上 `survey.as_of`，判断层刷新后不会返回旧专家结论。
9. `brain.run_cycle` 传 `survey=survey`；`lineage` 的 experts 指纹加入 `survey_alignment` / `survey_as_of`。

**实测（2026-11-03 10:40）**
- 研判团：`stance=defend`，`position_cap=20`，`survey_alignment=divergent`，
  `inputs.survey={regime:强势上行, divergence:true, alerts:8, scope:full_a_share}`。
- 闭环：`lineage.ok=true consistent=true gaps=[]`，7 条 citations 全部 `same_trace`；
  备忘录因 E6 分歧否决（0.81>0.55）仓位归零。

**验证**
- `pytest` **361 passed**（+4 三层贯通用例）；`ci_guard` 三 PASS；`system_audit --gate` 0 FAIL；
  5 个 Playwright 验收 18/21/19/17/13 全 PASS。

## 2026-11-02（23:55）— 系统调试与修复：缓存四处缺陷 + 三项安全加固 + 34 项自动体检

**背景**：要求「逐项检查服务与配置、排查缓存读写/失效/并发/异常回退、识别并修复已暴露的安全漏洞」。
本轮不是看一遍代码就下结论，而是先写体检脚本把 34 项事实打出来，再按证据修。

**缓存（4 项缺陷，全部有单测）**
1. `get()` 取走过期条目时**漏扣字节**（直接 pop，没减 `_bytes`）→ 账面只涨不跌，最终误触发 LRU 淘汰。
   改为统一走 `_drop()`（pop + 扣字节），作为所有删除路径的唯一出口。
2. `set()` **先删旧值再判断体积**，单条超大对象被拒写时把上一份好数据一起删了。改为先估体积，超预算保留旧值。
3. `get_or_fetch_ex()` 只处理「返回 None」，上游**抛异常**时不回落旧值 → 有可用缓存却返回 500/空。改为 try/except 后走 stale 回落。
4. 缓存无锁：`OrderedDict` 与字节计数在事件循环/线程池并发下可能交错。加 `threading.RLock`，`iter_entries()` 改为先快照再迭代。
   附带新增 `stats().recomputed_bytes`，把「账实一致」做成可观测项。

**安全（3 项加固）**
5. 缺 Host 校验：恶意页面可把域名解析到 127.0.0.1 发起请求（连接来自本机、`client.host` 也是回环），
   绕过「仅本机访问」判断。新增 `TrustedHostMiddleware`（白名单 127.0.0.1/localhost/[::1]）。
6. 缺基础安全头：新增 `SecurityHeadersMiddleware`（nosniff / X-Frame-Options DENY / Referrer-Policy / Permissions-Policy）。
   CSP 暂不启用并在文档说明原因（页面大量内联脚本，不精确的 CSP 会打白）。
7. `GET /api/admin/users` 回传完整手机号 → 列表脱敏（`138****8000`）。

**新增工具**
8. `scripts/verify/system_audit.py`：34 项体检（服务/配置/上游/缓存/存储一致性/安全），`--gate` 可作发布闸门。

**验证**
- 体检：**32 PASS / 2 WARN / 0 FAIL**（WARN 为 `ADMIN_TOKEN` 未配置、OpenAPI 本机可达，均有意保留并写明处理时机）。
- 账实：缓存 `累计 2024764 B = 实算 2024764 B`；命中率 80.7%；链路 7/7 fresh。
- 回归：`pytest` **357 passed**（+8 缓存审计）、`ci_guard` 三 PASS、`perf_budget --gate` 退出 0、
  5 个 Playwright 验收 18/21/19/17/13 全 PASS。
- 文档：`docs/40-ops/DEBUG-AUDIT-2026-11-02.md`（每项含定位/修复/验证）。

## 2026-11-02（23:30）— 全 A 股分层同步契约 + 结论溯源（layer-sync.v1 · lineage.v1）

**背景**：三层能跑通，但「层间到底同步了什么」只存在于代码里 —— 判断层拿到的是哪几个字段、
决策层的结论能倒推回哪一版事实，答不上来。同时实测发现三处数据失真：北交所报价缺口被整体
覆盖率掩盖、`920` 代码段被记成沪市导致标的池虚增、板上 ST 的涨跌停阈值套错。

**改动：契约**
1. 新增 `docs/10-architecture/LAYER-SYNC-CONTRACT.md`：把事实→判断、判断→决策、决策→留痕
   三条边界的**内容范围 / 字段规范 / 更新频率 / 传递方式**写死，字段表带单位与必填标记。
2. 字段规范七条：契约三件套（`version`/`as_of`/`trace_id`）、时点语义（`as_of`=数据时点、
   `generated_at`=生产时间）、时间格式统一解析、单位（元/股/亿元/百分点）、口径（`full_a_share`
   vs `core_sample` 不混比）、来源标注（代理必须写 `proxy`）、缺失用 `null` 不用 0。

**改动：事实层到底（沪深京）**
3. 新增 `server/market_coverage_bj.py`：东财 `clist m:0+t:81+s:2048` 直连北交所，363 只 / 523ms，
   补新浪未收录的 9 只，并提供第二来源交叉验证；单测覆盖「源失败 → `ok:false` 不伪装」。
4. **修数据完整性**：`_prefix_code` 把 `920xxx` 判成沪市，同一只北交所股票同时以 `sh920xxx`
   与 `bj920xxx` 存在 —— 标的池 6127 里约 355 条是幻影。修正后 **5782 只**
   （沪 2362 / 深 2944 / 京 476），有效报价 5581，覆盖率 91.1% → **96.5%**。
5. **修涨跌停口径**：北交所 30%（含 ST）、创业板/科创板 ST 仍 20%、主板 ST 5%。此前 ST 一律按
   4.8% 判，双创 ST 被误算成涨停。修正后涨停 145→**141**、跌停 23→**19**。
6. **修 `/api/decision/universe` 长期空返回**：`_LAST_ROWS` 被重新赋值，三处 `from-import`
   拿到的是旧对象；改为就地 `clear()+extend()`，并让排序把缺字段的行排到最后而不是丢掉。

**改动：结论溯源（lineage.v1）**
7. 新增 `server/lineage.py`：`coverage → packet → sentiment → sentiment_flow → survey → experts
   → crosscheck → memo` 八节点，每节点记 `version / as_of / built_at / trace_id / ok / digest / facts`；
   `digest` 是关键字段的 sha1 前 12 位，依据换版即变。
8. 结论自带 `citations[]`（含 `same_trace`）与 `lineage{chain_ok, consistent, gaps}`；
   新增 `GET /api/brain/lineage/{trace_id}` 与 `/lineage/recent`，一条命令回放整条依据链。
9. **闭环钉住快照**：新增 `pin_packet()`，闭环开始时钉住 packet（`force` 不得越过）。此前 packet
   TTL 55s、上游慢时构建要 27s，一轮闭环会跨两三代快照，实测 3 个节点 trace 不一致；修复后 **0**。

**实测**
- 覆盖：5782 只（沪 2362/2320 · 深 2944/2907 · 京 476/354），整体 96.5%，北交所 74.4%。
- 闭环：`lineage.ok=true`、`consistent=true`、`gaps=[]`，8 节点 digest 全非空。
- 回归：`pytest` **349 passed**（+13 溯源、+5 快照钉住）；`ci_guard` 三道闸门 PASS。

**已知边界（写进文档 §10）**：公告/舆情仍是抽样 10 条而非全市场公告流；资金流含代理；
北交所 122 只无报价（只在季报里存在，不用估值反推价格）；判断层 600s 会让情绪滞后。

## 2026-11-02（23:05）— 决策台架构规范：总线记账统一 + 配置可升级回滚 + 延迟预算可判（decision-desk-arch.v1）

**背景**：三层已经能合到一处、也能相互对证，但离「可交给别人维护的架构」还差三件事：
① 谁也不清楚三层精确的职责边界与数据契约；② 配置只能手改，没有预检、没有留痕、没有回滚；
③ 「低延迟」只是感觉，没有预算、没有实测基线、没有判定命令。

**改动**
1. **总线记账统一**：`publish()` 自己写 `last_refresh / refresh_count / last_ms / last_trace`。
   此前五个发布点各抄一遍，其中三处漏了耗时（页面长期显示 0ms）。顺手修掉两张缓存键映射表——
   `sentiment_flow`/`survey` 的键只在一处登记，失效会静默打偏；现在只在 `Resource.cache_key` 声明一次。
2. **新鲜度以缓存真值为准**：`as_dict()` 同时读总线时间戳与缓存条目，取**较新的一个**；
   未上报过耗时返回 `null` 而不是 `0.0`（0 会被读成「0 毫秒构建」）。
3. **修掉一个真缺陷**：`survey` 从不向总线报到 → 链路视图里它永远 stale，能力图把它算成 degraded。
   补上 `publish` 后能力图从 **ok 4/15** 变成 **13 ok / 2 degraded / 0 blocked**。
4. **配置升级机制**：`validate_payload()` 抽成纯函数（命令行与运行期同一套判据）；
   新增 `scripts/brain_config_admin.py`（`show/validate/apply/rollback/history`）：
   预检不通过**退出码 2、磁盘零改动**，写入走 `tmp + os.replace` 原子替换，
   每次变更留 `data/brain_config_history/` 快照 + `jsonl` 索引（sha256 / 变更路径 / 备注 / 告警数），可回滚。
5. **配置可观测**：新增只读 `GET /api/brain/config`（身份 / 校验 / 历史 / 生效值）。HTTP 上不开写口。
6. **延迟预算可判**：新增 `scripts/verify/perf_budget.py`——10 条读路径 p95 预算 + 冷启 + 并发压测，
   `--gate` 超预算即失败。
7. **架构规范成文**：`docs/10-architecture/A-SHARE-REALTIME-DECISION-DESK.md`，
   含整体架构、三层边界与数据契约、九步联动、配置升级策略、性能目标与硬约束、A1–A8 验收标准。

**实测（同机同进程，2026-11-02 22:4x）**
- 热态 p95：health 23.6ms、config 29.7ms、bus 17.3ms、对证 light 25.2ms、dashboard 32.0ms、对证 full 27.3ms。
- 冷启：首个触发全市场聚合的请求 3080ms（此后共享 packet），调查 2554ms，其余 4–58ms。
- 并发 8×6×6：**275.9 req/s**、p50 26.5ms、p95 42.7ms、**错误率 0**。
- 容量：TTLCache 120/800 条、1.46MB/64MB（2.2%）。
- 对证：9 项 7 一致 / 2 不可比 / 0 冲突，score 1.0。

**结果**
- `pytest` **331 passed**（新增总线记账 5 项 + 配置工具 9 项）；`ci_guard` 三道闸门 PASS。
- `perf_budget.py --gate --concurrency 8` 退出码 0；5 个 Playwright 验收 18/21/19/17/13 全 PASS。
- 顺手修掉接口文档失真：`artifacts/openapi.json` 快照过期，导致 API-REFERENCE 少 44 个端点（141 → 185）。

## 2026-11-02（22:10）— 主脑三层对证 + 阈值集中配置（layer-crosscheck.v1 · brain-config.v1）

**背景**：三层（事实 / 判断 / 决策）看的是同一个市场，却各算各的：判断层把结构分
（宽度+指数+涨跌停合成）标成「情绪分」显示 100，事实层同一时刻是 80.5；涨停 145 vs 12
是两种口径却直接相减；阈值散落在 8 个模块的常量里，改一处漏一处。**没有任何机制能发现
某一层算错了**——这才是「三层能力不对一」的实质。

**改动**
1. 新增 `data/brain_config.json` + `server/brain_config.py`（**brain-config.v1**）：
   容差 / 分档 / 时点上限 / 分类体系标签集中一份；深合并默认值、按 mtime 热重载、
   `validate()` 把越界或类型错误的项回落默认并返回 `issues`，坏 JSON 保留上一份好配置。
2. 新增 `server/layer_crosscheck.py`（**layer-crosscheck.v1**）：九项检查、八类判据
   （情绪同源项分查事实↔判断、事实↔决策），每项给 `a / b / delta / tolerance / status / note`。
3. **状态语义分五档**：`pass` 一致 / `warn` 超容差但在 2× 内 / `conflict` 真冲突 /
   `incomparable` 不可比（异量纲、分类体系不同、口径不同）/ `unavailable` 缺数据。
   后两者**不参与打分**——降级不能伪装成不一致，异量纲也不该制造假精度。
4. 结论 = `无 conflict 且 可比率 ≥ 0.5 且 无 blocker`；口径不同时显式挂 `scope_mismatch`
   阻塞并把宽度类改判 `incomparable`，而不是报一堆假冲突。
5. 判断层 payload 增 `facts`（上涨/下跌/平盘/涨停/跌停/成交额/来源/口径/时点），
   对证改读「判断层真正用到的输入」，不再读二手汇总 `data_coverage.ashare`。
6. 接进闭环与页面：`run_cycle()` 增加 crosscheck 步骤写入 `brain-cycle.v1`；
   `compose_memo` 有冲突时**置信 ×0.85** 并写入风险；`GET /api/brain/alignment`
   的 `ok` 只表示接口成功、业务结论放 `healthy`；决策层顶部常驻「三层对证 8/8 · 一致性 100%」
   条 + 逐项 chip + 「重新对证」。两档缓存分开（页面 light / 闭环 full）。

**对证当场抓到的 5 个真问题（均已修）**
- 判断层「情绪分 100」实为结构分 → 标签改「结构分」并附真实情绪分；
- 降级时 4 项永久 `unavailable` → 增 `facts` 后全部可判；
- 涨停 145 vs 12 被判冲突 → 口径不一致改 `incomparable` + `scope_mismatch`；
- 领涨板块永远对不上（申万 vs 东财）→ 登记 `sector_taxonomy`，异体系判不可比；
- 页面小条显示「暂不可用」→ 接口 `ok:false` 被前端 `jget` 当成请求失败，改为 `ok:true` + `healthy`。

**结果**
- `scripts/verify/verify_layer_crosscheck.mjs` **18/18 PASS**：配置校验通过、对证 9 项
  （light 8 项）、`ok/score/counts/comparable_ratio` 齐全、异量纲项不判数值、页面 chip 数与轻量档一致、
  无 `undefined/NaN`、控制台零错误。
- 实测：`healthy true · score 1.0`，9 项中 **7 一致 / 2 不可比 / 0 冲突**，时点偏差 27s；
  页面条读作「三层对证 一致 6 · 不可比 2 · 一致性 100%」（light 档 8 项）。
  条上刻意不写「6/8」：不可比按 1.0 计分，分子分母并列会与一致率自相矛盾。
- 新增单测 `tests/test_brain_config.py`(7) + `tests/test_layer_crosscheck.py`(16)，
  全库 **316 passed**；`ci_guard` 三道闸门 PASS；回归 21/19/17/13 全通过。
- 新增文档 `docs/10-architecture/LAYER-CROSSCHECK.md`。

**顺带修掉的文档与仓库失真**
- `static/docs-graph.json` 重生成：41 节点/105 边 → **52 节点/128 边**（0 孤立、0 悬空、0 缺失），
  此前 11 篇新增文档根本没进图；同步 `AXON-GRAPH-DESIGN.md` / `UI-VISUALIZATION-ROADMAP.md` /
  `DEPLOYMENT.md` 里过时的规模数字。
- `project-docs-data.js`：补登记 13 篇架构文档（含 ADR-001/002、DECISION-HUB、LAYER-CROSSCHECK），
  并把一条错放「问题及解决方案」的版本总账移回「重要里程碑」（此前它在问题表里三列全空）。
- `static/**/*.bak-*` 共 **61 个 / 12MB** 备份归档到 `_archive/static-backups-2026-11-02/`：
  `static/` 是 Web 根目录，这些备份此前可被直接访问（如 `/dashboard.html.bak-v85`），代码零引用。
  `REPO-HYGIENE.md` 增「static 不许放 .bak」规则。

## 2026-11-02（21:45）— 三工作区合一：决策台 = 主脑中心（decision-hub.v1）

**背景**：大盘纵览 / 动态市场调查 / 决策台三个并列工作区，读的是同一份
`/api/market-dashboard`（决策台本身就调用它），卡片大量重复；同一屏还会出现两个情绪分。

**改动**
1. 三个工作区合并为**决策台 · 主脑中心**的三层：事实层（行情）→ 判断层（调查）→ 决策层（结论）。
   `dd-hub-bar` + 三个 `dd-hub-seg`，靠 `.is-active` 互斥；hash 同步 `#fact / #survey / #decision`。
2. 工作区导航从 5 个减到 3 个（策略选股 / Qlib / 决策台）；`#market`、`#survey` 保留为兼容别名。
3. **修掉一个真实冲突**：`market-dashboard.js` 与 `intelligent-analysis.js` 都导出
   `window.switchWorkspace`，后者后加载覆盖前者——此前所有工作区切换实际跑的是后者。
   现在只保留一份实现，调查侧只暴露 `initSurveyLayer()` / `leaveSurveyLayer()`。
4. 决策层「市场情绪 v1 · 六维结构」精简为「情绪摘要 · 决策口径」，六维明细只在事实层维护。
5. 事实层去掉重复的「大盘纵览」大标题；结论条情绪分改用统一口径，并在情绪返回后重刷
   （此前同屏 83 vs 80）。
6. 卡片内「大盘纵览 / 完整调查」链接改为「事实层 / 判断层」，点击在本中心内切层。
7. 轮询按层收敛：事实层 45s、决策层 60s、判断层按用户开关；不在当前层不空转。

**结果**
- `scripts/verify/verify_hub_merge.mjs` **20/20 PASS**：工作区 3 入口、三层互斥、情绪分一致（80=80）、
  卡片内跳层、`#market→#fact`/`#survey→#survey`、1600/390 零溢出、控制台零错误。
- 回归：`verify_market_merge` 19/19、`verify_split_round` 13/13、`verify_armillary_drag` 17/17、
  `pytest` 290 passed、`ci_guard` 全 PASS。
- 新增文档 `docs/10-architecture/DECISION-HUB.md`；旧合并文档加「后续演进」注记。

## 2026-11-02（21:25）— 大盘纵览 × 市场情绪追踪合并（market-merge.v1）

**背景**：两个页面各抓一批行情、各算一个「情绪分」。同一系统里存在三种情绪口径
（dashboard.judgment / /api/market-sentiment / sentiment.v1），同一分数在不同面板显示成
「偏热 / 偏暖」两种说法；sentiment.html 的 11 张卡片里有 9 张与大盘纵览重复。

**合并方案（自拟）**
1. **只留一个市场页**：`dashboard.html#market` 按「结论 → 事实 → 状态 → 下钻」组织；
   `sentiment.html` 降级为兼容入口（3 秒跳转，保留 `?sector=`），附「原面板去向」表。
2. **情绪只留一个口径**：gauge 分数与档位取自同一响应 `sentiment-flow.v1`
   （`level` + `band`），`/api/decision/sentiment` 仅提供六维明细；接口不可用时回退
   `dashboard.judgment` 并在溯源里标注「回退」。
3. **分档阈值对齐**：`sentiment_tracker._BANDS` 30/45/58/72 → 20/40/60/80，措辞与
   `decision_snapshot._status_of` 完全一致（极冷/偏冷/中性/偏热/过热），
   并用测试对 `_status_of` 做交叉断言防止再漂移。
4. **情绪卡升级为状态带**：六维（带比例条）+ 情绪追踪（走向 / 近端变化 / 斜率 / 20 日分位 /
   持续性 / 拐点）+ 归因条（宽度/动量/量能贡献度）。
5. **三锚点迁入**：量能锚（成交额分档）/ 均线锚（上证收盘 vs MA5，真实日线）/ 情绪锚（统一分档），
   替代原独立页的信号塔。
6. **6 档涨跌分布归位**：从情绪卡移入「市场广度」卡，情绪卡高度 443 → 350px。
7. **入口统一**：页头、侧栏、量化页、管理后台、接口页、首页、图谱、文档站、决策台动作共 9 处
   指向 `dashboard.html#market`；`?sector=` 透传后自动筛选涨跌榜。
8. **深链**：新增 `#market / #decision / #survey / #qlib` hash 路由。

**结果**
- `scripts/verify/verify_market_merge.mjs` **17/17 PASS**：页面 80 分 = 接口 80 分；
  六维 6 项、追踪 4 指标、归因 3 条、三锚点 3 项；1600/390 零溢出；旧入口跳转保留参数；控制台零错误。
- 全量测试 **290 passed**；`ci_guard` 三道闸门全 PASS。
- 新增文档 `docs/10-architecture/MARKET-SENTIMENT-MERGE.md`（重合清单 / 合并架构 / 数据连接 / 验证）。

## 2026-11-02（21:05）— 情绪追踪并入市场调查 + 主脑能力契约 + 浑天仪拖拽根治

**背景**：情绪只有「此刻多少分」，无变化/拐点/持续性；调查与情绪是两套口径且从不交叉；
主脑只有调度、没有能力契约；星系浑天仪在极点与右侧栏仍拖不动（用户第三次反馈）。

**情绪追踪（sentiment-flow.v1）**
1. 新增 `server/sentiment_tracker.py`：纯函数 `build_flow()` 派生
   `level / band / regime / delta_short / delta_day / slope_per_hour / z20 / percentile20 /
   persistence / turning_point / drivers / drags / flags / evidence`。
2. 贡献度 = `(维度值-50) × 可用维度重归一权重`；样本 < 5 点时分位/斜率返回 None 并**降置信**，不编造方向。
3. `get_sentiment_flow()`：按 `trace_id + level` 命中缓存 → 落盘 `data/sentiment_flow.json`（240 点）
   → 发布总线资源 `sentiment_flow`。

**动态市场调查（survey-pipeline.v1）**
4. 采集阶段四路并发（新增 `_sentiment_flow_safe()`，12s 超时，失败不拖垮调查）。
5. 诊断阶段新增 `_sentiment_brief()` / `_divergence()` / `_sector_linkage()`：
   结构状态与情绪走向**交叉校验**，背离升级为「情绪-结构背离」预警。
6. payload 新增 `sentiment / divergence / sector_linkage / pipeline / inputs` 五键（只增不改）；
   `sector_linkage` 让每个板块自带「数据来源 + 窗口 + 时点」，不再需要读代码才知道口径。

**主脑（brain-capability.v1 / brain-cycle.v1）**
7. 新增 `server/brain_capabilities.py`：15 项能力（8 计算 + 7 决策），每项含
   `inputs（总线资源键）/ outputs / metrics[].target / fallback`；`capability_graph()` 26 条边；
   `report()` 按总线新鲜度输出 ok/degraded/blocked 与 available_ratio。
8. `compose_memo()` 纯函数：仓位规则显式（情绪降温 ×0.7、结构-情绪背离 ×0.8、否决归零、硬顶 80%），
   输出立场/理由/动作/风险/失效条件五要素（brain-memo.v1）。
9. `DAPENGBrain` 新增 `capabilities()` 与 `run_cycle()`：情绪 → 调查 → 专家 → 备忘录，共享 trace，
   结果写 KV 与洞察记忆。
10. data_bus 注册 `sentiment_flow`、`survey`，链路顺序扩为 7 段；5 个新端点（只增不改）。

**星系浑天仪（v85）**
11. 根因三条：① 两环欧拉在俯仰 ±90° 退化（水平拖拽变绕视线自转，实测 73.7px → 22.9px）；
    ② 拖拽增量被 `loop3D` 二次累加（松手后 3.25s 仍漂移 8.6px）；③ `DRAG_BLOCKERS` 含 `button`
    导致右侧说明栏（全是 `button.panel-row`）拖不动（3.3px）。
12. 修复：加**第三环滚转 rz**（`R=Ry·Rx·Rz`）+ 拖拽改**屏幕轴预乘旋转**；拖拽期间跳过惯性；
    `DRAG_BLOCKERS` 移除 `button`；删掉 `COSMO/galaxyArms/galaxySpinAngle` 重复定义。

**结果**
- 全量测试 **253 → 290 项通过**（新增 `test_sentiment_flow.py` 15 项、`test_brain_capabilities.py` 22 项）。
- 情绪追踪实测：`80.5 分 / 过热 / 20 采样 / 斜率 +0.18 分每小时 / 分位 85 / 驱动=宽度+10.5、动量+8.8、量能+6.8`。
- 主脑能力矩阵：15 能力 / 26 边 / ok 11 · degraded 4 · blocked 0 / 可用率 0.867。
- 闭环实测：情绪过热 + 结构强势上行 + 专家 +0.846「进攻」→ 备忘录「进攻、仓位上限 40%、置信 0.58」，
  含 3 条失效条件；缓存命中时 0.4ms。
- 浑天仪验收 **17/17 PASS**：极点水平 22.9→80.1px、超极点 18.9→63.2px、侧栏 3.3→64.0px；
  按钮仍可点击、松手零漂移、控制台零错误。
- 架构守卫：依赖环 0 / 跨层反向依赖 0 / >900 行模块 0；模块 170 → 172，路由 182 → 187。

**文档**：新增 `docs/10-architecture/SENTIMENT-SURVEY-BRAIN.md`（现状问题/改进思路/模块划分/数据连接/实现步骤/验证）；
`AXON-GRAPH-DESIGN.md` 追加 v85 条目。

## 2026-11-02（20:40）— P2–P4 收官：模块瘦身 + 契约统一 + 工程卫生

**背景**：P1 结束时仍有 10 个模块 > 900 行（最大 `strategies.py` 1562 行），
响应信封各写各的、失败无稳定错误码，根目录堆积 20+ 临时脚本。

**P2 模块瘦身（门面 + 分片）**
1. 新增 `tools/split_module.py`：按符号块机械拆分，`symtable` 算跨片引用自动补 import，
   原路径保留为**只做再导出**的门面；路由文件的门面改为 `include_router` 聚合器。
2. 10 个模块全部拆分：`strategies`(3) · `indicators`(4) · `routers/automation`(3) ·
   `routers/admin`(4) · `routers/kline`(3) · `qlib_factor_engine`(3) ·
   `stable_market_provider`(4) · `market_coverage`(5) · `fundamental`(4) · `decision_experts`(4)。
3. 拆分器三处坑（已修进工具，见 ADR-002）：`from __future__ import annotations` 让注解不进
   `symtable`（漏 `ConfigUpdateBody` → `app.openapi()` 抛 `PydanticUserError`，`/docs` 500）；
   分片成环（`signals ↔ tech`）需合并区间；门面只能导出「本片定义」的名字。
4. 顺带修出真实缺陷：`get_latest_expert_panel()` 用 `kv_get` 但只在另一函数里局部导入，
   调用即 `NameError`——`kv_get/kv_set` 提到模块级。

**P3 契约统一**
5. 新增 `server/contracts/envelope.py`：`SCHEMA_VERSION="1.0"`、`ErrorCode`、`code_for_status`、
   `ok()` / `fail(code,message,detail)` / `degraded_payload()`；响应头 `X-Schema-Version: 1.0`
   由全局中间件注入（payload 零改动）。
6. `automation_common` / `evolution` / `review` 三份同构 `_ok`/`_fail` 收敛为契约层别名；
   `etf`/`ict` 的领域降级信封保留（契约层不猜业务语义）。

**P4 工程卫生**
7. 归档：`_verify_*.mjs`/`_shot_*.mjs`/`_test_cache.py` → `scripts/verify/`；
   `_patch_*.py`/`_audit_undefined.py` → `_archive/patches/`；截图与中间 json → `_archive/tmp/`；
   拆分前原件 → `_archive/split-2026-11-02/`。映射表见 `docs/10-architecture/REPO-HYGIENE.md`。
8. 清掉根目录一个历史事故目录（`server" && cp C:…`，Windows 私用区字符，内含 payment 误拷贝副本）。
9. 新增 `scripts/ci_guard.py`（架构测绘 + 拆分计划 + 全量测试，CI 唯一入口）、
   `ADR-001-contract-envelope.md`、`ADR-002-module-split-facade.md`、`REPO-HYGIENE.md`。
10. `.gitignore` 补 `_routes_*.txt` / `_chk.json` / `_e.json` / `_backup/` / `artifacts/`。

**结果**
- 超大模块（>900 行）**10 → 0**（最大 694）；模块 131 → **170**；函数 1444 → 1443；同名函数 19 → 18。
- 依赖环 **0**、跨层反向依赖 **0**（`arch_report --check` 退出码 0）。
- 路由 **182 条 method+path 逐条一致**（排序 diff 为空），并冻结为 `tests/data/routes_baseline.json`。
- 全量测试 **243 → 253 项通过**（新增 `tests/test_contracts.py` 10 项）。
- `server.main` 导入 1.59/1.64/1.61s（中位 1.61s，+4.5%）；`/api/market-dashboard` 热缓存 9.4–10.2ms。
- 端点回归全 200：`decision/*`（9）· `review/summary|weekly` · `automation/config|positions` ·
  `admin/dashboard` · `kline/indicators/catalog` · `multi-period-resonance` · `openapi.json`。
- 浏览器验收 `scripts/verify/verify_split_round.mjs` **13/13 PASS**（文档页 P2/P3/P4 行、1600/390 零溢出、
  锚点零死链；决策台 17 卡 + 6 专家 + 扫描卡 + 控制台零错误）。

**兼容性代价**：门面只做再导出，`server.<mod>.<可变全局> = x` 不再影响实现分片——
patch 必须打到实现模块（`tests/test_decision_loop.py` 已按此修正）。

## 2026-11-02（20:20）— P1 收尾：最后一条反向依赖清零 + 数值/时间内核统一

**背景**：P0 后仍剩 1 条反向依赖（`loop_scheduler → routers.review`）与 19 处重复辅助函数
（`_now_iso`×9、`_finite`×6、`_safe_float`×6、`_clamp`×4），同一语义有 0/None 两种回退，易误用。

**改动**
1. 新增 `server/trading/review_service.py`：权益、FIFO 配对、基准对比、周报生成下沉；
   `routers/review.py` 保留薄封装与 `_xxx` 兼容别名，`automation.py`、`loop_scheduler.py` 改用服务。
   复盘归入交易域（与 `review_engine` 同级），避免 services 层跨到 engine。
2. 新增 `server/core/numbers.py`：`to_float`（缺失→None）/ `safe_float`（缺失→业务默认）/ `to_int` / `clamp`。
3. 新增 `server/core/timeutil.py`：`now_iso`（统一带时区、秒级）/ `iso_of`。
4. 迁移 19 个模块、约 120 处调用；删除全部本地 `_finite/_now_iso/_safe_float/_clamp` 定义，
   调用点通过 import 别名零改动。
5. 修复迁移中暴露的真实缺陷：`weekly_report` 内 `store = await store()` 自我遮蔽
   （UnboundLocalError）、服务缺少 `cache` 导入。
6. 新增 `tests/test_core_utils.py`（20 项）：空值/bool/NaN/inf/时区/精度契约。

**结果**
- 反向依赖 **1 → 0**（白名单清空）；依赖环维持 **0**。
- 重复函数条目 **23 → 19**；四类本地重复定义 **19 → 0**。
- 全量测试 **223 → 243 项通过**。
- `/api/market-dashboard` 热缓存 9.3–10ms；`server.main` 导入 1.54s（原 1.59s）。
- 端点回归：`market-dashboard`、`review/summary|weekly|weekly-list`、`decision/*`、`stream/stats` 全部 200。

## 2026-11-02（20:05）— 系统架构治理：依赖环清零 + 服务层抽取 + 架构守卫

**背景**：系统已积累 127 个 Python 模块 / 44,937 行 / 178 个路由，但缺少结构约束：
数据层反向依赖路由层、brain 与研判引擎循环引用、辅助函数多处重复，改动时容易踩到隐式耦合。

**新增**
1. `tools/arch_report.py`：AST 测绘工具，输出模块清单、分层分布、import 环、反向依赖、
   超大模块、重复函数、裸 except、TODO；`--check` 违规即非 0，可挂 CI。
2. `docs/10-architecture/SYSTEM-OPTIMIZATION-2026Q4.md`：现状测绘、10 项问题清单、
   目标架构与命名/异常规范、P0–P4 分阶段计划（含每阶段验收与预期）、兼容与性能约束、风险回滚。
3. `tests/test_architecture.py`（7 项）：无环、无未登记反向依赖、已知项必须带计划、
   services 不得依赖 routers、dashboard 单一实现、KV 端口降级、行情视图不补 0。

**重构**
1. 抽取 `server/services/market_dashboard.py`（build_dashboard / get_dashboard / last_good）；
   `routers/market_dashboard.py` 由 390 行降到约 30 行，保留 `_build_dashboard` 兼容别名；
   `ak_market` / `decision_snapshot` / `market_coverage` / `market_survey_engine` / `qlib_factor_engine`
   共 5 处改为依赖服务层。
2. 新增 `server/infra/kv.py`（KV 端口）：`trading` 装配期绑定，`brain` 委托，
   `decision_experts` / `decision_snapshot` 直连端口，消除 `brain ↔ experts ↔ snapshot` 环。
3. 抽取 `server/services/quote_view.py`（build_quote / bar_ts / market_open / opt_num）；
   `stream` 保留同名别名，`quote_hub` 改依赖服务，消除 `quote_hub ↔ stream` 环。
4. 修复测试事件循环混用（`get_event_loop` → `asyncio.run`），消除顺序敏感。

**结果**
- 依赖环 **3 → 0**；未登记反向依赖 **10 → 0**（1 条已登记并附 P2 计划）。
- 全量测试 **216 → 223 项通过**。
- 端点回归：`market-dashboard` / `decision/*` / `stream/*` 全部 200。
- 性能：热缓存 10ms、冷启动 1.15s、`server.main` 导入 1.59s，与改造前持平。
- 兼容：端点路径、响应字段、缓存键、数据库结构均未变，前端零改动。

## 2026-11-02（19:40）— 修复星系拖拽死角：拖拽范围扩到整个图版

**问题**：用户反馈「仍然有死角无法拖拽」。读数 + 网格探测后定位到真因：旋转拖拽只绑定在
`#graph3d`（中间 SVG），右侧说明栏、底部图例、题签区都在监听范围之外——鼠标落到这些区域时
指针事件不会进入相机逻辑，表现为整片区域拖不动。俯仰范围只是次要因素。

**排查方法**：关闭星系自转（`galaxySpin:off`）后逐点采样 `.n3d` 的 transform，
分别在图区、右栏、底部带、页脚、题签区拖拽，只有「底部带」不变化；再用
`elementFromPoint` 确认命中元素为 `.lg-item[role=button]`，最终定位到两处叠加原因。

**改动**（`plate-graph.html`）：
1. 旋转/平移的 `pointerdown` 从 `G3` 改绑 `PLATE`（整个图版面板），仅排除
   `a / button / input / select / textarea / label / .zoomer / .sh-search / .scope-bar / .focus-bar / .prefs`。
2. 图例项是 `span[role="button"]`，原被 `[role=button]` 规则挡掉；改为允许起拖，
   并在拖拽结束后**定向抑制**起点元素的那一次 click（不是全局时间窗），
   保证「拖拽只旋转、轻点仍筛选」。
3. 去掉 `setPointerCapture`：它会把后续 click 的 target 改到 PLATE，导致图例点击全部失效。
4. 拖拽期间用 `user-select:none` 抑制文字选区，不调用 `preventDefault`（避免吞掉 click）。
5. 3D 模式下整块面板 `cursor: grab`，`.figure` 加 `touch-action: none`。

**验证**（关闭自转做纯净测量，1600 / 1280 视口）：
- 图区、右栏、底部带、页脚、题签区 5 处拖拽全部使节点 transform 变化；1280 同样通过。
- 图例：拖拽后 `aria-pressed` 不变（不误触筛选）；轻点正常切换 `true → false`。
- 拖出面板边界仍持续旋转；右侧栏拖拽不产生文字选区；视图按钮照常循环（星系→平面）。
- 控制台零错误。注：≤1240px 布局本身不提供 3D 视角（按钮隐藏），故不在窄屏验证范围。

## 2026-11-02（19:20）— 星系视角取消俯仰限制，任意角度可达

**问题**：AXON「三维视角 → 星系（浑天仪环绕）」的水平旋转可以无限环绕，但垂直角度被限制在
`0.25–1.15 rad`（14°–66°）——只能在一个很窄的俯视区间拖动，无法俯视全景，也不能翻到环面下方，
所以拖起来像 2D 平移而不是 3D 环绕。第一版放宽到 ±83° 后仍有极区死角，本轮改为整圈环绕。

**改动**（`plate-graph.html`）：
1. 星系的 pitch 由 `clamp` 改为 `wrapPi`：垂直方向可连续转整圈，越过极点画面上下翻转但**不会停住**，
   任意角度可达；水平 yaw 仍为无限环绕。拖拽与惯性回落两处同步修改。
2. 其余 3D 视角（轴测 / 动态 / 环幕）仍保留 `clamp(0.12, 1.5)`，避免非星系视图翻面。
3. HUD 提示改为「拖拽 360° 全向环绕（无死角）」；`GAL_RX_LIMIT` 仅保留给聚焦/复位等收敛逻辑。
4. 3D 星图（`plate-starfield.js`）的 pitch 同步改为 `wrapPi`，两处视角手感一致。

**取舍**：越过极点时环面会上下翻转，这是球面旋转的正常表现（等同转动实体浑天仪）；
若方向不顺手，点工具栏「复位」即可回到标准视角。

**验证**：星系视角连续 6 次向上拖拽（每次 520px，跨越两次极点），节点 transform 每一步都变化，
无任何卡死区间；横向连续 3 次拖拽同样持续变化；极点附近截图环、节点、标签仍完整渲染；控制台零错误。

## 2026-11-02（19:10）— 修复文档页「AXON 图版」入口丢失

**问题**：顶栏分组成「视图切换 / 业务入口 / 工具」后，`docs-graph-personal.js` 的
`injectTopbarLink()` 仍以组内按钮为参照执行 `host.insertBefore(a, first)`。参照节点不是
`host` 的直接子节点，`insertBefore` 抛 `NotFoundError`，又被 `try/catch` 静默吞掉，
导致「AXON 图版 ↗」入口不再出现。

**修复**：改为插入业务入口分组（`.docs-nav-group.is-business`）首位；失败分支改为
`appendChild` 兜底。`docs-graph-personal.js` 版本推进至 v4。

**验证**：文档页顶栏恢复「列表 | 图谱 · AXON 图版 ↗ 返回看板 接口目录 量化回测 市场情绪 · 首页 后台」；
1600 / 1280 / 768 / 390 四档 `scrollWidth == viewport`、零溢出、零控制台错误；入口在各档均可见。

## 2026-11-02（17:10）— 3D 星图迁入 AXON 图版

**背景**：上一轮把文档面板右上角的「3D 星图 360°」连同实现文件一并删除。用户指出星系效果本就属于
AXON 图谱，不应从文档面板直接删掉。本轮将其**重建并迁入 AXON 图版**，文档面板保持干净顶栏。

**涉及文件**：`static/js/plate-starfield.js`（新增）、`static/css/plate-starfield.css`（新增）、
`static/plate-graph.html`（入口 + 资源引用）。

**实现方式**：
1. 独立增强层，不改 `plate.js` 的视图机与 SVG 渲染；入口放在图版工具栏「三维视角」按钮右侧（`#starfield-btn`）。
2. 布局：球面斐波那契初始化 + 3D 力导向（斥力 / 弹簧 / 分组锚点，320 次迭代一次性求解），
   节点 id 做确定性随机——同一份 `docs-graph.json` 每次打开布局一致。
3. 渲染：yaw/pitch 旋转 + 近大远小透视，画家算法按深度排序；连线按深度给透明度；
   节点辉光按目录族配色（墨金 / 钢蓝 / 驼 / 灰绿等七组）。
4. 标签：仅枢纽（度数 ≥ 7）与选中/悬停显示，按深度淡出，矩形碰撞避让 + 左右边界收拢。
5. 交互：拖拽 360° 环绕、滚轮缩放、点击节点出档案卡（分组 / 度数 / 行数 / 更新时间 / 摘要 +
   「在原版图谱中查看」深链）、空格暂停自转、Esc 关闭、重置视角。
6. 无障碍：`role=dialog` + `aria-modal`，打开焦点入内、关闭回入口，尊重 `prefers-reduced-motion`。
7. 移动端：≤860px 隐藏图例、档案卡改为底部横条，入口按钮图标化（≤980px 隐藏文字）。

**影响范围**：仅 AXON 图版（`plate-graph.html`）；经典图谱与文档面板未改动，文档顶栏保持三组结构。

**验证**：1600 / 768 / 390 三档视口 `scrollWidth == viewport`、无 404、控制台零错误；
星图打开后 41 节点 / 105 关系正常渲染，点击节点档案卡正确（`00-strategy/README`，度数 28），
Esc 关闭并归还焦点；页面前后 AXON 图版 41 节点渲染不受影响。

## 2026-11-02（界面收口）— 移除文档面板 3D 星图，顶栏导航分组重构

**改动原因**：文档页右上角的「3D 星图 360°」把 AXON 图谱的星系视觉带进了以阅读为主的文档面板——
与文档内容无关，却以高饱和金色按钮占据首位注意力；同时顶栏 8 个按钮平铺、无层级，移动端堆到三行。

**涉及文件**：`static/project-docs.html`、`static/js/docs-graph-3d.js`（删除）、
`static/css/docs-graph-3d.css`（删除）、`docs/10-architecture/AXON-GRAPH-DESIGN.md`、
`docs/10-architecture/UI-VISUALIZATION-ROADMAP.md`。

**改动内容**：
1. 删除 3D 星图增强层的 CSS/JS 引用与两个资源文件（仅文档页引用，无其他调用方）；
   AXON 环幕（`plate-graph.html`）与经典力导向图谱（`docs-graph.js`）不受影响。
2. 顶栏导航由「8 个平铺按钮」改为三组：
   - 视图切换（列表 / 图谱，分段控件，标识当前页）
   - 业务入口（返回看板 / 接口目录 / 量化回测 / 市场情绪）
   - 工具（首页 / 后台，幽灵按钮，降低视觉权重）
   组间 1px 竖线分隔；`focus-visible` 补键盘焦点环。
3. 响应式：≤1100px 收紧按钮；≤860px 工具组右对齐换行；≤720px 重排为两行
   （视图切换 + 工具 / 业务入口整行），粘性顶栏高度 155px → 122px。

**影响范围**：仅文档页顶栏与图谱视图入口；文档正文、目录、表格、搜索与 AXON 图谱交互均未改动。
桌面单行 57px、无横向溢出。

**验证**：1600/1280/768/390 四档视口 `scrollWidth == viewport`、顶栏零溢出、无 404、零控制台错误；
文档页 17 章节 / 25 表格 / 62 目录锚点 0 死链；「图谱」视图 41 节点 / 105 连线正常渲染；
页面文本与 DOM 中已无「3D 星图」入口。

## 2026-11-02（三次升级）— 决策台分析深度 + 专家团 v3 + 全局数据总线 + 缓存治理

### 一、分析深度：新增 market-scanner.v1 派生层

**改动原因**：决策台已有全 A 股广度与指数，但缺少横截面结构、板块轮动与资金面证据；
专家团的 E2/E3 只能靠 17 个核心板块与指数资金流推断，分析深度不足。

**新增 `server/market_scanner.py`**（只消费已加载快照，不新增重量级抓取）：

| 能力 | 口径 | 说明 |
|---|---|---|
| 横截面扫描 | 全 A 有效涨跌样本（实测 5000+） | 涨幅 6 档分布、中位/均值、涨停跌停、换手 >10%/5-10% 家数、大中小市值分化、PE 四档估值带、强弱综合分（宽度 50% + 中位 25% + 涨跌停均衡 25%） |
| 板块轮动 | 东财 `BOARD_NAME` 全行业（实测 128 个） | 今日排名、相对上一交易日的排名变化、上涨行业占比、成交额占比、5/20 日动量窗口、轮动信号（主线延续 / 温和轮动 / 快速轮动 / 首日基线） |
| 资金流向 | 东财 `1.push2` clist（尽力通道） | 行业主力净流入（f62）与个股净流入排行；**被频控时退避 30 分钟并降级为「注意力代理」**（行业成交额占比变化 × 方向），首日无基线时展示当前成交份额榜，全部显式标注 `proxy:true` |

板块历史自积累：`data/sector_history.json` 每交易日一条（保留 40 日，原子写），
动量窗口随积累自动解锁，不用常数填坑。

### 二、专家团 v3：分工深化 + 联合研判 + 命中账本

- **证据升级**：E1 消费横截面强弱分与全市场中位涨跌；E2 改为全市场 128 行业轮动（排名迁移、
  轮动信号、拥挤度）；E3 消费行业主力净流入（实测）或注意力代理（标注降权）。
- **联合研判**（`build_collaboration`）：取置信度前 3 位专家各一条最强证据拼成共识论点，
  并按证据来源统计「跨域确认数」；共识被否决翻转时以加权主导方向为确认基准。
- **命中账本**（`data/expert_ledger.json`）：每日记录各专家立场（幂等），T+1 用当日情绪宽度维
  （≥55 上涨 / ≤45 下跌）回看计分，输出命中率；中性不计分，满 20 样本后用于权重校准。
- **否决复核**：E6 的「分歧超阈值」否决基于第一轮分歧，若匿名互评后终局分歧 ≤ 0.55 则撤销，
  并在 `consensus.lifted_vetoes` 留痕；数据质量类否决不受影响。

实测：共识 +0.53「进攻」，E6 分歧否决经复核撤销（0.57 → 0.47），跨域确认 3 个独立数据域。

### 三、全局数据连接：新增 data-bus.v1

**改动原因**：各模块自管 TTL，上游刷新后下游最长滞后一个 TTL，面板之间会显示不同时点数据；
SSE 只能按固定周期读缓存，刷新发生后无法即时推送。

- 资源依赖图：`coverage → packet → sentiment / scan → experts`，禁止反向依赖。
- 四类触发：时间（worker 120s/1800s）、事件（刷新即发布）、请求（TTL + single-flight）、手动（`?refresh=1`）。
- **级联失效闸门**：coverage→packet 60s、packet→sentiment/scan 40s、sentiment/scan→experts 180s，
  防止上游高频刷新把专家团（含互评与账本落盘）打成重算风暴。
- 事件驱动 SSE：`/api/decision/stream` 首帧推三份当前值，之后由总线事件即时推送，30s 心跳保活。
- 观测端点：`/api/decision/bus`（资源图、链路年龄、待重建标记、最近事件、订阅者数）。

### 四、缓存治理：cache-governance.v1

- **双上限**：800 条 + 64MB。任一超限时**先回收过期条目，再按 LRU 淘汰**。
- **单条拒写**：单条超 64MB 直接拒写并计入 `oversize_rejected`，避免一个异常大对象清空整库。
- **字节记账**：写入/覆盖/删除/回收/淘汰全路径扣减，`stats()` 暴露 `bytes / max_bytes /
  bytes_utilization / largest_entries`。
- **观测端点**：`/api/decision/cache`；联合视图 `/api/decision/system`。
- 历史文件保留：`sector_history` 40 日、`expert_ledger` 120 日、`sentiment series` 上限、
  全部临时文件 + 原子替换写入。

### 五、面板同步与接口兼容

- 决策台 14 → **17 卡**：新增「全市场扫描」「板块轮动」「资金流向」；数据源 17 → 18。
- 前端接入 SSE：脉冲条显示「● 实时 / ○ 轮询 / ○ 重连中」与「同快照 / 同窗口 / 快照时差」，
  并保留 60 秒轮询兜底（EventSource 断线自动重连）。
- 接口 7 → **11 个**：新增 `/scan`、`/bus`、`/cache`、`/system`、`/stream`；
  原有端点路径与字段不变，新增字段均为追加。
- 面板一致性：情绪、专家团、扫描共用同一 `MarketPacket` 口径；全 A 广度优先于核心样本；
  同步标记基于 trace_id，跨快照时再按 180s 数据时点容差判定。

### 验证

- `tests/test_decision_loop.py` 27 项、`tests/test_data_bus.py` 11 项；**全库 216 项通过**。
- 端点：11/11 返回 200；`/api/decision/bus` 依赖图与链路年龄正确；`/api/decision/cache` 显示
  510 条 / 5.5MB / 64MB（利用率 8.3%）。
- 事件驱动推流实测：订阅后强制刷新 sentiment / scan，客户端即时收到 `event: sentiment|scan`
  （含级联 experts），无需等待轮询周期。
- 浏览器：17 卡、1600px 与 390px 零横向溢出、控制台零错误、`streamStatus=live`、`via=stream`。

### 已知边界

- 量能维仍待成交额样本满 20 个交易日（当前用沪/深综指成交量回填并标 `estimated`）。
- 事件维仍为情报风险代理，新闻正负面分类未上线。
- 东财主力资金流在本机长期被频控，常态为注意力代理；恢复后自动切回实测（无需改配置）。
- 板块动量需 20 个交易日积累，`mom5/mom20` 当前为 null 并标注窗口进度。

## 2026-11-02（二次收尾）— 六专家两轮互评 + 量能维补齐

### 主脑研判升级为 v2：匿名两轮互评 + 交叉验证

- 新增 `deliberate_expert_opinions()`（`server/decision_experts.py`）：第一轮六专家独立研判后，以「甲-己」**匿名**互评，输出 `reviews / disputes / audit_queries / cross_validation.matrix / process`。
- 判定规则（确定性纯函数，同输入必同输出）：
  - **冲突只在方向不同时成立** —— 同为进攻而强度不同算「认同」。早期实现用连续方向差值判定，把强度差误报成对立，导致一致率恒为 0，已修复并加回归测试。
  - **修正门槛**：反对票信心之和 > 支持票 × **1.15** 才迫使改判；改判后方向向同行均值靠拢，信心按证据差衰减（上限 35%），同步下调仓位上限。
  - **坚持**：支持信心不低于反对方时保留判断，写入 `disputes`，由主脑计入分歧指数，不被静默抹平。
  - **认同**：方向一致则信心 +0.03（上限 0.95），作为交叉验证加成。
  - **E6 不参与打分**，只对「有反证却未自我修正」的专家发定向质询（≤4 条），问题取自该专家自己的 `counter_evidence`。
- `aggregate_expert_opinions()` 新增可选 `deliberation` 参数，消费**互评后**的意见，`method` 标记为 `rule_v2_deliberated`，`summary` 带完整流程串。
- 决策台专家卡新增：四步流程条（独立研判 → 匿名互评 → 反方质询 → 主脑汇总）、认同/修正/坚持计数与一致率、逐人审阅徽标、改判痕迹（首轮方向 → 互评后）、E6 质询清单。

### 量能维补齐：六维覆盖率首次达到 100%

- 新增 `get_volume_backfill()`（`server/market_coverage.py`）：拉取 **沪市综指 `sh000001` + 深证综指 `sz399106`** 日成交量（真实日 K，26 个交易日），合计近似全市场规模，1 小时缓存。
- 量能维口径优先级：**成交额 20 日分位**（样本满 20 日时）→ **成交量 20 日分位**（回填）→ `null`。
- 盘中成交量按时段折算：新增 `_session_volume_progress()`，采用 A 股日内成交量典型 **U 型曲线**（每 30 分钟区间权重，11:30 累计 52%、14:00 累计 70%），折算结果标记 `estimated: true` 并在 `note` 明示「预估，非实测」；收盘后自动转为实测。
- 实测效果：`coverage` 由 0.85 提升到 **1.00**，情绪分由五维加权改为六维完整加权。

### 测试与验证

- `tests/test_decision_loop.py` 由 10 项增至 **17 项**，新增：时段折算曲线、量能回填、量能无历史保持 null、互评匿名与可追溯、互评确定性、主脑消费互评、同向不算冲突。
- 全库 **195 项测试通过**。
- 真实接口验证：`method=rule_v2_deliberated`，4 认同 / 1 修正 / 0 坚持，一致率 80%，E6 质询 4 条；量能维 100 分位并标注 `estimated`。
- 决策台：14 卡、6 专家席位、流程条与徽标渲染正常，1600px 与 390px 零横向溢出、控制台零错误。

### 已知限制（更新）

- 量能维当前为**成交量**口径（成交额历史需累积 20 个交易日，满后自动接管）。
- 事件维仍为情报风险代理，新闻正负面分类未上线（P2）。
- 专家历史权重校准（kᵢ）与 LLM 专家接入为 P2。

## 2026-11-02 — 决策闭环 P0 全量落地：全 A 股 + 全球 + 宏观数据面，sentiment.v1 与六专家规则 v1

### 数据面：从「内置静态 + 447 只核心样本」升级为「全市场实测」

- 新增 `server/market_coverage.py`（`market-coverage.v1`），四个数据面统一构建：
  - **A 股全市场**：新浪 `Market_Center.getHQNodeData` 按 100 条/页并发拉取，实测 **5581 / 6123 只有效报价（91.1%）**，含 price/chg/amount/turnover/pe/pb/market_cap/float_cap；单次构建 18.6s，5 分钟节拍缓存。
  - **季度基本面**：东财 `datacenter-web` `RPT_LICO_FN_CPD`，实测 **5759 条（94.1%）**，PE 中位覆盖 4027 只（估值覆盖 65.8%）。分页改用 `sortColumns=SECURITY_CODE&sortTypes=1` 保证稳定，修正原先按 `UPDATE_DATE` 排序导致 12 页仅得 3461 条且重复的问题。
  - **全球**：腾讯 `qt.gtimg.cn` 批量，17 个指数 + 8 个国家代理 ETF + 11 个美股行业 ETF（行业 ETF 明确标注为风险偏好代理，不冒充当地交易所行业指数）。
  - **宏观**：19 项全球指标 —— US Treasury 收益率曲线（3M/2Y/10Y/30Y）、NY Fed EFFR/SOFR、ECB DFR 共 7 项日频，World Bank 美/中/日/欧元区 CPI·GDP·失业率共 12 项年度；另含中国 5 项宏观。
- 数据通道配置版本化为 `data/decision_config.json`：端点、鉴权方式（全部 `public_no_key`）、超时、节拍（交易时段 120s / 非交易时段 1800s）、质量门槛。**未引入任何付费凭证，无需新增密钥。**
- 后台 `start_coverage_worker()` 持续刷新，`/api/decision/coverage/stream` 以 SSE 推送进度，前端无需轮询等待。
- 明确放弃不可达路径：FRED 从本机 Python 端 TLS 恒超时（25–30s），改用 US Treasury / NY Fed / ECB / World Bank 官方源（均 2–7s 可达）；东财 push2 `clist/get` 恒 502，全市场行情改走新浪。

### 决策中台：单一事实源 + 情绪 v1 + 六专家规则 v1

- 新增 `server/decision_snapshot.py`：共享 `MarketPacket`，所有派生结论统一携带 `trace_id / as_of / generated_at / coverage / quality / evidence[] / version`，同一 trace 可复算。
- `sentiment.v1` 六维加权：宽度 30% / 动量 20% / 涨跌停 15% / 量能 15% / 全球 10% / 事件 10%；**缺失维度按剩余权重重新归一，coverage < 70% 不出总分（返回 null）**；禁止补 0 / 补 50。
- 新增 `server/decision_experts.py`：六专家规则引擎 v1 —— E1 趋势结构、E2 板块轮动、E3 资金流动性、E4 全球事件、E5 情绪行为、E6 反方审计。共识公式 `C = Σ(wᵢ·kᵢ·cᵢ·sᵢ) / Σ(wᵢ·kᵢ·cᵢ)`、分歧 `D = 1 - |C|`；E6 具备风控否决权，异议单独保留。
- `server/market_survey_engine.py` 接入全市场：`_apply_full_market()` 用全 A 广度与成交额替换原 447 只核心样本口径，payload 增 `data_scope: full_a_share` 与 `data_coverage`；顶层 `quality` 同时保留 `core_coverage_pct` 与全市场 `coverage_pct`，避免用核心样本覆盖率冒充全市场。
- 新增 `server/routers/decision.py`，7 个端点：`/api/decision/snapshot|sentiment|sentiment/history|experts|coverage|universe|coverage/stream`。
- `server/brain.py` 增 `expert_panel` 调度（900s，窗口 8:45–15:30）；`/api/decision/sentiment` 每 4 分钟采样写入 `data/sentiment_history.json`（同 trace 去重）。

### 决策台 v3

- 决策台数据源 14 → **17 个**，新增「全市场覆盖」「六专家研判」「统一情绪」三块；总卡数 14。
- 新增 `renderCoverage()` / `renderExperts()` / `renderSentiment()`；`computeVerdict()` 改为优先消费 `sentiment.score` 与 `panel.consensus`，全市场广度优先于核心样本。
- `num()` 修复 `null → 0` 语义缺陷（缺失行情不再显示为 0.00）；删除 v71 遗留的独立轮询与 `limit=3` 固定 422 请求。
- 情绪面板新增口径徽标：全市场显示绿色「全市场口径 + 有效报价 / 全库」，降级为核心样本时显示琥珀色「核心样本」，避免 447 只样本的百分比被当成全市场。
- 实测：17/17 源接通、14 卡渲染、桌面与 390px 零横向溢出、控制台零错误。

### 回归中修正的四处精度问题

1. **风险旗标误判等级**：原规则「涨停 ≥ 20 且跌停 ≥ 20」一刀切判 `high`，导致「涨停 60 / 跌停 27、上涨占比 57.3%、中位 +0.39%」的普涨日被判为「结构撕裂」。改为按均衡度分级：`min/max ≥ 0.5` 才判 `high`，一侧主导降为 `medium` 并改文案为「分化加剧」。
2. **口径不可见**：`sentiment.v1` 原先不暴露数据范围。服务重启后全市场缓存未就绪时，情绪分会静默回退到 447 只核心样本（如 `上涨 281 / 下跌 149`），消费方无法分辨。新增 `data_scope` / `scope_label` 与 `scope_degraded` 标记。
3. **降级态被长缓存**：`MarketPacket` 原统一缓存 55 秒，重启后会把降级包冻结近一分钟。改为无 `full_market` 时只缓存 5 秒，全市场就绪后立即切回。
4. **动量维无兜底导致整体失分**：动量权重 0.20，量能维长期缺失（0.15），因此可用覆盖率上限只有 0.85；指数行情偶发缺 `chg` 时会掉到 0.65（< 0.70），**整个情绪分直接消失**。改为缺指数时用全市场个股涨跌中位数替代，并在 `note` 与 `inputs.momentum_source` 中显式标注口径不同；两者都没有时仍返回 null，不填 50。

新增 4 项测试覆盖上述行为（`test_limit_split_flag_calibrates_by_balance`、`test_sentiment_declares_data_scope`、`test_momentum_falls_back_to_breadth_median`、`test_momentum_stays_null_without_any_fallback`）。

### 文档与测试

- 新增权威规划 `docs/00-strategy/DECISION-CLOSED-LOOP-PLAN-2026Q4.md`（含 0.3 实施进展表、数据类型表、来源优先级、L0–L5 节拍、存储与配置、质量硬规则、六专家表、输出契约、七步流程、公式与阈值、冲突处理、决策依据优先级、六维公式与档位、闭环 I/O 与 P0–P2 优先级）。
- 文档站新增「整体规划 · 三系统闭环」章节，`dataDict.sources` 补入 5 条新通道；`project-docs-data.js` → **v20**。
- 新增 `tests/test_decision_loop.py`，**10 项全部通过**：情绪可复算、缺失维度不补值、无关键维度返回 null、六专家顺序与共识、审计否决、季度候选、全市场 breadth 涨跌停阈值、涨跌停撕裂等级校准、情绪口径自描述、动量中位数兜底、无兜底时保持 null。全库 **188 项测试通过**。

### 已知限制（诚实标注，未掩盖）

- 量能维需积累 20 个交易日成交额，当前 `history_n = 0` → 该维度返回 null，按剩余权重归一。
- 事件维暂以情报风险分代理，新闻正负面分类未上线。
- 匿名两轮互评、反方质询、专家权重校准为 P1，未在 v1 交付。
- 全市场 56 页请求不适合 60 秒全量重拉，定为 5 分钟节拍；核心指数仍为 60 秒。

## 2026-11-01 — 决策台 v2 + 决策闭环整体规划

- 决策台数据接通：14 个接口统一为单一管线，按源独立超时、逐块渐进渲染；断源保留 last-good 并标记 stale，删除重复轮询与 `limit=3` 的固定 422。
- 修复缺失值语义：前端不再把 `null` 转成 0；`server/routers/market_dashboard.py` 对缺失指数返回 `null`，不参与判断。
- 面板重构：结论优先布局、市场脉冲、板块阶段结构、移动端两列脉冲、深浅色适配；验证 14/14 接通、控制台零错误、390px 零横向溢出。
- 新增 `docs/00-strategy/DECISION-CLOSED-LOOP-PLAN-2026Q4.md`：动态市场调查的数据类型 / 来源 / 频率 / 存储配置，六专家主脑的独立研判、互评、冲突处理与汇总公式，情绪六维量化与展示，以及调查 / 情绪 / 主脑 / 决策台 / 复盘之间的 I/O、依赖与 P0-P2 优先级。
- 文档站新增「整体规划 · 三系统闭环」章节（`project-docs-data.js v17` / `project-docs.html`），16 个导航项、0 死锚、桌面与 390px 零溢出。

## 2026-10-29 — 决策台 v1 + 行情数据源直连修复

- 修复：server/main.py 启动即设置 NO_PROXY=*，避免服务端 urllib 走本机系统代理导致
  腾讯/东财/AkShare 全部失败（WinError 10061）；K线/分时/报价/资金流/新闻/龙虎榜全面恢复。
- 新增工作区「决策台」：盘前/盘中/盘后自动切换；今日结论（风险指数 + 攻守结论 + 仓位上限）、
  世界局势、市场温度、资金板块（板块行可点击进入策略选股）、持仓风控、事件情报、盘后复盘、
  决策卡（8 项自动检查 + 计划生成/保存/复制/历史）。
- 新增文件：static/js/decision-desk.js、static/css/decision-desk.css；改动 dashboard.html、
  market-dashboard.js、intelligent-analysis.js。
- 验收：决策台 8 卡正常、无控制台错误；体检 40/40 PASS。

## 2026-10-29（晚）— 板块趋势 + 题材炒作雷达

- 新增 `/api/themes/overview|board/{code}|char/{char}`（server/routers/themes.py）：
  本地东财板块快照（496 板块/5657 只股票索引、5/20/60 日收益）+ 新浪全市场实时快照（5576 只，180 秒缓存）；
- 决策台新增两张卡：「板块趋势分析」（趋势分/阶段/5日/20日/上涨占比/龙头，点击下钻成分股）、
  「题材炒作 · 字辈雷达」（生肖字 + 高位股异常聚集字自主发现，含羊/马/龙等，点击下钻个股）；
- 下钻浮层内点个股可直接进入策略选股；启动后台预热，首开不等待 5–6 秒；
- 实测：决策台 10 卡、题材下钻（羊 → 三羊马/水羊股份）、板块下钻均正常，无控制台错误；体检 40/40。

## 2026-10-29（深夜）— 远程访问 / 离线兜底 / 通达信数据接口

- 新增 `server/tdx_local.py` + `/api/tdx/status|kline`：读取通达信 vipdoc 本地 `.day/.lc1/.lc5`（离线历史行情，已验证解析）；
- 新增 `static/mobile.html` 手机端决策简报（结论/市场温度/题材/板块/情报/复盘，60s 刷新，断网显示缓存）；
- 新增 `scripts/push_decision.py`：汇总决策摘要并通过 QQ SMTP 推送手机邮箱（默认 dry-run，`--send` 发送）；
- 新增 `docs/40-ops/REMOTE-ACCESS.md`：宿舍网络三条路线、Tailscale/Cloudflare Tunnel、推送定时任务、离线兜底；
- 说明：通达信公开行情服务器（7709）当前协议握手异常，故采用本地盘后数据方案；ego-browser 已验证官网无免费实时行情接口。

## 2026-10-30 — 战略文档 / 数据平台 / 云部署方案

- 新增 `docs/00-strategy/PRODUCT-STRATEGY-2026Q4.md`（定位、护城河、P0–P3、指标、预算、风险）；
- 新增 `docs/00-strategy/REFERENCE-AI1FOO.md`（ai1foo 股先生实测：学任务化/专家化/基准化，不学荐股平台）；
- 新增 `docs/10-architecture/DATA-PLATFORM.md`（数据源注册表、缓存分层、口径与质量标记、Tushare/TDX 接入计划）；
- 新增 `docs/40-ops/CLOUD-DEPLOYMENT.md`（云服务器能否联网、价格、部署步骤、安全、迁移清单）；
- 新增 `server/datasources.py` + `GET /api/data-sources/health`（6 源健康检查，critical 判定）；
- 新增 `server/tdx_local.py` + `/api/tdx/status|kline`（通达信本地 vipdoc 离线读取，已自测）。

## 2026-10-30 — 浑天仪拖动偏移 / 链条重绘 / 流速

- 修复星系拖动后偏移：禁用指针视差、拖动结束不重算 galFit、清零惯性；
- 数据链条改为默认悬停显示，全部细链仅同层近邻，去掉满屏乱线；
- 粒子流速降至约 37%；104 FPS；体检 46/46。

## 2026-10-30 — 浑天仪维度/链条/光点优化

- 星尘每环 96→156 + 环间浮尘场；数据链条（细链/全链，悬停高亮）；光点精灵双层辉光 + 十字星芒 + 闪烁；
- 维度线加亮加粗，天轴 TOP/BASE + 当前维度标签；纵深切换 620ms 缓动；
- 个性化新增数据链条 / 星系光点；实测 114–118 FPS，体检 46/46。

## 2026-10-30 — 按 ARMILLARY-DESIGN 升级浑天仪

- 按 ARMILLARY-DESIGN.md 落地 Canvas 版：日期星历、开普勒差速、刻线点尘、四道倾斜环架、MY ASTERISM 星座；
- 个性化新增环架倾角 / 星尘密度 / 形态种子，保留环面倾角 / 维度线 / 色调 / 自转；
- 95–119 FPS，无报错；体检 46/46；WebGL 银河保持删除。

## 2026-10-30 — 删除 WebGL 银河，恢复平面默认

- 删除 gal-gl 画布、WebGL 星系渲染与相关个性化入口（星系渲染/质感/辉光/种子/底纹/星尘/旋臂/银河带/标签/外观方案）；
- 原版平面设计恢复为默认；v4→v5 迁移强制 view3d=flat、design=original，保留关注/主题/密度/字体；
- 星系唯一设计 = 浑天仪环绕（环面倾角 + 维度线 + 自转）；保留星系自转/环面倾角/维度线/色调；
- 体检 46/46；平面/轴测/动态不受影响。

## 2026-10-30 — 浑天仪 3D 动态与维度线个性化

- 星系浑天仪升级：环面倾角（平缓/标准/纵深）+ 呼吸摆动、深度分层；
- 维度线：天轴 + 12/24 辐条 + 2/4 子午圈，随自转旋转；个性化面板新增「环面倾角」「维度线」并持久化；
- 环线 Path2D 合并绘制，实测 100–131 FPS；体检 46/46。

## 2026-10-30 — 星系视图恢复浑天仪环绕

- 星系默认渲染改回浑天仪七环星盘（v39 设计），3D 银河保留为可选；
- 偏好键 v3→v4 迁移，保留关注/主题/设计版本，星系渲染强制 atlas；
- 个性化文案与设计版本入口同步；平面/轴测/动态不受影响；体检 46/46。

## 2026-10-25 — 并行合并 v40×v41/v42（AXON §三十五）

> 事件：另一会话同日交付 v41（星系渲染器切换）与 v42（原版设计为默认，偏好键 → `axon-plate-v3`），
> 以 v39 为基线整体覆盖了本日 v40/v40.1/v40.2 的全部改动。
>
> 处置：保留 v41/v42 机制，v40 系列按 `AXON-GRAPH-DESIGN.md` §三十二账本逐条重放合并——
> 数据治理（99 边/95 对去重、群落重标、layers[] 11 项配置）、板块联动与图签配置、板块配置卡、
> 纵深标尺、图例双重绑定修复、自转停档等全部恢复；`_verify_galaxy_v40.mjs` 28/28 PASS、
> 体检 40/40；合并版已备份 `static/plate-graph.html.bak-merge-v40-v42`。
> v41/v42 详情见设计账本 §三十三/§三十四（外部会话补记）。

### 观测者手记（v43 同日 · §三十六）

理念：图版不是公共图表，而是观测者的私人仪器——它认识你。四件手记全部落地并通过实测：

- **署名 + 仪器编号**：面板可设观测者署名；编号 CAT.№ 由全部仪器设定哈希派生（改一项设定即换号），与署名一起印在星系右下角与图签底部；
- **我的星座**：关注 ≥2 份文档按收藏顺序连成金色虚线星群（MY ASTERISM · n），顺序即手迹；
- **我的方案 + 分享码**：自定义外观库存为命名方案（应用/删除）；`AXON1.` 分享码把方案库/仪器参数/关注/署名打包导出导入；
- **回到上次观测**：视图/筛选/聚焦轨迹落盘，页首恢复条一键「继续」（修复：启动阶段写会话会清掉上次轨迹，已加就绪门闩）。

验证：`_verify_galaxy_v40.mjs` **37/37 PASS**（新增署名/编号/方案/分享码/会话/星座 9 项）；截图 `shots/_galaxy_v43_asterism.png`；体检 40/40。

### 星系精修：混合渲染与交互维度（v44 同日 · §三十七）

- **混合渲染**：修复 v41 双路径割裂——GL 模式下环铭牌/纵深标尺/署名/星座全部不可见、GL 星与标签错位。现 GL 只画照片式星尘背景（文档星撤出 GL），2D 仪器层在两种渲染模式下都叠加，星与标签同用 project3 严格对齐。
- **动态细节**：恒星呼吸（pagerank 相位）、核心脉动、悬停/聚焦星的四向衍射星芒、平均 15s 一颗的流星（均尊重 reduce-motion）。
- **交互维度**：环体可点（`ringAt` 命中 ±12px，悬停高亮 + 点击选/取消板块，环即板块维度本体）；双击空白复位相机；图签新增操作提示行。
- 验证 **41/41 PASS**（新增 7 项，含 GL 模式仪器层像素证据与停转下的环交互可测性）；截图 `shots/_galaxy_v44_hybrid.png` / `_galaxy_v44_atlas.png`。

### 星系重设计：制图美学（v45 同日 · §三十八）

> 用户反馈"星系设计太丑"——诊断是两种视觉语言打架（粒子噪底 + 仪器细线）与元素过载。整层重设计：

- **设计语言重定为「制图美学」**：暖墨纸面（微尘减到 1/3）、环系外强内弱 + 刻度只留外环、铭牌固定方位角铭刻（不再漂移）、核心仪表（灯丝呼吸 + 准线环 + reticle）、**星带轨道尾迹**（公转方向可读）、星座平滑曲线、星点尺寸上调。
- **图签可折叠**（⌃/⌄，记忆状态）——解除右侧外环遮挡，折叠态评审 9/10。
- **GL 星尘降级为可选叠加**（默认改为仪器图谱）：配色向暖金靠拢、粒子缩小、bloom 收敛。
- 视觉评审：仪器图谱 8/10、折叠态 9/10、星尘叠加修复后协调；验证 41/41 PASS、体检 40/40；截图 `shots/_galaxy_v45_final_*.png`。

### 悬臂星系（v46 同日 · §三十九）

> v45 解决了配色与噪声但用户仍不满——结构性根因是星不成形（散布圆上）。v46 把星系结构真正立起来：

- **单臂对数螺旋** `θ=3.3·ln(r/r₀)`：半径=板块层带、角度=螺旋相位+小抖动，35 颗星连成一条清晰旋臂；
- **银河带辉光**（沿螺旋 46 团暖光）给旋臂身体；核心加 130px 宽晕；层带引导弧降为发丝级半径标尺；
- 选中层带 = 整带充色 + 螺旋段提亮；层带命中改带状采样（容差 9px 消除相邻带歧义），环交互全保留；
- 评审 **9/10**（"结构感与生命力显著提升"）；验证 41/41、体检 40/40；截图 `shots/_galaxy_v45_final_atlas.png`。

### 琉璃日冕星盘（v47 同日 · §四十）

> 用户否决悬臂（"螺旋并不好看"），要求更精进美观个性化。构图范式从"拟态天文"转向**秩序**：

- **曼陀罗星盘**：7 板块各占一等角扇形光区（中心向外的暖光楔 + 细辐条），区内文档**按被引度排序排珠于弧上**（项链式奇偶微偏）——节奏与对称取代一切哈希散布；
- **盘心印记（个性化）**：观测者名讳首字以 7.5% 透明度衬于核心之后；
- 评审驱动精进：图签减重（默认 34% 透明+细金边，悬停才坐实，消除"两张皮"）、铭牌半径交错避开珠串、星点与标尺文字对比度上调；
- `ringAt` 改扇区采样，点区选板块/悬停亮区交互全保留；
- 视觉终审 **9/10（"达到精进、美观、个性化标准，无必须修的问题"）**；验证 41/41、体检 40/40。

### 绚烂光晕（v48 同日 · §四十一）

> 四版"克制仪器"被否后更换质感范式：深空有身体感、结构在发光、星是宝石（保留 v47 秩序几何与交互）。

- **光斑精灵系统**：预烘焙光晕贴图 + drawImage 合成，绚烂与性能兼得；
- **深空星云**：靛蓝底 + 5 团蓝/紫/青/暖星云随盘缓转 + 微闪星尘；
- **发光珠链**：每个扇区沿珠串弧铺暖光带（"发光的绳"）；扇区光楔提亮；
- **核心**：三层白暖辉光 + 横向镜头光斑；盘心印记改为暗色印于辉光之上（更可辨）；
- **星宝石**：光晕精灵 r×7.5；尾迹增亮；边缘 42% 暗角聚焦；
- 视觉评审 **9/10（"有视觉冲击力，像高级作品而非工程图"）**；验证 41/41、体检 40/40。

### 背景分离与维度设计（v49 同日 · §四十二）

- **背景体系分离**：星系色调只在星系视图挂载（`applyPaletteAttr`），平面图版恢复材料本色，不被星系个性化污染；实测 cyan→无→cyan 三态正确。
- **维度形变**：切换纵深维度时星高 620ms 缓动到新位（换镜片不跳变，`depthY` 逐节点插值）。
- **维度快捷条**：盘下居中 8 键一键切换纵深维度（激活金色、aria tablist、仅星系显示），与 HUD 巡览/个性化面板同源。
- 验证 **44/44 PASS**（新增 3 项）；体检 40/40。

## 2026-10-30 — 缩放接通 · 空范围优雅态 · 维度显隐（v60 §五十二）

- **缩放接通**：#figure 捕获层兜底滚轮（平面→setZoom 指针锚定 / 星系→userZoom+HUD），实测连续缩放 ×1.00→1.47→2.16、HUD/页脚同步——"面板缩放不接通"修复。
- **数据链容错**：读数面板空范围优雅态（拒绝假零值 + 清除筛选指引）。
- **维度显隐**：平面模式隐藏 X/Z 维度标签（实测 display:none 生效）。
- **新基线**：并行会话重生成数据至 41 节点/105 边，出现孤立节点 1（P1 补引用接入）；验证套件断言与 41/105 新基线及 atlas-mode 实现的对齐列入下一轮。
- 备份 bak-v60。

## 2026-10-28（深夜）— 浑天仪设计代码全稿归档（§五十一）

- 并行会话再次整体改写 plate-graph.html（现网 galTilt/arm/ripples 为外部实现，浑天仪代码被覆盖）。
- 新增 `docs/10-architecture/ARMILLARY-DESIGN.md`：浑天仪设计代码**权威全稿**（着色器三分支/粒子生成/开普勒星历/准星环烘焙/2D chrome/集成要点，全注释可重放）；恢复：`cp static/plate-graph.html.bak-v58 static/plate-graph.html`。
- 教训重申：多会话严禁同时改同一文件；改前必比对 mtime 与账本。

## 2026-10-28（晚）— 刻线仪器（v58 §五十）

> 用户否决光晕渲染："重新设计升级一下，而不是加光晕去渲染"。设计反转：从"涂光"到"刻线"。

- 撤除黄道盘辉与文档星软晕；数据环粒子加密 2 倍、带宽收窄至贴线——环成**发光细丝**；核球收紧为致密白金灯芯；环架细线化。
- **文档星准星环**：亮核 + 12 微刻度绕星缓旋（半径随被引度、每星相位错开、low 档只画亮核）——仪表对焦环替代光球。
- 视觉终审 **9.5/10（"涂与雕的量级差……最终答案"）**；验证 46 项全 PASS、体检 40/40；备份 bak-v58。

## 2026-10-28 — 浑天仪升级 · 柱洗色 · 数据源状态条（v57 §四十九）

- **浑天仪三件套**：黄道面基辉（46 团大尺寸暗光斑铺面）、环架伴线（双线仪器感）、十二宫金刻（外环架 30° 金亮星刻度）。
- **平面图版**：列底衬 col-wash（accent 2.2%，hot 5.5% 提亮）——七列有了空间分区与交互反馈。
- **情绪页数据源状态条**：6 源自探（60s 轮询，绿/红点+条数+耗时），实测 4/6 存活——"数据不足"自解释；修复探针对对象型返回的计数缺陷；北向（链路废弃）与资金流（上游超时）如实标红。
- 验证 46 项全 PASS、体检 40/40；备份 bak-v57。

## 2026-10-26（傍晚）— 板块面板精细化（v56 §四十八）

- **六大面板精修**：图签（动态副标 / 层行共享占比条 + 距今日数 / 新增群落区）、读数（我的关注行，点击切换只看关注并联动画布）、图例（每层最近修订日期）、更新分布（最新列金色高亮）、阶段（**修复现行期硬编码 i===0**，改按今天判定）。
- **修复**：图签群落区 `byid`→`byId`（被补装的 console.warn 定位）；renderGalaxyPlate 7 处空 catch 全部改为输出堆栈。
- 验证 46 项全 PASS、体检 40/40；备份 bak-v56。

## 2026-10-26（下午）— 文档规划同步 · 负载治理 · 数据总表入图（v55 §四十七）

- **CPU/负载治理补全**：空闲 8s 降帧扩展到自转态（交互即刻恢复满帧）；GALGL.low 低档跳过文档星底晕（GPU 填充率减半）；自适应降档闭环（EMA>24ms→low）核实完好。
- **文档优化**：product-roadmap 新增 §七 审计驱动规划更新（实测基线表 + P1 四项：感知层预取/信号补 ATR 离场计划/风控 warn 清零/样本门控 + P2 探索）；DEPLOYMENT 新增 §10 运行指标基线与巡检手册。
- **GRAPH-DATA.md 入图**：图谱增至 **36 节点 / 100 边（96 对去重）/ 0 孤立**，内嵌块全量重算同步，数据总表重生；测试预期同步 36/100/96。
- 验证 46 项全 PASS、体检 40/40；备份 bak-v55。

## 2026-10-26 — 浑天仪精进 + 系统体检 + 决策审计（v54 §四十六）

- **浑天仪高级细节**：密度波雕环（周向亮度波）+ 每环伴线细环 + 环架丝带亮度波 + 文档星双层光斑（底晕+亮核）。
- **个性化同步**：新增「环架倾角 ringTilt」（紧凑/标准/舒展）偏好，入 sig 即时重建——个性化与新设计同步。
- **系统指标实测**：缓存命中率 82.7%（淘汰 0）；决策链延迟 信号26ms/风控16ms/复盘14ms；**行情 23.3s / 情绪 5.7s（上游拉取）= 感知层瓶颈，记 P1 预取改进**。
- **交易决策审计**：信号字段完整但**缺止损/目标**（P1：附 ATR 离场计划）；风控 warn(67) 工作正常；复盘样本 17/60 诚实降级。
- 验证 46 项全 PASS、体检 40/40；备份 bak-v54。

## 2026-10-25（夜）— 浑天仪 ARMILLARY（v53 §四十五）

> 用户否决旋臂，要求高级且个性化的设计逻辑；重申重要改动必须记录到项目文档。

- **旋臂盘 → 浑天仪**：七层发光数据轨道环（内暖外冷 + palette 浸染 + 金屑）+ 四道倾斜环架与竖直子午环（钢蓝+金屑，绕 Y 进动）+ 暖金核球；文档星仍逐帧烘焙在数据轨道上；个性化四 hooks（色调浸染 / 盘心观测者小印 / 关注星座 / CAT.№ 编号）。
- **静默错误治理**：renderGalGL 的空 catch 改为输出 err.stack（本轮靠它定位环架 z1 误写 z2 的 ReferenceError）；__AXON_DEBUG__ 增加 glStats() 探针。
- 视觉终审 **9/10 可交付**（"与 AXON 仪器图谱语言高度统一"）；验证 46 项全 PASS、体检 40/40；备份 bak-v53。

## 2026-10-25（深夜）— 真·银河（v52 §四十四）+ 高阶集成审计

- **星系换根本路线**：七轮 2D 方案被否后，把内置 WebGL 粒子银河引擎扶正为主渲染——6 万尘埃双旋臂 + 棒状核球 + 280 团 HII 粉红星云 + ACES 辉光管线；**35 颗文档星逐帧烘焙进 GL**（独立缓冲，自转/星历/维度形变/聚焦过滤与 2D 同源）；GL 与 project3 相机数学同构证明 + galFit 废弃 + 滚轮直写 cam.zoom 三处同源；uIntro 显影动画；2D 只留 chrome，atlas 保留为回退；galmig52 一次性迁移 + 旋臂默认 2 条。
- **修复**：着色器缺逗号致页面语法损坏；chrome 守卫误圈声明致 GL 模式每帧报错连锁会话失效。
- **高阶集成审计**：感知→信号→决策→执行→校准→同步→脉搏 **8/8 端点连通**（决策链闭环在线）；情绪端点纠正为 /api/market-sentiment。
- 视觉终审 **9.5/10（"质的飞跃……可以交付"）**；验证 46 项全 PASS、体检 40/40；备份 bak-v52。

## 2026-10-25（晚）— 参考重制 · 性能治理 · 数据同步 · 数据文档（AXON v50/v51）

> 用户指令：完善配置、处理缓存/CPU 占用、同步各板块数据、优化稳定性、按 solar-system-atlas 重制星系、数据入文档。

- **星系按参考站重制（v50 §四十三）**：实测提取参考规格——#020308 深蓝黑、0.5px 超细灰蓝轨道渐淡、金色仅四向长刻、小核心十字刻、负空间（R 252 + 大边距）；星历几何：轨道相位=修订日期；去星云/暗角/辉光/光斑/扇区/珠链。
- **v51 去文字汤 + CPU 治理**：星名默认悬停才显示（画布极简）；停转无交互时 ~30fps 跳帧，占用减半。
- **数据同步**：图谱再生成 35/99/0 悬空；全链审计 PASS（内嵌口径自洽 + 与 docs-graph.json 节点集合一致）。
- **数据入文档**：新增 `docs/30-data/GRAPH-DATA.md` 格式化总表（口径/板块配置/可信分布/35 行清单/群落），生成物不回灌图谱（避免自引用漂移）。
- 验证 **44/44 PASS**、体检 40/40、视觉终审 9/10 可交付；备份 bak-v51。

## 2026-10-24 — 星系板块联动 + 图谱数据治理（AXON v40）

> 用户要求：完善 plate-graph.html 的 3D 星系项目分类设计、修复系统板块数据缺陷、完善各板块配置。
> 设计账本：docs/10-architecture/AXON-GRAPH-DESIGN.md §三十二；验证脚本 `_verify_galaxy_v40.mjs`（28/28 PASS）。

### 一、数据缺陷（对内嵌 __AXON__ 全量审计后修复）

| 缺陷 | 根因 | 修复 |
|---|---|---|
| 2 份文档成孤岛（metrics.isolated=2，图分 3 块） | IMPECCABLE-INTEGRATION.md §4 只用反引号提及相关文档、HARDENING-MOAT.md 无交叉引用，链接提取抓不到 | 源文档改真 md 链接 / 补"相关文档"节；内嵌数据加 5 条真实引用边 → 0 孤立、连通 1 块 |
| 层间引用矩阵虚高 | 3 对互引（README↔AXON、AXON↔UI-ROADMAP、impl-checklist↔openbb）按两条边入库，邻居表/矩阵/读数双重计数 | 前端按"文档对"去重（互引计一对）：00↔10 由 16 条修正为 15 对；页脚规模改 35/95（去重对） |
| 群落 1 标注"归档" | 战略/归档并列 3:3 时取错组，而锚点是战略 README | 标注规则定为"多数派优先、并列取锚点组"，4 个群落全部按此重标（群落 1：归档 → 战略与计划） |
| AXON-GRAPH-DESIGN 自身摘要为空 | 手工维护遗漏 | 补摘要 |
| 策略 README 死链 | `docs/10-architecture/...` 应为 `../10-architecture/...`（gen_docs_graph 再生成时暴露：悬空引用 1） | 修正后 docs-graph.json 再生成 35/99/0 悬空 |
| metrics 口径混杂 | 密度/平均度按边目数（含互引）计算 | 按去重图全量重算：密度 0.1597 / 平均度 5.43 / pagerank / 直径 5 / 模块度 0.2348 / 聚类 0.4339 |

### 二、3D 星系项目分类设计（v40）

| 项 | 改前 | 改后 |
|---|---|---|
| 板块辨识 | 7 条环无任何标注，分不清哪条环是哪个板块 | 每环一枚巡行铭牌 `编号 · 板块名 · 份数`（选中板块高亮、其余压暗），沿半径外偏避让星点引线 |
| 板块联动 | 筛选只作用于 SVG 标签层，canvas 星点与图签读数纹丝不动 | 文档星按 matchDoc/previewDoc 联动压暗（范围外 18% 亮度）；图签读数 / TIERS / 板块行随 scope 重绘；角标显示 `14/35 STARS · SCOPE` |
| 各板块配置 | 图签只有静态全量计数，且仅进星系模式时渲染一次 | 图签新增 7 行板块配置行（范围内/总数，如 14/14），可点击切换层级筛选（is-hot 金色高亮，键盘可达）；环数/刻度环/轴向说明全部由 LAYERS.length 驱动 |
| 信息卡分类维度 | 层级字段读不存在的 `group_label`，一直显示英文 key | 修正为 `groupLabel`；补"第几环""群落 · 份数"两个分类维度 |
| 代码健康 | COSMO / galaxyArms / galaxySpinAngle 重复定义两遍、`prefs.labels` 死变量 | 清理；`ringR` 分母改 `LAYERS.length-1`（层数增减不再失真） |

### 三、验收

| 项目 | 结果 |
|---|---|
| `_verify_galaxy_v40.mjs`（playwright 实测） | 28/28 PASS：数据 35/98、0 孤立、图签联动 14/15↔35/95、矩阵 15 对、信息卡中文字段、停档、聚焦图签、空范围提示、控制台零错误 |
| `tools/check_data_sources.py` | 40/40 PASS |
| `tools/gen_docs_graph.py` 再生成 | 35 节点 / 99 连线 / 0 悬空 / 0 孤立 |
| project-docs.html（经典图谱，消费 docs-graph.json） | 图谱视图 ECharts 正常建图，无回归 |
| 截图 | `shots/_galaxy_v40_full.png`（35 STARS · 95 PAIRS）、`shots/_galaxy_v40_scope.png`（14/35 STARS · SCOPE）、`shots/_galaxy_v40_depth_tier.png`（纵深标尺） |

### 四、第二轮细节精进（v40.1 同日）

- **修复存量交互缺陷**：图例（可信层级）筛选此前**静默无效**——renderLegend 与 bindBlockEvents 对同一批不重建的节点重复绑定 click，一次点击触发偶数次 toggle（设了又取消），且监听随范围变化不断叠加；收敛为 renderLegend 单一绑定。
- 信息卡从右上移到左上，不再压住星系图签（实测零重叠）；星系自转加「停」档（galSpeed=halt，固定相位）。
- 板块选中表达强化：选中环提亮 1.9×、其余压暗，并加 ±9 环带充色；层间矩阵悬停预览同样驱动环强调。
- 非层级纵深模式在左缘新增虚线标尺（Y 轴上下端语义：真相源/归档、新/旧、被引多/少…），与星点高低方向一致。
- 星系画布星点与三维 SVG 节点同步响应「仅看关注」与检索；铭牌/角标加墨色描边提升交叠处可读性。
- 聚焦模式下图签标注「聚焦」、读数按视野收窄（=邻居数+1）并随进/出聚焦即时刷新；空范围给「范围内无文档」空态；EPOCH 纪元年份动态化。

### 五、板块数据配置（v40.2 同日）

- `layers[]` 配置补全为 11 项（+bytes / internalPairs / crossPairs / avgDegree / maxDegree / tiers / kinds / bridges / updated / hub），口径与"互引计一对"一致（Σ内部 33 + Σ跨板块/2 62 = 95 对自洽）。
- 新增**板块配置卡**（复用档案侧滑）：六格统计、关系配置、可信层级分布（可点筛选）、类型分布、枢纽 TOP3、只看该板块 / 聚焦枢纽；入口为图签行尾 ⇱ 与平面视图双击列头；图签 tooltip 换成完整配置摘要；平面列头 meta 增加 KB。

## 2026-10-22（晚间）— 面板信息互联 + 文档图谱精进

> 用户要求：完善系统的漏洞、连接好各面板之间的信息、所有修改统一记录到项目文档、继续精进图谱设计。

### 一、两套板块体系此前完全无法互通

| 体系 | 数据源 | 口径 |
|---|---|---|
| 情绪页 | `/api/sector-ranking` | 东财行业板块，**50 个**，代码形如 `BK1500 医美耗材` |
| 仪表盘 | `/api/quotes?sector=` | `server/config.py` 自选板块，**26 个**，key 形如 `semiconductor / pharma` |

名字与代码都对不上 → 「看到某板块走强，去仪表盘看成分股」这条最自然的动作做不了。
`js/sentiment.js` 新增 `SECTOR_ALIAS`（26 组关键词别名）+ `mapToSector()`：
命中则热力格可点、跳 `dashboard.html?sector=<key>`；未命中**不做假跳转**，`title` 注明「未纳入自选板块」。
别名顺序敏感（具体在前），避免「消费电子」被「消费」先截走。

顺带修掉一个误导：`.hm-cell` 原来**所有**格子都是 `cursor:pointer`，但绝大多数点下去没反应 —— 现在只有可跳转的才有手型。

### 二、上下文传递链路

| 方向 | 形式 | 落地行为 |
|---|---|---|
| 情绪 → 仪表盘 | 热力格点击 | `?sector=<key>`，由 `_resolveDeepLink()` 选中该板块 |
| 仪表盘 → 情绪 | header「情绪」按钮 | 自动带当前板块 `?sector=` |
| 情绪页内聚焦 | `?sector=` | 顶部聚焦条（板块名 + 东财口径命中数 + 一键清除）+ 对应格子描边高亮 |
| 仪表盘 → 回测 | 浮层「相关信息」条 | 带 `?code=&sector=`，回测页显示来源说明（池化回测不预填标的，避免假装） |

`dashboard.html` 的 K 线浮层底部新增 `.kline-overlay-links`，**9 个 Tab 共用**一条出口，
href 由 `app.js` 新增的 `_syncContextLinks()` 在 `init / switchSector / selectStock` 三处刷新。

### 三、图谱：从「无名球云」到「分组可见」

| 项 | 改前 | 改后 |
|---|---|---|
| 数据新鲜度 | 生成于 10-21，`CHANGELOG.md` 节点仍记 566 行 | 重新生成，31 节点 / 80 连线，字节数与行数同步 |
| 分组结构 | 只在初始位置分扇区，FR 力导向随后把分组打散，颜色混成一锅 | 迭代中加入**分组凝聚力**（向组质心收拢，`COHESION=0.16`），簇边界清晰 |
| 标签覆盖 | 固定 Top5 枢纽，30 个节点只显示约 8 个名字 | 按 45% 自适应（下限 5 / 上限 16），重叠仍由 `labelLayout.hideOverlap` 兜底 |

### 四、文档统一记录

- 新增 `docs/40-ops/CHANGELOG-2026-10-22.md`：四轮改动按「问题 → 根因 → 修复 → 验证」表格化，
  含总览表、逐项明细、复现命令、遗留项。
- `docs/40-ops/DEPLOYMENT.md` §6 补充缓存健康判据与本文档入口。
- 本文顶部加入总账入口。

### 五、验证

| 项目 | 结果 |
|---|---|
| `_audit_undefined.py`（HTML 属性 + JS 调用点） | HTML 内联调用 **0 处未定义**（`startEventStream` 类问题已清零） |
| 图谱数据生成 | 31 节点 / 80 连线 / 0 缺失 / 0 悬空引用 / 0 孤立节点 |
| 全站 7 页扫描 | 0 溢出 / 0 4xx / 0 脚本错误 |
| 情绪页 `?sector=pharma` | 聚焦条「医药 · 东财口径命中 2 个行业板块」，医美耗材 / 医美服务 两格高亮 |
| 热力格映射 | 50 个格子中 23 个可跳转（27 个未纳入自选池，`cursor:default` 不可点） |
| 点击热力格 | 落到 `/dashboard.html?sector=pharma`，板块标题「医药」、列表 21 行，三者一致 |
| 仪表盘外链 | `hdr-link-sentiment` 与 `kol-sentiment` 均为 `/sentiment.html?sector=pharma` |
| 浮层出口条 | 4 个链接可见，文案随板块变化（「📈 半导体 · 市场情绪」） |
| 回测页上下文 | `?code=sh600276&sector=pharma` → 来源条显示并给回跳 |
| 文档站问题条目 | 42 条（新增 I-36 ~ I-42），表格与逐条明细同步 |

### 六、顺带修掉的口径矛盾

情绪页热力格显示的是 **5 日涨幅**（`hm-vol` 标注「5日」），tooltip 却只写「今日 +2.35%」——
同一个格子上两个数字对不上（医美耗材 今日 +2.35% / 5日 +0.44%）。tooltip 改为同时给出两个口径。

## 2026-10-22（下午三）— 缓存自动回收重建 + 系统级漏洞修复

> 用户要求：修复好系统和设计的漏洞，以及完善好缓存的自动清理。

### 一、缓存：43% 的内存被「垃圾」占着

线上实例实测（`cache._store` 直接盘点）：**68 条缓存里 29 条已过期，占 43%**，
它们既不会被读到（`get()` 只在被访问时才删），也不会被回收，一直占着容量。

根因在 `_maybe_cleanup()` —— **它只在 `set()` 内被调用**。
服务一旦停止写入（夜间、周末、低峰、Agent 空转），过期条目就无人清理；
等下次写入时又因为「容量满 → 直接 `popitem` 淘汰 LRU」的旧顺序，
把**仍然有效的数据淘汰掉、留下过期垃圾**。

`server/cache.py` 重写，逐条对齐：

| # | 缺陷 | 修复 |
|---|---|---|
| 1 | 过期条目只靠写入触发回收，空闲期永不清理 | 新增 `background_cleanup()` 后台任务，`lifespan` 启动，周期 30s（短于最短业务 TTL 60s），与写入**完全解耦** |
| 2 | 容量满时先淘汰 LRU，导致「淘汰有效数据、留下垃圾」 | `set()` 改为**先 `purge_expired(force=True)` 回收过期，仍有缺口才淘汰 LRU** |
| 3 | `get_or_fetch` 的等待者在 leader 失败后各自重试，N 并发 = N 次上游调用（惊群） | 等待者改为 `await event.wait()` 后 `return self.get(key)`，**失败即共享 None，只打 1 次上游** |
| 4 | `quotes.py` / `admin.py` 直接读写私有 `cache._store` | 补齐公共接口 `get_entry()` / `iter_entries()` / `purge_expired()` / `stats()` / `contains()`，调用方全部迁移 |
| 5 | `clear()` 不重置清理游标；`max_size<=0` 时 `set()` 在空表 `popitem` 抛 `KeyError` | `clear()` 重置 `_last_cleanup`；`max_size` 归一化为 `max(1, …)`，淘汰循环包 `try/except KeyError` |

新增可观测字段（`stats()`）：`live` / `expired_now` / `hits` / `misses` / `hit_rate` /
`sets` / `evictions` / `expired_purged` / `pending_fetches` / `cleanup_interval` /
`seconds_since_cleanup` / `cleanup_task_running`。

**自测**：`_test_cache.py` 9 组 31 条断言全通过，含两条关键回归 ——
「零写入下后台任务仍把 4 条过期清成 0」（旧实现恒为 4）、
「8 并发失败场景只打 1 次上游」（旧实现为 8）。

> 测试过程中发现两条**断言本身写错**，已修正而非放宽实现：
> ① 原用例给 `max_size=3` 塞了 4 条数据，在未过期时就已满容，淘汰 LRU 是正确行为；
> ② 原用例用 `cleanup_interval=0.15`，但实现有 1.0s 下界（防热循环），0.6s 等待永远等不到第一次 tick。

### 二、`/api/quotes` fast 降级：一条永远走不到的死代码

```python
cached_quotes = cache.get(quotes_cache_key)   # 遇过期 → 直接 pop 掉
if not cached_quotes:
    entry = cache._store.get(quotes_cache_key) # 此时必然为 None
```

`cache.get()` 删除过期条目之后再去底层读，注释写着「即使过期也用」，
实际**从未生效过一次**。改为 `cache.get_entry()`（保留过期条目）读取，
并把 `expired`/`age` 打进日志，fast 模式超时后能真正回落到上一份行情。

### 三、管理端缓存页：从「只有一个总数」到「回收健康度可见」

- `/api/admin/cache` 增加 `stats` 块，行内增加 `age` / 已过期标记；
- 新增 `POST /api/admin/cache/purge-expired`（手动即时回收，后台每 30s 也会自动跑）；
- 页面新增三张卡片：**过期未回收**（>0 标红）、**缓存命中率**、**自动回收**（运行中/未启动 + 周期 + 淘汰数），
  卡片副标题实时显示「共 N 条，有效 N 条，过期待回收 N 条 · 距上次回收 Ns」；
- 表格里过期行降透明度 + 红色「过期」角标，TTL 列直接显示「已过期」；
- 日志动作新增 `CACHE_PURGE`（筛选下拉 + 文案映射同步）。

### 四、`admin.html`：一个未定义函数废掉三块内容

```js
startEventStream();   // 全项目 0 处定义
document.getElementById('dash-sectors').innerHTML = ...   // 永远执行不到
loadRevenueChart(); loadOrdersPieChart();                  // 同样执行不到
```

`loadDashboard()` 抛 `ReferenceError` 后被 `catch` 吞成一条 toast（"仪表盘加载失败"），
于是**板块卡片永久空白、两个图表永不渲染**，而页面看起来只是「没数据」。

实时刷新本来就有：`_liveEngine` 30s 轮询、`document.hidden` 时暂停、覆盖全部 8 个页面。
所以直接移除这条死调用（保留注释说明原因）。
顺带修掉 `loadOrdersPieChart()` 重复请求 `/api/admin/dashboard` ——
`loadDashboard()` 手里已有 `d.orders`，改为传参复用，每 30s 少一次全量仪表盘查询（主题切换仍走原取数路径）。

实测：`#dash-sectors` 从 0 子元素 → 10 个子元素，页面 0 错误。

### 五、信号监控：一封邮件撑爆整轮扫描

`monitor.py` 的邮件推送是 `asyncio.to_thread` + 重试，单次最长可达数十秒，
而它嵌在 `SCAN_ROUND_TIMEOUT_SECONDS=90s` 的 `wait_for` 预算内：
**一封邮件的网络抖动就会让本轮剩余标的全部漏扫**，并触发连续失败退避。
（日志里 `TimeoutError` 堆栈的最后一帧正停在 `_dispatch → send_signal_batch → to_thread`。）

改为 `asyncio.wait_for(..., timeout=NOTIFY_TIMEOUT_SECONDS=8.0)`，
超时/异常一律落 `PushStatus.FAILED`，不再向上冒泡 —— 通知慢不该拖垮监控。

### 六、`chart_analysis.py` 私有缓存只按条数裁剪

`_cache` 满 200 条才裁，且从不检查过期 —— 低流量下过期条目会一直留着。
补一步「先回收过期，仍超 200 再裁最旧 50」。

### 七、验证

| 项目 | 结果 |
|---|---|
| `_test_cache.py` | **31/31 PASS** |
| `py_compile`（cache/main/admin/quotes/monitor/chart_analysis） | 全部通过 |
| `/api/health` | `expired_now=0`、`cleanup_task_running=true`、`expired_purged` 递增 |
| `/api/admin/cache` | 124 条、`stats` 完整、`purge-expired` 返回 `{purged:0}` |
| `/api/quotes?sector=pharma&fast=1` | 21/21 有效，`degraded=false` |
| 管理端缓存页（浏览器） | 7 张卡片、124 行、副标题与按钮正常，0 控制台错误 |
| 全站 7 页扫描 | 全部 0 溢出、0 4xx/5xx、0 脚本错误（`admin` 板块区块已渲染） |
| `dashboard.html` 专项 | 28 行、`k-name`=华微电子、`k-vol`=64.81万手、`k-turnover`=6.75%、`k-vol-ratio`=2.74 |

## 2026-10-22（下午二）— 站内互链打通 + 情绪页板块修复

> 用户要求：读取项目文档的系统数据进行分析与配置优化，完善每一页面的板块，完善数据间的相互链接。

### 一、互链矩阵：从「7 页各自为政」到「七页互达」

改前实测（`grep` 全站入链/出链）：

| 页面 | 入链 | 出链 | 主要缺口 |
|---|---|---|---|
| `index.html` | 6 | **2** | 落地页不指向量化/情绪/文档/接口 |
| `sentiment.html` | **0** | **1** | **完全孤立**：除返回箭头外无任何站内导航 |
| `quant.html` | 1 | 2 | 缺后台/文档/接口/情绪 |
| `api-reference.html` | 2 | 2 | 缺首页/量化/后台/情绪 |
| `project-docs.html` | 3 | 4 | 缺量化/情绪 |
| `admin.html` | 3 | 2 | 缺量化/情绪/文档/接口 |

改后：**7 个页面全部 6/6 出链**，`sentiment.html` 入链 0 → 6。

做法：按各页原有导航组件逐个补齐，不引入新框架 ——
`index` 用 `.nav-links`、`dashboard` 用 `.header-actions`、`quant` 用 `.nav-link`、
`admin` 用侧栏 `.nav-item`、`docs` 用 `.docs-nav-btn`、`api-reference` 用新增 `.api-nav` 包裹、
`sentiment` 新增 `.sh-nav`（≤900px 时换行横滚，不隐藏，保证移动端仍可达）。

### 二、跨页深链：`?code=` 从空操作到真正生效

**根因**：`js/app.js` 全文没有 `URLSearchParams` / `location.search` —— 落地页卡片、
情绪榜单、任何 `/dashboard.html?code=xxx` 链接**都不会选中标的**，点进去一律停在默认板块第一只。

**修复**（`js/app.js` 新增 `_resolveDeepLink()`）：
1. 解析 `?code=` / `?sector=`；代码经 `/api/search?keyword=` 反查所属板块（返回 `{code,name,sector}`）。
2. 在 `init()` 最前面写回 `appState.activeSector` / `activeCode`，再走原有加载链路；
   `_preserveActiveStock()` 见到该代码仍在列表中就保留，不会被首只覆盖。
3. 板块标题不会随 `activeSector` 自动更新（只在 `switchSector()` 内赋值），深链改板块时显式同步 ——
   否则会出现「列表已是医药 21 只、标题仍写半导体」的错位（已实测复现并修掉）。
4. 目标代码不在该板块时给出 toast 提示，而不是静默显示首只；命中时把选中行滚进可视区。

实测：`?code=sh600276` → 板块 `pharma`、标题「医药」、侧栏高亮「医药 21」、选中 `sh600276`、
K线标题「恒瑞医药」；`?code=sz002185` 同板块场景正常；`?code=sh999999` 回落首只并提示；无参数时行为不变。

### 三、K 线头部：三个从未被赋值的字段

`dashboard.html` 的 K 线统计区有 7 个字段，其中 **`k-vol` / `k-turnover` / `k-vol-ratio`
在 `js/` 全目录 grep 结果为 0 次赋值**，DOM 恒为 `--`：

- `#k-name`：只在 `selectStock()` 内赋值，而 `init()` 从不调用它 → **首屏（含深链）标题恒为 `--`**
- 成交量 / 换手：`/api/quotes` 已返回 `volume`（单位**手**）与 `turnover`（%），只是没人写进 DOM
- 量比：接口无此字段，改由日K缓存计算，口径与后端 `server/strategies.py:249` 完全一致
  （当日成交量 ÷ 前4日均量）

实测与独立计算逐位对齐：`sh600276` 614815 / 564758 = **1.09**；`sh600436` 25909 / 36998 = **0.70**。

> 取不到日K时显示 `--` 并在 `title` 说明原因，不用 0 或估值冒充。

### 四、情绪页：4 个板块从空白/卡死到有内容

| 板块 | 改前 | 根因 | 改后 |
|---|---|---|---|
| 板块热力图 | 永久「加载中...」 | `loadSectorHeatmap()` 判断 `data.sectors`，而 `request()` 已把信封解包，`/api/sector-ranking` 的 `data` **本身就是数组** → 恒 `undefined` 直接 return | 50 个板块单元格正常渲染 |
| 板块涨跌排行 | 空白 | 同上（`_marketData.sectors` 从未被赋值）；且原逻辑取 API 顺序前 15 个，而 API 已按涨幅降序 → **等于只画涨幅榜** | 改为今日涨跌幅排序，取涨幅端 10 + 跌幅端 10 |
| 北向资金趋势 | 空白卡片 + `LIVE` 角标 | `/api/north-holding` 返回 `ok:false`（westock CLI 链路已废弃），`request()` 吞掉 reason 后静默 return | 显示后端原始原因，角标改「未接入」 |
| 资金流向概览 | `暂无资金流数据` + loading 样式 | 同上 | 显示后端原始原因，改用空态样式 |

顺带修掉 `LIVE` 角标的诚实性问题：`/api/sector-ranking` 常态返回 `degraded=true` 的历史快照
（实测 `source` 标注 15:41 快照），原实现无论数据新旧都挂 `LIVE`。改用 `fetchSectorRankingEnvelope()`
读取 `degraded`，降级时角标显示「快照」并 `title` 给出数据源。

### 五、其他

- `admin.html` / `api-reference.html` / `quant.html` / `sentiment.html` 缺 `link[rel=icon]`，
  每次访问产生一次 `/favicon.ico` 404 —— 统一补内联 SVG favicon
- 情绪页榜单行改为可点击（`data-href` → `/dashboard.html?code=`），事件委托挂在 `document` 上
- `index.html` 导航增至 9 项后，新增 `@media(max-width:1040px)` 收紧间距（实测 736px / 1080px 容器，无溢出）

### 六、验证

Playwright + 系统 Chrome，7 页 × 1440×900 逐页巡检：

- **7/7 出链 6/6、favicon 齐全、无横向溢出、无 4xx、控制台零错误**
- 情绪页 14 个板块全部有内容（改前 3 个空白 + 1 个卡死）
- 深链 4 个场景（同板块 / 跨板块 / 非法代码 / 无参数）全部符合预期

**已知未改动**：`/api/meta/capability` 作为「前端角标唯一事实源」目前仅被 `js/fundamental.js` 消费，
且多数数据域返回 `unavailable`（反映的是 L2 缓存现状）。接入各页需要先解决冷启动无缓存的问题，
本次未擅自改动。

## 2026-10-22（下午）— 落地页 `static/index.html` 缺陷修复与性能优化

> 用户要求：先读懂系统，再对 `http://localhost:8900/index.html` 进行优化。

### 一、修复的确定性缺陷（8 项，均由浏览器实测确认）

| # | 位置 | 缺陷 | 修法 |
|---|---|---|---|
| 1 | 卡片涨跌色 / 图表 series | 涨=绿、跌=红，与全站 `dashboard.css` 的 A 股惯例（`--red`=涨）相反 | 新增 `.chg-up/.chg-dn` 类，涨红跌绿；图表 series 配色同步互换 |
| 2 | `.ds-reason` | `signals` 是对象数组 `[{type,label,score,desc}]`，却用 `.join(' · ')` → 渲染出 `[object Object]` | `buildReason()` 取 `.label`；无信号时改述真实行情事实（换手/日内位置/振幅） |
| 3 | 信号文案 | 三档分支全是 `🟢`（买入/关注/观望同图标）；`const sig` 为死代码 | 按分数分档，买入=红、关注=金、观望=绿，与交易面板约定一致 |
| 4 | `renderMiniChart` | 8 个板块 `for...await` **串行**，冷缓存最坏 8×2s 白屏 | 并入统一快照层 `Promise.all` 并行 |
| 5 | 假数据兜底 | `upData.fill(0).map(...)` 返回值未接收，随机数逻辑从未生效；且金融页展示随机行情不当 | 删除随机逻辑与 `renderDemoFallback()` 硬编码假股票，改真实空态 + 重试 |
| 6 | `<head>` | 1,029,203 B 的 `echarts.min.js` 同步加载，阻塞首屏 | 改 `defer`，首屏不再等待 |
| 7 | `.stats` | 内联 `gap:48px` 压过 `@media` 的 `gap:24px`，移动端 4 项挤压溢出 | 抽 `.stats-inner` 类 + 768/420 两级断点 |
| 8 | 加载动画 | 引用 `animation:spin` 但全文件**从未定义**该 keyframe，转圈静止 | 补 `@keyframes spin` |

### 二、请求数 14 → 8，串行 → 并行

原链路：健康探测 2 次 + 演示 4 次 + 图表 8 次串行 = **14 次**，其中 4 个板块被重复请求 3 次。
改为统一快照层 `loadSnapshot()`：8 个板块并行一次拉取，45s TTL 复用，三处消费者共享同一快照。
实测热缓存下单板块 0.155s，8 并行 ≈ 单次耗时。

### 三、诚实性修正（金融页面的底线）

- `fast=1` 且无策略缓存时，后端 `score = clamp(50 + chg*5)`（`quotes.py:229`）——**是涨跌幅推导值，不是策略评分**。原页面一律标注「买入信号」并参与排序。
- 现按 `signals` 是否存在区分：有信号 → 「买入信号/关注/观望」+ `title="基于日 K 线策略评分计算"`；无信号 → 「行情评分」+ `title="该评分由实时涨跌幅推导，仅供参考"`。
- 支付弹窗二维码为随机点阵示意图形（`drawQR`），原页面无任何提示。补「演示环境 · 示意图形，不会真实扣款」。
- 首屏徽章不再直接显示冷启动耗时（实测 4067ms 观感不佳），延迟明细移入 `title`。

### 四、其余改进

- `<head>` 内联脚本预置主题，消除浅色用户的深色闪烁（FOUC）
- 演示卡片改为**每板块取最优 1 只**，保证 4 卡覆盖 4 个板块（原实现可能 4 张全是半导体）
- `ResizeObserver` 跟随容器宽度，替代仅 `window.resize`
- 60s 轮询在 `document.hidden` 时跳过
- `prefers-reduced-motion` 降级（关闭 blob 漂浮与入场动画）
- 补 `og:` / `twitter:` / `theme-color` meta 与 `<noscript>` 兜底
- 用户字段经 `_esc()` 转义后再插入 DOM

### 五、验证方式

Playwright + 系统 Chrome 实测（1280×900 / 390×844 / 浅色 / `reducedMotion:'reduce'`）：

- 页面错误 0、控制台异常 0
- 8 次 API 请求全部并行，`[object Object]` 与 `🟢` 残留均为 0
- 图表涨跌家数与接口逐板块核对一致（半导体 5/23、新能源 6/20、医药 1/20…）
- 移动端 390px 无横向溢出；支付弹窗开合、主题切换后图表重绘、刷新按钮均正常

**已知未改动**：首页「AI 信号准确率 95%+」为营销数字，`server/routers/review.py` 虽有回测能力，但未与该文案建立数据绑定，属内容/合规决策，未擅自修改。

## 2026-10-23 — 集成连接修复 + 图谱视觉配置化

> 用户要求：修连接/接入、提升设计、优化图谱美感与**配置的可调性与可扩展性**，并明确修改范围 / 设计要点 / 验收标准。

### 一、修改范围（只动这些，别的没碰）

| 层 | 文件 | 改动性质 |
|---|---|---|
| 后端 | `server/routers/market_data.py` | 信封补 `data` 别名；资金流 SWR；失败负缓存 |
| 后端 | `server/main.py` | `/api/sectors` 补 `data`/`count` |
| 前端 | `static/js/docs-graph.js` | 新增 theme 配置层 + 设置面板 + `setTheme/getTheme` |
| 前端 | `static/project-docs.html` | 工具条「显示」按钮 + 弹出层容器 |
| 前端 | `static/css/docs-graph.css` | 弹出层样式 + 层叠修复 |
| 验收 | `artifacts/verify_theme.mjs`（新增） | 主题配置 11 条断言 |

### 二、集成连接修复（实测驱动）

| 问题 | 改前实测 | 改后实测 |
|---|---|---|
| **信封三套并存**：`/api/sectors` 只有顶层 `sectors`，`linkage`/`clusters`/`graph` 无 `data` | 7 个板块端点中 **2/7** 有 `data` | **7/7** 有 `data`（加字段，旧消费方零改动） |
| **资金流失败后不缓存**，每次请求重打上游 | 每次 **3.2–3.7s**，且 `ok:false` | 首次失败后 **1–2ms** 返回同一原因（20s 负缓存），带 `retry_after` |
| 资金流有旧值时仍同步等上游 | 冷启动 3.5s | 有旧值即刻返回并标 `stale`，后台刷新 |

负缓存的意义：上游持续不可用时，「快速如实报错」远好于「每次都让用户等 3 秒再报同一个错」。

### 三、设计要点（配置化而非硬编码）

把此前散落的魔法数字收敛成一份 `theme`，**四组开关**覆盖观感自由度：

| 键 | 取值 | 影响 |
|---|---|---|
| `motion` | off / soft / normal | 环呼吸幅度、星尘闪烁、核心呼吸光晕、粒子动画（off 时粒子停跑并说明 `why=motion-off`） |
| `labels` | low / normal / high | 常显标签占节点数的比例（20%/40%/60%，仍受 4–9 上限约束） |
| `nodes` | compact / normal / large | 节点直径整体 ×0.86 / 1 / 1.16 |
| `glow` | 0 / 1 / 2 | 节点光晕 `shadowBlur` 倍率 |

三条设计约束：
1. **只影响观感，不影响布局**——主题切换后节点坐标逐字节不变（有断言守着 `maxMove=0` 的等价不变量）。
2. **数据尺寸与显示尺寸解耦**：`symbolSize` 先取 JSON 里 Python 预计算的 `raw.size`，再在渲染层乘一次主题缩放；避免「换主题要重算布局」。
3. **可扩展**：`THEME_VALUES` 是唯一真源，新增维度只需加一项 + 在消费处读一次；`DocsGraph.setTheme()` 支持外部（将来后台配置）下发，`getTheme()` 可回读。

**踩到并修掉的坑**：`.dg-toolbar` 的 `backdrop-filter` 会自建层叠上下文，弹出层即使 `z-index:30` 也只会压在工具条内部 —— 视觉上表现为「点了没反应」。给工具条本身 `z-index:20` 才浮到画布之上。

### 四、验收标准（可复跑的断言，不靠肉眼）

| 套件 | 断言数 | 覆盖 |
|---|---|---|
| `verify_theme.mjs`（新增） | **11** | 默认主题、节点尺度三档递增、标签密度 low<high、motion=off 停跑且说明原因、glow 三档、**改配置坐标不变**、刷新后设置保留、面板 4 组且每组恰一选中 |
| `verify_docs_graph_final.mjs` | **16** | 列表视图对照组指纹、三档视口标签零重叠、筛选零漂移、降级可读 |
| `verify_particles.mjs` | **19** | 方向语义 63/17、不触发重绘、reduced-motion 不启 rAF、视图切换暂停 |

合计 **46/46 通过**。后端另有实测：信封 7/7、资金流失败路径 3670ms→2ms。

## 2026-10-22（晚）— 图谱动效/光效再升级 + 系统↔设计双向索引

### A. 动效与光效（`docs-graph.js` v27→**v28**、`docs-graph.css` v10→**v11**）

在已有「仪器环 + 粒子 + 凸包」之上，补四层克制的动效。共同前提是**幅度小、周期长**：
大起大落会变成闪烁，反而把"精密感"做没了。

| 层次 | 实现 | 目的 |
|---|---|---|
| **环呼吸** | 三环透明度以 ~7s 周期起伏 ±15% | 让静态框架有生命感，但不抢注意力 |
| **星尘闪烁** | 28 个确定性位置的点，亮度按 ~2.6s 相位起伏（0.05–0.21） | 补"空气感"；位置由哈希决定，同一尺寸每次一致，不会乱跳 |
| **核心呼吸光晕** | 3 个核心节点各两圈错相位外扩环（周期 3.2s，α≤0.18） | 它是"心跳"不是氛围灯，所以只给核心，给全部节点就成噪音 |
| **聚焦增强** | hover/选中时，被聚焦链路上的粒子半径 ×1.25、透明度 ×1.3 | 让"我正在看这条链路"有可见强度差，而不是只靠旁边变暗暗示 |

性能未变：粒子 223 个、帧率与关闭动画时一致、`rendered` delta 仍为 0、选中响应 3ms。

### B. 系统 ↔ 设计 双向索引（`tools/gen_system_links.py`）

**动机**：文档图谱只描述「文档之间的引用」。读者看完一篇架构文档，无法知道它写的是哪个模块、
对应哪些接口；反过来也无法从模块查到"谁在讲我"。设计与实现是两套孤立信息。

**做法**：扫 `docs/*.md` 抽出正文里出现的 `server/xxx.py` 与 `/api/...`，与
`server/` 实际文件、路由装饰器、**运行中服务的 OpenAPI** 三方对账，产出
`static/system-links.json`（模块 32 / 被文档引用 28 / 端点 163 / 关联文档 29 篇；
端点校验 144 个真实端点）。面板新增「系统关联」区块，chip 三态：
已实现（金）/ 计划中（灰虚线）/ 缺失（红）。

**这一步立刻查出了真问题**——也修正了检查器自己的两个误报：

1. `etf_rotation.py`、`mcp_client.py` 被 4 篇文档引用，磁盘上并不存在。进一步看上下文，
   它们都写着「**新增**/**新建**」，是 PRD 里的规划项，不是断链 → 检查器改为识别
   「新增/新建/规划/待实现/TODO」上下文并标记 `planned`，而不是一上来就报红。
2. `server/__init__.py` 被扫模块列表跳过，却被判成"不存在的模块" → 误报，已排除。

**保持不变**：索引只是**增补面板信息**，不改变图谱节点/边/标签语义，也不改变任何既有端点。

### C. 验收

`verify_docs_graph_final` **16/16**、`verify_particles` **19/19** 全过；列表视图对照组指纹一致、
三档视口标签零重叠、`maxMove=0`、`prefers-reduced-motion` 下不启 RAF。

**可持续性**：两条生成链都可重跑 —— `python tools/gen_docs_graph.py`（文档图）、
`python tools/gen_docs_layout.py`（布局）、`python tools/gen_system_links.py`（系统索引）。
数据变了重跑即可，前端全部做「校验失败则回退」处理。

## 2026-10-22 — 板块体系缺陷修复 + 图谱离线布局（Python）

> 用户要求：找出板块设计的缺陷与不兼容处并修复；动态图谱再升级，可用 Python 提升。

### A. 板块体系：实测出的 6 个缺陷

| # | 缺陷 | 证据 | 处理 |
|---|---|---|---|
| 1 | **成分股接口不认东财板块码** | `?sector=BK0438` 返回 `unavailable`，而 `data/sector_map.json` 里该板块有 **131 只**成分股 | 已修：新增解析层，BK 码/中文名/自建 key/同花顺码全通 |
| 2 | **前端把接口结果整体丢弃** | 后端返回 `rows`，`app.js:5474` 读 `data.stocks` → 永远走进本地降级分支 | 已修：三字段同源（`stocks`/`rows`/`data`）+ 前端四路兼容 |
| 3 | **三套板块标识互不连通** | 自建 key `semiconductor` / 东财 `BK1036` / 同花顺 `pt01801081` 各说各话 | 已修：`server/sector_resolve.py` 统一解析，`semiconductor → BK1036（188 只）` |
| 4 | **板块排行冷启动 12.9 秒** | 缓存过期后同步拉取：push2 多 host 重试 + CLI 兜底 | 已修：启动预热 + stale-while-revalidate（**首调 12866ms → 34ms**，命中 3ms） |
| 5 | **时间字段两套命名** | 有的端点 `as_of`，有的 `as_of_date`，消费方要写双份兼容 | 已修：两个都给同值，并统一 `stale` 判定（>4 天为陈旧） |
| 6 | **离线产物陈旧却无提示** | `sector_map.json` = `2026-10-16`，接口只报 `coverage{496/496}` 不报年龄 | 已修：所有板块响应带 `as_of` + `stale`，`note` 直说「N 天前快照」 |

**未处理（记录）**：信封仍有三种形态（`{ok,data}` / `{ok,sectors}` / `{ok,...,rows,data}`）；`/api/fund-flow/sector` 缓存过期后仍需 3.6–4.3s。两者改动面大、需前端同步回归，留作独立一轮。

### B. 图谱：Python 离线布局（`tools/gen_docs_layout.py`）

前端力导向只保证「大致均匀」，不保证「团簇清晰」，且社区聚类、凸包这类几何都得在浏览器里重写。因此把布局挪到离线：

- **算法**：BFS 全源最短路 → 应力优化（SMACOF / stress majorization），比纯力导向的结构保真度高、结果可复现。
- **第二版修正（关键）**：对整图直接跑 SMACOF 会得到**一个大圆环**——因为本图 65% 的边是跨组的，全局应力最优解就是把点均匀铺在环上，7 个分组凸包互相重叠成一团灰（实测截图确认，构图反而比原前端布局更差）。改为 **分组约束两层布局**：组内各自 SMACOF 保留结构，组质心按椭圆均分摆放，组半径按规模开方加权。
- **凸包**：Andrew monotone chain 算出每个分组的领地多边形；前端只做坐标投影（从两个节点反解等比缩放系数，roam 后仍贴合）。
- **降级**：节点集合对不上（数据变了）→ 整份布局作废，回退前端力导向，绝不拿错位坐标硬画。

**一个设计修正**：7 个 10% 透明凸包叠在一起就是一团灰。改为**静止只描边、当前筛选的分组才填色**，让填充承担「选中」语义而不是当背景噪声。

### C. 顺带修正的两处不一致

1. 标签分层的**注释与常量互相矛盾**（注释写 45%/下限 5/上限 16，代码是 40%/4/9）。已对齐，并把「为什么不是全显、也不是只显 8 个」的实测理由写进注释。
2. 验收脚本把「常显标签 = 8」写死。改为校验**不变量**：`核心数 + clamp(⌈节点数×40%⌉, 4, 9)`，并要求「至少 8 个」——参数可以调，退化到匿名球不行。

### D. 验收

- 图谱：`verify_docs_graph_final` **16/16**、`verify_particles` **19/19**（列表视图对照组指纹一致、三档视口零重叠、`maxMove=0`、`rendered` delta=0、reduced-motion 不启 rAF）
- 板块：成分股 `BK0438` 131 只 / 12ms；`semiconductor` 188 只 / 58ms；排行首调 34ms
- 当前图谱数据：31 节点 / 80 边 / 7 分区 / 65% 跨组边

## 2026-10-21（晚）— 文档关系图谱 + 三个前端缺陷修复

> 用户要求：`luoluo-121/neural-creator-dashboard` 的设计可作参考，据此搭建与配置项目文档站。

### A. 文档关系图谱（双视图，列表视图零改动）

| 层 | 交付 | 关键数字 |
|---|---|---|
| 数据 | `tools/gen_docs_graph.py` → `static/docs-graph.json` | **30 节点 / 78 边**（`link` 61 + `data_link` 17）/ `missing` 0 / `dangling` 0 |
| 前端 | `static/js/docs-graph.js`、`static/css/docs-graph.css`（均新增）、`project-docs.html` 纯增量 +90 行 | 顶栏「列表/图谱」切换，默认列表 |

**`link` 边自带可回查出处**：每条带 `line`，逐条打开 `source` 文件第 `line` 行确认该行确实出现目标文件名 —— **61/61 命中，0 条对不上**。`data_link` 来自 `project-docs-data.js` 的导航分组登记，**`line` 恒为 `null`**（它本来就没有行位置），故 UI 按 `type` 分支：`link` 显示「引用位置：第 N 行」，`data_link` 显示「登记分组」，**`null` 不渲染而非补 0**。

**修掉一个会诬告自家仓库的缺陷**：旧扫描器把 `docs/20-prd/openbb-dsa-integration-prd.md:89` 表格单元格里这段

```
| `README.md` §License | `[MIT License](LICENSE) © 2026 ZhuLinsen` |
```

反引号内**引用的是 OpenBB 仓库的许可证**，当成了本仓库链接，凭空造出 `docs/20-prd/LICENSE` 节点并标注「被引用但仓库内缺失 · 疑似重构后未更新引用」。根因是行内代码未切分；修在规则层（`link` 边与 `dangling` 共用同一迭代器），非打补丁。

**悬空引用重新可诊断**：`dangling[]` 记录「markdown 链到不存在的文件」，当前为空。此机制一度被降级为命令行输出，后果是文档改名后图谱会静默少一条边，而 `missing` 仍为 0 ——「边消失了」与「边从未存在」在产物里无法区分。

**三条工程取舍（记录在案）**：
1. **放弃 ECharts `layout:'force'`，改为自算一次布局后固定坐标**。原因：force 在每次 `setOption` 重跑模拟，点分组 chip 这种只改透明度的操作也会让节点漂移 —— 实测 `maxMove` 达 **421–520px**。改为 Fruchterman–Reingold 变体（分组圆环扇区初始化 + id 哈希抖动，迭代 340 次，30 节点 < 5ms）+ `layout:'none'`。收益：筛选零漂移（实测 `maxMove=0`，逐节点坐标比对）、CPU 零持续计算、布局可复现。代价：无「拖拽回弹」物理感。
2. **9 张 `.mermaid` 架构图中 7 张不入图**。实测文档里对 `.mermaid` 的 **markdown 链接数为 0**（5 处只是正文/表格/目录树里用行内代码提到文件名），另 2 张零引用。它们没有任何关系边，入图只是孤立节点并虚高节点统计，故不入。这不是漏算。
3. **节点标签分层而非全显**。30 个中文长文件名全显会糊成一片，改为：核心节点（`root` 分组 / `kind` 为 `entry`/`readme`）+ 度数前 5 枢纽节点常显，其余 hover/选中显示，窄屏全部退回 hover-only。`labelLayout.hideOverlap` 保留。

**前端四处修正**（均由实测/截图确认，非推测）：
- `kind` 原本直接 `esc(n.kind)` 输出，面板「类型」显示英文枚举 `entry` → 加中文映射，**未知值兜底为「文档」而非吐原始枚举**
- `isCore` 中 `kind === 'index'` 已成死条件（生成器改产 `readme`），索引节点被漏判为普通节点
- `link.line` 完全未被消费，可回查的出处被浪费
- 首屏 30 个匿名圆点，读者无法分辨节点对应哪篇文档

### B. 基本面「切标的一直转圈」修复（竞态）

根因：基本面与估值区块**共用一个 `_token`**。快速切股时后发请求递增令牌，先发响应归来被丢弃，且被丢弃分支无兜底 → UI 永久停在「取数中…」。改为双令牌 `_tokenFund` / `_tokenVal`，守卫配合 `_isTarget(code)`。

**验证（行为证据，非代码确认）**：三次切换均在 13/25/35ms 到达终态（可排除「慢被误判为卡死」）；写入值 1058/2842 与接口一致；A→B→A 不错配且静置 12s 复查不变；注入全 `null` 响应时渲染 6×「不可用」、`fund-value` 中 **0 个 `0/0.00`**；两区块单向断网互不牵连；`pageerror`/`console.error`/`unhandledrejection` 全为 0。

### C. 浮层误关修复（`app.js` v39 → v40）

现象：基本面 Tab 下点浮层内的按钮，浮层被关掉，与 `app.js:1741` 注释声明的「基本面例外」矛盾。

**根因不是策略不一致，是判定时机错了**：
1. `fundamental.js:671` 在 document 注册了重试按钮的点击委托；
2. 它加载于 `dashboard.html:1425`，**早于** `app.js`（:1437），故先注册先执行；
3. 点重试时同步重渲染，把被点按钮当场摘出文档树；
4. `app.js` 的「点浮层外部收起」监听到达时 `e.target` 已是游离节点，`closest('#kline-overlay')` 返回 `null`；
5. 于是被判为「点了浮层外部」。

修法：**捕获阶段**先算好「本次点击是否落在 `#kline-overlay` / `#kline-tabs` 内」存入闭包，冒泡阶段只读结果，`null` 时退回原判定兜底。刻意保留冒泡阶段执行，使「被 `stopPropagation` 拦下就不收起」的原语义不变 —— 只修时机，不动策略。

顺带排除一个嫌疑：`fundamental.js:675` 的 `e.stopPropagation()` **不是元凶** —— 它不阻止同一节点上的其他监听（那需要 `stopImmediatePropagation`）。

### D. 已知问题（记录，暂不处理）

| 问题 | 为何暂不动 |
|---|---|
| `project-docs-data.js:1888-1889` 导航条目 `name` 并列 3 个 mermaid 文件名，`path` 只指 `v54` 一个 | 改 `name` 会改变文档站列表页文案，直接推翻「列表视图与改造前完全一致」这条已通过验收。需单独一轮清理（拆 `path` 为多条会使 `data_link` 锚点顺序变化、`stats.links` 可能 +4，前端需同步回归） |
| `CHANGELOG.md:8` 写作 `~/.workbuddy//skills//ict-trading-model//` | 是**源文本身**如此，非生成器压坏。曾疑为 `_first_summary` 缺陷，核实后排除 |

### E. 粒子流动 UI（`docs-graph.js` v3→**v6**）

用户要求：**「图谱面板设计太过单一，可增添粒子动态效果 UI 设计，要求能够正确的链接功能间的数据传导」**。

**语义正确性边界（本设计的核心约束：宁可少画，不让动画说谎）**

| 边的类型 | 数量 | 粒子表现 | 依据 |
|---|---|---|---|
| `link`（正文引用） | 61 | **沿 source → target 流动 + 拖尾** | 方向是真实的：引用方 → 被引用方，与 `edgeSymbol` 箭头同向 |
| `data_link`（登记关联） | 17 | **原地明灭的静止点，不流动** | 语义是「两篇被登记在同一导航分组」，**谁也不是谁的来源**；让它流动等于编造一个不存在的方向 |

**数据传导的联动规则**（粒子活性严格跟随真实交互状态，优先级从高到低）：

`hover 节点` → 只该节点的链路通流；`选中节点` → 同左（关闭面板恢复）；`分组 chip / 搜索` → 只有两端都被点亮的边通流（与被淡出的边保持一致，不给已淡出的边画流动）；`无筛选` → 全部通流。

**技术实现**
- 端点坐标取自 `chart.convertToPixel({seriesIndex:0}, pos)`，与缩放/平移同源。实测真实滚轮缩放下**已绘制连线端点匹配 156/156**，坐标空间一致（残差 9–17px 是连线在节点符号边界处的裁剪，属正常）。`roam` 后由 `rendered` 事件节流重算。
- 独立 overlay canvas（`pointer-events:none`，z-index 2），**不触发 ECharts 重绘** —— 实测静置期间 `rendered` delta = 0，原有「静置不重绘」验收未被破坏。
- **避让标签**：粒子层在 ECharts 画布之上，实测会把 10.5px 标签打成麻点（放大 3 倍核对过文字本身完好，破坏来自光点覆盖）。故采集 zrender 中 `tspan` 的屏幕矩形，粒子进入矩形即跳过。
- 性能：粒子总数硬上限 260（实测 217），`requestAnimationFrame` 单循环，帧率与关闭粒子时无差异；选中响应 2ms。
- 无障碍：`prefers-reduced-motion` → 不启动 RAF、整层 `display:none`、开关置灰并说明原因；`document.hidden` / 列表视图 / 卡片隐藏 → 暂停。开关状态存 `localStorage`。

**最终验收：34 条断言全通过**（`artifacts/verify_particles.mjs` 19 条 + `artifacts/verify_docs_graph_final.mjs` 15 条），含列表视图对照组指纹一致、三档视口标签绘制数（1440→8 / 1920→8 / 800→0）、分组筛选 `maxMove=0`、404 降级可读且无未捕获异常。

### F. 修掉本轮引入的两个真实缺陷（均为接手时发现）

1. **图谱整块白屏（P0）**：`docs-graph.js` 在上一轮改动中引用了 `labelText(n)`，但该函数只被写了一半——`computeLabelTexts()` 定义了却**从未被调用**，访问器根本不存在。浏览器报 `labelText is not defined`，整块显示「图谱数据不可用」。已补 `labelText()` 访问器并在 `ingest()` 中调用 `computeLabelTexts()`。**教训**：写了一半的函数 + 未调用的定义，静态检查（`node --check`）完全看不出，必须真实打开页面。
2. **错误文案误导**：渲染阶段抛错被 `fetch` 的 catch 一并吞下，显示成「图谱数据不可用 / 请重新生成 `static/docs-graph.json`」——把用户引向错误方向。现用 `err._stage='render'` 区分，渲染错误明确提示「数据已读到，是代码问题，**不要**重新生成数据文件」。

**标签修复**（承接上一轮遗留）：标签由 `title` 改为文件名（撞名逐级加父目录，`README` / `00-strategy/README`），截断后二次查重；8 个常显标签经实测**两两互异、零重叠**，且 `labelLayout.moveOverlap:'shiftX'` 让 1440 宽下第 8 个标签不再被 `hideOverlap` 吞掉（此前被吞的恰是入链最多的 `DATA-DICTIONARY`）。

### G. 视觉升级：光效与高级 UI（`docs-graph.js` v6→**v9**、`docs-graph.css` v4→**v6**）

用户要求：**「画面还是太单调了，可以配置一些高级的 UI 或者光效等等」**。

**四层光效，全部只改观感、不改语义，也不改任何布局度量**（`maxMove=0`、标签 8 条、`rendered` delta=0 等既有验收全部复跑通过）：

| 层 | 内容 | 关键约束 |
|---|---|---|
| **大气层**（最底，新 canvas） | 底色径向渐变 + 星尘（按尺寸自适应密度，越靠边越稀暗）+ 3 个缓慢漂移的极光光斑（青/金/紫，周期 20–37 秒）+ 暗角 | 静态部分预渲染到离屏 canvas，每帧只 `drawImage`；动态光斑用缓存径向渐变，**每帧不新建渐变对象**；主动限帧 ~24fps |
| **节点** | 球面径向渐变（左上受光、中间原色、右下入影）+ 亮边 + 光晕，**光晕强度随度数增长**（`shadowBlur = min(18, 7 + deg×1.1)`，核心节点 26） | 色相仍取自分组色，球面渐变只做同一色相的明暗；核心仍为 `GOLD` 不偏色。光晕强弱来自**真实度数**，不是随机装饰 |
| **连线** | 辉光（有向 `shadowBlur 7 / rgba(6,182,212,.30)`，无向 `3 / rgba(236,217,166,.16)`），并把颜色沿用粒子层语言：**有向偏青、无向偏金** | 让「实线=流动 / 虚线=不流动」在静态时也有色相提示，与粒子语义一致 |
| **交互光效** | 选中/悬停节点的**双环脉冲 + 中心柔光**（画在粒子层）；悬停节点 `scale: 1.12` 轻微放大 | 光环只在选中/悬停时出现；放大是纯渲染态，不写坐标，不影响零漂移验收 |

**UI 质感层**（纯 CSS，只作用于图谱视图）：工具条与面板换成「有光的玻璃」（顶部 1px 高光、底部渐隐、极淡色相晕染）、面板顶部青→金色带、分组 chip 选中时按自身色相发光、按钮与搜索框聚焦外发光、出链/反链条目悬停时左侧亮条、面板滚动条改暗色细条。

**三处必须守住的底线**
1. **`background-clip:text` 必须包在 `@supports` 里** —— 一旦浏览器不支持，`color:transparent` 会让面板标题**彻底消失**。宁可纯色，也不能丢字。
2. **`prefers-reduced-motion` 下背景不能退回纯黑**：实测 `display:none` 只管粒子层，大气层需改为静态绘制一帧。已验证该模式下画布 `非黑像素占比 99%`、均值亮度 24——「静态但有光」，不是黑屏。
3. **动画层不得触发 ECharts 重绘**：大气层与粒子层都是独立 canvas，实测静置期间 `rendered` delta 仍为 0，原有性能验收未被破坏。

**调优过程（记录一次真实的自查）**：第一版极光过亮（alpha 0.16/0.11/0.13），整图发灰、标签对比度被吃掉；放大核对后把 alpha 压到 0.095/0.062/0.078、暗角从 `.62` 加强到 `.78` 并增加中间档，才达到「有纵深但不抢主体」。

### G2. 按参考站重做设计语言：仪器式极简（`docs-graph.js` v9→**v13**、`docs-graph.css` v6→**v9**）

用户要求：**「目前的动态设计效果显得过于简陋，请参考 solar-system-atlas 中的动态 UI 设计风格进行升级…重点优化动效的流畅度与节奏感、过渡与交互反馈、层次与视觉质感…同时保持现有功能与内容不变，并兼顾不同屏幕尺寸下的适配与动画性能」**。

**先测量再动手**。参考站实测到的设计令牌：

| 维度 | 参考站实测值 |
|---|---|
| 背景 | `rgb(5,5,5)` 近黑中性，body 无背景图 |
| 正文色 | `rgb(231,225,213)` 暖骨白 |
| 强调色 | `rgba(214,176,132,.9)` 暖金 / `rgb(197,138,85)` 铜，**全站仅 17 处非灰文字色** |
| 边框 | `rgba(198,168,130,.7)` 1px 暖金细线 |
| 玻璃 | `backdrop-filter: blur(12px)` + `rgba(8,8,11,.55)` + `border-radius:12px` |
| 标题 | `Times New Roman`、`font-weight:300`、`letter-spacing:6.72px` |
| 图层 | WebGL 主画布 + 固定 `ui-particles` canvas（z-index 150） |

**结论：那种高级感来自克制，不是叠加特效。所以这一版是往回收，不是继续加。**

| 项 | 改前（v7–v9） | 改后（v10 语言） |
|---|---|---|
| 背景 | 青/金/紫三色极光叠加，整图中灰 | 中性近黑 `#070708`，星尘密度减半、亮度降低，暗角加强到 `.88` |
| 强调色 | 青色 `#06b6d4` 铺满连线与粒子 | 统一暖金 `#D6B084`，**只用于悬停/选中/核心节点** |
| 节点 | `shadowBlur` 随度数到 18，核心 26（塑料球感） | 哑光球面（明暗对比 0.58→0.34），光晕只留核心且降到 11 |
| 连线 | 辉光 blur 7 | 1px 细线 + blur 2；无向边改中性灰虚线 |
| 粒子 | 亮青彗星 + 双帧拖尾 | 暖金细尘（核心 0.95px），拖尾缩到 1 个衰减样本 |
| 框架 | 无 | **同心仪器环 ×4 + 径向刻度 + 稀疏金色主刻度 + 12 条极暗引导线 + 十字准线**（参考站的核心识别元素） |
| 排版 | 常规字重、正常字距 | 细字重 + 宽字距（`.dg-kv .k` 大写 10px、HUD 字距 `.10em`）、chip 加 `01/02` 等宽编号 |
| 圆角/发光 | 8–12px + 大范围外发光 | 4px（控件）/12px（面板），去掉外发光，只保留细线 |

**动效的"节奏感"怎么落地的（三道：进入 → 过渡 → 交互）**
1. **进入编排**：`.dg-entering` 一次性序列——工具条 480ms → 画布 640ms(+90ms 延迟) → chip 逐个淡入（120ms 起，每个 +28ms），总时长 < 900ms；只播一次，反复进出不重放。
2. **过渡**：把 `animation:false` 改为 `animationDuration:0` + `animationDurationUpdate:240ms` + `cubicOut`，让筛选的透明度变化有缓动。**关键验证**：坐标固定（`layout:'none'`），实测 `maxMove` 仍为 **0**，没有因为开动画而让节点被插值移动。
3. **交互反馈**：悬停放大 1.12→**1.06**（克制）、选中/悬停双环脉冲周期 1800→**2600ms** 且去掉中心填充光斑、连线高亮 2px→1.4px。

**两处必须记下的坑**
1. **JS 里的 `cssVar('--cyan')` 读的是 `:root`，拿不到 `body.docs-view-graph` 上的重定义**。我原本想用「在图谱视图内重定义 `--cyan` 为金色」一次性替换所有青色高亮，CSS 规则确实生效了，但 `seriesPayload` 里 `cssVar('--cyan','#06b6d4')` 仍然返回青色 → **连线高亮还是蓝的**。已改为直接用 `GOLD` 常量，并在注释里写明原因。
2. **窄屏两行图例压住节点**：`.dg-hint` 是绝对定位在画布左下角，窄屏画布只有 460px 高时第二行正好落在节点密集区 → 用 `<span class="dg-hint-legend">` 包起来，`@media (max-width:900px)` 收起，并给提示加 `text-shadow` 保证压在星尘上也读得清。

**验收（全量回归，未因改视觉而放宽任何一条）**
- `artifacts/verify_docs_graph_final.mjs` **15/15**：列表视图对照组指纹一致、三档视口标签 8/8/0 且零重叠、分组筛选 `maxMove=0`、404 降级可读且无未捕获异常
- `artifacts/verify_particles.mjs` **19/19**：方向语义分类 61/17、粒子不触发 ECharts 重绘（`rendered` delta=0）、`prefers-reduced-motion` 下不启动 rAF 且开关置灰、列表视图自动暂停
- 性能：帧率与关闭动画时一致（headless 240fps 上限），选中响应 2ms；大气层仍限帧 ~24fps
- 适配：1920 / 1440 / 820 三档实拍确认；环数量与刻度密度在 <900px 自动降级（96→60 刻度）

### H2. 视觉体系重做：层级 / 布局 / 光效 / 散点节奏（`docs-graph.js` v13→**v24**、`docs-graph.css` v9→**v10**）

用户要求：**「整体观感偏基础，缺乏高级感与精致度…重点：提升视觉层级与审美质感；优化 UI 布局、间距、字体与色彩；改进光效避免生硬堆砌；调整散点分布/大小/密度/透明度使其富有节奏」**，且不改变功能与内容结构。

**先量化，再动手。** 三项实测把"基础感"定位到了具体数字：

| 实测项 | 改前 | 说明 |
|---|---|---|
| 节点尺寸分档 | 5 档（13/18/23/28/34） | **30 个节点里 21 个挤在 23px 与 28px 两档**；度数 8/10/12/15/17/21 的 6 个枢纽全被压成同一个 34px |
| 节点透明度 | 全部 `1` | 没有前后景，所有点等权重抢视线 |
| 连线透明度 | 全部 `1` | 78 条线在中心叠成灰雾 |
| 最近邻距离 | 36px | 与最大节点直径 80px 同量级，视觉上易读成"粘连" |

#### 一、散点体系：三通道同源驱动

| 调整 | 实现 | 带来的视觉改进 |
|---|---|---|
| **尺寸连续化** | 五档改为幂函数：`18 + 20 × (deg/maxDeg)^0.62` | 枢纽真正"站"出来（度数 21→36px，度数 2→19px），中间层形成连续梯度，不再是一堆等大的球 |
| **透明度分层** | `0.70 + 0.30 × (deg/max)^0.55` | 叶子沉为"远景点"，枢纽留在前台，产生前后景纵深 |
| **空气透视** | 低度数节点向背景色 `#0B0B0C` 混合最多 17% | 边缘节点自然退入画面，不再"所有点一样锐利" |
| **描边分级** | `0.7 + 0.6 × imp` px；重要度 ≥0.65 的枢纽改用金色描边 | 视觉重心集中到少数关键节点，读者第一眼有落点 |
| **光晕收敛** | 仅核心（blur 12）与重要度 ≥0.5 的枢纽（blur 7）有光，其余为 0 | 之前每个点都在发光＝没有重点；现在光是"标记"而非"氛围" |
| **滤镜态保真** | 筛选时按 `baseOpacity × 0.30` 衰减，不再一律拉回 1 | 按分组筛选后，空气透视层次不会突然消失 |

#### 二、连线：从"灰雾"到"仪器刻线"

透明度按**两端中较不重要的一端**取值：`0.34 + 0.48 × (minDeg/maxDeg)^0.5`。叶子间的线退到 0.34，枢纽间的线到 0.82。同时**彻底移除连线 `shadowBlur`**——78 条线各带一层辉光叠起来就是一层灰雾，这是"廉价感"的主要来源。

#### 三、布局与间距：两行分组 + 8pt 网格

| 调整 | 改前 | 改后 |
|---|---|---|
| 工具条结构 | 6 个元素塞在一个 `flex-wrap` 里，1440 宽下搜索框/chip/按钮**随机折行** | 显式两行：`定位/检索/读数` + `筛选/操作` |
| 控件高度 | 各不相同 | 统一 28px，形成一条水平基准线 |
| chip | 会换行成两排，高度参差 | 单行横向滚动（隐藏滚动条），窄屏才换行 |
| 面板留白 | padding 14px，kv 无分隔 | padding 16/20px，kv 块下方加 1px 分隔线，行高 1.45–1.5 |
| 面板标题 | 15px | 17px / 1.4 |

阅读顺序因此变成「先看在哪（面包屑）→ 再找什么（搜索）→ 有多少（读数）→ 筛什么（chip）→ 做什么（按钮）」。

#### 四、字体层级

- 节点标签从"8 个全 10.5px 只靠 700/400 区分"改为**核心 11px/600（近白 `#F2EDE3`）**、**枢纽 10px/500（暖白 `#D8D2C6`）**——之前两档在 1398px 画布上几乎看不出差别。
- 微标签（`分组/类型/大小/被引用`）统一 10px 大写 + `.10em` 字距；面板数值用等宽体，形成"标签弱、数值强"的对照。

#### 五、一次失败尝试（记录在案，避免后人重走）

试图通过"软性半径压缩 + 缩小 zoom"把散点收进仪器环，结果**越调越糟**：

- 压缩会让离群点被推向邻近节点，最近邻从 36px 降到 **25px**；
- 缩小 zoom 同样压缩全局间距（`minNN` 与 zoom 近似线性）；
- 根因：**ECharts 会把布局包围盒自动铺满画布**，所以布局空间的尺度调整会被重新归一化掉，只有"形状"生效。

最终决策：环不再承担"容器"职责，改为略大于短边的**背景量测线**（`0.58 × min(w,h)`，允许上下轻微出血，与参考站"大尺度仪器被画幅裁切"的语言一致）；spacing 优先。同时保留一个**确定性碰撞松弛**作为兜底——实测当前数据 **0 组重叠、0 组近贴**，它没有实际改动任何坐标，纯粹是防止将来节点变多时的保护。

**验收（未放宽任何一条）**：`verify_docs_graph_final` **15/15**、`verify_particles` **19/19**，含列表视图对照组指纹一致、三档视口标签 8/8/0 零重叠、`maxMove=0`、`rendered` delta=0、`prefers-reduced-motion` 不启动 rAF；性能与改前一致（选中响应 2ms）。

### H. 本轮文件版本号

`docs-graph.js` **v24**、`docs-graph.css` **v10**、`app.js` **v40**、`chart.js` v38、`fundamental.js` v2。

⚠️ 本轮曾出现 **`docs-graph.js` 内容已改但版本号未同步**（停在 `v3`）的情况——浏览器缓存会让用户拿不到新代码，且症状与「功能没做」完全一样，极难排查。改任何带 `?v=` 的文件必须同步 bump 并检查所有引用页。

---

## 2026-10-21 — 接入 ICT Trading Model（GitHub 开源，MIT）+ 结构有效性实证

> 用户要求：安装 GitHub 开源 Skill「ICT Trading Model」并融入现有系统，配置稳定、数据与功能相互链接形成正向反馈。

### A. 技能安装
- 来源 `carreypro/ict-trading-model`（**MIT**，可安全融入），安装到 `~/.workbuddy//skills//ict-trading-model//`（用户级，跨项目可用）。
- 组成：`SKILL.md`（九阶段证据链 + 六道 required gate）、`references/ict-model.md`、`references/output-contract.md`、
  `scripts/ict_scan.py`（纯 stdlib 扫描器）、`tests/`。
- 核心纪律：**单一 FVG/OB 不能单独构成信号**，必须六道门全过；输出仅四态 `confirmed/developing/invalidated/no_trade`；只分析不下单。

### B. 移植入系统（4 个端点，全部纯 stdlib、零新增依赖）
| 模块 | 内容 |
|---|---|
| `server/ict_model.py` | `Candle/parse_kline_rows/detect_swings/detect_fvgs/atr_at/detect_sweeps/build_candidate/scan`，输入直接吃 `/api/kline` 的 `[{d,o,h,l,c,p}]` |
| `server/routers/ict.py` | `GET /api/ict/scan`（六道门逐条 + annotations）、`/api/ict/history`（滚动回测）、`/api/ict/structure-stats`（结构统计） |
| `server/ict_history.py` | 滚动扫描（第 i 次只用 `candles[0:i+1]`，**防未来函数**）、confirmed 事件去重、1/3/5/10 日收益统计 |
| `server/ict_structure.py` | FVG 填充率 / sweep 反转率 / OB 回踩率 + **波动率匹配零假设基准** |

**两处 A 股适配**：
1. 原脚本硬编码 `America/New_York` → A 股永远 `outside_preferred_window`，时段门失效；改为 `Asia/Shanghai` + A 股七时段（含 `daily_bar` 语义）。
2. `swing_width` 默认 2 对 A 股日线过敏（250 根出 19 候选）→ 改 **5**（实测 300 根：茅台 25→16、五粮液 25→13、宁德 9→2）。
   并修移植坑：腾讯分钟线时间戳是 12 位 `YYYYMMDDHHMM`，原脚本按 epoch 解析会让时间错乱、session 恒为 `pre_open`。

### C. 实证结论（20 只横截面，400 根日线）
| 结构 | 结论 | 数据 |
|---|---|---|
| **confirmed 信号** | 321 个交易日 **0 次** | 六道门在 A 股日线几乎不触发；周线 400 根仅 1 次（样本不足） |
| **FVG** | **无稳定预测力** | 触及 edge 均值 +2.47pp / **中位 +2.65pp**，20 只仅 4 只超 +5pp；且 edge 随 horizon 拉长（3日+1.75→20日+15.02），符合短期均值回复而非机构订单块假说 |
| **sweep** | **≈ 随机** | 修前视偏差后均值 **+0.79pp**、中位 +0.74，正负对称 |
| **OB** | 无基准，不给结论 | 回踩率 63.11%，显式标 `edge=null` + 「无基准对照」 |

**判定：ICT = 结构看板，不接入决策闸**（填充率 ≠ 方向性收益；两个指标无超额、一个无对照）。

**两次结论修正（记录在案）**：
1. 首轮 sweep −4.57pp「A 股扫单后更倾向延续」→ **撤回**。根因是 `detect_sweeps` 可引用未确认摆动点（前视偏差）；修正后 +0.79pp ≈ 随机。
   连带修了同源的 `_latest_swing`（MSS 参照点）与 `_candidate_target`（目标位）。
2. 中间「FVG 100% 填充率」→ 系把「horizon 内未填充」误当右删失剔除；改三态后 88.35%。

### D. 前端（`chart.js` v34→**v38**，`app.js?v=38`）
- 周期栏旁新增「ICT 结构」开关（**默认关闭**、`localStorage` 记忆）：FVG 半透明色带（bullish 红/bearish 绿，alpha 0.10）、
  OB 虚线边框、sweep 三角标记；**不改主图 Y 轴**（独立 series 不参与 scale）、分时模式不显示、关闭彻底移除。
- **tooltip 携带历史统计**（数据与功能链接）：并发取 `scan` + `structure-stats`（各一次，120s/300s 缓存），
  显示「同类 FVG 历史（91/103 命中）：中位 1 天触及（平均 2.75 天）／触及率 88.35%（基准 76.32%，+12.03pp）」+ **「历史频率，非预测」**；
  负 edge 照实、样本 <10 不显示百分比、单标的外推警告显式给出。
- `median` 与 `avg` 并列（拒绝把平均当中位）：茅台 `median_days_to_partial=1.0` vs `avg=2.75`（91 命中里 48 个第 1 天触及）。

### E. 本轮修掉的既有缺陷（均跨模块）
| 缺陷 | 根因 | 证据 |
|---|---|---|
| `scan` 污染全站 K 线缓存 | `_load_klines` 把最多 800 根写回共享键 `raw_kline`，`/api/kline` 默认根数直接读它 | 改前 `raw_kline→801 根`；改后 scan 用独立键，scan(800)×3 后 `/api/kline` 仍 `bars=121` |
| `sweep` 前视偏差 | 摆动点需右侧 5 根确认，但 sweep/MSS/目标位允许引用未确认摆动点 | 修后茅台 sweep 20→34、edge −14.51→+2.15 |
| `activeCode` 抖动 | `app.js:3073` 刷新时**无条件**重置为列表第一只，图表不重绘 → 与图上标的永久分裂 | 新增 `_preserveActiveStock()`（保留选中/空列表保留/不在列表才回退）+ `_syncChartAfterRefresh()`，接入 5 处刷新路径 |
| 日频域误标「实时」 | `CAPABILITY_DEFS.daily` 字段存在但无人读取 | `_classify(allow_realtime=)` 接 `daily`，finance/valuation/dividend/ict/profile/research 恒 `cached`；`quote`/`positions` 不变 |
| 盘中「无人订阅」被报成「行情源故障」 | QuoteHub 惰性取数，无订阅者时缓存自然变老，`_classify` 判 `unavailable` | 改判 `closed` + 「未订阅 · HH:MM」+ 说明惰性取数（`degraded=false`）；有订阅者超限才仍报 `unavailable` |

### F. 验收
- `GET /api/ict/scan?code=sh600519&period=day` → `ok:true, status=developing, score=12, gates 6 条（1 pass/4 fail/1 unverified）`，`session=daily_bar`、`tz=Asia/Shanghai`
- `GET /api/ict/structure-stats` → `fvg samples=103 触及 88.35% vs 基准 76.32%（+12.03pp）`；`sweep samples=34 反转 61.76% vs 59.61%（+2.15pp）`
- `GET /api/ict/history?limit=400` → `confirmed=0, invalidated=1`，`verdict.available=false` + 原因
- 前端 v38：`node --check` 通过；Y 轴开启前后逐位相同；关闭后 `overlays=0`；重复开关 0 新请求；分时无标注；负 edge 照实
- 回归：`/api/health`、`/api/kline`、`/api/kline/indicators`、`/api/finance`、`/api/risk/portfolio`、3 个 ICT 端点、`/api/meta/capability` 全 200
- 三态验证（进程内）：盘中+无订阅→`closed/未订阅`；盘中+有订阅→`unavailable`；休市→`closed`

### G. 方法论沉淀
- **worker 反复 `502 / EAI_AGAIN` 断线时，代码常已落盘、只是报告没送达** —— 必须验版本号/行号/语法/服务，不能按「失败」重派。
- **不能用单次 curl 判定缓存型接口的字段有无**：`ict:structure` 结果缓存 600s，会读到改前结构；应跑 `scripts/verify_ict_structure_contract.py`。
- 结论若被后续修正推翻，**要在文档里写明撤回过程**，不能只留最终结论（sweep 案例）。

## 2026-10-19（三）— 分时数据接入 + 实时行情韧性（低延迟 / 断线自愈 / 一致性）

> 用户要求：「接入分时K线数据，实现实时行情对接，确保数据能够低延迟、稳定地持续更新。系统各模块之间需要保持可靠链接与协同，并具备断线自动重连、数据异常处理与容错机制，保证行情展示和后续处理的一致性与稳定性。」

### A. 统一报价中枢（新增 `server/quote_hub.py`）
- 所有消费方（SSE `quotes`、`/api/kline/quote`、分时校准）读**同一份短 TTL 快照**（`max_age=1.5s`），
  per-code **single-flight**：并发请求共享同一 future，不重复打上游。
- `snapshot_ex()` 返回本轮真实 `upstream_delta`，`stream.py` 用它累加 `quote_upstream_calls`
  （不再读全局计数器差值，修掉窗口边界双记）。
- 容错：上游失败保留上一份有效快照并标 `stale/stale_age`；超 `STALE_MAX=30s` →
  `price=None + chg_pct=None + valid=false + reason="行情源 N 秒无更新"`，**绝不用旧价或 0 冒充实时**。

### B. 分时数据（新增 `GET /api/timeshare?code=`）
- 三级真实源：腾讯分时（精确均价，累计额÷(累计手×100)）→ 腾讯 m1（均价推算，标 `avg_price_estimated=true`）
  → 新浪 1m（逐分钟量额，精确）；东财 `klt=1` 实测 403 不可用，未列入。
- 累计量额在服务端转**每分钟增量**；缓存盘中 5s / 盘后 60s；全失败返回 `points:[] + degraded + reason`，一个点都不造。
- 实测（茅台）：`tencent_minute`、150 点@13:28 / 267 点@收盘（含 15:06–15:30 盘后固定价格点）。

### C. SSE 与客户端韧性
- 信封新增全局单调 `seq`；心跳**注释行 `: ping` + 命名事件 `ping` 双发**（注释行不触发 EventSource 事件，
  仅靠它前端无法做假死检测）；连续失败 ≥3 推 `quotes.unavailable`，恢复首推 `quotes.snapshot`。
- `dashboard-stream.js` v1→**v3**：指数退避重连 1→2→4→8→16→30s（±20% 抖动、默认无限）、
  `staleTimeout=45s` 假死看门狗、`onResync()` 重连后拉快照补空洞、`seq` 回退/重复丢弃并计数、`stats()` 观测。

### D. 分时图（`chart.js` v31→**v34**，`app.js?v=36`）
- 周期栏新增「分时」（`data-kline-period="min"`）；ECharts 双网格（价格+均价 / 成交量），
  242 个交易槽（09:30–11:30 + 13:00–15:00），Y 轴以昨收对称 + 右轴涨跌幅，昨收虚线，涨红跌绿。
- 实时：复用**同一条 SSE**（不为分时另开连接），`quotes.tick` 按分钟归并（同分钟更新价/量/均价，跨分钟追加），
  每 60s 用 `/api/timeshare` 校准防累计漂移。
- **量轴 P90 截断**：`cap = max(P90×1.8, P50×2)`，数据不替换（顶格裁切），tooltip 给真实量并注明「顶格/真实值」，
  图内两处标注「量轴已截断至 P90」——像素覆盖 **2.49% → 18.92%**（1680×1000）/ 21.07%（1266）/ 16.06%（2200）。

### E. 本轮修掉的真实缺陷（均带实测证据）
| 缺陷 | 根因 | 证据 |
|---|---|---|
| 订阅预热串码 | `subscribe()` 把 `_last_events` 原样塞给新连接，不校验 codes/period | 新连接订阅 `sz999999` 却收到 `sh600519` 事件；修复后外来事件 0 条、单测 6/6 |
| 跨标的降级污染 | `_klineLiveHandleEnvelope` 先判 `degraded` 后校验 code | A 标的失败导致 B 的徽章显示「连续失败 4 次」且 `mode` 被置 poll；修复后 24 次采样 0 次 |
| 双源不自愈 | 只在 `onOpen` 复位 `mode` | 降级后 tick 到达仍 `mode=poll`，SSE+5s REST 并行；修复后 tick 即回 `sse` |
| 分时槽映射错位 | 用「数组尾巴」做时序基准，含 15:30 盘后点 | 早盘 tick 被当新点插入、`15:00` 槽被写脏；修复后按分钟序号+槽归一，早 5 分钟 tick 被拒 |
| 分时返程竞态 | 迟到响应无条件重绘 | 切日K 后被分时响应覆盖回分时（也是 `chartType` 卡住的真因）；修复后 renders 4→4→4 |
| 盘后降级卡 poll | `closedApplied` 一次性闸门提前 return，`heal` 永不触发 | 盘后降级后 20s 内 5 次 5s 轮询；修复后 2.0s 自愈、60s 请求增量 0 |

### F. 验收（QA 独立复验，非工程师自证）
- `verify_kline_live.mjs` **12/12**（连续 3 次）；`verify_realtime_resilience.mjs` **30/30**；
  P3 复验 **16/16**（独立 `page.on('request')` 计数，请求时刻 `[0, 4, 66]` 秒）。
- 报价一致性：REST 与 SSE 同一时刻 5 次采样 `diff=[0,0,0,0,0]`；TTL 内第二次 REST `upstream_calls +0`。
- 共享取数：1 连接 20s `hub +4 / polls +4`；3 连接同批 codes `+4 / +4`，**比值 1.00**（连接数不放大上游）。
- `expired` 路径独立实例验证 **12/12**（`artifacts/verify_expired_path.py`：抖动期留真实旧价并标 stale，超 30s 置 null + reason，
  全局单例 `_stale_max=30.0` 常量核对通过）。
- 断线重连方法学纠正：`page.route().abort()` 与 `context.setOffline(true)` **都打不断已建立的 EventSource**；
  改用「本地反代 + 服务端销毁 socket 并拒绝新连接」才拿到真实证据（退避 937/2362/4800ms，恢复后 `reconnects=1`）。

### G. 开源能力接入调研（规划，未实现）
- 新增 `docs/20-prd/openbb-dsa-integration-prd.md`（619 行）：OpenBB（**Apache-2.0**，此前误判为 AGPL）与
  DailyStockAnalysis（`ZhuLinsen/daily_stock_analysis`，MIT）的能力拆解、可复用 Top 8、四类缺口、
  P0 7 项（28–41 人天）/ P1 6 项 / P2 5 项、联动路径与优先级总表。
- 关键事实：OpenBB 18 个 provider **零中国源**；DSA 明确**不做下单**（全仓仅富途只读持仓）；
  交互式图表我方已强（`dataZoom` 80 / `axisPointer` 64 / `brush` 174），真正缺 **L4 点击钻取（2 处）** 与 **L5 图表联动（`connect` 0 处）**。

## 2026-10-19（续）— K 线实时对接验收收口 + 工作流/接口文档体系

> 用户要求：① 「股票的K线要求实时的动态对接」；② 「把该系统的工作流和接口给我」。
> 红线不变：真实数据 > 如实降级 > ✗编造；取不到一律 `null + reason`；涨=红/跌=绿。

### A. K 线实时对接：两处致命缺口修复（上一轮工程师被额度中断，主体已落盘但未通）

1. **`static/js/dashboard-stream.js`（?v=1→2）事件白名单缺 `quotes.tick`**
   K 线实时模块复用 `DashboardStream.create()`，而客户端只监听 `EVENT_NAMES` 白名单内的事件。
   缺 `quotes.tick` → 事件永远进不来，K 线恒显「实时连接中…」直至看门狗降级为轮询。
   已补 `quotes.tick` / `quotes.snapshot`。
2. **`.kline-live-badge` 无任何 CSS**（`chart.js:2168` 只挂类名）
   徽章裸渲染、文本粘连（`11:06:411343.16`）。已在 `static/css/dashboard.css`（?v=66→68）补：
   胶囊样式 + 状态配色（live 青 / poll 琥珀 / closed 灰 / degraded 红 / paused 半透明）+ 脉冲点动画
   + `.klb-price.up/.dn` 涨红跌绿；`chart.js`（?v=28→30）价格块前补空格，读屏不再连读。

### B. K 线实时端到端验收（`artifacts/verify_kline_live.mjs`，Playwright，12 项全通过）

| 断言 | 实测 |
|---|---|
| 首屏全量 K 线渲染 | bars=121 |
| 实时模块激活 + 徽章渲染 | code=sh600519 period=day，state=connecting |
| 真实 SSE 报价到达 | `status=live updates=1 asOf=2026-10-19 11:06:41` |
| 同周期报价 → 更新最后一根 | close 1336.5 → 1343.16，`appends=0` |
| 新周期报价 → 追加新 bar | `appends=1`，时间戳 1792368000000 → 1792425600000 |
| 页面隐藏 → 暂停 / 回前台 → 重建 | `paused active=false` → `connecting` |
| console 无脚本错误 | 0 条 |

真实运行态（`artifacts/shot_kline_live.mjs`）：`state.code=sh600519 status=live`
`badge="实时 · 11:10:33 1337.84 +0.67%"`，`lastBar.close=1337.84` 与实时价一致 —— 最后一根 bar 确由实时成交驱动。

### C. 新增：系统工作流与接口文档（用户第 ② 项）

1. **`docs/10-architecture/SYSTEM-WORKFLOW.md`（新）**
   六条主链路：数据接入（多源降级+分级缓存）→ 选股与信号 → 9 关风控 + 四维决策闸 → 持仓守卫
   → 复盘迭代（日终闭环）→ 实时推送；含 Mermaid 架构图、存储落盘表、KV 配置段、
   接口↔链路映射、自检命令、**已知边界（实盘未接入 / 休市行为 / 描述缺口）**。
   文中 38 个 `/api/...` 引用已逐一比对真实路由，全部命中。
2. **`docs/40-ops/API-REFERENCE.md`（新，141 端点 / 13 分组）**
   由运行中服务 `/openapi.json` 生成（`tools/gen_api_doc.py`），**不手工维护**，代码变了重跑即可。
3. **`static/api-reference.html`（新，在线可搜索）**
   搜索 + 分组筛选 + 点击复制路径 + 方法着色；数据由 `tools/gen_api_page.py` 内嵌生成。
   实测：搜索「复盘」→ 3 条，筛选「自动化交易」→ 22 条，0 console 错误。
   入口：`dashboard.html` 头部「🔌 接口」、`project-docs.html` 顶栏「🔌 接口目录」、看板文档地图两条新条目。

### D. 接口描述覆盖：从 30+ 条空描述补到 0

FastAPI 的 `description` 取自路由函数 docstring。此前大量端点只有自动生成的英文名（`Get Config` / `Run Backtest`），
文档里只能显示「—」。本轮在**源头**补中文 docstring（改动即文档，不另维护一份）：

- `server/routers/automation.py` 15 条（config/orders/positions/gate/live-switch/条件单/策略门禁/熔断/邮件…）
- `server/routers/evolution.py` 7 条（闭环总览/迭代日志/单笔复盘/策略库/提案/apply/reject）
- `server/routers/quant.py` 6 条（回测任务的提交/进度/结果/日志流，并注明该组为早期直返 JSON、非三键信封）
- `server/routers/market_dashboard.py` 1 条、`server/routers/scan.py` 1 条、`server/routers/brain.py` 1 条

覆盖率自检：`仍无描述: 0`（脚本比对 `artifacts/openapi.json` 全部 141 个操作）。

### E. 实测取证
- `curl /api/health` → 200（重启后）
- `curl /api/kline/quote?code=sh600519` → price 1336.51 / chg_pct 0.57 / market_closed false
- `node --check` 通过：`chart.js`、`dashboard-stream.js`、`project-docs-data.js`
- `py_compile` 通过：automation / evolution / quant / market_dashboard / scan / brain
- 版本号：`chart.js?v=30`、`dashboard-stream.js?v=2`、`dashboard.css?v=68`（dashboard/sentiment/project-docs 三页一致）、
  `project-docs-data.js?v=12`

## 2026-10-19 — 自动化中心四块补齐 + K 线实时动态对接

> 用户要求：① 界面与交互 ② 数据链路 ③ 买卖决策 ④ 复盘功能；随后追加「股票的K线要求实时的动态对接」。
> 红线不变：真实数据 > 如实降级 > ✗编造；取不到一律 `null + reason`，绝不给 0；涨=红/跌=绿。

### A. 后端：接口补齐与参数可配（买卖决策 / 数据链路）
- **`server/trading/decision_gate.py`**：决策闸四维权重、置信度阈值、目标日波动、波动因子边界、技术基础分
  由硬编码改为 KV `decision_gate` 可配（30s 缓存；`normalize_gate_config()` 逐项校验，非法项丢弃回退默认；`bust_gate_config_cache()`）。
  `evaluate()` 新增 `gate_cfg` 形参，缺省=默认值（向后兼容）。
- **`server/trading/risk.py`**：下单链路加载并传入闸配置（`load_gate_config(self._store)`）；
  `gate_overview()` 的策略视图补 `name`（中文显示名，取自 `Strategy.name`，去前端硬编码）。
- **`server/trading/store.py`**：新增 `list_audit()`（审计日志分页+过滤查询）与 `kv_updated_at()`（配置新鲜度）。
- **`server/trading/conditional.py`**：新增 `sweep_expired()` 并在 `poll()` 首步调用——pending 超过
  `conditional_expire_days`（KV，默认 30 天）未触发 → `expired` + audit 留痕（此前 `expire_conditional` 无调用方）。
- **`server/routers/automation.py`**：
  - 新增 `GET /api/automation/audit`（留痕查询，支持 action/actor/result 过滤 + 分页）；
  - `GET /config` 透出 `decision_gate`、`conditional_expire_days`、`updated_at`（各 KV 段更新时间）、策略 `display_name`；
  - `POST /config` 支持 `decision_gate` / `conditional_expire_days`，并对 `trigger.signal_types` 做**白名单校验**
    （合法 15 值源于 `panel_adapter._PASSTHROUGH_TYPES` + MACD + score/chg_spike，非法值 HTTP 400 拒绝，不再静默落库）。
- **`server/main.py`**：全局 500 信封补齐 `data: null`，与全站三键信封一致。
- **`server/routers/stream.py`**：`connected` 事件带 `sid`，供前端识别自身连接。

### B. 后端：K 线实时动态对接（本轮后半程）
- **`server/routers/stream.py`**：新增 SSE 主题 `quotes`
  （`GET /api/stream/quotes?codes=sh600519&period=day`，亦可在 `?topics=quotes&codes=...` 合并订阅）。
  - 事件 `quotes.tick`，3s 节拍（休市降频 30s 但仍如实推末次快照 + `market_closed:true`）；
  - 所有订阅者共享一次上游取数（`quote_polls == quote_upstream_calls`，实测 2:2，连接数不放大上游）；
  - 每只标的含 `price/open/high/low/prev_close/chg_pct/volume/amount/pe/ts/bar_ts/as_of/market_closed/valid/reason`；
  - `bar_ts` 按订阅周期对齐（日/周/月=当日日期，分钟=周期起点），前端据此判「更新最后一根」还是「追加新 bar」；
  - 取不到价 → `price:null` + `reason`，`degraded:true`，**绝不用 0 冒充**。
- **`server/routers/kline.py`**：
  - 缓存 TTL 分级：分钟级（1/5/15/30/60）15s，日/周/月 600s（原统一 600s，盘中明显滞后）；
  - `GET /api/kline` 新增 `fresh=1`（绕过缓存直取最新）；
  - 新增 `GET /api/kline/quote?code=&period=`（SSE 不可用时的轮询兜底，与 `quotes.tick` 同结构）。

### C. 前端：自动化中心信息架构与状态反馈
- **`static/js/automation.js`**（?v=7→8）：
  - 新增第 6 个 Tab「留痕」（`loadAudit/renderAudit` + 动作/结果过滤 + 分页 + 决策闸四维摘要），
    `dashboard.html` 同步加 tab 与 `#auto-tab-audit` 容器；
  - 配置 Tab 补「决策闸参数」卡（四维权重/阈值/目标波动/条件单过期天数，可保存）；
  - 配置/信号/订单/复盘四 Tab 补**错误态 + 重试按钮**（此前接口失败=永久「加载中…」）；
  - 状态灯区分「数据不可用」与「自动化已关闭」（此前接口挂了显示成已关闭）；
  - 信号类型拆成**显示映射**（补 7 类中文名）与**可配置白名单**（13 类，去掉不可配的 chg_spike）；
  - 策略名改用后端 `display_name`（`strategyLabel()` 优先后端，回退本地字典）；
  - 复盘 Tab：补绩效指标卡（胜率/PF/夏普/年化波动/成本/可回溯率）、出场归因分布、**交易明细回放表**
    （MFE/MAE/出场效率/归因标签）、历史周报串、数据截止时间 + 窗口切换（7/30/90/180/365 天，不再写死 90）；
    `alert()` 改 toast；`profit_factor=null`（全赢时）显示 `--` 而非 0；
  - 列表下拉加载保持滚动位置；SSE 降级判据改为读信封 `degraded/reason`（原只认 `signals.unavailable` 事件名，
    真实降级 100% 漏掉）；`visibilitychange` 隐藏即断开、回前台重建；降级时启动真实 30s 兜底轮询（原是空承诺）。
- **`static/css/dashboard.css`**（?v=65→66，三页一致）：`.auto-row-failed` 由「整行染红 !important」
  改为「仅左侧 3px 红条」，不再覆盖卖出绿；`.auto-tab-page` 补 `margin:0 auto`；`.auto-switch-row` 补 `flex-wrap`。

### D. 实测取证
- `GET /api/automation/audit` → total 1432 条，payload 已结构化 ✓
- `GET /api/review/trade-reviews` → 15 条（含 mfe/mae/tags）✓ `GET /api/review/weekly-list` → 2 周 ✓
- `GET /api/review/summary` → `risk.sharpe=-8.71 annual_vol_pct=0.59 sharpe_reason=null` ✓
- `POST /api/automation/config {"decision_gate":{"allow_threshold":0.55,"weights":{"sector":0.4}}}` → 生效并回读一致 ✓
- 非法 `signal_types` → HTTP 400 + 明确提示 ✓
- `GET /api/kline/quote?code=sh600519` → price 1337.87 / chg_pct 0.67 / market_closed false ✓
- SSE `quotes.tick` 3s 连续推送，`quote_polls=2 / quote_upstream_calls=2`（共享取数，不随连接数放大）✓
- `node --check static/js/automation.js` 通过；`py_compile` 8 个后端文件通过

## 2026-10-16（夜）— 极致查漏补缺：数据完整性 / 真实数据 / 后台可见性 / 推送健壮性

> 用户要求「解决以上问题，极致化的完善」。本轮不做新功能，只把**已确认的每一处缺漏当场修完并实测**。
> 全部结论均以**服务实测 + 数据库取证 + 浏览器端到端**为准，不接受"代码看起来对"。
> 平台侧多次出现 worker 超时（`PacRpcClient request timed out`）与协议错误，故本轮由主理人直接落地。

### 1. 宏观卡「假数据」纵深防御（前端）
- **`static/js/intelligent-analysis.js` `renderSurveyData()`**：新增 `MACRO_DEGRADED_SOURCES = ['fallback','example','sample','synthetic']`，
  渲染前**直接剔除**降级来源条目，**不让其进入 DOM**；剔除后为空 → 走 `market-account-empty` 空态 + `brainMarkFailure`（带具体 reason）。
- 删除 `isFallback` / `mc-badge-example` / 「示例数据（非实时）」/ `.is-example` 整套渲染路径；`allReal` 语义简化为「剔除后皆为实时源」。
- **`static/css/dashboard.css`**：删除 `.mc-badge-example`、`.macro-card.is-example` 死规则（保留 `.mc-badge-real`）。
- 背景：后端 `/api/intelligent/ak/macro` 已不再产生 `source:'fallback'`（见上一条日志），前端这套是**死代码 + 隐患**——
  哪天后端回归，编造的宏观读数（如「PMI 50.4%」）又会当真实值上屏。
- `?v=`：`intelligent-analysis.js 31→32`。

### 2. 订单「请求数量」不再丢失（P0 数据完整性）
**问题**：用户在数量换算**之前**被风控拦下时，落库 `qty=0` —— ① 交易台会显示「你要买 0 股」误导；② 复盘无法回放「如果当时买了会怎样」。
- **`server/trading/models.py`**：`OrderResult` 已有 `req_qty`/`req_amount` 字段，本轮补齐**写入链路与消费方**。
- **`server/trading/store.py`**：`_MIGRATIONS["orders"]` 补 `req_qty INTEGER / req_amount REAL`（已在库上生效）；
  `insert_order()` 的 INSERT 列表与参数补齐这两列。
- **`server/trading/risk.py`**：两个**拒绝分支**（决策闸拒绝、9 关拒绝）在构造 `OrderResult` 时先落
  `qty=order.qty, req_qty=order.qty, req_amount=round(qty×price,2)`——因为 `check_order()` 后续会把 `order.qty`
  改写为风控换算后的数量，不在此刻留痕就永久取不到原始请求值。
- **`server/routers/automation.py`**：`_order_summary()` 透出 `req_qty`/`req_amount`。
- **`static/js/automation-trade.js`**：订单列表在 `qty=0 且 req_qty>0` 时改显「N 请求」并带说明 tooltip（防御性兜底）。
- **实测**：`POST /api/automation/orders {"code":"sh601888","qty":100}` → 落库 `qty=100, req_qty=100, req_amount=4920.0` ✓

### 3. 演练单（`is_drill`）不再泄漏进真实统计
**问题**：真实统计里混入了 6 笔测试数据（`is_drill=0`），且**仍在持续新增**——因为手动下单接口没有声明演练意图的入口，
而 `marks.drill_active()` 在演练上下文关闭时恒为 False。
- **`server/routers/automation.py`**：`ManualOrderBody` 新增 `is_drill: Optional[bool] = None`（None = 交由服务端演练上下文判定），
  并透传到 `OrderRequest`。
- **`static/js/automation-trade.js`**：手动下单表单新增「**演练单**」勾选框（带说明「不计入真实统计与晋级降级」），提交时显式带 `is_drill`。
- **历史数据修复**：按**显式 id 列表**（非区间）把 2026-10-16 18:31~18:35 交易台联调产生的 5 笔补标为 `is_drill=1`。
  **`ord_f79f7f55c703`（2026-10-08, signal）是真实信号拒单，未动**。
- **实测**：全库 90 笔中 `is_drill=0` **仅剩 1 笔**（即上述真实拒单），分布：
  `signal/filled 46 · signal/rejected 31 · manual/rejected 8 · manual/partial_canceled 2 · conditional/filled 1 · manual/cancelled 1 · manual/filled 1`
- 端到端实测：浏览器勾选「演练单」下单 → 落库 `is_drill=1` ✓

### 4. 初始本金回归「单一真相源」
**问题**：同一个 `1_000_000.0` 在**四处**各自定义（`models.DEFAULT_INITIAL_CASH` 已声明自己是唯一真相源，但未被执行），
且 **iterator 的策略级最大回撤分母用了写死本金**——虚拟盘账户本金 ≠ 100 万时回撤百分比系统性算错。
- **`server/trading/backtest.py`** / **`server/trading/paper.py`**：改从 `models.DEFAULT_INITIAL_CASH` 导入，删除本文件内的字面量。
- **`server/trading/iterator.py`**：删除本地 `_INITIAL_CASH`；`strategy_metrics()` 新增 `initial_cash` 形参（分母），
  `run()` 从虚拟盘账户 `paper_account.initial_cash` 读取真实本金后传入，未初始化时回退缺省值；
  返回值新增 `maxdd_base_cash` 便于审计「回撤是按哪笔本金算的」。
- **实测**：`grep -rn "1_000_000" server/ --include=*.py` → 仅剩 `models.py` 的定义行与两处说明性注释。

### 5. 后台管理：阻塞 CPU 采样 + 假零 + 信息缺位
**问题 A**：`GET /api/admin/system` 在 `async def` 里直接 `process.cpu_percent(interval=0.5)` —— **阻塞事件循环 500ms**
（当时 SSE 正在推流，等于给所有请求叠加 500ms）；且 psutil 未建立基线时恒返回 `0.0`，被当**真实值**返回 → 前端显示「CPU 0%」（假零）。
- **`server/routers/admin.py`**：新增 `sample_process_cpu_percent()` / `sample_system_cpu_percent()`：
  非阻塞（`interval=None`）+ 5s 时间闸（避免相邻调用读出伪值）+ **首帧（基线未建立）返回 `None`**，由调用方如实显示 `--`。
  `system_status()` 增加 `cpu_scope:"process"` 与 `cpu_reason`；`_generate_system_notifications()` 改为
  「样本为 None 时不做阈值比较」，避免把「未采样到」误判成 0% 而静默放过真实告警。
- **实测**：首次调用 `cpu_percent=null` + reason；第二次调用 `cpu_percent=8.3`（真实值）。

**问题 B**：后台**看不到本轮升级模块的任何状态**（用户第 3 条要求实质未完成；`system-health` 全库 0 命中）。
- **新增 `GET /api/admin/system-health`**（`server/routers/admin.py`），五块，**每块带来源与时间戳，取不到一律 `null`+`reason`**：
  ① 外部因子新鲜度（`global_hist_*.json` 的 last_date/fetch_ts/行数 + 组内相对陈旧判定）
  ② 离线构建产物（`sector_map/sector_periods/sector_returns` 的条目数、mtime、大小、状态）
  ③ 交易引擎（初始本金/现金/订单总数/**真实单 vs 演练单分离**/source×status 分布）
  ④ 实时推送（复用 `stream.hub.stats_snapshot()`：在线订阅/广播/丢弃/上游刷新与缓存命中）
  ⑤ 功耗与缓存（`api_call_logs` 行数+文件大小+**人类可读的 last_cleanup**、缓存条目数与 TTL 分布、进程 CPU/内存）
- **新增 `static/js/admin-health.js`**（+ `static/admin.html` 容器与 `.ah-*` 样式）：渲染五块；
  **单一可暂停定时器**（45s，`visibilitychange` 时停止——页面隐藏期间零请求）；空值一律 `--`；
  CPU/内存/缓存等**非方向性指标用中性色 + 「正常/关注/偏高」文案，不使用红绿**；
  端点未就绪时显示「数据源不可用 + reason」空态。
- `?v=`：`admin-health.js?v=1`（新文件）。
- **实测（Playwright 无头）**：5 块全部渲染、来源标注齐全、`consoleErrors=[]`；
  页面隐藏后 `isRunning()=false` 且 3 秒内**零请求**，恢复可见后自动重启；VIX 被正确标为「落后于最新」（2026-09-15 vs 2026-10-16）。

### 6. 行情字段的「假零」清理（`_safe_float` → `_opt_float`）
**问题**：`server/ak_market.py` 的 `_safe_float(val, fallback=0.0)` 被用在**行情字段**上（价格/涨跌幅/成交额/净流入…），
上游缺字段时会返回 `0.0` → 前端渲染成「价格 0」「净流入 0」。
- 新增 **`_opt_float()`**：缺失/`-`/`--`/NaN/±inf → `None`（如实表示无数据），并替换：
  样本行情（price/chg/amount/turnover）、沪深港通（net_buy/net_inflow/index_chg）、个股资金流（f62/f2/f3/f66/f72/f78/f84）、
  指数行情（chg、amount_wan）、新闻时间戳（缺失→`null` 而非空串）。
- 北向 `total/hgt/sgt` 的换算链改为 `None` 安全的写法，`total` 仅在沪股通与深股通**都**有值时才求和。
- 保留 `_safe_float` 仅用于「0 有意义且已被 `<=0` 守卫」的位置（宏观指标取值等）。

### 7. SSE 实时推送：五项健壮性全部取证（非采信汇报）
- **断连回收（决定性实验）**：基线 `active_clients=0` → 连上 `1` → `kill -9` 客户端后回落到 `0` ✓
  （实现：`request.is_disconnected()` 以 `POLL_SECONDS=1.0` 粒度轮询，空闲订阅者也能在 ≤1s 内被发现；`finally` 注销）
- **快照共享（3 客户端 60s 压测）**：`market_refresh_invocations` **仅 +1**，`market_cache_hits` **+18** ✓
  —— 即「连接数翻倍，上游取数不翻倍」，符合单例共享发布模型。
- **背压**：`MAX_QUEUE=100`，灌入 125 条 → 队列保持 100、`dropped=25`、**丢最旧保最新**（队首 seq=25 / 队尾 seq=124）✓
- **心跳**：40s 采样捕获 **2 次 `: ping`**，与 `HEARTBEAT_SECONDS=15.0` 一致 ✓
- **语义正名**：`clients_total_ever` 已补齐，`clients_total` 注释明确「历史总数，非在线数」✓

### 8. 交易台 UI（阶段三）缺陷修复
- **信封级失败被吞掉**：`api()` 返回 `{ok:false, data:null, message:"…"}` 时（如「无法获取有效现价」），
  旧 `submitManual()` 只读 `res.data` → 结果面板空白、toast 显示「被拒：」后一片空白。现改为**如实透出服务端 message**。
- **实测（Playwright）**：已成交单 25 笔的撤单按钮 **25/25 全部 disabled**；**遍历点击这批按钮产生 0 个 DELETE 请求**
  （证明是真正拦截而非仅视觉变灰）；被拒结果面板完整显示
  「被拒：单笔金额超上限 50000 元或价格过高，换算数量为 0｜关卡：4_amount_per_order｜决策闸 confidence=0.523」；
  51 行订单的 `decision_json` 悬浮内容无 `undefined` / `[object Object]`；`consoleErrors=[]`。
- **toast 路径验证**：全屏 K 线切换指标参数（`chart.js:942/950` 两个 `showToast` 调用点）触发 2 次调用，
  **无 `Maximum call stack size exceeded`**，`consoleErrors=[]`。
- `?v=`：`automation-trade.js 1→3`。

### 未修 / 已知限制（如实记录，不掩盖）
- **交易台使用自包含的 `.td-toast`**（不使用全局 `showToast`）：文件设计为独立于 `app.js`，
  代价是 toast 样式与全站不完全统一。如需统一，应改 `automation-trade.js` 调用全局 `showToast`。
- **`req_qty`「N 请求」标记当前无数据可显**：因为第 2 条的修复已经让**显式请求的数量不再被清零**
  （`qty === req_qty`），该标记成为纯防御性兜底；历史行 `req_qty` 迁移默认 0，我们**确实不知道**它们的请求数量，故不臆造。
- **`sector_returns.json` 的 `count` 是字典键数**（6），与其真实交付形态（周期表）不完全对应，已在响应中保留原样。
- **`/api/profile`、`/api/finance`、`/api/report`、`/api/consensus`、`/api/north-holding` 仍为 `ok:false + reason`**
  （「westock CLI 链路已废弃（已迁移 MCP），数据源未接入」）——需**用户信任 MCP 连接器**后才能接真实财务三表/股东结构。
- **MCP 连接器当前全部 `unavailable`**（通达信/同花顺 iFinD/同花顺快查/腾讯自选股），仅 Agent Mail 可用。

### 9. 事件循环阻塞治理（冒烟测试暴露的真实性能缺陷）
**问题**：`GET /api/intelligent/ak/macro` 冷启动实测 **15.3s**。根因两层：
① `get_macro_data()`（async）**直接调用**同步的 `_fetch_eastmoney_macro()`，而后者内部是阻塞的 `requests.get(timeout=5)`；
② 三项指标（PMI/GDP/CPI）**串行**抓取 → 三项全超时即 5s×3=15s。
期间**整个事件循环被占满**，SSE 推流与所有其它 API 一起卡 15s。
- **`server/ak_market.py`**：`_fetch_eastmoney_macro()` 改为 `ThreadPoolExecutor` **并发抓取**三项；
  `get_macro_data()` 改为 `await loop.run_in_executor(None, cls._fetch_eastmoney_macro)` **卸载出事件循环**。
- **实测**：冷启动 **15.29s → 1.88s**（缓存命中后 0.003s）；同时段 `/api/admin/system` 0.024s、`/api/automation/orders` 0.005s，**不再被拖慢**。
- 另外，`GET /api/admin/system` 由原先「`async def` 内阻塞 500ms 采样」降为 **0.033s**（见第 5 条）。

### 冒烟测试结论（全端点，2026-10-16 夜）
| 端点 | HTTP | 耗时 |
|---|---|---|
| `/api/admin/system` | 200 | 0.033s（修前含 500ms 阻塞采样） |
| `/api/admin/system-health` | 200 | 0.030s |
| `/api/market/north` | 200 | 0.004s（该次为缓存；冷取数受上游 AkShare 延迟支配，约 7.7s，已 `run_in_executor` 不阻塞） |
| `/api/automation/orders?limit=1` | 200 | 0.002s |
| `/api/market-overview` | 200 | 3.71s（外部取数） |
| `/api/intelligent/ak/macro` | 200 | 1.88s 冷 / 0.003s 热 |
| `/api/sector-ranking?limit=3` | 422 | 0.004s —— **属正确校验**（`limit` 下限为 5），非缺陷 |

`py_compile`（9 个 Python 文件）+ `node --check`（3 个 JS）全通过；本轮多次重启后 `logs/server.log` 近 3000 行内 **Traceback = 0**。

### 验证脚本（可复跑）
`artifacts/verify_admin_health.mjs`（后台五块渲染 + 轮询暂停）、`artifacts/verify_trade_desk.mjs`（交易台 + toast）、
`artifacts/verify_manual_reject.mjs`（被拒面板 + 关卡 reason）、`artifacts/verify_reqqty_ui.mjs`（请求数量呈现）。

## 2026-10-16（补）— 自动化中心「数据来源」审计 + 假数据/静默降级治理

> 用户反馈「自动化中心设计不完善，数据推送与买卖执行断缺；不要填补空数据，要实时对接」。
> 本轮从**数据侧**（不改买卖执行前端，eng-4 负责）逐字段溯源并**直接修复**。

### 修复
- **`server/routers/automation.py` `GET /positions`**：端点原先**丢弃**了虚拟盘账户已持有的 `initial_cash`（`paper.py _DEFAULT_CASH=1,000,000`）与 `trading_days`，导致前端只能硬编码「/5」。现补回这两个字段，并新增 `paper_days_required`（取自策略配置）与 `source:"paper_sim"` 自证字段。
- **`static/js/automation.js`**
  - **消除静默填默认值（用户红线）**：`save-monitor / save-email / save-risk / save-cost` 原以 `parseInt(...)||80`、`parseFloat(...)||10000`、`readNum(id, 默认)` 把**留空输入**悄悄替换成默认值写库。改为新增 `numField()`：留空/非法 → **不提交该字段**（服务端保持原值，`/config` 为部分合并语义），并在 toast 里列出「留空已保持原值：…」；连带删除失效的 `readNum()` 死函数。
  - **消除"假零"显示**：复盘页「最大回撤/闭合交易/周盈亏/权益周变化/当前权益/总收益」原用 `|| 0` 或 `fmtPct(null)`（`Number(null)=0` → 显示 `0.00%`）在无数据时**伪装成 0**，改为 `== null ? '--'`。
  - **接上此前从未渲染的字段**：`/positions` 的 `gateway/initial_cash/trading_days` → 新增「数据口径：本地虚拟盘（paper）模拟数据 ｜ 已累计交易日 N 天」自证条；订单 `source`（manual/signal）→ 网关列追加来源徽章。
  - `?v=`：`automation.js?v=5 → 6`（仅 `dashboard.html` 引用，跨页一致）。

### 审计结论
- 自动化中心全部端点均为**本地可信源**（KV/SQLite 持久化 + 腾讯实时日K回测），不伪造行情；但**多数端点缺 `source/degraded/stale/reason` 自证字段**（本批已补 `paper_sim`）。
- 未修：`server/trading/backtest.py:23` 与 `iterator.py:57` 的 `_INITIAL_CASH = 1_000_000.0`（回测/策略回撤的假定本金，硬编码且 UI 未标注）—— 属交易引擎口径，需与买卖执行一并决策（详见交付报告）。

## 2026-10-16 — 九模块规划落地（M2/M3/M4/M5/M6）+ 数据真实性收口 + 配色一致性 + P0 稳定性

> 本轮为「MASTER-PLAN-2026Q4」八大模块的集中交付，主线是把"看起来有数据"变成"数据可溯源、可复算"。
> 全部关键指标均经过**独立复算**或**可复现实测**验收（证据见各节）。

### 1. 数据真实性治理收口
- 四级降级链固化：① 真实直采 → ② 历史快照回放（标 `stale` + `stale_from`）→ ③ 腾讯通道聚合（标 `aggregated`）→ ④ 本地样本（标 `synthetic`）
- **删除推算公式**：板块 `net_flow`（原按涨跌家数比推算）、5/20/60 日涨幅（原按当日涨幅×写死系数）；取不到一律 `null` + `reason`
- 龙虎榜钉住交易日（`sortColumns=TRADE_DATE,BILLBOARD_NET_AMT`）+ 陈旧护栏（`_EM_LHB_STALE_DAYS=10`），修复"2015 年数据伪装成实时"
- **"假零"治理**：盘前腾讯成交额 `amount`、`/api/market/north` 的 `total/hgt/sgt`（`disclosed=false` 时）由 `0.0` 改为 `null` + `reason` —— 0 会被前端当真实值渲染成"成交额 0"

### 2. M2 全球传导建模（`server/global_linkage.py`）
- 7 个外部因子（道指/纳指/纳指100/标普/VIX/恒生/恒生科技）日线领先滞后相关（lag 0..3）、开盘缺口传导（corr + 方向命中率）、每日影响打分卡
- 实测结论：美国因子 `lead=1`（隔夜效应）、港股 `lead=0`；样本 <30 → `insufficient_samples` 不给系数
- 打分卡 `composite = Σ分项`，拆出 `components.measured / heuristic`；`brent`（不作方向判断）、`dxy`（无实时行情）归 `notes` 不计分
- VIX 因日线停更（样本 21）改写 **heuristic** 分支（`rule_sign=-1`），**一旦历史样本足够 measured 自动优先**
- 验收：恒生 lag0 相关系数独立复算 0.508560（接口 0.50856，|ε|=2.21e-07）

### 3. M3 板块覆盖与市场金融属性关联（本轮最大交付）
- **覆盖 496 个东财行业板块**（`data/sector_map.json`，含 5657 只股票反向索引；`coverage{496/496}`），反查接口 `GET /api/sector-map/stock/{code}`
- **层级体系**：`level` 字段 414×0 / 39×2 / 43×3；`sector-ranking` **默认只在 `level=0`（414 大类）内排名**，`level=0|2|3|all` 可过滤；跨层级时响应带 `note` 提示不可直接比较
  - 依据：实测 **Ⅱ/Ⅲ 层级间存在 37 对成分股完全相同**（如 白酒Ⅲ=白酒Ⅱ，Jaccard=1.0）
- **全量取回 + 全量排序**：先取回全部板块，在选定层级全量上排序，再用 `limit` 截取（避免"前 50 名里的第一名"这种伪结论）
- **多周期真实收益**：`data/sector_periods.json`（离线构建，请求路径只读缓存，缺失 → `period_returns_cache_missing`）；公式 `d_n = close[-1]/close[-1-n] - 1`；单一口径 `eastmoney_kline`
  - 验收：独立复算 d5/d20/d60，**偏差 0.0000**
- **板块属性补全**：`pe / pb / market_cap / float_cap / turnover / member_count / lead_stock(_name/_chg)`，逐字段 `field_sources`；东财板块级 `f23`(PB) 恒为 `'-'` → `null` + reason，不填 0
- **金融属性敏感度** `GET /api/sector-linkage`：板块日收益 × 外部因子，n<30 降级
  - 验收：独立复算 lag1 纳指相关 0.306801（接口 0.306801，|ε|=4.47e-07）
- **关联图谱** `GET /api/sector-graph` + `GET /api/sector-clusters`：成分股重叠（Jaccard）+ 去市场因子残差相关聚类（168 簇）；边带 `same_lineage` 标记，**区分"父子板块天然高重叠"与"真实同类联动"**
- 新增离线构建：`scripts/build_sector_map.py`、`scripts/build_sector_periods.py`、`data/sector_returns.json`

### 4. M4 自动化交易中心
- **成交成本模型**：滑点（可配 bps）、佣金（含最低 5 元）、印花税（卖出 0.05%）、过户费；复盘统一 **净口径**（`pnl_basis=net`，拆 `pnl_gross`/`fees`）
- **真实约束**：涨跌停（优先用行情自带涨跌停价）、停牌、T+1 校验，拒绝时记录原因
- **`exit_reason` 四类**：`take_profit` / `trailing_stop` / `fixed_stop` / `signal`，供复盘归因
- **`is_drill` 演练标记**：演练造数订单自动打标并**默认排除出策略指标与迭代统计**（避免自造样本污染策略演进）
- **止盈默认值 = 0（关闭）**：能力保留，但不静默对所有策略注入出场规则
- 新增 `GET /api/review/round-trips?code=&include_drill=`（复用 FIFO 净口径配对，补 `open_ts`）

### 5. M5 大盘纵览面板
- 信息优先级六层：一行结论 → 三块体征（情绪/广度/资金面）→ 外部环境 → 指数与趋势 → 账户 → 次级分析
- **四级数据性质徽章**（真实 / 历史快照 / 聚合口径 / 本地样本）统一渲染 + 逐数字溯源（来源+时间）
- 外部环境区块消费 M2 `scorecard`（综合分 + 实测/经验规则拆分；样本不足项显示"不参与打分"，不显示数字）
- north 取数失败时沿用**同一交易日内**的成功快照（历史快照徽章 + 时间戳），**跨交易日一律不沿用**

### 6. M6 选股策略 K 线全屏展示
- 买卖点标注：`B` 买点落 bar 下方 / `S` 卖点落 bar 上方；交易时间戳截断对齐 bar；同一 bar 多笔垂直错开；**匹配不到 bar 的记录单列"未匹配 n 笔"**（不静默丢弃）
- 卖点悬浮显示**净盈亏**（红盈绿亏）与 `exit_reason`；演练单灰虚线圈 + 图例说明 + 「显示/隐藏演练单」开关（默认排除）
- 全屏状态栏：代码 / 名称 / 周期 / 数据源 / 数据时间 / 数据性质徽章；ESC 与 fallback 覆盖层均可退出，退出后指标状态不丢
- 数据源：`/api/review/round-trips` 为主、`/api/automation/orders` 为回退（回退时如实标注"净盈亏暂缺"）

### 7. 涨跌配色一致性（A 股惯例：红=涨/买/多/盈，绿=跌/卖/空/亏）
- **K 线图原本用"绿涨红跌"（欧美惯例）**，与全站其余部分相反 —— 两条渲染路径（ECharts 兼容 + pro 引擎）的蜡烛/边框/成交量/MACD/最新价全部翻转，**CDP 像素级验证**（涨 → `rgb(255,61,106)`，跌 → `rgb(0,233,106)`）
- 同类問題一并清理 20+ 处：主面板板块卡片、策略实验室环境标签、指数卡/涨跌表、涨跌分布图、板块热力图、5 日涨幅条、AI 决策（**买入原为绿、卖出原为红，与 `dashboard.css` 的 `.trade-side.buy` 直接冲突**）、板块热力图 treemap、涨跌比等
- **非方向性指标去红绿**：置信度、建议仓位改用中性色（cyan/gold/muted），避免被误读为涨跌
- 「上涨占比」从 `heat-*`（绿底）解耦为独立 `ratio-*` 类（高占比红系），修复"上涨占比 65% 却显示绿底"的误读
- 触发的连带修复：`app.js` `showToast ↔ enhancedToast` **无限递归**（买入/预警通知一弹即爆栈栈溢出）

### 8. P0 稳定性：老库迁移崩溃
- **现象**：服务 20 分钟内连挂 5 次（exit=1），`watchdog` 反复拉起
- **根因**：`server/trading/store.py` 的 `init()` 中 `executescript(_SCHEMA)` 在 `_migrate()` **之前**执行，而 `_SCHEMA` 内含引用迁移列（`is_drill`）的索引 → 老库上建表被跳过、索引语句失败 → 抛 `sqlite3.OperationalError: no such column: is_drill`
- **修复**：顺序改为 `_SCHEMA`（仅基列）→ `_migrate`（补列）→ `_ensure_indexes`（迁移列索引）；`init()` 全程持锁并在失败时关闭连接；`main.py` 为全部启动任务挂异常回调
- **验收**：构造"去掉 `is_drill`/`exit_reason`/`peak_price` 的老库副本"独立回归 —— 修前必抛异常，修后补列/索引/幂等/并发四项全通过，75 行数据无损

### 9. 其他修复与治理
- `/api/market/north` **NaN 崩溃**修复（`_safe_float` 增加 NaN/±inf 守卫，`int(nan)` 曾导致整个端点失败）
- 指数数据接入改走腾讯通道（AkShare 指数链路底层为东财 push2，已持续阻断）
- `static/js/bundle.js`（旧打包，含同款递归 bug、零引用）按"归档而非删除"迁至 `docs/90-archive/bundle.js.deprecated`
- **前端资源版本号统一 bump**：`chart.js?v=26`、`dashboard.css?v=62`、`ui-refinement.css?v=34`、`sentiment.css?v=3`、`sentiment.js?v=4`、`app.js?v=34`、`market-dashboard.js?v=19`、`market-data.js?v=12`、`strategies-ui.js?v=11`（不 bump 则缓存用户拿不到修复）

---

## 2026-10-14 — 文档体系重构（M9）+ 数据真实性治理文档同步

> 本节之后为历史条目。最新进展以在线文档站为准：
> `http://localhost:8900/project-docs.html`（内容源 `static/js/project-docs-data.js`）。

### 1. 文档信息架构重构（M9）
- `docs/` 由「98 个文件平铺」重构为编号目录：`00-strategy` / `10-architecture` / `20-prd` / `30-data` / `40-ops` / `90-archive` + `assets/`
- 归档而非删除：两份重复的 v4.0 报告合并为 `docs/90-archive/SYSTEM-OPTIMIZATION-v4.0.md`；`HANDOFF.md` 标注版本落后后归档为 `docs/90-archive/HANDOFF-v5.4.md`
- 76 张 PNG 截图统一收纳至 `docs/assets/`
- 项目根目录仅保留 `README.md` 与 `CHANGELOG.md`（其余 md 迁入 `docs/` 对应目录）
- 新增 `README.md`（项目定位 / 启动 / 目录导航 / 指向 MASTER-PLAN-2026Q4 的入口）
- 新增 `docs/30-data/DATA-DICTIONARY.md`（数据口径字典）与 `docs/40-ops/DEPLOYMENT.md`（部署运维）

### 2. 文档站新增 3 章节 + 排版重构
- 新增章节：`sec-strategy` 战略定位与发展目标、`sec-modules` 模块边界与验收标准、`sec-data` 数据口径字典（章节总数 12 → 15）
- 排版：分组树形导航（战略/架构/数据/过程/索引）+ 顶部面包屑 + 章节内小节锚点（38 个）+ 当前章节高亮 + 统一标题层级 + 长表格横向滚动 + 移动端单列
- 新增 `static/css/docs.css`（不改 `dashboard.css`）

### 3. 后端
- `server/main.py`：新增 `/docs` 静态挂载（**置于 catch-all `/` 之前**，避免被 `/` 吃掉导致 404）；`static` 与 `docs` 目录改为基于项目根的绝对路径（`Path(__file__).resolve().parent.parent`），修复 cwd 一变即 404 的历史缺陷

### 4. 数据真实性治理（同步自本轮改动）
- 兜底数据强制自证身份（`synthetic` / `stale` / `degraded`），修复「接口信封谎报来源」
- 龙虎榜陈旧护栏 `_EM_LHB_STALE_DAYS=10` + 多列排序，修复「2015 年数据伪装成实时」
- 四级降级链：真实 → 历史快照 → 腾讯聚合 → 本地样本（原子落盘 `os.replace`）
- 因子引擎去假（IC/IR/Q1~Q5/weights 真接线）、`quant_core.py` 回测链路复活
- 里程碑 / 问题清单同步写入文档站：`meta.uiVersion = dashboard.css v61 · ui-refinement.css v32 · intelligent-analysis.js v25 · market-data.js v11 · api.js v11 · app.js v32`

---

## 2026-06-03 — 价格过滤 + 智能扫描修复

### 1. 价格过滤 (5~40元区间)

**后端 (server/routers/scan.py):**
- 新增 `SCAN_PRICE_MIN` / `SCAN_PRICE_MAX` 全局变量
- `_phase1_tencent_screen()` 中：获取行情后滤除价格 < 5 或 > 40 的股票
- `/api/scan` 端点新增可选参数 `price_min` / `price_max`（默认 5/40）
- 调用 scan 时将参数写入全局变量

**前端 (static/js/app.js):**
- `appState` 新增 `priceMin: 5` / `priceMax: 40`
- `renderStockList()` 中新增价格区间过滤逻辑
- `renderScanResult()` 中新增价格过滤，更新统计文本
- `renderScanResults()` 中新增价格过滤
- `startScan()` 读取输入框值传给 API，轮询也使用相同price参数

### 2. 智能扫描面板升级

**HTML (static/dashboard.html):**
- 扫描面板新增价格区间输入控件（两个 input: min/max）
- 按钮文案改为 "⚡ 开始扫描"

**CSS (static/css/dashboard.css):**
- 新增完整扫描面板样式系统：
  - `.scan-panel` / `.scan-header` — 面板容器
  - `.scan-price-filter` / `.scan-price-input` / `.scan-filter-*` — 价格筛选控件
  - `.scan-btn` / `.scan-close-btn` — 按钮样式（含渐变、发光、hover、disabled）
  - `.scan-progress` / `.scan-progress-bar` / `.scan-progress-fill` — 进度条
  - `.scan-result-area` / `.scan-empty` / `.scan-loading` — 结果区域
  - `.scan-card` 系列 — 推荐股票卡片
  - `.scan-stats` / `.scan-stat-*` — 统计栏
- 新增响应式适配（小屏）

### 3. 其他改进
- 轮询超时从2分钟延长至3分钟
- bundle.js 重新打包

### 验证
- ✅ 后端扫描 API 价格过滤正确（返回数据全部在 5~40 区间）
- ✅ 前端 HTML 包含价格输入控件
- ✅ CSS 所有扫描面板样式就绪
- ✅ bundle.js 包含所有前端修改
- ✅ 服务重启成功

## 2025 — 自动化中心配色反向补漏（Explore-auto-front 勘察）

- `dashboard.css:3563-3564` `.data-table .col-chg-up绿/.col-chg-dn红` → 对调为 up=var(--red)/dn=var(--green)（涨红跌绿）。调用方 app.js:2031、market-data.js:976/1196-1197 语义即涨/跌，无需改 JS。
- `automation.js:1159` 单笔复盘"盈利/亏损"徽标误用状态语义类 `rv-pass(绿)/rv-fail(红)` → 新增方向类 `.rv-win{--red}/.rv-loss{--green}`（dashboard.css:5164-5166），与 `pnlClass` 盈红亏绿一致。`rv-pass/rv-fail` 的状态语义用法（达标/晋级/提案等）未动。
- 运行时验证（CDP）：col-chg-up=rgb(255,61,106)、col-chg-dn=rgb(0,233,106)、rv-win=红、rv-loss=绿；console 无报错。
- #86 运行时验证 5/5 通过：paper 自证条随 API（stub 注入 1234567/9 天 → DOM 跟随）、orders 来源徽标逐行一致、numField 留空不写默认、复盘无数据态全 `--` 无假零。
- 版本：dashboard.css v65（三页统一）、automation.js v7。
- 上报裁决：`.auto-row-failed` 整行红覆盖（状态语义，保留待裁）、SSE 三缺口（#89）、automation-trade.js 脱离设计系统（eng-4）。

## 2026-10-30（续）— 数据连线廊道分道（v61 §五十三）

- 同列对边的控制点横向扇形展开（预统计走廊边总数→按序偏移 min(9, 44/n)），消除枢纽列麻线；实测 105 边 → 78 个独立控制点。备份随 v60 链。

## 2026-10-30（续）— 看板通知中心接真实信号（v68 §六十一）

- 铃铛通知中心接入 /api/automation/signals（开机拉取+60s 轮询，评分映射类型、refId 去重、40 条封顶）；实测 8 条真实信号入列。app.js v35。
- 待办 P1：对比面板加购核验、情绪体征面板降级文案。

## 2026-10-30（续）— 缓存与数据接入硬化（v69 §六十二）

- 情绪状态条失败保留旧值（"缓存"标注，不翻红）+ 轮询 120s；服务端缓存 82.7% 命中率基线复核。

## 2026-10-30（续）— 铃铛/对比可见性修复（v70 §六十三）

- 铃铛提亮+暖金描边；对比按钮 👍→⇄ 并套用按钮样式；通知列表 10 条真实信号实测；备份 bak-v70。
