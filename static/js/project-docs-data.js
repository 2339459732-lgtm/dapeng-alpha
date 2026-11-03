/**
 * DAPENG Alpha · 项目文档数据源
 * ===========================================================================
 * 本文件是「项目文档页」唯一的可维护内容入口。
 *
 * 设计约定：
 *   1. 页面（static/project-docs.html）只负责渲染骨架，所有文案 / 数据全部来自本文件；
 *   2. 后续更新文档只需改本文件，不必触碰 HTML；
 *   3. 所有事实均来自项目内真实源文件（见 meta.sources），不杜撰版本号 / 日期 / 指标；
 *   4. 源文件中查无实据的内容一律显式标注「待补充」，不用猜测值填充。
 *
 * 挂载点：window.DAPENG_DOCS
 * ===========================================================================
 */
(function (global) {
  'use strict';

  var DAPENG_DOCS = {

    /* =====================================================================
     * meta —— 页面元信息
     * ===================================================================*/
    meta: {
      project: 'DAPENG Alpha 策略选股量化系统',
      version: 'v5.9+（后端 / 功能基线）',
      uiVersion: 'decision-desk v23 · docs-data v36 · board-review v1 · decision-routing v1 · gapfill v1',
      updatedAt: '2026-11-02',
      updatedNote: '2026-11-02 主脑收口：大盘纵览 / 动态市场调查 / 决策台三个并列工作区合并为「决策台 · 主脑中心」三层（事实层 → 判断层 → 决策层），工作区导航 5 → 3，hash 同步 #fact / #survey / #decision；情绪口径统一到 sentiment-flow.v1（同屏不再出现 83 与 80 两个分数），结构分与情绪分不再混称。新增 brain-config.v1（容差 / 分档 / 时点 / 板块体系集中配置，mtime 热重载 + 校验回落）与 layer-crosscheck.v1（九项对证，状态分 pass / warn / conflict / incomparable / unavailable，异量纲与异口径显式声明且不参与打分），决策层顶部常驻「三层对证」条，冲突时备忘录置信 ×0.85。已知边界：量能维待 20 日成交额样本、事件维为情报代理、资金流在东财频控期降级为注意力代理、板块级对证待东财↔申万映射表、调查层缓存 600s 会让情绪分轻微滞后（实测 Δ0.6 触发 warn）。逐版详情见 CHANGELOG 与 docs/10-architecture/LAYER-SYNC-CONTRACT.md、A-SHARE-REALTIME-DECISION-DESK.md、LAYER-CROSSCHECK.md、DECISION-HUB.md，如与代码不一致，以代码为准。2026-11-02 层间契约（layer-sync.v1）：把三层同步的内容范围/字段规范/更新频率/传递方式写死成契约；事实层补齐北交所直连源（363 只，全市场 5782 只含京）并修正 920 段代码前缀（此前同一只票被记成 sh920xxx 与 bj920xxx）、涨跌停阈值按板块分支（北交所 30% 含 ST、双创 ST 仍 20%）；新增 lineage.v1（8 节点链路、字段指纹、trace 一致性校验、GET /api/brain/lineage/{trace_id} 回放）；闭环 pin_packet 钉住快照，解决一轮决策跨两三代 trace 的问题。2026-11-02 架构收口（decision-desk-arch.v1）：三层职责边界与数据契约成文，总线 publish 统一记账、survey 补上向总线报到（能力图 ok 4/15 → 13/15），配置支持预检/留痕/回滚（scripts/brain_config_admin.py + GET /api/brain/config），延迟预算可判（scripts/verify/perf_budget.py，热态 p95 16–32ms、8 并发 276 req/s 零错误），验收标准 A1–A8 各带命令与阈值',
      port: 8900,
      entryUrl: 'http://localhost:8900/dashboard.html',
      docsUrl: 'http://localhost:8900/project-docs.html',
      repoPath: 'dapeng-water/',
      tagline: '面向个人投资者的 A 股量化决策辅助工作台',
      disclaimer: '个人量化工具，不构成投资建议（系统内已有同款免责声明）',
      sources: [
        'README.md',
        'CHANGELOG.md',
        'docs/00-strategy/MASTER-PLAN-2026Q4.md',
        'docs/00-strategy/DECISION-CLOSED-LOOP-PLAN-2026Q4.md',
        'docs/00-strategy/product-roadmap-2026.md',
        'docs/00-strategy/capability-evolution-plan-2026.md',
        'docs/00-strategy/OPEN_SOURCE_INTEGRATION_PLAN.md',
        'docs/10-architecture/auto-trading-architecture.md',
        'docs/10-architecture/system_design.md',
        'docs/10-architecture/DECISION-HUB.md',
        'docs/10-architecture/LAYER-CROSSCHECK.md',
        'docs/10-architecture/SENTIMENT-SURVEY-BRAIN.md',
        'docs/20-prd/auto-trading-prd.md',
        'docs/30-data/DATA-DICTIONARY.md',
        'docs/40-ops/DEPLOYMENT.md',
        'docs/40-ops/CHANGELOG-2026-10-22.md',
        'docs/90-archive/*（历史归档）'
      ]
    },

    /* =====================================================================
     * strategy —— 0. 战略定位与发展目标
     * 取自 docs/00-strategy/MASTER-PLAN-2026Q4.md 第 0 章
     * ===================================================================*/
    strategy: {
      title: '战略定位与发展目标',
      intro: '本系统是什么、为什么存在、衡量标准是什么、以及明确不做什么 —— 这是所有模块取舍与验收的总纲。',
      oneLine: 'DAPENG Alpha 是一套面向 A 股的「可解释决策辅助系统」—— 它把行情、板块、资金、全球市场、因子与策略信号收敛成可追溯的决策依据，再由自动化交易中心在严格风控下执行，并用复盘闭环反哺策略。',
      oneLineNote: '关键定语是「辅助」与「可解释」。系统的价值不在于预测明天涨什么，而在于让每一个买卖动作都有据可查、有错可溯、有据可改。',
      valueHeaders: ['层次', '用户问题', '系统回答'],
      valueProps: [
        ['看得清', '现在市场什么状态？', '大盘纵览 / 板块轮动 / 全球联动'],
        ['想得明', '为什么是这些票？', '因子分解 + 策略信号 + 缠论结构 + AI 主脑归因'],
        ['做得稳', '该不该动手？怎么控风险？', '九关风控 + 自动化执行 + 复盘归因']
      ],
      northStarHeaders: ['#', '指标', '定义', '来源'],
      northStarItems: [
        ['1', '可回溯率', '每笔交易都能追到信号 ID 与策略 ID', 'orders.signal_id / trade_reviews.signal_id 已具备'],
        ['2', '决策胜率', '闭合交易盈利笔数占比', 'review.py:166'],
        ['3', '盈亏比', '平均盈利 / 平均亏损', 'review.py:166'],
        ['4', '最大回撤', '权益曲线最大回撤', 'review.py:166'],
        ['5', '夏普比', '日收益均值 / 标准差 × √252', 'review.py:166'],
        ['6', '出场效率', '实际出场价相对 MFE 的捕获率', 'review.py:166'],
        ['7', '数据真实度（新增）', '前端展示数据中「实测来源」占比，禁止推算值冒充实测值', '本规划第一红线，见第 4 章']
      ],
      northStarNote: '前 6 项沿用 server/routers/review.py:166 已实现的指标；第 7 项为本规划新增，是「数据真实性治理」的量化抓手。',
      boundaries: [
        '不做投资建议与收益承诺 —— 所有输出必须带 disclaimer（strategy_lab.py 已有先例）',
        '不做荐股营销、不做社区、不做付费内容',
        '不接真实券商通道上线，除非用户显式配置且通过实盘风控准入（risk.py:342 live_switch，默认关闭）',
        '不做高频 / 日内 tick 级策略 —— 当前架构（scheduler.py 交易时段判定 + 分钟级扫描）定位在日频到分钟级'
      ],
      evolutionLadder: [
        { phase: '2026 Q4', theme: '可信化', desc: '数据真实度、指标正确性、模块边界清晰' },
        { phase: '2027 H1', theme: '可解释', desc: '因子归因、信号溯源、AI 主脑个性化' },
        { phase: '2027 H2', theme: '可执行', desc: '条件单 / 止盈止损、成本建模、真实成交语义' },
        { phase: '2028', theme: '可进化', desc: '策略自动迭代闭环、跨市场联动建模' }
      ]
    },

    /* =====================================================================
     * synergyPlan —— 整体规划 · 动态调查 / 主脑专家团 / 情绪追踪闭环
     * 现状依据：market_survey_engine.py、brain.py、finance_agent.py、
     *          decision-desk.js、static/sentiment.js
     * 目标设计：共享快照 → 三系统互相驱动 → 复盘校准
     * ===================================================================*/
    synergyPlan: {
      title: '整体规划 · 三系统闭环',
      kicker: 'DECISION CLOSED LOOP · 2026 Q4',
      intro: '把动态市场调查（感知）、主脑专家研判（判断）、市场情绪追踪（体温）接成一条可审计的决策链：同一份市场快照驱动三方，三方输出再汇入决策台，交易结果回流校准下一轮判断。',
      statusNote: 'P0 已交付（2026-11-02）：共享 MarketPacket / sentiment.v1 / 六专家规则 v1 / 全市场 A 股覆盖 / 全球指数与行业代理 / 19 项全球宏观均已接入并实测。当前六专家为规则引擎独立研判 + 主脑置信度聚合；匿名互评、反方质询、专家权重校准和新闻情绪分类列入 P1，不以规划冒充实测。',
      principles: [
        { name: '单一事实源', text: '一次决策周期只冻结一份 MarketPacket；调查、情绪、主脑、决策台共用同一 trace_id、as_of、coverage 和 quality，禁止各自拼接不同时间戳的数据。' },
        { name: '证据先于观点', text: '每个判断必须绑定指标值、来源、样本量、更新时间与失效条件。没有证据的观点可以保留，但不能进入加权分。' },
        { name: '风控优先级最高', text: '风险否决高于多数意见，数据质量门槛高于模型自信。冲突无法收敛时，默认降低仓位或输出观察，不强行制造共识。' },
        { name: '诚实降级', text: '缺数据返回 null + reason，按可用权重重新归一；覆盖不足 70% 不输出可执行结论。历史快照、聚合口径、规则推断必须显式标记。' },
        { name: '闭环可学习', text: '每次研判保存专家原始意见、分歧、最终决定和结果。T+1 / T+3 / T+5 回看后才允许调整专家权重与情绪权重。' }
      ],
      loopNodes: [
        { code: '00', name: '统一市场快照', role: '数据底座', text: '多源适配、质量门禁、trace_id、coverage。下游只读快照，不重复抓源。', io: '数据源 → MarketPacket' },
        { code: '01', name: '动态市场调查', role: '感知层', text: '把行情、板块、资金、全球、情报、宏观整理为市场状态、轮动、异动与证据。', io: 'MarketPacket → SurveySnapshot' },
        { code: '02', name: '市场情绪追踪', role: '体温层', text: '把宽度、动量、涨跌停、量能、全球与事件压缩成可复算的情绪分与风险背离。', io: 'MarketPacket → SentimentSnapshot' },
        { code: '03', name: '主脑专家团', role: '研判层', text: '六位专家独立判断并由主脑按证据与置信度聚合；匿名互评与质询为 P1。', io: '两份快照 + 记忆 → BrainDecision' },
        { code: '04', name: '决策台', role: '执行闸', text: '将结论翻译成仓位上限、方向、触发条件、止损与不行动条件，并保留全部异议。', io: 'BrainDecision + 风控 → DecisionMemo' },
        { code: '05', name: '复盘与校准', role: '反馈层', text: '用真实结果检验判断，更新专家、情绪与调查阈值；失败案例进入经验记忆。', io: '成交与结果 → CalibrationUpdate' }
      ],
      deliveryHeaders: ['能力', '版本 / 接口', '状态', '验证结果 / 说明'],
      deliveryRows: [
        ['共享 MarketPacket', 'market-packet.v1 · GET /api/decision/snapshot', '已交付', 'trace_id / as_of / coverage / inputs_as_of；全市场覆盖与核心样本双时间戳'],
        ['全 A 股覆盖', 'market-coverage.v1 · GET /api/decision/coverage', '已交付（partial）', '5581 只有效报价（91.1%）、5759 条季度基本面（94.1%）、PE/PB 中位与行业聚合可用'],
        ['sentiment.v1', 'GET /api/decision/sentiment', '已交付', '六维计算、缺失重归一、历史落盘；量能维需积累 20 个交易日'],
        ['六专家研判', 'brain-panel.v1 · GET /api/decision/experts', '已交付（规则）', '6 份意见、C/D、仓位上限、审计否决、异议保留；LLM 互评为 P1'],
        ['全球市场与宏观', 'GET /api/decision/coverage', '已交付', '17 指数 / 8 国家代理 / 11 行业代理 / 19 项全球宏观 + 中国 5 项'],
        ['决策台接入', 'decision-desk v13', '已交付', '17/17 数据源接通；专家、覆盖、情绪三块新面板；桌面与 390px 无溢出'],
        ['专家权重校准', 'T+1/3/5 结果回写', 'P1 待办', '需先积累有效决策结果；当前权重等权、专家校准系数固定 1.0']
      ],
      survey: {
        title: '一、动态市场调查',
        goal: '以可验证的公开实时链路覆盖全 A 股行情与季度基本面、全球主要指数/国家与行业代理、官方利率与年度宏观，把数据组织成可追溯、可比较、可驱动决策的市场事实包。',
        status: '2026-11-02 已接全 A 覆盖：调查优先读取后台 MarketPacket / 全市场规模与基本面；A 股有效报价 5581、最新季度基本面 5759 条、全球指数 17 / 国家代理 8 / 行业代理 11、全球宏观 19 项。调查结果写入 history 时新增 data_scope 与 coverage_pct；LLM 摘要失败时明确规则兜底，不伪装成 AI。',
        dataHeaders: ['数据域', '需要的数据 / 关键字段', '主要来源', '采集与更新频率', '当前落点 / 状态'],
        dataRows: [
          ['市场核心', '指数点位与涨跌、涨跌家数、涨停 / 跌停、成交额、中位涨跌、全市场覆盖', '新浪 hs_a；腾讯核心行情；东财 push2 降级', '全市场 5 分钟 / 核心 60 秒', '已接入 /api/decision/coverage + market-dashboard；调查 data_scope=full_a_share'],
          ['全市场基本面', 'PE、PB、总/流通市值、EPS、BPS、ROE、营收/净利同比、毛利率、行业', '新浪 Market_Center + 东财 RPT_LICO_FN_CPD', '估值随报价；季度财务缓存 6 小时', '已接入 /api/decision/coverage；PE 中位覆盖 4027，基本面 5759'],
          ['板块轮动', '板块均涨、上涨占比、成交额、领涨股、5 / 20 / 60 日收益、阶段', '东财行业板块 + 腾讯成分股聚合；本地 sector_periods.json', '5 至 15 分钟；盘后重算多周期', '已接入 /api/themes/overview 与 /api/sector-ranking'],
          ['资金结构', '指数资金流、板块资金、北向成分股涨跌结构', '东财 fflow；北向成分股接口', '5 至 15 分钟', '部分可用。北向净买额自 2024-08 停止披露，禁止用推算值代替'],
          ['全球传导', '外盘指数、VIX、美元、商品、8 个国家代理 ETF、11 个行业 ETF、领先滞后相关系数', '腾讯 / 新浪行情；global_linkage 滚动相关', '行情 60 秒；日终重估', '已接入 /api/global/assets、linkage 与 /api/decision/coverage'],
          ['新闻与情报', '财经要闻、事件标题、风险等级、风险摘要、影响渠道', 'AkShare 要闻；东财数据中心；情报 Agent + LLM', '15 至 30 分钟；盘后降为 60 分钟', '已接入 /api/news/market 与 /api/brain/intel'],
          ['宏观背景', '美债 3M/2Y/10Y、EFFR、SOFR、欧央行 DFR；美/中/日/欧元区 CPI、GDP、失业率；中国 PMI/CPI/GDP', 'US Treasury、NY Fed、ECB、World Bank、AkShare', '官方利率 30 分钟；年度指标按发布', '19 项全球宏观 + 中国 5 项；缺失 null + reason'],
          ['历史与记忆', '调查状态、情绪分、摘要、异动数量、专家研判、经验条目', '本地 JSON 档案 + trading.db KV', '每次调查追加；经验蒸馏每 6 小时', 'survey history 50 条；insights 30 条；lessons 20 条'],
          ['情绪序列', '情绪总分、六维贡献、覆盖度、风险旗标', '由市场快照与情绪引擎计算', '盘中 1 分钟采样、5 分钟发布', '已接入 /api/decision/sentiment；历史写 data/sentiment_history.json']
        ],
        frequencyHeaders: ['节拍', '频率 / 窗口', '执行内容', '理由'],
        frequencyRows: [
          ['L0 行情快照', '全市场 5 分钟；核心指数 60 秒', '全 A 报价、核心指数、宽度、涨跌停、成交额', '56 页全市场请求不适合 60 秒全量重拉；核心样本仍 60 秒'],
          ['L1 动态调查', '常规 15 分钟；压力态 5 分钟；闭市暂停', '生成 SurveySnapshot、异动、轮动与总结', '现状为 30 分钟 / 极端 15 分钟，逐步加密；闭市后仅保留 15:05 日结归档'],
          ['L2 情绪采样', '随核心快照计算；5 分钟发布', '计算六维分、总分、覆盖度、背离', '已实现 sentiment.v1；量能维需积累 20 个交易日'],
          ['L3 主脑专家团', '盘中 15 分钟；快照 trace 变化才计算', '六专家独立研判 + 主脑聚合', '规则 v1 已实现；同一 trace 5 分钟内复用，不重复计算'],
          ['L4 全球 / 情报', '全球 30 至 60 分钟；情报 30 分钟，盘后 60 分钟', '更新领先滞后、事件风险与外部环境', '变化慢于 A 股盘面，不需要分钟级轮询'],
          ['L5 日结校准', '15:05 后一次；每周一次汇总', '冻结当日最终快照、写复盘、更新经验', '让结果与当天决策一一对应，避免事后回填']
        ],
        triggers: [
          '情绪分相对上一快照变动 ≥ 10 分，或状态跨档（偏冷 / 中性 / 偏热互相切换）。',
          '上证或创业板日内涨跌绝对值 ≥ 1%，或涨跌停净差变化 ≥ 20 家。',
          '情报风险等级升至中 / 高，或来源从不可用恢复为可用。',
          '用户手动刷新；手动刷新永远生成新 trace_id，不复用旧结论。'
        ],
        sourceHeaders: ['优先级', '来源', '覆盖能力', '当前用途', '降级规则'],
        sourceRows: [
          ['P0', '腾讯行情', '指数与个股快照、K 线', '行情主链路', '失败 → 磁盘快照 → 明确标记 stale；禁止补零'],
          ['P0', '大盘聚合接口', '指数、宽度、板块、资金、风险标签', '调查与情绪的共同底座', '返回 ok:false + reason；下游保留上次成功快照'],
          ['P1', '新浪财经', '全市场列表、财经要闻', '宽度补充、新闻 fallback', '作为备用源；源名必须写入响应'],
          ['P1', '东财 push2 / datacenter', '行业板块、资金流、数据中心', '板块轮动与资金结构', '间歇可达；多节点失败时降级为聚合口径'],
          ['P1', 'AkShare', '中国宏观、要闻、部分指数', '中国 PMI/CPI/GDP 与新闻', '失败不影响主结论；对应维度权重重归一'],
          ['P0', '新浪 Market_Center', '全 A 股 5581 只报价 + PE/PB/市值/换手', '全市场覆盖与估值', '100 条/页并发；部分页失败保留已取记录并标记覆盖率'],
          ['P0', '东财 datacenter', '5759 条最新季度基本面（ROE/营收/净利/EPS/毛利率）', '全市场基本面', '按 SECURITY_CODE 稳定排序分页；失败返回旧缓存/partial'],
          ['P1', 'US Treasury / NY Fed / ECB', '美债 3M/2Y/10Y、EFFR、SOFR、欧央行 DFR', '全球利率与流动性', '官方公开接口；单源失败该组 null + reason'],
          ['P1', 'World Bank', '美/中/日/欧元区 CPI、GDP、失业率（年度）', '跨市场宏观背景', '年度发布滞后，period 必须展示；不冒充实时'],
          ['P2', '本地档案 / cache', '历史快照、板块多周期、经验、情绪序列', '降级回放与可复算', '必须带 stale / as_of，不得冒充实时']
        ],
        storage: [
          { layer: '热缓存', path: '进程内 cache + 前端 last-good', retention: '调查 600 秒；前端 30 秒', note: '只提升速度，不作为审计依据；命中时仍必须返回原始 as_of。' },
          { layer: '调查档案', path: 'data/market_survey_history.json', retention: '最近 50 条', note: '现状已落盘。目标补充 trace_id、输入哈希、质量与分项证据。' },
          { layer: '主脑记忆', path: 'data/trading.db · brain_insights / brain_lessons / brain_intel', retention: '洞察 30、经验 20、情报历史 5', note: '现状已使用 SQLite KV。目标新增 expert_opinions 与 decision_memos，保留原始意见和异议。' },
          { layer: '情绪序列', path: 'data/sentiment_history.json', retention: '最近 2000 条；daily 保留 400 日', note: '已交付 append-only；同 trace 去重、4 分钟采样，供量能分位与历史复算。' },
          { layer: '目标历史库', path: 'data/trading.db · market_snapshots / survey_runs', retention: '原始快照 90 天、5 分钟聚合 1 年、调查记录 1 年', note: 'P1 待建；现有覆盖快照落 data/market_coverage_latest.json，完整原始快照尚未入库。' }
        ],
        configHeaders: ['配置项', '目标默认', '作用', '变更约束'],
        configRows: [
          ['survey.base_interval_sec', '900 秒', '常规调查间隔', '上线初期保留 1800 秒，观察上游压力后再收紧'],
          ['survey.stress_interval_sec', '300 秒', '压力态调查间隔', '仅在触发条件成立时生效，最多连续 3 轮'],
          ['survey.cache_ttl_sec', '600 秒', '相同快照复用窗口', '不得大于基础间隔，避免读到过期结论'],
          ['quality.full_confidence_pct', '85%', '可输出完整结论的样本覆盖', '低于此值必须在 UI 显式降级'],
          ['quality.min_actionable_pct', '70%', '允许输出可执行建议的最低覆盖', '低于此值只能观察，不能给仓位建议'],
          ['sentiment.publish_interval_sec', '300 秒', '情绪分发布节奏', '原始采样可 60 秒，发布保持稳定节奏'],
          ['brain.max_rounds', '2', '专家交叉复核轮次', '超过 2 轮收益递减，禁止无限辩论'],
          ['retention.raw_days', '90 天', '原始快照保留', '后续历史越大，回测与校准越可靠']
        ],
        configNote: '目标配置入口为 data/decision_config.json（版本化、原子替换、保留 last-known-good），与 server/config.py 的静态数据源配置分离；前端 localStorage 只保存视图偏好，不保存采集频率或风控阈值。',
        qualityRules: [
          '所有快照必须包含 trace_id、as_of、generated_at、scope、coverage、source、quality；缺少任一字段不得进入下游。',
          '同一字段出现多源冲突时，按来源优先级 + 同一 as_of 取数；无法对齐则标记 conflict 并剔除该字段，不做平均。',
          '关键字段为 null 时按缺失处理并重归一权重；禁止用 0、50 或经验常数填坑。',
          '历史快照进入结论时必须显示 stale=true 与距今时长；超过 1 个交易日的快照不得参与当日执行建议。'
        ]
      },
      brain: {
        title: '二、主脑研判机制',
        goal: '把单一 LLM 的“一段总结”升级为六位专家独立研判、匿名互评、交叉质询、主脑汇总的可审计决策过程，既提高视角覆盖，也让分歧本身成为风险信号。',
        status: '2026-11-02 已交付六专家规则 v2（两轮互评）：第一轮 E1-E5 独立生成方向/证据/反证/失效条件/仓位上限，E6 审计覆盖与分歧并可否决；第二轮以「甲-己」匿名互评，按方向符号判定冲突（同为进攻仅强度不同算认同），反对票信心需超支持票 1.15 倍才迫使修正，修正后信心按证据差衰减；E6 再对未自我修正者逐条质询；主脑消费互评后意见聚合 C/D、仓位与一致率。仍待 P2：LLM 专家、历史权重校准。',
        expertHeaders: ['代号', '研判专家', '独立视角', '必须回答的问题', '否决 / 降级权'],
        experts: [
          { id: 'E1', name: '趋势与结构专家', lens: '指数趋势、均线结构、市场宽度、动量持续性', question: '当前是趋势延续、箱体震荡还是结构破坏？关键失效位在哪里？', veto: '无硬否决；可给出趋势失效条件' },
          { id: 'E2', name: '板块轮动专家', lens: '板块相对强弱、轮动阶段、领涨股质量、题材集中度', question: '主线是否可延续？是扩散、加速还是高位分歧？', veto: '无硬否决；可标记拥挤与退潮' },
          { id: 'E3', name: '资金与流动性专家', lens: '成交额、换手、资金流、北向结构、流动性折价', question: '上涨是否有量能承接？下跌是否出现流动性风险？', veto: '流动性硬否决：可把仓位上限压到 0 至 30%' },
          { id: 'E4', name: '全球与事件专家', lens: '外盘领先滞后、VIX / 美元、宏观、地缘与情报', question: '外部冲击是否足以改变 A 股方向？影响是脉冲还是持续？', veto: '重大事件硬否决：可要求暂停新增仓位' },
          { id: 'E5', name: '情绪与行为专家', lens: '情绪分、六维贡献、宽度背离、涨跌停极端、拥挤交易', question: '情绪处于修复、过热还是衰竭？是否出现价格与宽度背离？', veto: '极端情绪降级：可要求从进攻降为观察' },
          { id: 'E6', name: '反方与证据审计', lens: '反例、尾部风险、样本充分性、时间戳一致性、逻辑跳跃', question: '最强共识最可能错在哪里？哪些证据足以推翻它？', veto: '审计硬否决：数据不足或证据冲突时强制输出观察' }
        ],
        contractHeaders: ['输出字段', '类型 / 范围', '约束', '用途'],
        contractRows: [
          ['stance', 'attack / neutral / defend', '只能三选一；不得输出模糊情绪词', '方向加权与冲突距离'],
          ['horizon', 'intraday / 1-3d / 1-2w', '必须与证据时间尺度一致', '避免用日线证据指导分钟级交易'],
          ['confidence', '0 至 1', '必须给出形成理由；不得由主脑代填', '加权与最终置信度'],
          ['thesis', '不超过 120 字', '必须包含因果链，不得只复述数据', '决策摘要与审计'],
          ['evidence', '3 至 5 条 {metric, value, source, as_of}', '每条可下钻；禁止无源数字', '证据质量评分'],
          ['counter_evidence', '至少 1 条', '主动列出反例；无法给出则说明盲区', '防止单边叙事'],
          ['invalidation', '明确阈值或事件', '触发即撤销本轮观点', '盘中监控与复盘'],
          ['position_cap', '0 至 100%', '风控专家与审计专家可压低', '最终仓位上限'],
          ['questions', '0 至 3 条', '只问能改变结论的问题', '第二轮交叉质询']
        ],
        workflow: [
          { step: 'S0', name: '冻结快照', rule: 'SnapshotHub 生成 MarketPacket，校验覆盖、时间戳、来源与哈希；所有专家收到同一份只读快照，不允许自行补数。', output: 'frozen_packet + trace_id' },
          { step: 'S1', name: '独立研判', rule: '六位专家并行、互不可见。每位只读取本领域证据、公共市场摘要和自己的历史校准；先写结论，再列证据与反证。', output: '6 份 round=1 opinion' },
          { step: 'S2', name: '匿名互评', rule: '隐去身份，代号甲-己。冲突只在“方向不同”时成立——同为进攻而强度不同算认同，不算争点。反对票信心 > 支持票信心 × 1.15 才迫使修正；修正后方向向同行均值靠拢，信心按证据差衰减（上限 35%）；支持方不低于反对方则「坚持」并写入争议记录。已交付（无 LLM，规则确定性可复算）。', output: 'cross_review 记录 + 更新意见' },
          { step: 'S3', name: '交叉质询', rule: 'E6 对「有反证却未自我修正」的专家逐条质询，问题取自该专家自己列出的 counter_evidence（“若该反证成立，请下调仓位上限或给出失效位”）。已交付，最多输出 4 条。', output: 'challenge_log' },
          { step: 'S4', name: '主脑汇总', rule: '按证据质量、专家校准、置信度加权；先执行风控否决与数据门槛，再决定方向、仓位和失效条件。不得删除异议。', output: 'BrainDecision' },
          { step: 'S5', name: '发布与留痕', rule: '结果同时写入决策台与调查档案；包含专家意见、讨论轨迹、分歧指数、最终依据和“什么会改变结论”。', output: 'audit_record' },
          { step: 'S6', name: '结果校准', rule: 'T+1 / T+3 / T+5 回看方向与仓位是否正确；每 20 个有效样本更新一次专家校准，单次调整不超过 0.10。', output: 'calibration_update' }
        ],
        scoring: {
          title: '汇总公式与判定线',
          formula: '共识分 C = Σ(wᵢ · kᵢ · cᵢ · sᵢ) / Σ(wᵢ · kᵢ · cᵢ)',
          fields: [
            'sᵢ：专家方向，attack=+1、neutral=0、defend=-1。',
            'cᵢ：专家自评置信度（0 至 1）；主脑不得代填。',
            'kᵢ：专家历史校准系数（0.5 至 1.0），初始统一，按结果更新。',
            'wᵢ：角色基础权重，初始六人等权，后续按可验证表现调整。',
            'D：分歧指数，D = 1 - |C|；D 越高，结论越应轻仓或观察。',
            'Q：数据质量系数，由 coverage、时效、来源一致性决定，范围 0 至 1。'
          ],
          thresholds: [
            ['C ≥ +0.25 且无风控否决', '进攻', '仓位上限 = min(专家建议, 风控闸门)；证据不足则降为观察'],
            ['-0.25 < C < +0.25', '均衡 / 观察', '不追涨杀跌，给出触发条件；不为了给答案而选方向'],
            ['C ≤ -0.25 或风控否决', '防守', '仓位上限取专家最低值；重大事件可降为 0'],
            ['Q < 0.70 或关键数据缺失', '无结论', '只输出数据缺口与补数计划，不给可执行仓位'],
            ['D > 0.55 且双方置信度均 ≥ 0.70', '重大分歧', '标注“不建议执行”，等待下一次快照或明确裁决证据']
          ],
          confidenceFormula: '最终置信度 = 6 位专家平均置信度 × (1 - 0.4D) × Q；UI 同时显示方向分、分歧指数、数据质量，不只显示一个“看多 / 看空”。'
        },
        conflictHeaders: ['冲突类型', '典型场景', '处理规则', '输出表现'],
        conflictRows: [
          ['方向冲突', '趋势专家进攻，资金专家防守', '按校准权重计算 C；不得按人数简单多数；C 落入中性区则观察', '展示双方证据与 D；不隐藏少数意见'],
          ['风控否决', 'E3 判断流动性风险，E4 判断事件风险', '硬否决优先于加权分；仓位上限取最低值，主脑无权覆盖', '顶部红色“风控闸门”标识，附触发指标'],
          ['证据冲突', '同一指标两个来源数值 / 时间戳不一致', '按来源优先级与同一 as_of 对齐；无法对齐则剔除并标记 conflict', '数据质量扣分；进入 E6 审计意见'],
          ['数据不足', '覆盖低于 70% 或关键指数缺失', '相关专家必须弃权，其他权重归一；不得补 0 或补中性值', '输出“数据不足”，禁用进攻仓位'],
          ['锚定与从众', '第二轮意见无新增证据却集体改判', '改判必须引用新增证据或明确承认不确定性；否则保留原意见', '审计记录标记“无证据改判”，降低当轮可信度'],
          ['成本 / 收益冲突', '短线机会强但风险收益比不足', '仓位上限由风险专家与决策台共同约束；不以“可能涨”为由突破止损预算', '输出条件单式触发条件，不直接追价']
        ],
        decisionBasis: [
          '第一优先是事实完整性：快照覆盖、时效、来源一致性未过门槛时，任何专家都不得输出可执行结论。',
          '第二优先是风险约束：硬否决和仓位闸门先于方向判断，避免“看对方向却输在回撤”。',
          '第三优先是可验证证据：有来源、有时间、有样本的证据权重高于叙述性判断。',
          '第四优先是历史校准：长期校准好的专家权重更高，但单次结果不能立即大幅改权重。',
          '第五优先是保留异议：最终结论必须附带主要反方意见和失效条件，便于复盘判断是“判断错”还是“运气差”。'
        ],
        completionNote: '已交付 v2（2026-11-02）：共享快照 → 六专家独立研判 → 匿名两轮互评 → E6 定向质询 → 主脑汇总，全链路 method=rule_v2_deliberated，决策台展示流程条、逐人审阅结论、改判痕迹与质询清单。待办 P2：专家历史权重校准（kᵢ，每 20 个有效样本更新，单次 ≤0.10）、LLM 专家接入。无 LLM 时始终标记 rule 系列方法名，不冒充 AI 结论。'
      },
      sentiment: {
        title: '三、市场情绪追踪',
        goal: '用一套可复算、可解释、可降级的指标体系统一回答“市场现在偏冷还是偏热、由什么驱动、是否存在背离”，并作为调查与研判的量化输入。',
        status: '2026-11-02 已交付 sentiment.v1 后端：宽度/动量/涨跌停/量能/全球/事件六维，缺失维度重归一，coverage<70% 不输出总分；历史写入 data/sentiment_history.json。旧的两个情绪口径仍保留兼容，但决策台与专家团统一消费 v1。',
        scope: '采样范围为系统覆盖的沪深 A 股样本（不含 ETF / 港股）；指数、宽度、涨跌停、成交额来自同一快照；北向只使用已披露的成分股涨跌结构；新闻与事件情绪标注为启发式或 AI 辅助，不冒充实测。页面必须显示样本覆盖率，不能声称代表交易所全市场。',
        dimensionHeaders: ['维度', '采集指标', '量化方式（0 至 100）', '权重', '可用性'],
        dimensionRows: [
          ['宽度 B', '上涨 / 下跌 / 平盘家数、中位涨跌', 'B = 100 × up ÷ (up + down)；无有效家数则不计算', '30%', '已具备 /api/market-dashboard.breadth'],
          ['动量 M', '上证、深成、创业板平均涨跌；指数相对 MA20 位置', 'M = clamp(50 + 25 × 平均涨跌% + 0.3 × MA20 上方占比%, 0, 100)', '20%', '指数已具备；MA20 需从 48 点趋势补齐'],
          ['涨跌停 L', '涨停家数、跌停家数、连板与炸板（若可得）', 'L = clamp(50 + 40 × (涨停 - 跌停) ÷ (涨停 + 跌停 + 5), 0, 100)', '15%', '已具备；连板 / 炸板待补'],
          ['量能 V', '样本成交额、平均换手', 'V = 当日成交额在过去 20 个交易日的百分位 × 100；样本不足返回 null', '15%', '需新增历史序列；当前只有当日快照'],
          ['全球 G', '全球联动 composite、外盘风险偏好', 'G = clamp(50 + 12 × linkage_composite, 0, 100)', '10%', '已具备 /api/global/linkage'],
          ['事件 E', '情报风险分、新闻负面占比', 'E = clamp(50 - 0.25 × 情报风险分 - 50 × 负面新闻占比 + 50 × 正面新闻占比, 0, 100)', '10%', '规划：需新闻情绪分类；先用规则关键词，标注 heuristic']
        ],
        composite: {
          formula: 'S = 0.30B + 0.20M + 0.15L + 0.15V + 0.10G + 0.10E',
          missing: '缺失维度不补 50 分：按剩余可用权重重新归一，并输出 coverage = 可用权重之和；coverage < 70% 时总分显示为 null。',
          confidence: '置信度 = coverage × 数据质量 × 样本稳定度；分高 / 中 / 低三档，页面同时显示，不把低置信度分数当成强信号。',
          levels: [
            ['0 至 20', '极冷', '恐慌 / 流动性收缩', '绿'],
            ['20 至 40', '偏冷', '风险偏好低，等待修复', '浅绿'],
            ['40 至 60', '中性', '多空平衡，重结构轻方向', '灰 / 金'],
            ['60 至 80', '偏热', '风险偏好回升，注意拥挤', '橙红'],
            ['80 至 100', '过热', '情绪拥挤，防冲高回落', '红']
          ]
        },
        riskFlags: [
          '价格与宽度背离：指数上涨但上涨家数占比 < 40%，标记“指数强、个股弱”。',
          '量价背离：指数上涨但成交额处于近 20 日低分位，标记“无量上攻”。',
          '极端分歧：涨停与跌停同时高于近 20 日 80 分位，标记“结构性撕裂”。',
          '拥挤交易：前 3 板块成交额占比 HHI 显著抬升，标记“主线过度集中”。',
          '事件冲击：情报风险等级为中 / 高，情绪分虽高也强制降低可信度与仓位。'
        ],
        presentation: [
          { name: '顶部总览', text: '情绪总分仪表 + 状态档位 + 置信度 + 覆盖度 + as_of；一眼回答“冷热与可信度”。' },
          { name: '六维分解', text: '横向贡献条展示 B/M/L/V/G/E 每项得分与权重；点击任一项下钻到指标、来源和时间。' },
          { name: '历史轨迹', text: '1 日 / 5 日 / 20 日曲线叠加状态带；标出跨档、背离和事件冲击时点。' },
          { name: '结构体温', text: '涨跌家数阶梯、涨跌停温度计、板块热力与集中度；不把单一指标重复画成多张图。' },
          { name: '风险旗标', text: '背离、极端分歧、拥挤、事件风险为独立红色条带，不混入“看多 / 看空”叙述。' },
          { name: '证据抽屉', text: '每个分数附公式、输入值、来源、样本覆盖率、计算版本；支持从情绪页跳转到调查与决策台。' }
        ],
        frequencyNote: '原始指标 1 分钟采样，情绪分 5 分钟发布；盘中每次发布生成 sentiment_id，收盘 15:05 定格为当日最终值。历史值只追加不覆盖，保证可复算。',
        boundaryNote: '本系统情绪分不是投资者调查，也不是交易所全市场情绪；它是系统覆盖样本的风险偏好代理。任何页面必须同时展示口径、覆盖率和置信度，禁止用“全市场情绪”命名。'
      },
      integration: {
        title: '四、三者打通：数据流与功能衔接',
        intro: '闭环不是三个模块互相调用接口，而是共享同一份事实、各自只做擅长的事、把结果和结果检验写回同一本账。',
        ioHeaders: ['模块', '核心输入', '核心输出', '下游消费', '同步方式', '失败策略'],
        ioRows: [
          ['统一快照 SnapshotHub（v1 已交付）', '行情、板块、资金、全球、新闻、宏观、情报', 'MarketPacket：trace_id、as_of、coverage、quality、标准化字段', '调查、情绪、主脑、决策台', '60 秒刷新；同 trace 只读', '保留 last-good；关键字段缺失即降级，不让下游各自补数'],
          ['动态市场调查（已接全 A 覆盖）', 'MarketPacket + 全市场规模/基本面 + 宏观 + 新闻 + 情报', 'SurveySnapshot：regime、rotation、alerts、证据、synthesis', '情绪（基线）、主脑（事实材料）、决策台（摘要）', '常规 15 分钟 / 压力 5 分钟；事件触发', '部分维度缺失仍输出观察版；不生成虚构数字'],
          ['市场情绪追踪', 'MarketPacket + SurveySnapshot 的宽度 / 板块结构 + 全球联动 + 新闻', 'SentimentSnapshot：总分、六维、coverage、confidence、risk_flags', '主脑（第五专家输入）、决策台（仓位修正）、下一轮调查', '1 分钟原始 / 5 分钟发布', '缺失维度重归一；coverage < 70% 返回 null + reason'],
          ['主脑专家团', 'SurveySnapshot + SentimentSnapshot + 记忆 + 组合与风控约束', 'BrainDecision：六份意见、讨论轨迹、C / D、最终方向与仓位上限、异议', '决策台、复盘、经验记忆', '快照实质变化触发；最多两轮；缓存不可复用旧 trace', 'LLM 不可用 → 规则专家保底并标注；数据不足 → 审计否决'],
          ['决策台', 'BrainDecision + 当前持仓 / 风控闸门 + 用户计划', 'DecisionMemo：方向、仓位、触发条件、止损、不行动条件、证据链', '用户、模拟执行、复盘', '用户打开 / 手动刷新；60 秒同步状态', '结论缺失显示“无结论”，不默认中性或沿用昨日方向'],
          ['复盘与校准', '成交、持仓、DecisionMemo、市场后续走势', '结果标签、专家 Brier / 命中率、权重更新建议、失败经验', '主脑权重、情绪权重、调查阈值', 'T+1 / T+3 / T+5；每日 / 每周汇总', '样本不足不更新权重；只记录不学习']
        ],
        contractFields: [
          'trace_id：一次决策周期的唯一键，贯穿快照、调查、情绪、专家意见、决策与复盘。',
          'as_of / generated_at：数据观察时间与生成时间分离；禁止用生成时间伪装行情时间。',
          'coverage / quality：样本覆盖、缺失字段、降级级别；所有下游页面可见。',
          'source / stale / conflict：来源名、快照状态、字段冲突；禁止在 UI 中隐去。',
          'evidence[]：指标、数值、单位、来源、时间、样本量；判断必须能回到这条记录。',
          'version：数据契约版本、情绪公式版本、专家提示词版本，保证事后可复算。'
        ],
        dependencyHeaders: ['依赖关系', '类型', '说明', '断链影响', '处理'],
        dependencyRows: [
          ['SnapshotHub → 三系统', '硬依赖（P0）', '统一字段、时间戳、质量与 trace', '各模块口径分裂、无法复盘', '先建契约与质量门禁，再接 UI'],
          ['动态调查 → 情绪', '软依赖', '调查提供宽度、轮动与 regime；情绪也读取原始快照', '情绪仍可计算，但少结构解释', '缺调查时只用原始快照，标 degraded'],
          ['情绪 → 主脑', '软依赖', 'E5 读取情绪分与背离；不是唯一方向来源', '可退回价格 / 宽度规则判断', '情绪缺失时 E5 弃权，权重重归一'],
          ['调查 + 情绪 → 主脑', '硬依赖（决策前置）', '两份快照是六专家的事实输入', '没有完整输入则不输出进攻', '缺一即降级为观察或规则研判'],
          ['主脑 → 决策台', '硬依赖（执行前置）', '决策台不得自行编造方向', '只显示事实与数据缺口', 'BrainDecision 缺失时禁止下单'],
          ['决策台 → 执行', '硬依赖（风控前置）', '风控闸门与仓位上限先于用户操作', '无法生成可执行计划', '保持纸面模式，仅展示分析'],
          ['复盘 → 校准', '软依赖（学习层）', '需要至少 20 个有效闭合结果', '不影响当前决策，只影响权重', '样本不足时保持默认权重'],
          ['任一数据源 → 全局', '局部依赖', '单源失败不应阻塞全部', '指标降级或缺失', '保留 last-good、标记 stale、重归一权重']
        ],
        priorityHeaders: ['优先级', '升级项', '覆盖模块', '为什么先做', '完成判定'],
        priorityRows: [
          ['P0', '统一 MarketPacket + 质量门禁 + trace_id', '底座 / 调查 / 情绪 / 主脑 / 决策', '没有同源快照，三系统无法真正闭环，也无法复盘', '已交付：/api/decision/snapshot；一次周期内 trace_id 相同，缺失字段不补 0'],
          ['P0', 'sentiment.v1 公式 + 六维输入 + 覆盖率', '情绪 / 调查 / 决策', '解决当前多个情绪分并存、无法复算的问题', '已交付：/api/decision/sentiment + 6 项单元测试；缺失维度重归一'],
          ['P0', '六专家独立意见契约 + 主脑汇总 + 冲突处理', '主脑 / 决策台', '把“为什么”从一段文本拆成可审计证据与异议', '已交付规则版：6 份意见、C/D、风控闸门、失效条件；LLM 互评 P1'],
          ['P0', '三系统共享读模型 + 决策台证据链', '全链路', '让用户在一个界面看到数据、判断和风险来自哪里', '已交付：17/17 源接通，新专家/覆盖/情绪卡；断源保留 last-good'],
          ['P1', '匿名互评两轮 + 反方质询 + 讨论时间线', '主脑 / 决策台', '单轮独立意见缺少交叉验证，分歧价值未被利用', '重大分歧 D > 0.55 正确展示并禁止强行进攻'],
          ['P1', '情绪历史序列 + 量能分位 + 趋势图', '情绪', '没有历史就无法判断偏冷 / 偏热是否极端，也无法校准', '连续 20 日可重放；量能分位与手工复算一致'],
          ['P1', '专家校准与决策结果回写', '主脑 / 复盘', '让专家权重从“角色设定”变成“可验证能力”', '每 20 个有效样本才更新；单次权重变化 ≤ 0.10'],
          ['P2', '事件驱动调频 + 源恢复重算 + 告警', '调查 / 情绪', '分钟级跟踪只在真正变化时发生，控制成本', '触发、抑制、恢复均有审计日志；不重复调用 LLM'],
          ['P2', '新闻情绪分类 / 学习权重 / 多周期专家', '情绪 / 主脑', '提高解释力，但依赖长期样本与成本预算', '启发式与实测可区分；回测显示增益后才启用']
        ],
        acceptance: [
          '同一轮决策可在一次查询中还原：原始快照 → 调查 → 情绪 → 六专家意见 → 讨论 → 主脑结论 → 决策卡 → 复盘结果。',
          '任一数据源断开时，其余模块仍可显示最近成功快照和明确降级原因；不出现 0.00、50 分或“中性”冒充真实值。',
          '情绪分可从已存原始输入独立复算；专家方向可从证据表复核；最终仓位不超过风控闸门与专家最低仓位上限。',
          '所有结论带“什么会改变我的判断”；复盘能区分方向错误、执行偏差与数据缺失，而不是只记盈亏。'
        ],
        nonGoals: [
          '不做自动实盘下单；本规划只把结论送到决策台与纸面执行闸门。',
          '不追求“每次都给出方向”；没有优势时输出观察是合法且优先的结果。',
          '不把 LLM 当事实源；LLM 只能基于冻结快照和证据表组织语言，不能补数、改数或绕过风控。',
          '不引入新的高频行情供应商作为前置条件；先用现有可验证数据把口径和闭环做对。'
        ]
      },

      architecture: {
        title: '系统架构与数据治理',
        intro: '决策台定位为覆盖全 A 股的决策支持系统。本轮把「数据怎么流、谁来刷、什么时候失效、面板是否同一时点」固化成七层架构 + 一张资源依赖图 + 一条事件总线，避免分析能力增长后出现口径分裂与状态漂移。',
        layerHeaders: ['层级', '职责', '核心模块', '输出'],
        layerRows: [
          ['L0 采集层', '拉取并校验外部数据；缺失返回 null + reason，禁止推算', 'market_coverage（新浪全 A / 东财基本面 / 腾讯全球 / US Treasury·NY Fed·ECB / World Bank）', '原始报价、季度基本面、全球指数与宏观指标'],
          ['L1 派生层', '只消费已加载的全市场快照，派生结构指标，不新增重量级抓取', 'market_scanner（cross_section / rotation / money_flow）', '横截面结构、板块轮动、资金流向'],
          ['L2 快照层', '冻结同一时点的市场事实，作为跨模块单一事实源', 'decision_snapshot.get_market_packet', 'MarketPacket（trace_id / as_of / quality / evidence / chain）'],
          ['L3 情绪层', '六维情绪；缺失维度按剩余权重重归一，覆盖 < 70% 不出总分', 'decision_snapshot.build_sentiment_snapshot', 'sentiment.v1'],
          ['L4 研判层', '六专家独立研判 → 匿名互评 → 联合研判 → 命中账本', 'decision_experts', 'brain-panel.v3'],
          ['L5 呈现层', '结论优先的 17 卡决策台，SSE 实时同步与同快照标记', 'decision-desk.js / dashboard.html', '决策台界面'],
          ['L6 治理层', '资源依赖图、级联失效、事件推送、缓存容量治理与观测', 'data_bus / cache /路由观测端点', '/api/decision/bus · cache · system']
        ],
        moduleHeaders: ['模块', '职责边界', '输入', '输出', '触发方式'],
        moduleRows: [
          ['server/market_coverage.py（门面）', '全市场与全球数据采集、分级 TTL、后台 worker；只采不算。实现拆为 fetch / sources / macro / payload / worker 五片', '公开数据接口', 'coverage payload + 全市场行快照', '时间触发（交易 120s / 非交易 1800s）'],
          ['server/market_scanner.py', '横截面扫描、板块轮动、资金流向；只算不采', 'coverage 行快照 + sector_history.json', 'scan payload（含 chain 时延）', '请求触发（TTL 120s），随 coverage 失效'],
          ['server/decision_snapshot.py', '共享快照与情绪 v1；量能回填、风险旗标、口径自描述', 'dashboard / north / intl / coverage / scan', 'MarketPacket + sentiment.v1', '请求触发（packet 55s / sentiment 45s）'],
          ['server/decision_experts.py（门面）', '六专家意见、匿名两轮互评、联合研判、命中率账本；实现拆为 base / global / ledger / aggregate 四片', 'MarketPacket + sentiment + scan', 'brain-panel.v3（含 collaboration / calibration）', '请求触发（TTL 300s），随上游失效'],
          ['server/sentiment_tracker.py', '只做派生不算原始数据：变化 / 斜率 / z 分位 / 拐点 / 持续性 / 驱动归因', 'sentiment.v1 + 历史序列', 'sentiment-flow.v1（240 点 ring）', '请求触发（TTL 60s），随 sentiment 失效'],
          ['server/brain_capabilities.py', '能力契约：15 项能力（8 计算 + 7 决策）的输入/产出/退化/提升目标 + 备忘录合成', 'capability 声明（无 IO）', 'brain-capability.v1 / brain-memo.v1', '纯函数，被主脑与前端直接消费'],
          ['static/dashboard.html · #decision', '主脑中心唯一入口：事实层 / 判断层 / 决策层三段，同容器互斥切换', 'market-dashboard + sentiment(+flow) + survey + decision/*', '三层视图（#fact / #survey / #decision）', '切层即按需加载；hash 同步'],
          ['static/dashboard.html · 事实层', '指数 / 广度 / 资金 / 板块 / 情绪（六维+追踪）/ 三锚点 / 涨跌榜', 'market-dashboard + sentiment + sentiment-flow + 上证日线', '事实层视图', '进入事实层拉取（45s / SSE）'],
          ['static/sentiment.html', '兼容入口：3 秒跳转 dashboard.html#market（落到事实层），保留 ?sector=；不再渲染面板', '无（仅 URL 参数）', '跳转页', '打开即跳转'],
          ['static/js/market-dashboard.js', '大盘事实渲染 + loadSentimentState()（统一情绪口径与追踪）+ 三锚点', '四个 HTTP 接口', '三块体征 / 三锚点 / 板块 / 榜单', '视图激活 + 45s + 手动刷新'],
          ['server/data_bus.py', '资源注册、依赖图、级联失效、事件发布订阅、链路年龄', '各资源刷新事件', '事件流 + /api/decision/bus', '事件触发（发布即推送）'],
          ['server/cache.py', 'TTL + LRU + 字节双上限、后台回收、命中率统计', '键值读写', '缓存统计 /api/decision/cache', '后台 30s 回收 + 写入前腾位'],
          ['server/routers/decision.py', '统一决策 API 与 SSE；只做编排，不写业务规则', '上述模块', '11 个端点（新增 scan / bus / cache / system / stream）', '请求 / 订阅']
        ],
        triggerHeaders: ['触发类型', '触发条件', '作用范围', '节拍 / 闸门'],
        triggerRows: [
          ['时间触发', '后台 worker 到点', 'coverage 全量刷新', '交易时段 120s；非交易 1800s；失败退避'],
          ['事件触发（级联）', 'coverage 刷新成功', '失效 packet', '闸门 60s，避免高频覆盖刷新打穿快照'],
          ['事件触发（级联）', 'packet 重建完成', '失效 sentiment / scan', '闸门 40s，快照 TTL 55s 内不重复失效'],
          ['事件触发（级联）', 'sentiment 或 scan 刷新', '失效 experts', '闸门 180s，防止专家团（含互评与账本落盘）被重算风暴拖垮'],
          ['请求触发', 'HTTP 读取且缓存过期', '单资源按需重建', 'TTL 55 / 45 / 120 / 300s；single-flight 去重'],
          ['手动触发', '?refresh=1', '单资源强制刷新', '立即生效，但仍受上游退避约束'],
          ['订阅推送', '任意资源发布事件', 'SSE 客户端即时收到', '事件驱动 + 30s 注释心跳保活'],
          ['级联（新增）', 'sentiment 刷新', '失效 sentiment_flow', '闸门 30s：追踪只做派生，重算便宜'],
          ['级联（新增）', 'sentiment_flow 或 scan 刷新', '失效 survey', '闸门 180s：调查含四路采集与 LLM 汇总'],
          ['按需闭环', 'POST /api/brain/cycle', '情绪→调查→专家→备忘录', '同步链路，共享 trace_id；单步失败不打断整链'],
          ['分层切换', '决策台 · 事实/判断/决策', '切层按需加载对应数据', '不在当前层不轮询（事实 45s / 决策 60s / 判断按用户开关）']
        ],
        cacheHeaders: ['策略', '参数', '行为'],
        cacheRows: [
          ['双上限', '800 条 / 64MB', '任一超限：先回收过期条目，再按 LRU 淘汰，直到回到预算内'],
          ['单条拒写', '单条 > 64MB', '直接拒写并计入 oversize_rejected，避免一个异常大对象把整库清空'],
          ['分级 TTL', '45s ~ 300s（缓存）/ 1800s（worker 节拍）', 'packet 55s、sentiment 45s、scan 120s、coverage 与 experts 300s，按数据变化速度分层'],
          ['后台回收', '30s', '与写入解耦，夜间无写入时过期条目也会被清掉，不长期占容量'],
          ['历史文件保留', 'sector_history 40 日 / expert_ledger 120 日 / sentiment series 上限', '按交易日裁剪，写入采用临时文件 + 原子替换，避免半写损坏'],
          ['可观测', '/api/decision/cache', '字节占用与利用率、命中率、淘汰/回收/拒写计数、最大 5 条目']
        ],
        compat: [
          '新增字段一律追加，不改既有字段名与语义：旧前端忽略新字段仍可正常渲染（已用 17 卡页面验证）。',
          '端点只新增不删改：原有 7 个决策端点路径与返回结构保持不变，新增 /scan、/bus、/cache、/system、/stream。',
          '缓存公共接口 get / set / delete / clear / get_or_fetch 签名不变，新增参数均有默认值，存量调用无需改动。',
          '数据口径统一为全 A 股；核心样本仅作降级路径，必须在 payload 里用 data_scope / scope_label 显式标注。',
          '缺失一律 null + reason，禁止补 0 或补中性值；面板对缺失显示「--」并给出原因。',
          '模块拆分采用「门面 + 分片」：原路径只做再导出，所有既有 import 不变；但 monkeypatch 可变路径（如 EXPERT_LEDGER_PATH）必须打到实现分片，门面同名变量不再联动。',
          '响应信封只追加不删改：新增 code / schema_version 与响应头 X-Schema-Version，ok / data / message 形状不变，前端零改动。'
        ],
        impactHeaders: ['改动', '原因', '涉及模块', '影响范围', '验证'],
        impactRows: [
          ['全市场扫描', '决策台只有广度与指数，缺少横截面结构（分布/市值/换手）', 'market_scanner、decision-desk', '新增「全市场扫描」卡，卡片数 14 → 17', '单测 3 项 + 浏览器双视口'],
          ['板块轮动', '只有当日板块强弱，没有排名迁移与动量窗口', 'market_scanner、E2 专家、sector_history', '轮动信号 / 排名变化 / 动量窗口（1/20 日）', '两日合成数据单测'],
          ['资金流向', '资金面证据只靠指数资金流与北向代理，缺行业与个股口径', 'market_scanner、E3 专家', '东财实测通道 + 注意力代理（显式 proxy）+ 首日份额榜', '真实接口探测 + 代理回退单测'],
          ['专家协作 v3', '互评只解决「谁说服谁」，缺少联合结论与历史命中追踪', 'decision_experts', '联合研判（joint_thesis / 跨域确认）+ 命中账本 + 分歧否决终局复核', '决策测试 27 项'],
          ['全局数据总线', '各模块自管 TTL，上游刷新后下游最长滞后一个 TTL，面板时点不一致', 'data_bus、coverage worker、packet / sentiment / scan / experts', '资源依赖图、级联失效、事件推送、链路年龄', '/api/decision/bus + 事件驱动推流实测'],
          ['缓存容量治理', '只有条数上限（800），无法约束总内存；大对象可把缓存挤空', 'cache.py', '字节上限 64MB、单条拒写、字节统计与最大条目', '11 项总线/缓存单测'],
          ['SSE 实时同步', '固定 30s 轮询，刷新发生后要等一个周期才可见', 'routers/decision.py、decision-desk.js', '事件驱动推送 + 同快照/同窗口标记 + 轮询兜底', '浏览器实测 via=stream、零控制台错误'],
          ['模块瘦身（P2）', '10 个模块超 900 行，最大 1562 行；单文件混装结构体/计算/装配/出口，改动要翻 1500 行', 'strategies、indicators、routers/automation·admin·kline、qlib_factor_engine、stable_market_provider、market_coverage、fundamental、decision_experts', '>900 行模块 10 → 0（最大 694）；模块 131 → 170；路由仍 182 条且逐条一致', 'tools/split_module.py 机械拆分 + 路由快照 diff + 253 项测试'],
          ['契约统一（P3）', '成功/失败信封各写各的，失败无稳定错误码，前端只能按中文文案分支', 'contracts/envelope.py、automation_common、evolution、review、main 中间件', 'ok/fail/degraded_payload + ErrorCode + X-Schema-Version: 1.0 响应头；3 份重复实现收敛', 'tests/test_contracts.py 10 项（信封字段/状态码映射/OpenAPI 可生成）'],
          ['工程卫生（P4）', '根目录堆积 20+ 临时脚本与截图，交付物与调试产物混杂', 'scripts/verify、_archive、tools、docs', '根目录临时文件清零；ADR ×2 + REPO-HYGIENE + ci_guard 守卫入口', 'python scripts/ci_guard.py 三道闸门全 PASS'],
          ['情绪追踪（sentiment-flow.v1）', '情绪只有「此刻多少分」，无变化/拐点/持续性/驱动归因；历史只落盘不消费', 'sentiment_tracker、decision_snapshot、routers/decision', '变化/斜率/z 分位/拐点/持续性/驱动分解 + 240 点 ring + 2 端点', '15 项纯函数测试 + 实测 80.5 分/过热/20 采样/分位 85'],
          ['调查并入情绪（survey-pipeline.v1）', '调查与情绪是两套口径且从不交叉；板块数据三处各一份、关系不可见', 'market_survey_engine、data_bus', '三段流水线 + 结构-情绪背离预警 + sector_linkage 数据来源自描述', 'payload 五新键 + 背离检测单测'],
          ['主脑能力契约（brain-capability.v1）', '主脑只有调度：能算什么/不能算什么/退化怎么办/提升目标都散在代码里', 'brain_capabilities、brain、routers/brain', '15 能力 / 26 边 / ok·degraded·blocked + brain-memo.v1 + 按需闭环', '22 项契约测试 + 实测闭环「进攻·40%·0.58」'],
          ['浑天仪拖拽根治（v85）', '极点附近水平拖拽退化为绕视线自转；拖拽增量二次累加导致松手漂移；侧栏按钮区拖不动', 'plate-graph.html（cam/project3/拖拽/loop3D）', '第三环滚转 + 屏幕轴预旋 + 跳过惯性叠加 + 放开 button', '17/17 PASS：极点 22.9→80.1px、侧栏 3.3→64.0px、松手 0.00px'],
          ['三工作区合一（decision-hub.v1）', '大盘纵览 / 动态市场调查 / 决策台三个并列入口读同一份行情，卡片大段重复；两个脚本还重复导出 switchWorkspace（后者覆盖前者）', 'dashboard.html、market-dashboard.js、intelligent-analysis.js、decision-desk.js、decision-desk.css', '合并为决策台 · 主脑中心三层（事实/判断/决策）；工作区 5→3 入口；情绪摘要与六维分层维护；同屏两个情绪分归一', '20/20 PASS：三层互斥、80=80、卡片内跳层、#market→#fact、1600+390 零溢出'],
          ['大盘纵览 × 情绪页合并（market-merge.v1）', '两个页面各抓一批行情、各算一个情绪分；同一系统三种情绪口径；情绪页 11 张卡里 9 张重复', 'dashboard.html、market-dashboard.js、market-overview.css、sentiment.html、sentiment_tracker、app.js', '情绪页降级为深链跳转；大盘纵览按「结论→事实→状态→下钻」重组；情绪口径统一为 sentiment.v1；分档对齐 20/40/60/80；三锚点迁入；9 处入口统一', '17/17 PASS：页面分=接口分、六维 6/追踪 4/归因 3/锚点 3、1600+390 零溢出、旧入口保留参数']
        ]
      }
    },

    /* =====================================================================
     * modules —— 模块边界与验收标准
     * 取自 docs/00-strategy/MASTER-PLAN-2026Q4.md 第 2 章（M1-M9）
     * ===================================================================*/
    modules: {
      title: '模块边界与验收标准',
      intro: '每个模块的「目标 / 做什么 / 不做什么 / 验收标准」—— 边界清晰是"做到什么程度算完"的前提。',
      note: '以下内容摘自 docs/00-strategy/MASTER-PLAN-2026Q4.md 第 2 章。M1~M9 均已于 2026-10-16 回填验收结论；其中 **M4 为「部分交付」**（手动下单 / 撤单 / 条件单 / 订单状态机 / 决策质量评分仍在交付中）。',
      items: [
        {
          id: 'M1',
          name: '计算能力与核心策略指标',
          goal: '让"系统算出来的数"可信 —— 指标定义明确、口径一致、可复现、无前视偏差。',
          do: [
            '修复 quant_core.py 指标名绑定（_ta_sma/_ta_ema/_ta_macd/_ta_rsi/_ta_bb/_ta_atr），恢复回测链路',
            '建立指标口径注册表 server/indicators.py（名称/参数/公式/数据窗口/输出列名/单位）',
            '真正实现 qlib_factor_engine 声明的 6 个因子，或如实降级为 4 个并改名（禁止声明 6 实算 4）',
            '实算 IC/IR：IC = 因子值与未来 N 日收益的截面相关系数时间序列；IR = mean(IC)/std(IC)',
            '修掉前视偏差：改为逐期截面回测，每期只用当期可见数据选股',
            '参数全部接线：weights / selected_factor_ids / start_date、end_date 真实生效',
            'stock_pool 搜索从 O(N) 线性改为字典索引；quant_core 加行情缓存'
          ],
          notDo: '不引入 Qlib 完整框架、不引入 GPU、不做分钟级特征',
          accept: [
            'quant_core.make_stock_features 合成数据冒烟通过，8 个关键列非全 NaN',
            '因子回测返回的 IC/IR 可被独立复算（第三方能算出同一数）',
            '同一配置连跑两次结果完全一致（可复现）',
            'weights 改动能真实改变回测结果（Δ 非零）'
          ],
          status: 'verified',
          evidence: '核心指标链路复活（曾因指标名未绑定到模块级全局，导致整条 /api/quant/run 链路是死的）；冒烟测试 13 个关键列非全 NaN（sma_10=18.163、rsi_14=40.108、macd=-0.190、atr_14=0.374）；strategies.py 长度门槛越界修复。'
        },
        {
          id: 'M2',
          name: '市场指标分析与世界金融市场动态影响建模',
          goal: '从"罗列全球行情"升级为"量化外部市场对 A 股的传导关系"。',
          do: [
            '全球资产从 11 个扩到覆盖度可用规模（补纳指期货/费半 SOX/VIX/美债 10Y/美元指数 DXY/A50 期货/恒生科技/布伦特原油）',
            '领先滞后分析：计算外部市场相对 A 股的滞后相关系数（lag 0/1/2/3 交易日），输出领先性排序',
            '开盘缺口传导：隔夜美股/亚太 → 次日沪深 300 开盘缺口的统计关系（相关性 + 命中率）',
            '风险偏好通道：VIX / 美债 10Y / 美元指数 → A 股风险偏好代理',
            '影响打分卡：每日输出"外部环境利好/利空/中性"打分卡，每项附证据与权重',
            '传导关系每日滚动重估并落盘，前端展示历史稳定度'
          ],
          notDo: '不做宏观因子择时模型、不做汇率对冲计算',
          accept: [
            '每个传导结论都能下钻到"用了哪段数据、算出什么系数、样本量多少"',
            '相关系数滚动窗口可复算；样本不足时明确标注"样本不足"而非给数',
            '前端明确区分"实测相关"与"经验判断"'
          ],
          status: 'verified',
          evidence: '已落地 server/global_linkage.py（routers/global_market.py 接线为 GET /api/global/linkage）。外部因子 7 个：djia/ixic/ndx/spx/vix/hsi/hstech。领先滞后 lag 0..3；实测美国因子 lead=1（隔夜效应）、港股 lead=0；样本 <30 → insufficient_samples 不给系数。VIX 日线停更（样本 21）→ 历史相关不可得，改走 heuristic（evidence_type:"heuristic" + rule_sign），一旦历史样本足够 measured 自动优先。打分卡 composite=Σ分项，components.measured / components.heuristic 拆分；brent（不作方向判断）/ dxy（无实时行情）归 notes 不计分。独立复算：恒生 lag0 相关 = 0.508560（接口 0.50856，|ε| = 2.21e-07）。'
        },
        {
          id: 'M3',
          name: '板块覆盖范围与市场金融属性关联能力',
          goal: '板块不只是"今天涨了多少"，而是有属性、有关联、有资金真相的一张网。',
          do: [
            '立刻停止伪造（G5）：net_flow 与 5/20/60 日涨跌幅必须来自实测接口，推算值标 estimated:true 且不计入排序/结论',
            '板块属性补全：PE(TTM)、PB、总市值、换手率、成分股数、领涨股、行业分类（申万一级/二级）',
            '板块—个股映射表落盘（data/sector_map.json），支持反查"某股属于哪些板块"',
            '多周期真实收益：从日 K 聚合出 5/20/60 日板块收益，替代写死系数',
            '板块关联图谱：基于成分股重叠度 + 历史收益相关性构建板块间关联',
            '板块覆盖从 Top50 扩到全量申万一级 + 二级'
          ],
          notDo: '不做行业估值分位排名（可延后）、不做供应链上下游图谱（需外部数据，列入 P2）',
          accept: [
            '前端任意板块数据可点开看到 source + updated_at',
            '关闭所有推算逻辑后页面仍能正常展示（只是少几个字段），不出现假数',
            'sector_map.json 反查任意一只股票能返回其所属板块列表'
          ],
          status: 'verified',
          evidence: '覆盖东财行业板块 496 个（data/sector_map.json，含 5657 只股票反向索引，coverage{496/496}）；层级 level 414×0 / 39×2 / 43×3。sector-ranking：全量 496 取回、在选定层级全量排序、limit 仅控返回条数，响应带 total / universe_total / level。多周期收益由离线构建 data/sector_periods.json（公式 d_n = close[-1]/close[-1-n]-1，请求路径只读缓存，缺失→period_returns_cache_missing），口径统一 eastmoney_kline。属性补全 pe/pb/market_cap/float_cap/turnover/member_count/lead_stock(_name/_chg)，逐字段 field_sources，pb 板块级恒为 "-" → null + reason。新增 GET /api/sector-linkage（板块×外部因子相关，n<30 降级）、GET /api/sector-graph（重叠 Jaccard + 收益相关，边带 same_lineage）、GET /api/sector-clusters（168 簇）。证据：多周期收益独立复算 d5/d20/d60 偏差 0.0000；金融属性敏感度复算 |ε| = 4.47e-07；关联簇 168 个。'
        },
        {
          id: 'M4',
          name: '自动化交易中心：买卖执行 + 复盘 + 决策',
          goal: '从"能跑通的模拟盘"升级为"语义正确的模拟盘" —— 成交要像真的，风险要拦得住，决策要有账可算。',
          do: [
            '成交成本模型（G9）：滑点（可配 bps）、佣金、印花税（卖出 0.05%）、过户费；成交价改为"现价 ± 滑点"',
            '涨跌停/停牌校验：涨停买单、跌停卖单直接拒绝并记录原因',
            'T+1 约束：当日买入不可卖出',
            '止盈能力：补 take_profit_pct / 移动止盈，与现有止损对称',
            '手动下单/撤单：新增 POST /api/automation/orders、DELETE /api/automation/orders/{id}',
            '条件单：价格触发/时间触发两类，落库由 SignalMonitor 轮询触发',
            '订单状态机：补 partial / partial_canceled，为将来真实通道预留',
            '决策质量评分（G11）：按"信号强度 vs 实际结果"给每次决策打分并归因',
            '交割单导入：支持 CSV 导入历史成交',
            '买卖点 K 线标注：在 K 线上标出每个信号的买/卖点与盈亏'
          ],
          notDo: '不实现真实券商通道（保持 broker_adapter() 可插拔但默认空）；不做算法单/拆单',
          accept: [
            '同一笔模拟交易，手续费+印花税+滑点合计与手算一致',
            '涨停价买入被拒绝，日志含明确 reason',
            '当日买入的持仓调卖单被拒绝',
            '止盈单在价格触发时正确平仓（可用合成报价验证）',
            '导入一份交割单后，复盘页能看到对应闭合交易与归因'
          ],
          status: 'partial',
          pending: '手动下单（POST /api/automation/orders）、撤单（DELETE /api/automation/orders/{id}）、条件单（价格/时间触发）、订单状态机 partial / partial_canceled、决策质量评分 —— 交付中（2026-10-16 排期中），对应缺口 G10 / G11。',
          evidence: '成本模型 server/trading/costs.py 已接线（paper.py）并采用净口径 pnl_basis=net（滑点/佣金/印花税/过户费；手算一致：BUY total=10010.10、SELL net=9984.90）。exit_reason 四类：take_profit / trailing_stop / fixed_stop / signal。is_drill 演练标记（默认排除出统计与迭代）。新增 GET /api/review/round-trips。止盈默认值 = 0（关闭）—— 能力保留但不静默启用。'
        },
        {
          id: 'M5',
          name: '大盘纵览面板展示布局',
          goal: '一屏之内回答"今天市场什么状态、我该警惕什么"。',
          do: [
            '布局重构为信息优先级分层：一行结论 → 三块体征（情绪/广度/资金）→ 指数与趋势 → 账户',
            '补资金面区块（当前缺失）',
            '补外部环境入口（接 M2 的打分卡缩略）',
            '每个数字加 source + updated_at 悬浮说明',
            '移动端单列重排 + 关键数字字号放大'
          ],
          notDo: '（本模块为布局重构，边界为不改变既有数据接口语义）',
          accept: ['1600px 与 390px 两种宽度下无横向溢出、无文字截断；首屏 5 秒内完成首帧渲染'],
          status: 'verified',
          evidence: '信息优先级六层布局落地；四级数据性质徽章（真实 / 历史快照 / 聚合口径 / 本地样本）；外部环境区块消费 M2 的 scorecard；north 快照沿用规则为「仅同一交易日内沿用，跨日不沿用」。实测 390 / 1600 宽度均零横向溢出。'
        },
        {
          id: 'M6',
          name: '选股策略 K 线全屏展示图细节优化',
          goal: 'K 线全屏态的细节可用性与状态一致性。',
          do: [
            '全屏态补标的名称/代码/当前价/涨跌幅标题栏',
            '指标切换（MA/BOLL/成交量/副图）在全屏态一致且可切换',
            '时间周期切换（日/周/60 分）在全屏态可用',
            '十字光标信息窗排版优化（OHLC + 涨跌幅 + 成交量）',
            '接入 M4 的买卖点标注，全屏下显示历史信号与盈亏',
            '退出全屏（含 Esc、fallback 模式）状态一致性修复',
            '移动端全屏手势与安全区适配'
          ],
          notDo: '（保持 KLineChart v10 + ECharts 双引擎架构，不更换图表栈）',
          accept: ['桌面与移动端进入/退出全屏各 3 次，K 线不丢数据、不白屏、指标状态不串'],
          status: 'verified',
          evidence: '买卖点 overlay 落地：B 买下 / S 卖上、时间戳截断对齐 bar、同 bar 垂直错开、未匹配单列显示。演练单以灰虚线区分并带显示开关；状态栏可溯源；ESC 与 fallback 模式状态一致。'
        },
        {
          id: 'M7',
          name: 'AI 主脑面板个性化动态升级与规整展示',
          goal: '主脑模块可配置、可持久化、规整展示。',
          do: [
            '定义主脑模块注册表（id/名称/数据源接口/刷新频率/是否可关闭/默认顺序）',
            '模块开关 + 拖拽排序 + 折叠，配置持久化到 localStorage（dapeng_brain_modules），并提供"恢复默认"',
            '动态布局：无数据的模块自动折叠并提示原因（而非空白）',
            '规整展示：统一标题层级、卡片间距、颜色语义、数字等宽字体',
            '每个模块显示数据来源 + 更新时间 + 是否为实测',
            '布局预设：极简/标准/全量 三档一键切换'
          ],
          notDo: '（不引入第二套前端框架；沿用原生 JS + localStorage）',
          accept: [
            '关闭任一模块后刷新页面，配置仍生效',
            '拖拽排序后刷新仍保持',
            '恢复默认能一键回到出厂布局',
            '任一模块取数失败时显示明确原因，不静默空白'
          ],
          status: 'verified',
          evidence: 'BRAIN_MODULE_REGISTRY 单一事实来源（8 模块，后扩为 9：新增「沪深港通成分股涨跌分布」）；模块开关 / 拖拽排序 / 折叠持久化到 localStorage 键 dapeng_brain_modules；极简 / 标准 / 全量三档预设；四级数据性质徽章。'
        },
        {
          id: 'M8',
          name: '数据接入与因子回测的数据与设计动态化',
          goal: '参数不再是装饰，改一个参数结果就真的变。',
          do: [
            '后端暴露元数据接口 GET /api/intelligent/qlib/meta（可选因子/基准/时间区间上限）',
            '前端回测表单从元数据动态生成（因子多选/基准下拉/区间/TopK/调仓频率）',
            '参数真实生效：修 weights、selected_factor_ids、start_date/end_date（G4）',
            '回测结果补图表：净值曲线 + 回撤曲线 + IC 衰减图 + 分位收益柱状图',
            '回测任务化：长回测改异步任务（复用 quant.py 的 task/status/log SSE 模式）',
            '回测结果缓存并可按参数键复用',
            '数据接入实时化：关键接口加 refresh 主动刷新与前端倒计时'
          ],
          notDo: '（不引入外部回测框架；沿用现有 quant.py 任务模式）',
          accept: [
            '换一个因子，回测净值曲线必然变化（并给出差异说明）',
            '改 start_date 后区间真的变化',
            '回测任务的进度可通过 SSE 看到',
            '元数据接口描述的因子集合与实算集合完全一致（G2 的根治点）'
          ],
          status: 'verified',
          evidence: '因子库 6 个（3 个实算可用、3 个如实标 available:false + 原因）；IC / rank_ic / IR 真算（独立复算 max|ε| = 4.91e-07）；Q1~Q5 真实分位分组；参数真接线（改权重 IC 0.003526 → 0.011504）；/api/intelligent/qlib/meta 元数据与实算集合一致。'
        },
        {
          id: 'M9',
          name: '文档体系重构',
          goal: '从"98 个文件堆在 docs/ 里"变成"有信息架构的文档站 + 有秩序的文件树"。',
          do: [
            '建立目录规范：docs/00-strategy · 10-architecture · 20-prd · 30-data · 40-ops · 90-archive · assets',
            '归档而非删除：v4.0 两份重复报告移入 90-archive/ 并合并为一份',
            '根目录只保留 README.md 与 CHANGELOG.md，其余 md 迁入 docs 对应目录',
            '文档站新增章节：战略定位、模块边界与验收标准、数据口径字典',
            '文档站排版重构：树形导航 + 当前章节高亮 + 小节锚点 + 面包屑；统一标题层级与卡片间距；长表格横向滚动',
            '建立文档与代码同步机制：数据来自 project-docs-data.js，功能变更须更新 meta.uiVersion 与 updatedAt'
          ],
          notDo: '不删除任何原始内容（一律归档）',
          accept: [
            '根目录 md 文件数 ≤ 2',
            'docs/ 下每个 md 都能在文档站索引里被引用（无孤儿文档）',
            '文档站无死链、无过期版本号',
            '新增一个模块说明只需改 project-docs-data.js 一处'
          ],
          status: 'verified',
          evidence: 'docs/ 编号目录（00-strategy / 10-architecture / 20-prd / 30-data / 40-ops / 90-archive / assets）；根目录仅 README.md + CHANGELOG.md；76 张 PNG 归入 assets/；文档站 16 个导航项（2026-11-01 新增整体规划 · 三系统闭环）、deadAnchors=0、390/1600 零溢出；/docs 静态挂载（文档索引可点击打开）。'
        }
      ]
    },

    /* =====================================================================
     * dataDict —— 数据口径字典
     * 配套 docs/30-data/DATA-DICTIONARY.md
     * ===================================================================*/
    dataDict: {
      title: '数据口径字典',
      intro: '数据源清单、可用性与降级行为、字段单位与来源标注约定 —— 数据真实度是本项目第一红线。',
      sourceHeaders: ['通道', '端点 / 模块', '状态', '降级行为'],
      sources: [
        ['腾讯行情 / K 线', 'tencent_kline.py；/api/global、/api/kline、/api/quotes', '✅ 存活（实时主链路）', '磁盘快照 → 本地样本'],
        ['新浪财经', '/api/intelligent/ak/news', '✅ 存活（新闻 fallback）', 'source = "Sina Finance Roll Stream (fallback)"'],
        ['东方财富 push2', 'stable_market_provider.py（板块排行 / 热门股）', '⚠️ 间歇可达（ReadTimeout / RemoteDisconnected）', '多节点轮询 → 磁盘快照 → 本地样本'],
        ['东方财富 datacenter-web（HTTPS）', '龙虎榜', '✅ 可达（有抖动）', '陈旧护栏 + 磁盘快照'],
        ['东方财富 ulist.np（批量）', 'get_stock_fund_flow()、个股资金流', '✅ 可用', '失败返回 ok:false（不再用空数组掩盖）'],
        ['AkShare 指数', '/api/intelligent/ak/index', '❌ 不可用（ok=False，0 条）', '前端降级为不可用提示'],
        ['AkShare 全市场快照', '/api/intelligent/ak/spot', '❌ 不可用（ok=False，0 条）', '同上'],
        ['AkShare 北向', '/api/market/north', '⚠️ 接口可用但净买额自 2024-08 停止披露', 'disclosed:false → 呈现成分股涨跌分布'],
        ['新浪 Market_Center（全 A 股）', 'market_coverage.get_sina_universe()；/api/decision/coverage、/api/decision/universe', '✅ 存活（100 条/页并发，5581/6123 有效报价 · 91.1%）', '返回 null + quote_coverage 下降；不补 0，不缩样本冒充全市场'],
        ['东方财富 datacenter-web（季度基本面）', 'market_coverage.get_fundamentals()；RPT_LICO_FN_CPD', '✅ 存活（5759/6123 条 · 94.1%，PE 中位覆盖 4027 只）', '字段缺失返回 null；sortColumns=SECURITY_CODE 稳定分页'],
        ['US Treasury / NY Fed / ECB（官方利率）', 'market_coverage.get_macro_snapshot()：3M/2Y/10Y/30Y、EFFR/SOFR、DFR', '✅ 存活（7 项日频，2–7s）', '单源失败则该项 null + reason，其余照常'],
        ['World Bank（年度宏观）', 'market_coverage.get_macro_snapshot()：美/中/日/欧元区 CPI·GDP·失业率（12 项）', '✅ 存活（官方 API，年度口径）', '同比由日历一年前取值，缺年则 null'],
        ['腾讯全球 / 行业 ETF', 'market_coverage.get_global_snapshot()：17 指数 + 8 国家代理 + 11 行业代理', '✅ 存活（qt.gtimg.cn 批量）', '单标的失败置 null，不参与广度扣分']
      ,
        ['东财主力资金流', '1.push2 clist（行业 f62 + 个股排行）', '⚠️ 尽力通道：本机常被频控（连接重置），连续 2 次失败封锁 30 分钟', 'money_flow.mode=proxy → 成交份额迁移代理（显式标注，不冒充实测）']],
      sourceNote: '可达性本身是波动的。因此不能把"可达性"写进逻辑假设；且恢复可达是最危险的时刻 —— 真数据回来时若响应里还混着推算字段，绿标会让编造内容获得"实时"的可信外观。',
      unitHeaders: ['字段', '单位', '说明'],
      units: [
        ['price / open / high / low / close', '元（人民币）', 'A 股股价'],
        ['chg / chg_pct', '百分比数值', '如 1.85 表示 +1.85%'],
        ['amount', '视口径', '接口原始金额；前端按亿元换算并注明 amount_yi'],
        ['net_flow', '亿元', '资金净流入；取不到时返回 null，不推算'],
        ['turnover', '百分比数值', '换手率'],
        ['up_count / down_count / flat_count', '只', '涨跌平家数'],
        ['flow_5d / flow_20d / flow_60d', '百分比数值', '多周期涨跌幅，须来自日 K 聚合']
      ],
      conventions: [
        '实测优先：能用真实接口取到的，绝不用推算。',
        '降级必须可见：fallback 数据必须带 source（真实来源名）+ degraded:true；行级 synthetic:true（本地样本）/ stale:true + stale_from（历史快照）；前端显示醒目横幅。',
        '取不到就说取不到：返回 null + reason，前端显示"暂不可用"；不允许用假数填充，更不允许把假数标注成真实数据源。',
        '口径必须自描述：结论类接口输出 data_scope（full_a_share / core_sample）+ scope_label；全市场缓存未就绪时回退 447 只核心样本，必须打 scope_degraded 标记，不允许让核心样本百分比冒充全市场。',
        '旗标按强度分级：涨跌停两端同时活跃时，仅 min/max ≥ 0.5 判 high（结构撕裂），一侧主导降为 medium（分化加剧），避免普涨日被误判为高风险。',
        '单一事实源：同一 trace_id 的结论必须可复算；跨模块共享 MarketPacket，不允许各模块各自取数导致同日不同值。'
      ],
      degradeHeaders: ['级别', '触发条件', '返回内容', '行级标记'],
      degradeChain: [
        ['① 真实', '网络正常', '实测数据', 'synthetic:false · stale:false'],
        ['② 历史快照', '网络失败，有盘缓存', '最近一次真实数据', 'synthetic:false · stale:true · stale_from'],
        ['③ 腾讯聚合', '东财 push2 不可达，腾讯成分股可达', '腾讯成分股聚合的真实板块指标', 'synthetic:false · net_flow:null'],
        ['④ 本地样本', '网络失败且无盘缓存', '硬编码样本', 'synthetic:true']
      ],
      examples: [
        'server/global_assets.py → coverage={available:0,total:11} + degraded:true + price:null：降级如实上报，绝不填充。',
        'server/ak_market.py get_north_flow → 返回 disclosed 标记，如实说明"停止披露"。',
        'stable_market_provider.py 盘缓存降级链：成功即原子落盘（os.replace）→ 失败回放最近一次真实数据并标 stale → 连缓存都没有才用本地样本。'
      ]
    },

    /* =====================================================================
     * background —— A. 项目背景与目标
     * ===================================================================*/
    background: {
      title: '项目背景与目标',
      intro: '用数据与规则替代情绪化盯盘，把「选股 → 验证 → 决策 → 复盘」收敛到一个界面内完成。',
      positioning: '面向个人投资者的 A 股量化决策辅助工作台',
      why: [
        '盯盘时间碎片化：有本职工作，只能用业余时间看盘，行情窗口经常错过。',
        '决策凭盘感：缺少可复核的规则，买卖依据事后无法还原。',
        '缺少交易记录与复盘：赚亏都靠回忆，无法定位「哪一类交易在亏钱」。'
      ],
      audience: [
        {
          tag: '核心用户（当前即本人）',
          desc: '有本职工作、用业余时间做 A 股短线/波段的个人投资者。'
        },
        {
          tag: '次级用户（半年内可触达）',
          desc: '亲友圈与小型投资社群中「想用量化但不会写代码」的用户；需求为开箱即用、明确的买卖建议呈现与风险提示。'
        },
        {
          tag: '不服务的用户',
          desc: '高频/套利交易者（延迟与合规不匹配）、机构（功能深度不足）。'
        }
      ],
      orientation: {
        label: '产品取向',
        value: '可读性 > 信息密度 > 功能数量',
        note: '用户画像决定了产品取向，这也是界面持续优化的主线。'
      },
      differentiation: [
        '市面工具要么是纯行情软件（同花顺 / 东方财富），要么是重平台量化框架（vn.py / QMT）。',
        '本系统的生态位是「轻量级个人决策台」——不追求机构级执行，而是把「AI 研判 + 规则化风控 + 纸面验证」做成年内可信赖的个人驾驶舱。'
      ],
      coreValue: [
        {
          title: 'AI 研判的可解释化',
          desc: '不只给「买入」结论，而是同时给出置信度、入场区间、止损、双止盈位与仓位建议——把 AI 从「黑盒预测」变成「可核验的决策草稿」。'
        },
        {
          title: '规则先于直觉',
          desc: '所有自动交易必须先回测、过风控门控（gate_state 状态机）、纸面账户验证后才能进入实盘——这是系统最值得长期投入的信任基石。'
        },
        {
          title: '一屏闭环',
          desc: '选股、K 线、板块、AI、回测、自动化在同一仪表盘内，个人用户的「工具切换成本」趋近于零。'
        }
      ],
      exclusions: [
        '不追实盘盈利 —— 券商通道未接通且涉及合规，2026 年内实盘不在目标内。',
        '不追功能数量 —— 不能服务北极星指标的新功能一律缓建。',
        '不做多用户 / 商业化 —— 单机自部署边界不变，先对自己负责。'
      ]
    },

    /* =====================================================================
     * northStar —— B. 北极星目标
     * ===================================================================*/
    northStar: {
      title: '北极星目标',
      deadline: '2026-12-31 前达成',
      statement: '纸面交易账户在 90 天滚动窗口内持续跑赢沪深300，且每一笔交易 100% 可回溯。',
      quote: '先证明「规则+AI」能在纸面上稳定赚钱，再谈一切其他功能。',
      rationale: '规划此前只有路线没有终点。本节补上终点——所有排期、取舍、验收都以这一条为准绳。',
      metricHeaders: ['维度', '指标', '达标线', '数据来源（均已具备）'],
      metrics: [
        ['收益', '90 天纸面收益率 vs 沪深300', '≥ 基准 +3pct', 'paper_account + 指数行情'],
        ['风险', '最大回撤', '≤ 10% 且 ≤ 沪深300同期回撤', '净值曲线'],
        ['质量', '胜率 / 盈亏比', '≥ 55% / ≥ 1.5', 'signals + 成交流水'],
        ['过程', '交易可回溯率', '100%（每笔成交能查到当时策略信号+AI研判+风控快照）', '审计日志'],
        ['习惯', '周复盘报告', '每周自动生成，连续不缺周', '复盘系统（Phase 2）']
      ],
      sideEffects: [
        '复盘系统从「P1 之一」提为「关键路径」——没有它，北极星指标连仪表盘都没有。',
        '配置 DEEPSEEK_API_KEY 提为 P0 前置——未配置会导致 AI 决策卡（核心价值）实际不可用。',
        '风控 2.0 与回撤指标绑定——熔断/降仓规则的验收标准就是「90 天 maxDD ≤ 10%」。',
        '策略超市化与胜率/盈亏比绑定——回测对比矩阵的产出必须能解释「为什么这季度的胜率达标/未达标」。'
      ],
      implementationNote: '衡量指标已在 2026-10-07 由 /api/review/summary 真实产出（6 项指标 + pass/fail/na 判定），并在自动化中心「复盘」Tab 呈现为北极星目标卡。'
    },

    /* =====================================================================
     * techStack —— C. 技术选型（含「为什么这么选」）
     * ===================================================================*/
    techStack: {
      title: '技术选型',
      intro: '所有选型都服从两条纪律：稳定优先、零新重依赖。',
      layers: [
        {
          layer: '后端',
          icon: 'server',
          items: [
            {
              name: 'FastAPI + uvicorn',
              why: '原生 async/await，与 asyncio 后台任务（监控循环、闭环调度器）天然契合；自动生成 OpenAPI；零构建流程。',
              since: 'v3.0 起（2026-05）'
            },
            {
              name: 'Python 解释器自动探测（优先项目 .venv，回退系统 Python 3.11）',
              why: '启动脚本（scripts/server_watchdog.py、start_server.py、start.bat、start_silent.bat、start-dapeng.vbs）在启动时按候选列表逐个实跑验证（python -c "import fastapi, uvicorn"），自动选用第一个可用解释器，不再写死 venv 绝对路径。HANDOFF 与优化报告记载为 Python 3.13，属历史基线。',
              since: '2026-10-11 修复'
            },
            {
              name: 'Trade-off：不使用 pandas / akshare 进主链路',
              why: '团队决策 D4：回测器用纯 Python + numpy 实现，避免重依赖绑架依赖树与拖慢冷启动。开源库一律隔适配层引入。2026-10-13 补充：沪深港通资金流向（/api/market/north）在项目内无替代数据源，已通过 server/ak_market.py 的 AkShareDataHub.get_north_flow() 引入 akshare；为延续该纪律，akshare 仅在该 hub 内惰性导入（首次导入较慢，放线程池执行），不进入回测与主链路。',
              since: '2026-10-06 拍板'
            }
          ]
        },
        {
          layer: '前端',
          icon: 'layout',
          items: [
            {
              name: '原生 HTML + CSS + 原生 JS（无构建工具）',
              why: '稳定优先：保留现有架构，不直接迁入 React/Vue/Streamlit 等第二套前端框架，避免依赖膨胀与升级困难。',
              since: 'v3.0 起'
            },
            {
              name: '暗色玻璃态（Dark Glassmorphism）单页 Dashboard',
              why: '信息密度高、长时间盯盘友好；玻璃拟态在暗底上层次清晰。',
              since: '2026-08-04'
            },
            {
              name: '共享设计系统（common.css + shared.js + sse-client.js）',
              why: '三页（index / dashboard / admin）视觉与交互统一，避免各页各自为政。',
              since: '2026-08-04'
            }
          ]
        },
        {
          layer: '图表',
          icon: 'chart',
          items: [
            {
              name: 'ECharts 5.x（本地化 js/echarts.min.js）',
              why: '本地化引入，不依赖任何 CDN；作为全部可视化与 K 线的永久兜底引擎。',
              since: 'v3.0 起'
            },
            {
              name: 'KLineChart v10（金融专用引擎）',
              why: '专业金融图表的缩放/十字光标/副图体验更好；与 ECharts 组成双引擎，异常时自动回退 ECharts（proVisible=false → legacy 分支）。',
              since: '2026-10-06'
            }
          ]
        },
        {
          layer: '数据源',
          icon: 'database',
          items: [
            {
              name: '腾讯行情 / K 线 HTTP',
              why: '毫秒级响应，作为实时主链路；批量 1 次请求可取 200+ 股票。',
              since: 'v3.0 起'
            },
            {
              name: 'westock-data CLI（Node.js 脚本）',
              why: '覆盖龙虎榜、热门股、板块排行、资金流、研报、财务等腾讯自选股结构化数据；异步子进程封装，超时 25s + Semaphore(3) 并发控制。',
              since: 'v5.0 起'
            },
            {
              name: 'AkShare（P2 可选扩展数据源）',
              why: '补充财务、公告、股东、估值与板块历史数据；以 provider 适配层接入，失败自动回退现有源，绝不替换实时主链路。',
              since: '规划中（2026-09-29 立项）'
            }
          ]
        },
        {
          layer: 'AI / LLM',
          icon: 'spark',
          items: [
            {
              name: 'DeepSeek（deepseek-chat / V3）',
              why: '中文语境下研判质量与成本平衡最好；输出结构化决策卡（入场 / 止损 / 止盈 / 仓位 / 置信度），服务端一个环境变量即可解锁。',
              since: '2026-07-04'
            },
            {
              name: '规则模板兜底',
              why: 'LLM 无 Key 或调用失败时，市场调查总结 / 复盘叙述自动降级为规则模板，并标注 ai_powered 状态，避免页面出现空洞。',
              since: '2026-10-07'
            }
          ]
        },
        {
          layer: '实时通道',
          icon: 'bolt',
          items: [
            {
              name: 'SSE（FastAPI StreamingResponse + asyncio.Queue）',
              why: '替代 60s 轮询：5 通道（market / quotes / signals / scan / config）5s 推送；广播器单任务拉取、所有连接复用数据；零新依赖。',
              since: '2026-08-04'
            }
          ]
        },
        {
          layer: '缓存与存储',
          icon: 'archive',
          items: [
            {
              name: '进程内 TTLCache + get_or_fetch',
              why: '行情/板块数据按 TTL 复用，减少上游调用；缓存优先、后台刷新。',
              since: 'v3.0 起'
            },
            {
              name: 'SQLite data/trading.db（WAL，5 表）',
              why: '团队决策 D5：signals / orders / positions / config / audit_log 统一落 SQLite；单写者 + asyncio.Lock；WAL 兼顾读写并发。',
              since: '2026-10-06'
            },
            {
              name: 'runtime_config.json（运行时配置）',
              why: '配置持久化不污染 config.py：null 值 fallback 到代码默认，原子写入，admin 页面可视化编辑。',
              since: '2026-08-04'
            }
          ]
        },
        {
          layer: '部署与运维',
          icon: 'shield',
          items: [
            {
              name: '单机自部署 + 看门狗自启',
              why: 'scripts/server_watchdog.py 守护 uvicorn（崩溃退避重启 5s→60s 封顶、稳定 120s 重置），start-dapeng.vbs 以 pythonw 无窗体独立启动并放入 Startup；stop.bat 特征匹配 + netstat 反查兜底。',
              since: '2026-10-07'
            },
            {
              name: '静态资源版本号约定 ?v=N',
              why: 'HTML 用 no-store、JS/CSS 用 no-cache，改动后必须递增 ?v=N，否则浏览器会继续用旧代码。',
              since: 'v3.0 起'
            }
          ]
        },
        {
          layer: '可视化与动效',
          icon: 'palette',
          items: [
            {
              name: '粒子可视化复用 ECharts（零新增依赖）',
              why: '用 lines.effect 做「资金迁移粒子」、effectScatter.rippleEffect 做脉冲涟漪、RadialGradient/LinearGradient 做光影渐变。项目内仅 echarts.min.js 与 klinecharts.umd.js，无任何手写 canvas 动画；引入 three.js / d3 会新增数百 KB 依赖与一套全新渲染生命周期，收益不匹配。',
              since: '2026-10-13'
            },
            {
              name: '粒子分档逼近「逐条控参」',
              why: 'ECharts 的 effect 是序列级、无法逐条设参。故按 |资金差额| 把粒子流分 strong / mid / weak 三档，各设为独立 lines 序列，分别控制 period（速度）、symbolSize（粒子大小）、trailLength（轨迹长度），从而实现「密度与速度随强度变化」。实测三档 period 3.2/4.6/6.4、trail 0.60/0.40/0.24。',
              since: '2026-10-13'
            },
            {
              name: '动效统一采用既有设计令牌',
              why: ':root 早已定义 --ease-out-expo / --ease-spring / --transition-fast|normal|slow，且已有 .skeleton 与 skelSweep 骨架屏基件。改造只把使用点统一到既有令牌、复用既有骨架屏，不新建第二套体系，避免设计系统分裂。',
              since: '2026-10-13'
            },
            {
              name: 'ECharts 实例复用而非重建',
              why: '绘图若每次 dispose() + init() 会打断过渡动画（净值曲线硬跳变）。改为复用实例 + setOption 合并更新，统一 500~700ms 的 animationDurationUpdate，让数据变化平滑迁移。',
              since: '2026-10-13'
            }
          ]
        }
      ]
    },

    /* =====================================================================
     * decisions —— D. 关键决策记录
     * ===================================================================*/
    decisions: {
      title: '关键决策记录',
      intro: '这些决策是当前代码形态的「为什么」。改变它们之前请先读这一节。',
      items: [
        {
          id: 'D-01',
          date: '2026-08-04',
          title: '涨跌色改为 A 股惯例「红涨绿跌」',
          decision: '全站统一：涨 = 红（--red #ff3d6a），跌 = 绿（--green #00e96a）。common.css 中 --up/--down 与 --red/--green 指向同色；K 线阳线 #ff3d6a / 阴线 #00e96a。',
          rationale: '服务对象是 A 股个人投资者，红涨绿跌才符合国内看盘直觉，降低误读成本。',
          alternatives: '国际惯例「绿涨红跌」——项目早期 chart.js 曾采用，2026-08-04 UI 升级时全站统一为 A 股惯例。'
        },
        {
          id: 'D-02',
          date: '2026-09-29',
          title: '前端坚持原生 JS，不引入 React / Vue / Streamlit',
          decision: '保留 FastAPI + 原生 HTML/JavaScript + ECharts 架构，不直接迁入第二套前端框架。',
          rationale: '稳定优先：既有代码已 20 万行级原生 JS，引入框架意味着全量重写与长期的依赖膨胀、升级困难；本项目的竞争力在决策逻辑而非前端框架。',
          alternatives: 'React / Vue / Streamlit 全部评估后拒绝；连 Lightweight Charts 这类「更好的图表库」也因「当前已有 ECharts，不更换图表栈」而放弃。'
        },
        {
          id: 'D-03',
          date: '2026-08-04',
          title: '用 SSE 替代 60s 轮询',
          decision: '以 FastAPI StreamingResponse + asyncio.Queue 实现 SSE 广播器单例，开 5 个通道（market / quotes / signals / scan / config），5s 推送。',
          rationale: '轮询既费上游配额又有延迟；SSE 让后台单任务统一拉取、所有前端连接复用同一份数据，实时性与配额效率同时改善，且零新增依赖。',
          alternatives: '① 保留 60s 轮询（已否决）；② WebSocket（双向能力用不上，SSE 更简）；③ APScheduler 等调度框架（被明确否决，改用 asyncio 后台任务复刻 AlertAgent._run_loop 范式）。'
        },
        {
          id: 'D-04',
          date: '2026-10-06',
          title: '自动化交易必须「先回测 → 过风控门控 → 纸面验证」才谈实盘',
          decision: '每策略独立维护门禁状态机 draft → backtested → paper_validating → paper_passed → live_enabled，服务端强制；broker_adapter = null 使实盘物理不可达（架构级保证，而非 UI 遮挡）。',
          rationale: '规则先于直觉。杜绝「裸奔」策略直接下场，是系统最值得长期投入的信任基石；即便用户不打算长期用模拟盘，这道闸门也必须存在。',
          alternatives: '① 直接开实盘（合规与资金风险不可接受）；② 仅用 UI 灰掉按钮（可被绕过，已否决）；③ 用 JSON 日志代替状态机（无强约束力）。'
        },
        {
          id: 'D-05',
          date: '2026-08-04',
          title: '配置持久化用 runtime_config.json，而非改 config.py',
          decision: '运行时可变配置写入独立 runtime_config.json（原子写入），null 值 fallback 到 config.py 默认。',
          rationale: '配置与源码分离：改配置不产生代码 diff，回滚只需删字段；同时避免多进程并发改源码文件的风险。',
          alternatives: '直接编辑 config.py（会污染源码、无法热更新、易与其他改动冲突）。'
        },
        {
          id: 'D-06',
          date: '2026-10-06',
          title: '持久化统一用 SQLite，不用 JSON',
          decision: 'PRD 允许「SQLite 或 JSON」，团队拍板 SQLite：data/trading.db，WAL 模式，5 张表 signals / orders / positions / config / audit_log。',
          rationale: '审计、冷却去重、分页查询都需要事务与索引能力；JSON 文件在并发写与部分更新上不可靠。',
          alternatives: 'JSON 文件存储（早期草案 docs/auto-trading-arch.md 的方案，已被推翻并重命名为 auto-trading-arch-v0-deprecated.md 归档）。'
        },
        {
          id: 'D-07',
          date: '2026-10-06',
          title: '禁止引入 akshare / pandas 等重依赖进交易主链路',
          decision: '回测器用纯 Python + numpy 实现；任何开源组件不得直接出现在 server/trading/ 交易主链路里，必须隔着 server/vendor/<name>/ 适配层。',
          rationale: '五把尺子中的「依赖成本」与「可剥离性」：引入失败时拔掉适配层即恢复原状，改动 ≤ 1 个适配层文件；避免重依赖绑架整棵依赖树。',
          alternatives: 'PRD 原提法「依赖补齐 pandas」被决策 D4 覆盖；vn.py 整体引入亦被否决（体量巨大且面向实盘网关）。'
        },
        {
          id: 'D-08',
          date: '2026-10-07',
          title: 'AI 主脑借鉴 Hermes Agent 架构，原生实现而非嵌入重型 agent OS',
          decision: '新建 server/brain.py，自研 3 个交易时段感知调度任务 + 分层记忆 KV；不把通用 agent OS 嵌入系统。',
          rationale: '评估 Hermes Agent（Nous Research，MIT，207K star）后判断：其「自进化学习闭环 + 分层记忆 + cron + 技能注册」思路值得吸收，但重型通用 agent 运行时不宜进入交易主链路。',
          alternatives: '直接集成 Hermes Agent 或同类 agent 框架（引入不可控的运行时与依赖面）。'
        },
        {
          id: 'D-09',
          date: '2026-09-29',
          title: '开源融合「能力借鉴，不做整仓复制」+ 五把尺子 + 许可证红线',
          decision: '候选项目按「许可证 / 维护活跃度 / 依赖成本 / 架构契合 / 可剥离性」五把尺子评估；MIT、Apache-2.0 可参考实现，AGPL-3.0 仅作产品与指标参考不复制代码，无明确许可证不引入。',
          rationale: '既要吸收社区成熟能力，又不能被许可证传染（GPL 一票否决）或拖垮架构。',
          alternatives: '整仓引入 QUANTAXIS / vn.py / backtesting.py（前两者依赖成本过高，后者 AGPL 传染风险，均否决）。'
        },
        {
          id: 'D-10',
          date: '2026-08-04',
          title: 'admin 后台暂不做鉴权（本地单用户）',
          decision: 'admin.html 与管理接口不设登录鉴权，依赖「单机自部署」这一部署边界。',
          rationale: '系统定位是单用户本地工具（明确排除多用户/商业化），服务只监听本机，鉴权带来的复杂度与收益不匹配；同时投顾牌照合规问题在商业化前不存在。',
          alternatives: '引入账号体系（在当前边界下属于过度设计）。若未来对外提供服务，必须先评估投资顾问牌照合规问题。'
        },
        {
          id: 'D-11',
          date: '2026-09-29',
          title: '单机自部署、不代客理财的边界',
          decision: '坚持「单机 / 自部署、无代客理财」；所有自动交易只用于研究，不触发券商下单。',
          rationale: '合规风险前置规避：一旦对外提供投资建议或代客理财，即触及投顾牌照问题。远期若要「可分享的半托管服务」，须先完成合规评估。',
          alternatives: '直接做多账户 SaaS 化（被明确排除，2026 年内不做）。'
        },
        {
          id: 'D-12',
          date: '2026-10-07',
          title: '参数变更不绕门禁：自动化只加速流转',
          decision: '改参数 → gate_state 强制回退 draft；迭代层的自动晋级严格、自动降级保守、调参只提案不生效。',
          rationale: '自动化如果绕过门禁，风控就形同虚设。所有参数提案必须过「全样本回测 + 末 20% 样本外复测」双轨验证才自动应用，且 100% 入 audit_log。',
          alternatives: '让迭代层直接改配置并跳过回测（会引入过拟合与不可归因的漂移）。'
        },
        {
          id: 'D-13',
          date: '2026-10-13',
          title: '轮动粒子图采用「相对中位数强弱迁移」而非绝对正负',
          decision: '粒子方向 = 资金从净流入低于榜内中位数的板块流向高于中位数的板块，并在图例、横轴、中位数参考线三处显式标注分界。',
          rationale: '数据源 /api/sector-ranking 返回的是涨幅榜前 50 板块。实测普涨日仅 1 个板块净流入为负，若按绝对正负画「流出→流入」，普涨日几乎无粒子可画、可视化会长期空转。相对中位数分界在任何分布下都有明确语义。',
          alternatives: '绝对正负方向（语义更直观但普涨/普跌日失效）；全市场净流入排名（需要额外的全量板块接口，当前不可得）。'
        },
        {
          id: 'D-14',
          date: '2026-10-13',
          title: '粒子图横轴使用带符号对数刻度',
          decision: '横轴以 sign(f)·log10(1+|f|) 映射，轴标签用逆变换还原为真实亿元值并注明「对数刻度」。',
          rationale: '板块净流入分布极度偏斜（实测中位数 8.23 亿 / 头部 158 亿），线性轴把约 90% 节点压在最左侧、标签相互遮挡。改用对数刻度后节点分布空间占比由约 13% 提升到 35.6%（约 2.7 倍）。单调变换，不影响排序与 tooltip 的真实取值。',
          alternatives: '线性轴 + 内部缩放（保留原尺度但静态视图仍拥挤，故两者并存）。'
        },
        {
          id: 'D-15',
          date: '2026-10-13',
          title: '北向净买额已停止披露，改用成分股涨跌分布呈现',
          decision: '北向「成交净买额」自 2024-08 起交易所已停止实时披露（接口固定返回 0）。接口返回 disclosed 标记；前端在 disclosed=false 时显示「已停止披露」，并改以仍然真实可得的成分股涨跌分布 + 相关指数表现填充该位置。',
          rationale: '原实现会把 0 渲染成「0.00 亿」，属于拿无效数据冒充结论。宁可显式说明口径变更，也不用误导性数字占位。',
          alternatives: '隐藏该模块（信息损失）；继续显示 0（误导）。'
        },
        {
          id: 'D-16',
          date: '2026-10-13',
          title: '后端情报写入加「防退化护栏」',
          decision: 'scan_and_persist 在新扫描的 global_snapshot / headlines / channels 为空、而上次非空时，沿用上次值并记录 carried_forward + carried_from_ts；brief / risk 等结论性字段始终使用本次结果。',
          rationale: 'scan_intel 对新闻与全球资产取数使用 return_exceptions=True，任一取数失败时这些字段会静默退化为空；原实现无条件覆盖 brain_intel，会把上一次的富情报整体顶掉，前端表现为「影响渠道 / 全球快照 / 命中新闻」突然全部消失而 AI 研判文本还在（最难排查的一类退化）。',
          alternatives: '保留双份数据（改动更大）；前端本地缓存兜底（多端不一致）。'
        },
      ]
    },

    /* =====================================================================
     * milestones —— E. 重要里程碑（时间线）
     * ===================================================================*/
    milestones: {
      title: '重要里程碑',
      intro: '按时间线排列。每条都标注了版本 / 产出文件，便于回溯当时的状态。',
      items: [
        {
          date: '2026-05',
          version: 'v3.0 立项',
          title: '项目起步与早期系统设计',
          summary: '确立 FastAPI + 原生 HTML/JS + ECharts + SQLite 的技术骨架，配置 17 个板块、覆盖 200+ 只 A 股标的。',
          highlights: [
            '早期系统设计落盘 docs/10-architecture/system_design.md',
            '17 板块配置：半导体 / 新能源 / 医药 / 消费 / 金融 / 军工 / AI算力 / 光伏 / 互联网 / 房地产 / 钢铁有色 / 汽车 / 电力设备 / 通信 / ETF基金 / 网络安全 / 消费电子',
            '端口 8900，静态资源由 FastAPI StaticFiles 挂载'
          ],
          files: ['docs/10-architecture/system_design.md', 'server/config.py', 'server/main.py']
        },
        {
          date: '2026-06-03',
          version: '扫描修复',
          title: '价格过滤 + 智能扫描升级',
          summary: '智能扫描加入 5~40 元价格区间过滤，扫描面板样式系统与结果卡片重做，轮询超时从 2 分钟延长到 3 分钟。',
          highlights: [
            '后端新增 SCAN_PRICE_MIN / SCAN_PRICE_MAX 与 /api/scan 的 price_min / price_max 参数',
            '前端 appState 新增 priceMin:5 / priceMax:40，三处渲染统一过滤',
            '新增完整 .scan-* 样式系统（进度条 / 结果卡片 / 统计栏）',
            '轮询超时 2min → 3min，解决长扫描被前端提前放弃'
          ],
          files: ['server/routers/scan.py', 'static/js/app.js', 'static/dashboard.html', 'static/css/dashboard.css', 'CHANGELOG.md']
        },
        {
          date: '2026-07-04',
          version: 'v4.0',
          title: '系统优化：稳定性、缓存与前端功能完善',
          summary: '一次性补齐错误处理、缓存控制与前端体验：全局错误中间件、NoCacheHTMLMiddleware、多主题、板块热力图、迷你走势图、API 请求重试。',
          highlights: [
            '全局错误处理中间件 + API 访问日志（方法/路径/状态码/耗时）',
            'NoCacheHTMLMiddleware：HTML no-store、JS/CSS no-cache、静态资源 max-age=3600',
            '腾讯行情超时 8s → 15s',
            '四套主题配色（极光暗夜 / 海洋蓝 / 绿色交易 / 暖阳）；板块热力图 treemap；Canvas 迷你走势图',
            '代码去重：删除 app.js 重复的 calculateSectorStats()；修复加载卡 78% 问题',
            '稳定性指标：API 成功率 >95%、K 线缓存命中 <1s、200+ 只扫描 <30s'
          ],
          files: ['docs/90-archive/SYSTEM-OPTIMIZATION-v4.0.md', 'server/main.py', 'static/js/app.js']
        },
        {
          date: '2026-07-07',
          version: 'v5.4',
          title: '侧边栏 5 工具深度升级',
          summary: '选股器、智能选股、板块分析、热门股、龙虎榜五个侧边栏工具成形；板块分析完全重构为 31 板块排行 + 5 周期切换 + 详情 + 成分股弹层。',
          highlights: [
            '选股器：评分 / 涨幅 / 换手率 / 量比 / PE 五条件筛选 + 独立全屏结果面板',
            '智能选股：全市场扫描 + 行业 checkbox 过滤 + 评分排序',
            '板块分析：31 个申万一级板块排行 + 5 个周期切换 + 资金流 + 成分股弹层',
            '热门股：50 条排行 + 股票/ETF tab + 详情弹卡',
            '龙虎榜：5 个 tab（机构 / 一日榜 / 营业部 / 个股买卖 / 龙虎新闻）+ 日期选择器',
            '产出 AI 接驳手包 HANDOFF.md，固化架构、API 清单与技术约定'
          ],
          files: ['docs/90-archive/HANDOFF-v5.4.md', 'static/dashboard.html', 'static/js/app.js', 'static/js/market-data.js', 'static/js/api.js']
        },
        {
          date: '2026-08-04 ~ 08-05',
          version: 'v3.0 → v4.0 UI 升级',
          title: 'SSE 实时推送 + 全新管理后台 + 全站设计系统',
          summary: '16 个文件（10 新建 + 6 修改）完成一次架构级前端升级：SSE 替代轮询、admin 后台重做、红涨绿跌全站一致、共享设计系统落地。',
          highlights: [
            'SSE 5 通道替代 60s 轮询（零新依赖，广播器单例 5s 推送）',
            'admin.html 全新管理后台（板块管理 / 策略权重 / 缓存管理 / 系统状态 + 靛蓝主题）',
            'RuntimeConfig 运行时配置（JSON 持久化 + 原子写入，不污染 config.py）',
            '共享设计系统：common.css + shared.js + sse-client.js',
            '红涨绿跌全局统一（common.css / dashboard 内联 / chart.js 三处一致）',
            '修复 agents.py 缺 import asyncio 导致的 Agent 启动 NameError（QA 发现）'
          ],
          files: ['server/sse_broadcaster.py', 'server/runtime_config.py', 'server/routers/stream.py', 'server/routers/admin.py', 'static/css/common.css', 'static/js/sse-client.js', 'static/admin.html']
        },
        {
          date: '2026-08-09',
          version: 'index.html 升级',
          title: '首页升级为数据驱动门户',
          summary: 'index.html 从占位页重做为实时数据门户：5 列指数 LIVE Ticker、交易时间智能识别、市场脉搏、板块全景、策略权重 donut。',
          highlights: [
            '5 列指数（上证 / 深成 / 创业板 / 纳指 / 黄金），SSE 实时推送 + 轮询降级',
            '交易时间智能识别：交易中 / 尚未开盘 / 午间休市 / 已收盘 四态',
            '市场脉搏：并行拉取 5 个板块 /api/quotes，合并排序取 Top 涨跌 5',
            '修复 admin/status 返回 {ok,data} 嵌套结构导致前端永远走 fallback 的 bug',
            '全站零 emoji、全部内联 SVG 线性图标'
          ],
          files: ['static/index.html', 'static/js/app.js']
        },
        {
          date: '2026-08-21',
          version: '图标体系',
          title: 'dashboard 全面优化：emoji → SVG 图标体系',
          summary: '24 个板块 emoji 与 header 按钮全部替换为内联 SVG；新增离线横幅与交易状态六态徽章。',
          highlights: [
            'SECTORS 24 个板块图标全部改为 24×24 viewBox SVG 路径',
            'Header 5 处导航 + 刷新/对比/情绪按钮全部 SVG 化',
            '离线横幅：固定顶部 + 毛玻璃 + pulse 动画 + slide-down',
            '新增 .trading-status 六态徽章（盘前 / 竞价 / 交易中 / 午休 / 已收盘 / 休市）',
            '确认服务实际从 .openclaw/workspace 路径提供，此前编辑的 D: 路径文件未被使用'
          ],
          files: ['static/js/app.js', 'static/css/dashboard.css', 'static/dashboard.html']
        },
        {
          date: '2026-08-24 ~ 08-25',
          version: 'v7 → v8.1',
          title: '股票面板设计三连改',
          summary: '从「花哨」到「克制」：移除 3D 倾斜与装饰件，保证主信息（股票名）在所有宽度下完整可读，再补足行高与留白。',
          highlights: [
            'v7：新增评分徽章 / 行工具 / 排序菜单 / 右键菜单 / 信号筛选 / 骨架屏（含 2 个 localStorage 潜在崩溃修复）',
            'v8 返工：4 字股票名在 220px 窄面板被压成 2 字 → 移除 3D 透视、.score-badge、.hot-band；评分改为「评 88」彩色小字',
            'v8.1 透气化：行高 min-height 50px，换手/量比默认隐藏仅 hover/active 显示，信号标签折叠为 1 + 「+N」'
          ],
          files: ['static/js/app.js', 'static/css/dashboard.css']
        },
        {
          date: '2026-08-27',
          version: 'index.html 稳定性',
          title: '首屏稳定性修复 + 设计优化',
          summary: '定位「面板不稳定」真因是 .fade-in 默认 opacity:0 依赖 IntersectionObserver，改为默认可见；Hero 状态由硬编码改为真实健康探测。',
          highlights: [
            '.fade-in 默认 opacity:1，仅 JS 接管时隐藏；1.2s 兜底移除动画类',
            '计数器首屏立即动（不再等滚动到 50%）',
            'Hero 健康检测 probeHeroHealth()：2/2 成功 = 实时运行中，1/2 = 部分服务异常，0/2 = 维护中，60s 复探',
            'Demo 加载改 Promise.allSettled + 4s AbortController 超时，单板块失败不影响其他',
            '回归 8/8 全过，0 控制台错误'
          ],
          files: ['static/index.html']
        },
        {
          date: '2026-09-10',
          version: 'v10 / v11',
          title: '界面精修：移动流动版重设计 + 跑马灯可读性',
          summary: '390px 视口下股票列表一度只剩 1 行可视，重构为「头部单行 44px + 横向滚动芯片 + 列表 9 行可视」；跑马灯从「密到不可读」改为药丸化。',
          highlights: [
            '修复 dashboard.html 被外部回滚（丢失排序下拉 DOM 与直方图容器）',
            'v10：评分圆点指示器、排名变化箭头（↑红/↓绿 spring 动画）、直方图柱顶数字',
            'v11：移动端头部压缩为单行 44px sticky，列表 height:56vh（9 行可视）',
            'v11.1：跑马灯每项药丸化（圆角边框 + 底色 + 内边距）、A 股红涨绿跌显式着色、悬停暂停滚动',
            '定位「列表 overflow 被板块行情摘要卡视觉遮挡」这类几何与视觉不一致的坑'
          ],
          files: ['static/dashboard.html', 'static/css/dashboard.css', 'static/css/ui-refinement.css', 'static/js/app.js']
        },
        {
          date: '2026-09-29',
          version: '开源融合规划',
          title: 'OPEN_SOURCE_INTEGRATION_PLAN 立项',
          summary: '核验 9 个 GitHub 量化项目（AKShare 22,791★ / QUANTAXIS 11,249★ / Lightweight Charts 17,389★ / QuantStats 7,668★ 等），确立「稳定优先、能力借鉴、不做整仓复制」的融合路线。',
          highlights: [
            '总体原则 6 条：稳定优先 / 能力借鉴 / 许可证优先 / 数据源可降级 / 不接真实交易 / 渐进交付',
            '分阶段：P0 稳定基线与已有模块激活 → P1 QuantStats 风格组合风险中心 → P2 AKShare 可选数据适配层 → P3 新闻情绪与事件中心 → P4 高级回测与复盘',
            '许可证红线：backtesting.py（AGPL-3.0）仅参考功能范围，不复制代码',
            '红线：任何开源组件不得直接出现在 server/trading/ 交易主链路里'
          ],
          files: ['docs/00-strategy/OPEN_SOURCE_INTEGRATION_PLAN.md']
        },
        {
          date: '2026-10-06',
          version: '自动化交易 M1-M4',
          title: '自动化交易后端 + 前端全链路交付',
          summary: '按 docs/10-architecture/auto-trading-architecture.md（唯一权威蓝图）落地：12 个后端文件 3364 行 + M4 前端四 Tab 浮层，实盘物理不可达。',
          highlights: [
            '架构核心：asyncio 后台任务（复刻 AlertAgent._run_loop 范式）而非 APScheduler，零新依赖',
            '门禁状态机：draft → backtested → paper_validating → paper_passed → live_enabled',
            'SQLite data/trading.db 五表（WAL，单写者 + asyncio.Lock）',
            '回测实测：sh600519 / ma_cross_atr → 6 交易、胜率 0.333、年化 0.45%、maxDD -6.23%、pf 1.03',
            'M4 前端：automation.js 842 行 + 四 Tab 浮层 + 状态灯 + 熔断横幅 + 合规页脚',
            'K 线引擎换代：KLineChart v10 接线，ECharts 永久兜底；修复移动端 K 线卡塌缩（根因 #right-panel flex 塌缩）',
            '三项界面优化：K 线视觉精修、AI 分析内联摘要 + 全屏大屏（v5.0/v5.1）',
            '历史草案 auto-trading-arch.md 归档为 v0-deprecated（JSON 存储 / pandas / /api/auto 前缀方案被推翻）'
          ],
          files: ['server/trading/*（12 文件）', 'server/routers/automation.py', 'static/js/automation.js', 'docs/10-architecture/auto-trading-architecture.md']
        },
        {
          date: '2026-10-06 ~ 10-07',
          version: 'roadmap v1.1 / v1.2',
          title: '北极星目标确立 + 复盘系统 MVP + DeepSeek 通电',
          summary: '补齐「路线缺终点」的问题：定义北极星目标与 5 项验收指标，并当天交付复盘系统 MVP 把指标变成可见仪表盘。',
          highlights: [
            '北极星：纸面账户 90 天滚动窗口跑赢沪深300（≥基准 +3pct）且每笔交易 100% 可回溯',
            '验收口径：maxDD ≤10% 且 ≤基准同期、胜率 ≥55%、盈亏比 ≥1.5、周复盘连续不缺周',
            'DEEPSEEK_API_KEY 通电：旧 key 账户 402 欠费 → 换用有效 key，4 只验证股票均返回 ai_decision（买入68 / 观望78 / 卖出82）',
            '新建 server/routers/review.py 三端点：/api/review/summary|equity|weekly',
            '复盘前端：自动化中心第 5 Tab，北极星目标卡 + 净值 vs 基准曲线 + 周复盘叙述',
            '修复 analyze 对异常 K 线条目的防御过滤（裸代码不带 sh/sz 前缀会走「数据不足」stub）'
          ],
          files: ['docs/00-strategy/product-roadmap-2026.md', 'server/routers/review.py', 'static/js/automation.js', 'server/routers/analyze.py']
        },
        {
          date: '2026-10-07',
          version: 'Phase 1 核心',
          title: '自动化闭环全链路落地（自复盘 + 自迭代）',
          summary: '一天内交付迭代层引擎、单笔复盘引擎、策略库沉淀、闭环调度器与 evolution API ×8，系统第一次实现「自己复盘、自己调参提案、自己晋级/降级」。',
          highlights: [
            '新建 server/trading/review_engine.py：FIFO 配对 → MFE/MAE/出场效率/卖后回升/同期基准 → 规则化中文结论 + 标签，幂等落 KV',
            '新建 server/trading/iterator.py：决策矩阵 promote/demote/propose/retire + 调参提案双轨验证（全样本 + 末 20% OOS）+ library_sediment 版本谱系',
            '新建 server/trading/loop_scheduler.py：工作日 15:08 后日结（快照→复盘→迭代），周五加跑周报，loop_state KV 幂等',
            '新建 server/routers/evolution.py ×8 端点 + 复盘 Tab 闭环看板五区块',
            '策略扩充 2 → 5：新增 rsi_mean_reversion / macd_trend / volume_breakout，既有 2 策略补固定止损兜底与可选量能确认',
            'QQ 邮件通道闭环：_resolve_recipient() 空收件人回退自发自收；实测 POST /api/automation/notify/test 返回 sent:true',
            '端到端验证 scripts/test_closed_loop.py：11 笔合成交易 → 复盘幂等 → promote/demote 正确 → 提案双轨验证（真改善自动应用、噪声改善自动拒绝）'
          ],
          files: ['server/trading/review_engine.py', 'server/trading/iterator.py', 'server/trading/loop_scheduler.py', 'server/routers/evolution.py', 'scripts/test_closed_loop.py']
        },
        {
          date: '2026-10-07',
          version: 'v47',
          title: '账户面板重设计 + 动态市场调查真实化 + AI 主脑',
          summary: '三大改造同日落地：大盘纵览账户面板重构、市场调查从 100% 硬编码假数据改为真实三段式、新建 DAPENG Brain 主脑。',
          highlights: [
            '账户面板：Hero（总资产+累计盈亏）→ 6 格 KPI → 双图表（收益曲线 + 持仓 donut）→ 交易表单 → 持仓明细 + 最近交易',
            '动态市场调查真实三段式：收集（market_dashboard 60s 缓存 + ak_hub 宏观/东财新闻）→ 分析（规则引擎 regime/板块轮动/真实异动预警）→ 汇总（DeepSeek 生成总结，无 Key 规则模板兜底）',
            '实测调查：区间震荡 score 53、医药农业主升、3 涨停、8 异动，全部真实数据',
            'DAPENG Brain：3 个交易时段感知调度任务（market_survey 30min / daily_loop 触发闭环 / memory_distill 6h 经验蒸馏）+ 分层记忆 KV（brain_insights 留 30 / brain_lessons 留 20）',
            '网站崩溃根治：server_watchdog.py 看门狗 + start-dapeng.vbs（pythonw 无窗体）+ 停用旧自启 dapeng-alpha.vbs',
            '关键运维事实：沙箱内无法留下跨会话存活进程，持久服务必须由用户在沙箱外启动'
          ],
          files: ['static/dashboard.html', 'static/js/market-dashboard.js', 'server/market_survey_engine.py', 'server/brain.py', 'server/routers/brain.py', 'scripts/server_watchdog.py']
        },
        {
          date: '2026-10-08',
          version: 'css v49',
          title: '动态市场调查散点图修复与双向交互',
          summary: '把「板块轮动」从静态 innerHTML 列表升级为 ECharts 键控散点图，并修复 chart_analysis 路由双重前缀 404。',
          highlights: [
            '后端 market_survey_engine.py 补 rotation 数值字段（avg_chg/up_ratio/amount_yi/count）+ 新增 _build_scatter() 输出 top12 集',
            '前端 ECharts 轮动散点图：气泡 = 成交额开方映射 14~46px，x = 平均涨跌 / y = 上涨占比，四象限虚线',
            '散点 ↔ 列表行双向联动（select/highlight/downplay/showTip + scrollIntoView smooth），刷新后选中态保持',
            '修复 server/main.py 中 chart_analysis 路由 prefix 叠加导致 /api/api/analyze/chart-summary 404'
          ],
          files: ['server/market_survey_engine.py', 'server/main.py', 'static/js/intelligent-analysis.js', 'static/css/dashboard.css']
        },
        {
          date: '2026-10-10',
          version: 'css v51',
          title: '动态市场调查 Hero / 主脑三区重设计 + Qlib 因子挖掘视图',
          summary: '按「信息优先级金字塔」重构调查视图：新增市场状态总览 Hero，主脑由单块改为三区结构，宏观与 AI 评述合并为一个面板。',
          highlights: [
            'P0 Hero：状态标签 + 情绪分 + 置信度 + 情报风险徽章 + 数据源徽章 + 异动/流动性摘要',
            'P1 AI 主脑三区：brain-main（研判升为第一主视觉）/ brain-side（情报评估 + 内核运行态）/ brain-scheduler（任务调度降级为可折叠）',
            'P2 宏观环境 + AI 综合评述合并为 macro-panel（左宏观卡右评述），消除割裂',
            '工作台新增 Qlib AI 因子挖掘视图',
            '工程经验：重设计保留既有元素 id、只调容器与包装层，可零改动复用 JS 数据绑定',
            '另一侧：D:\\L 工作区定位「开机后网页打不开」根因 —— 看门狗里写死的 venv 解释器路径已不存在'
          ],
          files: ['static/dashboard.html', 'static/css/dashboard.css', 'static/js/intelligent-analysis.js', 'D:\\L\\.workbuddy\\memory\\2026-10-10.md']
        },
        {
          date: '2026-10-13',
          version: 'v5.5 数据链路补全',
          title: '修复三处结构性断链 + 补齐被丢弃的主脑情报',
          summary: '定位并修复「模块写了却从未挂载」「委托已失效 CLI 的死接口」「消费方是死代码」三类根因；让 6 个此前后端可用但前端未使用的结构化字段真正上屏。',
          highlights: [
            '北向资金 404 根因：server/routers/market.py 从未在 main.py 注册，且 import 了不存在的单例 ak_market（实际只导出 ak_hub）→ 一旦挂载即 ImportError；修正后注册 /api/market/north',
            '新增 AkShareDataHub.get_north_flow()，实测 3.59s 返回真实数据；如实标记北向净买额自 2024-08 起停止披露',
            '板块资金流死接口：原委托 westock CLI（该 CLI 已按设计永久不可用），长期返回空数组且以 ok:true 掩盖；改用东财 ulist.np 批量接口，实测 20 只成分股约 3s，半导体主力净流入 -4.46 亿',
            '主脑情报补齐：global_snapshot（6 个全球资产）、headlines（命中新闻）、channels（影响渠道）三块此前被前端整体丢弃',
            '发现并接入死代码：updateNorthFlow 定义了却从未被调用，导致其 404 断链长期不可见',
            '修正 2 处与 A 股惯例相反的涨跌配色（app.js 北向、sentiment.css 的 .ff-val）'
          ],
          files: ['server/ak_market.py', 'server/routers/market.py', 'server/routers/market_data.py', 'server/main.py',
                  'static/js/app.js', 'static/js/intelligent-analysis.js', 'static/js/sentiment.js', 'static/css/sentiment.css']
        },
        {
          date: '2026-10-13',
          version: 'v5.6 动效体系与数据稳健性',
          title: '统一动效采用 + 骨架屏 + 图表平滑 + 情报防退化',
          summary: '把散落的硬编码过渡统一到既有设计令牌，补齐 6 处骨架屏与 KPI 数字滚动，让图表复用实例恢复过渡动画；并为后端情报写入加防退化护栏。',
          highlights: [
            'KPI 数字滚动 countUpKpi()：保留前缀/符号/小数位，ease-out-cubic，tabular-nums 防字宽抖动，连续调用自动取消上次动画（轮询安全）',
            '骨架屏 6 处（宏观/轮动/预警/新闻/动态因子/分位图）+ 指数条，复用既有 .skeleton shimmer；数据到达后全部归零',
            '图表改为复用 ECharts 实例（实测 navReused / quantReused 均为 true），恢复被 dispose+init 打断的过渡动画',
            '接入此前唯一未使用的 /api/intelligent/ak/index，新增 7 大宽基指数条（含涨跌家数）',
            '修复北向 ticker 被覆盖的竞态：loadQuotes 每次刷新都会整体重写 #ticker-track，新增 _northTickerHtml 缓存 + applyNorthTicker() 让轨道重建后自动恢复',
            '后端 scan_and_persist 加防退化护栏，单元测试双向验证 IS_PASS: YES（退化输入沿用+标注来源、正常输入不干预）',
            '消除长期存在的 /favicon.ico 404，控制台 error 归零'
          ],
          files: ['static/css/dashboard.css', 'static/css/sentiment.css', 'static/dashboard.html',
                  'static/js/intelligent-analysis.js', 'static/js/app.js', 'server/agents/intel_agent.py']
        },
        {
          date: '2026-10-13',
          version: 'v5.7 板块资金迁移粒子图',
          title: '轮动模块升级：粒子可视化 + 自动总结',
          summary: '新增板块资金迁移粒子图（零新增依赖）与基于真实资金流计算的自动总结，作为「动态市场调查」视图的轮动核心可视化。',
          highlights: [
            '三序列组合：lines+effect 粒子流、scatter 渐变光球节点、effectScatter 涟漪（仅净流入 Top3，避免满屏闪光）',
            '粒子分档（strong/mid/weak）实现密度与速度随资金强度变化',
            '交互：悬停节点高亮其关联资金流（实测关联 1.0 / 非关联 0.02）；点击聚焦后仅保留关联流、涟漪降为 1、其余 23 个节点淡出；重播粒子；滚轮缩放 + 滑块拖拽',
            '性能与适配：移动端自动降为 14 节点 / 5 条流 / 关闭光影 / 涟漪降为 1；尊重 prefers-reduced-motion；页面隐藏时跳过重绘',
            '自动总结：资金集中度（含抱团判定）、迁移方向（含净流差）、阶段结构分布、量价背离提示（涨幅高于中位数但净流入低于中位数）'
          ],
          files: ['static/dashboard.html', 'static/css/dashboard.css', 'static/js/intelligent-analysis.js']
        },
        {
          date: '2026-10-13',
          version: 'v5.8 文档治理',
          title: '项目文档与最新实现对齐',
          summary: '同步三批改造到文档站，修正过期描述（UI 版本号、akshare 纪律、资金流接口口径），并补充决策、问题与 API 索引。',
          highlights: [
            'meta.uiVersion 由 v51/v32/v7 更新为 v58/v32/v16/v30',
            '修正「不使用 pandas / akshare 进主链路」的过期表述（北向实现已引入 akshare）',
            '新增决策 D-13~D-16：相对中位数迁移、对数刻度、停止披露的呈现口径、防退化护栏',
            '新增问题 I-20~I-26 与对应根因',
            'apiIndex 修正 /api/sector-ranking 与 /api/fund-flow/sector 的实际口径，新增本次端点分组'
          ],
          files: ['static/js/project-docs-data.js', 'static/project-docs.html']
        },
        {
          date: '2026-10-13',
          version: 'v5.9 粒子渲染完整性',
          title: '粒子图「显示不全」系统性排查与修复',
          summary: '定位并修复导致粒子元素缺失/被裁/被遮挡的 4 类根因，建立可复现的多场景渲染完整性验证方法。',
          highlights: [
            '根因一：退化行情下净流入全部相同 → 中位数分组为空 → 粒子流 0 条（改为上下半区分组 + 退化警示）',
            '根因二：curveness 外扩贝塞尔控制点 + 序列默认 clip:true → 弧顶被 grid 裁掉（全部改 clip:false，curveness 0.32→0.22）',
            '根因三：多板块同坐标 → 散点完全重合只看得见最上面一个（像素距离聚类 + 环形错开 + 斥力松弛，保证中心距 ≥ 1.02×半径和）',
            '根因四：视口变化只 resize 不重算 → 桌面↔移动端偏移失配（改为按新宽度重跑去重叠）',
            '轴范围按数据实测值留白（x/y 各自 11%/24% 余量），消除右侧大片空白与端点贴边被裁',
            '验证方法：CDP 注入 6 种数据分布，逐条计算二次贝塞尔 t=0.5 极值点判越界 —— outside 0 / nodeOutside 0 / severeOverlap 0 / distinctPos = nodes'
          ],
          files: ['static/js/intelligent-analysis.js', 'static/dashboard.html']
        },
        {
          date: '2026-10-13 ~ 10-14',
          version: 'v5.9+ 数据真实度治理',
          title: '数据源真实性治理：兜底数据强制自证身份',
          summary: '定位并修复"接口信封谎报来源"这一最隐蔽的假数据来源：接口曾无条件标注"东方财富行业板块实时行情"，而实际返回的是 24 条硬编码样本。',
          highlights: [
            '被暴露的缺陷：server/stable_market_provider.py 三个取数方法（get_hot_stocks / get_lhb_data / get_sector_ranking_data）在真实请求失败后返回写死样本，行级 source 是诚实的"系统快照"，但被 server/routers/market_data.py 的信封无条件覆盖为"东方财富行业板块实时行情"',
            '后果：前端拿到 24 个全同值（chg 全=1.85 / net_flow 全=12.5 亿）的编造板块，却被标注为"实时行情"——这正是此前"粒子图全部板块净流入都是 12.5 亿"之谜的真因',
            '修法：兜底数据强制自证身份 —— 信封级 degraded:true + 真实来源名；行级 synthetic:true（本地样本）/ stale:true + stale_from（历史快照）；前端显示醒目横幅',
            '正面范例（全项目模板）：global_assets.py 的 coverage/degraded/price:null；ak_market.get_north_flow 的 disclosed 标记'
          ],
          files: ['server/stable_market_provider.py', 'server/routers/market_data.py', 'server/global_assets.py']
        },
        {
          date: '2026-10-14',
          version: '龙虎榜陈旧护栏',
          title: '龙虎榜「2015 年数据伪装成实时」事件修复',
          summary: '龙虎榜曾把 2015 年救市日的数据当成"今日龙虎榜"展示 —— 根因是排序字段选择错误叠加交易日探测失败。',
          highlights: [
            '根因链：交易日探测失败 → 触发无日期过滤的全历史查询 → 按净买额降序 → 早至 2015 年的救市日（净买额极大）霸榜 → 被当作"实时"展示',
            '修法一：排序改为多列 sortColumns=TRADE_DATE,BILLBOARD_NET_AMT + sortTypes=-1,-1（先交易日倒序，再净买额倒序），保证"最近的"排在最前',
            '修法二：新增陈旧护栏 _EM_LHB_STALE_DAYS=10，返回数据最新交易日距今超过 10 天即判定陈旧并降级',
            '教训：排序字段决定"最近的"还是"最大的"排最前，分页取头部时二者混淆会静默返回历史极值'
          ],
          files: ['server/stable_market_provider.py', 'server/routers/market_data.py']
        },
        {
          date: '2026-10-14',
          version: '四级降级链',
          title: '板块/热门股/龙虎榜四级降级链 + 原子落盘',
          summary: '把"全局硬编码兜底"升级为分级降级：真实 → 历史快照 → 腾讯聚合 → 本地样本，成功即原子落盘。',
          highlights: [
            '① 真实（degraded:false）→ ② 历史快照（stale:true + 来源含时间戳）→ ③ 腾讯聚合（东财 push2 不可达时用腾讯成分股聚合真实板块指标）→ ④ 本地样本（synthetic:true）',
            '落盘为原子写：写 .tmp<pid> → flush + fsync → os.replace；任一步失败只记 warning，不污染已有缓存',
            '关键性质：④ 只在"首次部署 / 缓存被删"时出现，而非每次网络抖动都出现 —— 此前 push2 一断就整屏编造常量的问题由此根除',
            '缓存命中时 logger.info 记录使用了哪份缓存、哪个 fetch_ts，便于事后追溯'
          ],
          files: ['server/stable_market_provider.py']
        },
        {
          date: '2026-10-14',
          version: '板块自建聚合',
          title: '板块数据自建聚合：东财不可达时用腾讯成分股聚合',
          summary: '东财 push2 不可达时，不再直接落本地样本，而是用腾讯成分股行情聚合出真实板块指标。',
          highlights: [
            '聚合口径：成交额加权涨跌幅 + 成交额求和 + 涨跌家数（均为真实成分股行情推导）',
            'net_flow 如实返回 null，不推算 —— 宁可缺字段也不给假数',
            '该级位于降级链第 ③ 级，介于"历史快照"与"本地样本"之间，减少对硬编码样本的依赖'
          ],
          files: ['server/stable_market_provider.py']
        },
        {
          date: '2026-10-14',
          version: '因子引擎去假',
          title: 'qlib 因子引擎去假：IC/IR/分位/参数全部改为真接线',
          summary: '把编造的 IC/IR 与写死分位改为真实计算，并让此前为空壳的回归参数真正生效。',
          highlights: [
            'IC：由「动量分均值 × 0.15」改为真实截面相关系数时间序列',
            'IR：由「IC × 18」改为 mean(IC)/std(IC)',
            'Q1~Q5：由写死比例改为真实分位分组',
            'weights / selected_factor_ids / start_date：从空壳（仅回显）改为真接线生效',
            '配套：元数据声明的因子集合与实际计算集合需完全一致（消除"声明 6 个实算 4 个"）'
          ],
          files: ['server/qlib_factor_engine.py']
        },
        {
          date: '2026-10-14',
          version: '回测链路复活',
          title: 'quant_core.py 回测链路复活（NameError 修复）',
          summary: '修复长期不可运行的量化回测链路：一跑即抛 NameError。',
          highlights: [
            '根因一：指标名未绑定到模块级全局 —— SMAIndicator 等裸名只在某个函数体内导入，模块级只有 _ta_* 系列',
            '根因二：ta 库 MACD 参数名应为 window_sign 而非 window_signals',
            '修复后 make_stock_features 合成数据冒烟通过，8 个关键列非全 NaN'
          ],
          files: ['server/quant_core.py']
        },
        {
          date: '2026-10-14',
          version: '数据源实测',
          title: '数据源可用性实测结论（非推断）',
          summary: '对各数据源逐一探测，确立"可达性波动"这一前提，并据此调整降级设计。',
          highlights: [
            '东财 push2 间歇可达（ReadTimeout / RemoteDisconnected）；datacenter-web HTTPS 可达（有抖动）',
            'AkShare index / spot / north 不可用；腾讯与新浪存活',
            '关键结论：可达性本身波动 → 降级路径必须始终存在且正确；恢复可达时最危险（旧推算字段会被绿标伪装成实时）'
          ],
          files: ['logs/server.log', 'server/stable_market_provider.py']
        },
        {
          date: '2026-10-14',
          version: 'v5.10 文档体系重构',
          title: '项目文档信息架构重构（M9）',
          summary: '把 98 个混放文件整理为有信息架构的文档站 + 有秩序的文件树，并新增战略/模块/数据三章。',
          highlights: [
            '文件树：docs/ 拆为 00-strategy / 10-architecture / 20-prd / 30-data / 40-ops / 90-archive + assets（截图统一收纳）',
            '归档而非删除：v4.0 两份重复报告合并为一份移入 90-archive/；HANDOFF.md 标注版本落后后归档',
            '根目录只保留 README.md 与 CHANGELOG.md',
            '文档站新增三章：战略定位与发展目标、模块边界与验收标准、数据口径字典；章节总数 12 → 15',
            '排版重构：分组树形导航 + 当前章节高亮 + 小节锚点 + 顶部面包屑 + 统一标题层级 + 长表格横向滚动 + 移动端单列',
            '新增 static/css/docs.css（不触碰 dashboard.css）'
          ],
          files: ['README.md', 'docs/00-strategy/MASTER-PLAN-2026Q4.md', 'static/project-docs.html', 'static/js/project-docs-data.js', 'static/css/docs.css']
        },
        {
          date: '2026-10-14',
          version: 'M2 全球传导建模',
          title: '外部市场 → A 股传导建模落地（M2）',
          summary: '从"罗列全球行情"升级为"量化外部市场对 A 股的传导关系"：领先滞后相关、开盘缺口传导、每日影响打分卡。',
          highlights: [
            '新增 server/global_linkage.py：仅使用实测可达通道（腾讯日K + 腾讯/新浪实时行情）',
            '领先滞后相关：外部市场日收益 vs 沪深300 日收益，lag 0..3，输出样本量 n',
            '开盘缺口传导：隔夜外盘收益 → 次日沪深300 开盘缺口（相关 + 方向命中率 + n）',
            '每日影响打分卡：每项 利好/利空/中性 + 分数 + 权重 + 证据，综合分可追溯',
            '红线：样本不足（n < MIN_SAMPLES）返回 null + insufficient_samples，不给编造系数；经验判断显式标 evidence_type="heuristic"',
            '已接线：server/routers/global_market.py 暴露 GET /api/global/linkage（get_linkage_with_scorecard）'
          ],
          files: ['server/global_linkage.py', 'server/routers/global_market.py']
        },
        {
          date: '2026-10-14',
          version: 'M4 成交成本模型',
          title: '模拟盘成交成本模型落地（M4）',
          summary: '给"纸面收益率"补上交易成本语义：滑点 / 佣金 / 印花税 / 过户费，手算可对齐。',
          highlights: [
            '新增 server/trading/costs.py：纯函数、无副作用、无 IO，便于单测与手算对齐；货币单位统一为元',
            '买入成交价 = 现价×(1 + slippage_bps/10000)；卖出成交价 = 现价×(1 − slippage_bps/10000)',
            '佣金 = max(成交额×commission_rate, commission_min)；印花税 = 成交额×stamp_tax_rate（仅卖出）；过户费 = 成交额×transfer_fee_rate',
            '买入总成本 = 成交额 + 佣金 + 过户费；卖出净收入 = 成交额 − 佣金 − 印花税 − 过户费',
            '成交价保留 3 位小数、金额保留 2 位小数，避免"手算对不上"',
            '已接线：server/trading/paper.py 调用 costs（result.slippage_cost 落库）；默认 slippage_bps=5.0'
          ],
          files: ['server/trading/costs.py', 'server/trading/models.py', 'server/trading/paper.py']
        },
        {
          date: '2026-10-14',
          version: 'M7 主脑个性化',
          title: 'AI 主脑模块注册表与个性化配置落地（M7）',
          summary: '主脑面板从"模块写死"升级为"可开关 / 可排序 / 可持久化"。',
          highlights: [
            '前端接入主脑模块配置持久化：localStorage 键 dapeng_brain_modules（模块开关 + 顺序）',
            '动态布局：按用户配置渲染，无数据的模块折叠并提示原因而非空白',
            '实现位置：static/js/intelligent-analysis.js（M7 主脑面板交互）'
          ],
          files: ['static/js/intelligent-analysis.js']
        },
        {
          date: '2026-10-30',
          version: 'v40-v85',
          title: 'AXON 图谱/星系大规模重设计 + 决策中枢融合 + 全局性能治理',
          summary: '六个迭代日的技术总账：图谱数据治理（41 节点/105 边/0 孤立）、星系从 2D 细线到 WebGL 刻线浑天仪的完整重构、观测者个性化体系、决策台×市场调查×情绪六源中枢、通知中心接真实信号、三层省电体系；逐版详情见 docs/10-architecture/AXON-GRAPH-DESIGN.md §32-73 与 CHANGELOG。',
          highlights: [
            '数据治理：互引按文档对去重、孤立节点清零、群落多数派重标、GRAPH-DATA.md 数据总表生成并入图',
            '星系五轮重构：2D 刻线 → 悬臂 → 曼陀罗 → 光晕 → 按参考站重制 → WebGL 刻线浑天仪（六万粒子双源差速 + 文档星逐帧烘焙 + 准星环）',
            '观测者手记：署名 + CAT.№ 仪器编号 + MY ASTERISM 关注星座 + 方案库/AXON1 分享码 + 回到上次观测',
            '决策中枢条（决策台×市场调查×情绪六源融合）：调查/资讯/上证/涨跌涨停/成交额/情绪分同条实时呈现（45s 轮询）+ DecisionHub 中心化取数接口',
            '看板通知中心接通 /api/automation/signals 真实信号（60s 轮询、refId 去重、实测 10 条）；对比面板开合兜底修复',
            '全局性能治理三层：星系空闲 8s→30fps 跳帧 + EMA 自适应降档 + 页面隐藏暂停；全局 CSS 动画空闲 10s 暂停；流动动画收敛为悬停态',
            '情绪页：数据源状态条（6 源自检）、dsFetch 会话级缓存取数层、死面板整节收起 + dense 回流、统一卡片视觉基线',
            '修复清单：图例双重绑定静默失效、byid→byId、GL 空 catch 吞错改堆栈输出、着色器缺逗号、平面滚轮死区（#figure 捕获层兜底缩放）、阶段现行期硬编码'
          ],
          files: ['static/plate-graph.html', 'static/dashboard.html', 'static/sentiment.html', 'static/js/decision-desk.js', 'static/js/sentiment.js', 'static/js/app.js', 'docs/30-data/GRAPH-DATA.md', 'docs/10-architecture/ARMILLARY-DESIGN.md']
        },
        {
          date: '2026-11-01',
          version: 'decision-desk v12 · 整体规划 v1',
          title: '决策台数据接通与面板重构 + 三系统闭环规划',
          summary: '决策台 14 个数据源统一管线并完成视觉重构；项目文档新增「整体规划 · 三系统闭环」，明确动态调查、六专家主脑、情绪六维与决策台的 I/O、依赖及 P0-P2 优先级。',
          highlights: [
            '决策台：逐接口超时与渐进渲染、断源保留 last-good、样本覆盖与 stale 状态可见，移除重复轮询与 422 请求',
            '修复 null → 0 数值语义；后端缺失指数返回 null，不再以 0.00 参与判断',
            '面板改为结论优先：市场脉冲、决策摘要、板块阶段结构、移动端两列脉冲与深浅色适配',
            '文档新增 12 张表 / 6 位专家契约 / 7 步讨论流程 / 情绪六维公式 / 三系统 I/O 与依赖图，源文件 docs/00-strategy/DECISION-CLOSED-LOOP-PLAN-2026Q4.md'
          ],
          files: ['static/js/decision-desk.js', 'static/css/decision-desk.css', 'server/routers/market_dashboard.py', 'static/js/project-docs-data.js', 'docs/00-strategy/DECISION-CLOSED-LOOP-PLAN-2026Q4.md']
        },
        {
          date: '2026-11-02',
          version: 'market-coverage v1 · brain-panel v1',
          title: '决策闭环 P0：全市场数据、情绪 v1 与六专家落地',
          summary: '共享 MarketPacket、全 A 股行情/估值/基本面、全球指数/国家/行业代理、全球宏观、sentiment.v1 与六专家规则研判全部接通；决策台升级为 17 数据源与 14 卡。',
          highlights: [
            '新增 /api/decision/snapshot|sentiment|sentiment/history|experts|coverage|universe|coverage/stream 七端点',
            '新浪 hs_a 全 A 股 5581 只报价（PE/PB/市值/换手）+ 东财 RPT_LICO_FN_CPD 5759 条季度基本面（ROE/营收/净利/EPS/毛利率）',
            '全球 17 指数 + 8 国家代理 ETF + 11 行业 ETF；US Treasury / NY Fed / ECB / World Bank 19 项全球宏观 + 中国 5 项',
            'sentiment.v1 六维权重 30/20/15/15/10/10，coverage<70% 不输出；历史落 data/sentiment_history.json',
            '六专家规则 v1：E1-E5 独立意见 + E6 数据审计，C/D 聚合与风控否决；主脑每 15 分钟调度',
            '决策台新增专家、覆盖、情绪三块面板，17/17 源接通；390px 零横向溢出'
          ],
          files: [
            'server/decision_snapshot.py', 'server/decision_experts.py', 'server/market_coverage.py',
            'server/routers/decision.py', 'server/market_survey_engine.py', 'server/brain.py',
            'server/main.py', 'data/decision_config.json', 'static/js/decision-desk.js',
            'static/css/decision-desk.css', 'tests/test_decision_loop.py'
          ]
        },
        {
          date: '2026-11-02',
          version: 'decision-desk v22 · brain-panel v3 · data-bus v1',
          title: '决策台分析深度升级 + 全局数据总线 + 缓存治理',
          summary: '补齐全市场扫描、板块轮动、资金流向三条分析深度线；专家团升级到 v3（联合研判 + 命中账本 + 否决复核）；新增进程内数据总线与缓存字节治理，决策台改为事件驱动实时同步。',
          highlights: [
            'market-scanner.v1：全 A 横截面扫描（涨幅 6 档分布 / 市值分化 / 换手 / 估值带）、128 个东财行业轮动（排名迁移 + 动量窗口）、资金流向（东财实测或显式代理）',
            '专家团 v3：E1 横截面、E2 全市场轮动、E3 行业主力资金；联合研判（joint_thesis + 跨域确认）；命中率账本；分歧否决按终局分歧复核',
            'data-bus.v1：coverage → packet → sentiment/scan → experts 资源依赖图、级联失效闸门、事件驱动 SSE、/api/decision/bus 链路观测',
            '缓存治理：800 条 / 64MB 双上限、单条超预算拒写、字节利用率与最大条目可观测（/api/decision/cache）',
            '决策台 14 → 17 卡、数据源 17 → 18、接口 7 → 11；脉冲条显示「实时 / 同快照 / 同窗口」'
          ],
          files: ['server/market_scanner.py', 'server/data_bus.py', 'server/cache.py', 'server/decision_experts.py', 'server/routers/decision.py', 'static/js/decision-desk.js', 'tests/test_data_bus.py']
        },
        {
          date: '2026-11-02',
          version: 'decision-hub v1 · market-merge v1',
          title: '三个工作区合一：决策台 = 主脑中心（事实 / 判断 / 决策）',
          summary: '大盘纵览、动态市场调查、决策台三个并列工作区读的是同一份行情、各算一个情绪分，卡片大量重复且同屏出现 83 与 80 两个分数。合并为一个中心的三层，靠 hub 条互斥切换，工作区入口 5 → 3；情绪口径统一到 sentiment-flow.v1，情绪页降级为兼容跳转页。',
          highlights: [
            '工作区导航 5 → 3（策略选股 / Qlib / 决策台），#market / #survey 保留为兼容别名并自动落到对应层',
            '三层结构：事实层（行情 / 广度 / 三锚点）→ 判断层（动态调查 + 结构↔情绪背离）→ 决策层（结论 / 专家 / 对证）',
            '情绪只留一个口径：gauge 与档位同取 sentiment-flow.v1；分档阈值 30/45/58/72 → 20/40/60/80 并与 decision_snapshot._status_of 对齐',
            '修掉真实冲突：market-dashboard.js 与 intelligent-analysis.js 都导出 window.switchWorkspace，后者覆盖前者，此前切换实际跑的是旧实现',
            '三锚点迁入（量能分档 / 上证 vs MA5 真实日线 / 统一情绪锚），6 档涨跌分布归位「市场广度」卡',
            '入口统一 9 处 → dashboard.html#market；sentiment.html 降级为 3 秒跳转并保留 ?sector= 透传'
          ],
          files: ['static/dashboard.html', 'static/js/market-dashboard.js', 'static/js/intelligent-analysis.js', 'static/css/decision-desk.css', 'static/sentiment.html', 'server/sentiment_tracker.py', 'docs/10-architecture/MARKET-SENTIMENT-MERGE.md', 'docs/10-architecture/DECISION-HUB.md']
        },
        {
          date: '2026-11-02',
          version: 'layer-crosscheck v1 · brain-config v1',
          title: '主脑三层对证 + 阈值集中配置',
          summary: '三层看同一个市场却各算各的：判断层把结构分标成「情绪分」显示 100（事实层同一时刻 80.5），涨停 145 vs 12 是两种口径却直接相减，阈值散落在 8 个模块里改一处漏一处 —— 且没有任何机制能发现某一层算错了。新增集中配置与九项对证引擎，把「不一致」变成可判定、可展示、可降置信的结构化结论。',
          highlights: [
            'brain-config.v1：容差 / 分档 / 时点上限 / 板块体系集中一份 JSON，mtime 热重载、深合并默认值、validate() 越界回落并报告 issues',
            'layer-crosscheck.v1：九项检查八类判据（情绪同源项分查事实↔判断、事实↔决策），每项给 a / b / delta / tolerance / status / note',
            '状态分五档：pass / warn（2× 容差内）/ conflict / incomparable（异量纲、异体系、异口径）/ unavailable，后两者不参与打分，避免降级伪装成不一致',
            '口径不同时挂 scope_mismatch 阻塞并把宽度类改判 incomparable，不再报一堆假冲突；板块体系（东财 vs 申万）登记后判不可比',
            '判断层 payload 增 facts（涨停 / 涨跌家数 / 成交额 / 口径 / 时点），对证比的是「判断层真正用到的输入」而非二手汇总',
            '接进闭环：run_cycle 增 crosscheck 步骤，冲突时备忘录置信 ×0.85 并写入风险；GET /api/brain/alignment 的 ok 只表示接口成功、业务结论放 healthy',
            '决策层顶部常驻「三层对证」条：逐项 chip + 「重新对证」，页面走 light 缓存、闭环走 full，互不干扰'
          ],
          files: ['server/brain_config.py', 'server/layer_crosscheck.py', 'data/brain_config.json', 'server/brain.py', 'server/brain_capabilities.py', 'server/routers/brain.py', 'server/market_survey_engine.py', 'static/js/decision-desk.js', 'tests/test_layer_crosscheck.py', 'tests/test_brain_config.py', 'docs/10-architecture/LAYER-CROSSCHECK.md']
        },
        {
          date: '2026-11-02',
          version: 'decision-desk-arch v1 · perf-budget v1 · config-admin v1',
          title: '决策台架构规范落地：总线记账统一、配置可升级回滚、延迟预算可判',
          summary: '把「三层协同」从口头约定变成可验收的架构：总线 publish 统一记账（此前五个发布点各抄一遍、三处漏耗时）、survey 补上向总线报到（能力图 ok 4→13）、缓存键只声明一次；新增配置升级/回滚工具与只读配置接口；新增延迟预算脚本（热态 p95 16–32ms、8 并发 276 req/s 零错误）；输出架构规范 A1–A8 验收标准。',
          highlights: [
            'data-bus：publish 统一记账（时间/耗时/次数/trace），新鲜度取「总线时间戳与缓存条目中较新者」，未上报耗时返回 null 而非 0.0',
            '真实缺陷：survey 从不向总线报到 → 链路里永远 stale、能力图把它算 degraded（ok 4/15）；修复后 13 ok / 2 degraded / 0 blocked',
            '配置治理：validate_payload 纯函数（命令行与运行期同一套判据）+ scripts/brain_config_admin.py（预检不通过零落盘、原子写入、sha256 留痕、可回滚）+ GET /api/brain/config 只读',
            '性能可判：scripts/verify/perf_budget.py 10 条读路径 p95 预算 + 并发压测 + --gate；实测热态 p95 ≤32ms、8 并发 276 req/s 错误率 0、冷启首聚合 3.08s',
            '接口文档去陈：artifacts/openapi.json 快照过期导致 API-REFERENCE 少 44 个端点（141→185），重下快照后重生成'
          ],
          files: ['server/data_bus.py', 'server/brain_config.py', 'server/market_survey_engine.py', 'server/routers/brain.py', 'scripts/brain_config_admin.py', 'scripts/verify/perf_budget.py', 'tests/test_brain_config_admin.py', 'tests/test_data_bus.py', 'docs/10-architecture/A-SHARE-REALTIME-DECISION-DESK.md', 'docs/40-ops/API-REFERENCE.md']
        },
        {
          date: '2026-11-02',
          version: 'layer-sync v1 · lineage v1 · market-coverage-bj v1',
          title: '全 A 股分层同步契约 + 结论溯源（沪深京全覆盖）',
          summary: '把「事实→判断→决策」的同步内容、字段规范、频率与传递方式写成契约并落到代码：事实层补北交所直连源、修正 920 段代码前缀与板块涨跌停阈值；新增 lineage.v1，让每条结论都能回放到上游依据（8 节点 + 字段指纹 + 同快照校验）；闭环钉住快照，杜绝一轮决策挂两三代 trace。',
          highlights: [
            '全市场：5782 只（沪 2362 / 深 2944 / 京 476），有效报价 5581；新增东财北交所源 363 只，补新浪未收录的 9 只',
            '修数据完整性：920 段被误判为沪市，同一只票同时记成 sh920xxx 与 bj920xxx（标的池虚增 355）→ 修正后 6127→5782',
            '修涨跌停口径：北交所 30%（含 ST）、双创 ST 仍 20%、主板 ST 5%；涨停 145→141、跌停 23→19',
            '修 /api/decision/universe 长期空返回：_LAST_ROWS 重新赋值导致三处 from-import 拿到旧对象；改为就地更新，排序不丢缺字段行',
            '新增 lineage.v1：coverage→packet→sentiment→flow→survey→experts→crosscheck→memo 八节点，节点带 version/as_of/built_at/digest/facts',
            '新增 GET /api/brain/lineage/{trace_id} 与 /recent；结论自带 citations（含 same_trace），依据换版立刻可查',
            '闭环 pin_packet 钉住快照（force 不得越过），实测 trace mismatch 从 3 个节点降到 0',
            '调查层补齐契约三件套（version/as_of/trace_id），此前溯源表只能填 null'
          ],
          files: ['server/market_coverage_bj.py', 'server/market_coverage_fetch.py', 'server/market_coverage_payload.py', 'server/decision_snapshot.py', 'server/lineage.py', 'server/brain.py', 'server/market_survey_engine.py', 'server/routers/brain.py', 'tests/test_lineage.py', 'tests/test_snapshot_pin.py', 'docs/10-architecture/LAYER-SYNC-CONTRACT.md']
        },
        {
          date: '2026-11-02',
          version: 'system-audit v1 · cache-fix v1 · security-hardening v1',
          title: '系统调试与修复：缓存账实 / 回落 / 并发 + Host 校验与安全头',
          summary: '按「先体检再修」的方式做了一轮全面调试：新增 34 项系统体检脚本，据此定位并修复缓存四处缺陷（过期不扣字节、超预算删旧值、异常不回落、并发无锁）与三项安全加固（Host 头校验防 DNS 重绑定、基础安全响应头、用户手机号脱敏）。',
          highlights: [
            '缓存账实：过期条目取走时漏扣字节 → 统一 _drop()；新增 stats().recomputed_bytes 让账实一致可观测（实测 2024764 B 完全相等）',
            '缓存安全：超预算写入不再删除上一份好数据；上游抛异常时按契约回落 stale 旧值而不是 500',
            '缓存并发：加 RLock，iter_entries 改快照迭代；8 线程混合读写删测试结束账实一致',
            '安全：TrustedHostMiddleware（伪造 Host → 400）、nosniff / X-Frame-Options / Referrer-Policy / Permissions-Policy',
            '隐私：/api/admin/users 列表手机号脱敏',
            '体检脚本：服务 / 配置 / 上游 5 源 / 缓存 / 存储 SQLite / 8 端点信封 / 安全，共 34 项，--gate 可做发布闸门'
          ],
          files: ['server/cache.py', 'server/main.py', 'server/routers/admin_core.py', 'scripts/verify/system_audit.py', 'tests/test_cache_audit.py', 'docs/40-ops/DEBUG-AUDIT-2026-11-02.md']
        },
        {
          date: '2026-11-03',
          version: 'market-coverage-gapfill v1 · expert-survey-link v1',
          title: '事实层补齐全 A 股 + 判断层真正进入研判团',
          summary: '补上两个实质缺口：事实层报价从「新浪单源」改为三层兜底（新浪 → 东财北交所 → 腾讯批量补缺），覆盖 5668/5782 = 98%（沪深各 100%）；研判团从只吃事实层改为消费判断层结论，背离时禁止进攻、判断层缺失直接否决可执行，共识输出 survey_alignment 并可回溯。',
          highlights: [
            '新增腾讯批量补缺（60 只/批、4 并发、GBK 解码），只补无价格的代码、不覆盖主源，避免口径漂移',
            '补不到的 114 只保持 null 并列进 unresolved（退市/停牌/代码失效），不拿估值反推价格',
            'E5 读判断层背离 → 禁止进攻 + 仓位 ≤35%；E6 判断层缺失 → 否决可执行',
            '共识新增 consensus.survey_alignment 与 inputs.survey；专家团缓存键加入 survey.as_of，判断层刷新即重算',
            '闭环实测：defend / cap 20 / divergent，lineage 7 条 citations 全 same_trace'
          ],
          files: ['server/market_coverage_gapfill.py', 'server/market_coverage_payload.py', 'server/decision_experts_global.py', 'server/decision_experts_aggregate.py', 'server/brain.py', 'server/lineage.py', 'tests/test_expert_survey_integration.py', 'docs/10-architecture/LAYER-SYNC-CONTRACT.md']
        },
        {
          date: '2026-11-03',
          version: 'decision-metrics v1 · decision-routing v1',
          title: '多市场指标统一接入 + 规则化决策分流闸门',
          summary: '趋势/资金流/买手率/情绪收敛成同一尺度的 8 项指标（score∈[-1,1]、带置信与时点），用配置化规则算出仓位上限闸门：6 条路由按优先级先保护后进攻，缺数据走观察兜底，只收紧不放大。',
          highlights: [
            '新增买手率：腾讯外盘/内盘，成交额前 120 只 + ETF 篮子，60 只/批 4 并发，GBK 解码',
            '8 项指标统一结构（source/fields/as_of/age/ttl/stale/coverage/confidence/fallback），缺失不填 0',
            '6 条路由：risk_off 100 > buy_squeeze 90 > risk_on 80 > trend_follow 70 > index_split 60 > neutral 10',
            '降级：可用 <4 或覆盖 <0.5 → observe 且仓位上限 0，并写明原因',
            '闭环接入：cap=min(cap,route.cap_max)，defend 时进攻降级中性；/api/decision/{metrics,routing} 可查',
            '实测：7/8 可用（北向未披露），买手率 47.97%，路由 index_breadth_split cap=35'
          ],
          files: ['server/decision_metrics.py', 'server/decision_routing.py', 'data/decision_routing.json', 'server/brain_capabilities.py', 'server/brain.py', 'server/routers/decision.py', 'tests/test_decision_routing.py', 'docs/10-architecture/DECISION-ROUTING.md']
        },
        {
          date: '2026-11-03',
          version: 'board-review v1 · statement-parse v1',
          title: '打板复盘工具链：交割单 → 回合 → 日报/台账/看板',
          summary: '把交易者成交流水变成逐日打板复盘：列名自动识别→FIFO 回合匹配→费用分摊→止损止盈执行判定，一次输出 Markdown 日报、Excel 台账与自包含 HTML 看板。',
          highlights: [
            '解析器兼容券商/同花顺导出：表头自动定位、列名别名映射、代码补前缀、缺费用按市场规则估算并标注',
            'FIFO 配对 + 买入费用按数量摊到各批次；未平仓只计持仓不计盈亏',
            '离场四类判定：止损触发 / 止盈触发 / 区间离场（计划未触发）/ 无计划离场；计划执行率可量化',
            '风格统计：板型、入场时段、单笔金额分位、同标的加仓、离场类型、胜率盈亏比与单笔期望',
            '示例数据跑通：4 回合、胜率 75%、净盈亏 +96,947；HTML 看板 0 控制台错误'
          ],
          files: ['scripts/review/parse_statement.py', 'scripts/review/build_review.py', 'tests/test_review_engine.py', 'docs/40-ops/BOARD-REVIEW.md']
        },
        {
          date: '2026-11-03',
          version: 'lhb-review v1 · lhb-fetch v1',
          title: '龙虎榜复盘：席位筛选 + 五维归因 + 封板时刻级推断',
          summary: '收盘后自动把营业部级买卖明细拉下来，筛掉噪声席位，对每条大单回答「为什么买 / 为什么卖 / 为什么是这个价」，并把「事实」与「推断」分级标注：能拿到涨停封板时间就用封板时刻推断，拿不到退回日级，绝不假装精确到分时。',
          highlights: [
            '席位五分类（机构/北向/散户/游资/其他）：散户关键词排在游资名单之前，否则拉萨天团会被当成优质游资混进复盘',
            '五维归因：题材只算板块效应、情绪拆「环境打折 0.55 + 龙头效应」、博弈看方向而非同榜、消息按距复盘日衰减、技术读涨停池连板与炸板次数',
            '情绪打折的必要性：不打折时 125 家涨停的一天把 35 条记录全归成「市场情绪」，归因失去意义',
            '推断分级：inference=seal-time（23/35，用封板时间）/ day-level；个股无特异证据时 mood_only=true 且 confidence≤0.45',
            '修 5 个真实缺陷：config 浅合并吞掉新增默认键、公告窗口用 now（未来函数）、涨停池用 T+1 广度、TOTAL_BUYRIO 小数当百分数、920 段判成沪市',
            '收盘后自动触发（15:35–23:00 每 30 分钟幂等覆盖）+ 两道止损闸门（19:00 后不再重跑、过配置的 retry_window 19:30 仍无数据才收工）',
            '实测 2026-11-02：35 条 / 25 只票，情绪 15 题材 9 技术 8 博弈 3，重复 0，耗时 4.7s'
          ],
          files: ['server/lhb_fetch.py', 'server/lhb_review.py', 'data/lhb_config.json', 'server/routers/decision.py', 'server/brain.py', 'tests/test_lhb_review.py', 'docs/10-architecture/LHB-REVIEW.md']
        },
        {
          date: '2026-11-03',
          version: 'lhb-insight v1 · 龙虎榜复盘页',
          title: '龙虎榜复盘：聚合三视角 + 展示页 + 三处入口',
          summary: '把 35 条平铺记录重排成标的 / 席位 / 题材三个视角（新增合力判定与封板质量标签），并做出完整展示页：一句话结论 → KPI → 市场底座 → 关键发现 → 动因分布 → 标的表 → 逐笔明细（可展开归因）→ 席位 → 题材，三处入口随时可进。',
          highlights: [
            '标的视角：alignment 按净买额占买卖总额划分（≥80% 合力做多 / ≤20% 合力出货 / 其余对峙），不用「买入选手数」因为金额才是方向',
            '席位视角：机构专用/北向资金标为「通道汇总」且不算集中度——那是几十上百个账户，当一个人算是误读；沪股通与深股通按类型合并成一行',
            '主因按 |净额| 加权而非条数投票，避免大量小额记录淹没主要矛盾',
            '展示页 10 个区块，逐笔明细可筛选（席位/方向/主因/搜索）并展开五维分数、证据、价位时机、卖出触发与完整推演逻辑',
            '筛选即钻取：点主因条 / 标的行 / 要点卡片都把明细收敛到子集，不跳页',
            '三处入口：左侧栏「📊 龙虎榜复盘」、决策层摘要卡（不进 60s 轮询，100KB 报告只拉一次）、直接 /lhb-review.html',
            '修阻断级缺陷：dashboard.css 的 html,body{height:100%;overflow:hidden} 让新页完全无法滚动，首屏以下 3 个面板永远看不到，而 Playwright 自动滚元素进视野导致交互测试全过',
            '修：席位列显示内部枚举 institution/northbound、动因条缺主因名称、归因文案英文泄漏'
          ],
          files: ['server/lhb_insight.py', 'server/lhb_review.py', 'static/lhb-review.html', 'static/js/lhb-review.js', 'static/css/lhb-review.css', 'static/js/decision-desk.js', 'static/dashboard.html', 'tests/test_lhb_insight.py', 'docs/10-architecture/LHB-REVIEW.md']
        }
      ]
    },

    /* =====================================================================
     * issues —— F. 遇到的问题及解决方案
     * ===================================================================*/
    issues: {
      title: '遇到的问题及解决方案',
      intro: '按「现象 → 根因 → 解决方案」记录。这些坑多数会在后续会话重复出现，优先阅读。',
      headers: ['ID', '日期', '问题', '根因 / 现象', '解决方案', '状态'],
      items: [
        {
          id: 'I-01',
          date: '2026-10-07',
          title: 'DeepSeek 旧 key 账户 402 欠费导致 AI 决策卡不可用',
          symptom: 'AI 决策卡长期空白；server.log 大量 Insufficient Balance；/api/analyze 1.8s 返回但无 ai_decision。',
          rootCause: '.env 中的旧 key（sk-7420…ffd）账户已 402 欠费；analyze.py 在 llm.api_key 为空时静默跳过 AI 调用，前端只看到「研判 X」。',
          solution: '换用有效 key（deepseek-flash 账户）并重启服务；同时给 analyze.py 加异常 K 线条目防御过滤，并规定探测股票代码必须带 sh/sz 前缀（裸 603501 会走「数据不足」stub）。实测 sh600519/sh688126/sz002185/sh603501 均返回 ai_decision。',
          status: '已解决'
        },
        {
          id: 'I-02',
          date: '2026-08-04',
          title: 'agents.py 缺 import asyncio 导致 Agent 启动 NameError',
          symptom: 'Agent 对话接口启动即抛 NameError。',
          rootCause: 'server/routers/agents.py 使用了 asyncio 但未 import；该路由注册到 main.py 后暴露出错。',
          solution: '补齐 import asyncio（QA 阶段发现并修复）。',
          status: '已解决'
        },
        {
          id: 'I-03',
          date: '2026-08-27',
          title: 'westock-data CLI 路径找不到',
          symptom: '日志出现 CLI not found，依赖 CLI 的板块排行 / 龙虎榜 / 研报数据全部取不到。',
          rootCause: 'Node.js CLI 的绝对路径在环境迁移后失效（可用 Node 实际路径为 ~/.workbuddy\\binaries\\node\\versions\\22.22.2\\node.exe）。',
          solution: '已定位；路径需按当前环境校正后重新指向。历史日志显示该问题在 2026-08-27 会话中被暴露并列入系统问题清单。',
          status: '待补充（需核对当前 westock.py 中的实际路径配置）'
        },
        {
          id: 'I-04',
          date: 'v5.4（2026-07-07）',
          title: '板块资金流 API 常返回空',
          symptom: '/api/fund-flow/sector 经常返回空数据，板块分析的资金流区域无内容。',
          rootCause: '上游数据源不稳定，该端点无有效返回。属已知限制而非代码 bug。',
          solution: '前端实现本地估算 fallback：当接口为空时按成分股涨跌估算资金流向并展示。',
          status: '已知限制（有兜底）'
        },
        {
          id: 'I-05',
          date: 'v5.4（2026-07-07）',
          title: '/api/sector-stocks 字段不全',
          symptom: '板块成分股弹层缺少涨跌幅、换手率等列。',
          rootCause: '该接口仅返回 {code, name, priority}，上游未提供更多字段。',
          solution: '列为已知限制；前端能不显示就不显示，避免伪造数值。',
          status: '已知限制'
        },
        {
          id: 'I-06',
          date: '2026-06-03',
          title: '扫描轮询超时 2 分钟不够',
          symptom: '全市场扫描（200+ 只）经常在 2 分钟时被前端放弃，结果丢失。',
          rootCause: '扫描耗时随行情源响应波动，2 分钟阈值过紧。',
          solution: '轮询超时从 2 分钟延长至 3 分钟（CHANGELOG 记录）。',
          status: '已解决'
        },
        {
          id: 'I-07',
          date: '2026-10-06',
          title: 'AkShare spot 抓取失败 RemoteDisconnected',
          symptom: 'logs/server.log 反复出现 AkShare spot fetch failed: … ProxyError … RemoteDisconnected(Remote end closed connection without response)。',
          rootCause: '系统代理（127.0.0.1:60430）拦截本机请求，NO_PROXY 为空，请求经代理后被断开。',
          solution: 'ak_market.py 对每个数据段做逐项降级（真实值缺失即回退 fallback 并标注 source），宏观指标区分 realtime / partial / fallback，新闻源有新浪备用通道；测本机服务时 curl 必须加 --noproxy "*" 绕过系统代理，否则会得到 502 假阳性。',
          status: '已知限制（有降级兜底）'
        },
        {
          id: 'I-08',
          date: 'v5.4 起约定',
          title: '静态资源缓存导致改动不生效',
          symptom: '改了 JS/CSS 但浏览器仍执行旧代码，或页面样式没更新。',
          rootCause: '浏览器缓存 + NoCacheHTMLMiddleware 对 JS/CSS 使用 no-cache，版本号未变即复用旧资源。',
          solution: '确立 ?v=N 版本号约定：dashboard.html 中统一声明并递增；HANDOFF 第 8 节明确「版本号务必递增，避免浏览器缓存旧代码」。注意 dashboard.html 历史上曾被外部进程回滚版本号（v16→v15），改完要立即 grep 验证。',
          status: '已解决（约定固化）'
        },
        {
          id: 'I-09',
          date: '2026-10-10',
          title: '开机后网页打不开：看门狗写死的 venv 解释器路径已不存在',
          symptom: '开机后 http://localhost:8900/dashboard.html 无法访问。',
          rootCause: 'scripts/server_watchdog.py 里写死的解释器 ~/.workbuddy\\binaries\\python\\envs\\default\\Scripts\\python.exe 已不存在（envs 目录被清理），看门狗启动 uvicorn 失败。',
          solution: '5 个启动入口（scripts/server_watchdog.py、start_server.py、start.bat、start_silent.bat、start-dapeng.vbs）改为解释器自动探测：按候选列表（项目 .venv → workbuddy envs/default → sys.executable → 系统 Python 3.11 → versions\\*）逐个实跑验证 python -c "import fastapi, uvicorn"，选用第一个可用者；全部失败则记录清晰错误并退出。实测 start-dapeng.vbs 拉起后 /api/health 返回 ok、/dashboard.html 200。',
          status: '已修复（2026-10-11）'
        },
        {
          id: 'I-10',
          date: '2026-10-07 ~ 10-10',
          title: 'watchdog 反复以 0xC000013A 退出',
          symptom: 'logs/watchdog.log 中 uvicorn 反复以 3221225786（0xC000013A，CTRL_CLOSE_EXIT）退出。',
          rootCause: '子进程未加 CREATE_NO_WINDOW，会弹出控制台窗口；窗口被关闭即等价于向进程发 CTRL_CLOSE，导致退出。',
          solution: '已修复（2026-10-11）：_start_child 的 Popen 增加 creationflags = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP（仅 Windows）；同时以原子单实例（独占守卫端口 8899 的 socket bind）替换 TOCTOU 端口探测，8900 端口探测降级为辅助日志。',
          status: '已修复'
        },
        {
          id: 'I-11',
          date: '2026-10-07',
          title: '网站崩溃 502（dashboard 每次打开都「崩溃」）',
          symptom: '用户每次打开 dashboard 看到 502 崩溃页。',
          rootCause: '三重叠加：① uvicorn 此前作为 WorkBuddy 会话的临时后台任务，会话结束即被回收（无独立存活能力）；② 系统代理 127.0.0.1:60430 拦截 localhost（NO_PROXY 为空），后端一挂用户就看到代理的 502 页；③ Startup 里旧自启脚本每次开机先 taskkill 8900 再拉起无守护 uvicorn。',
          solution: '交付 scripts/server_watchdog.py（崩溃退避重启 5s→60s 封顶、稳定 120s 重置、双重端口探测单实例、SIGTERM 优雅退出、日志滚动）、start-dapeng.vbs（pythonw 无窗体独立启动并放入 Startup）、stop.bat（特征匹配 + netstat 反查兜底）；旧 dapeng-alpha.vbs 改名 .bak 停用（可逆）。QA 两轮回归全 PASS：崩溃自愈 7 秒、15 个静态资源全 200、trading.db/.env/server/ 零污染。',
          status: '已解决'
        },
        {
          id: 'I-12',
          date: '2026-10-08',
          title: 'chart_analysis 路由双重前缀导致 404',
          symptom: '前端调用 /api/analyze/chart-summary 返回 404。',
          rootCause: 'server/main.py 中 chart_analysis 路由 include 时 prefix="/api"，而路由自身已带 /api/analyze → 实际注册为 /api/api/analyze/chart-summary。',
          solution: '去掉 include 时重复的 prefix，端点恢复 200。',
          status: '已解决'
        },
        {
          id: 'I-13',
          date: '2026-08-04',
          title: 'dashboard 加载卡在 78%「计算策略评分…」',
          symptom: '加载遮罩永不消失，dashboard 无法使用。',
          rootCause: 'strategies-ui.js 第 197 行引用未定义的 combinedScore → ReferenceError 在 app.js init() Stage 5 抛出 → loader.hide() 永不执行。',
          solution: '在 tacticScores 数组定义后补 combinedScore = 各战法得分均值；并给 init() 加 try-catch-finally + 12 秒绝对超时兜底，loader 不会再卡死。',
          status: '已解决'
        },
        {
          id: 'I-14',
          date: '2026-08-05 / 08-21',
          title: '旧 uvicorn 进程导致新增路由 404',
          symptom: '/api/global、SSE、/api/admin/* 全部 404，但 /api/quotes、/api/scan 正常。',
          rootCause: '运行的 uvicorn 进程启动于这些 router 写盘之前且从未重启，内存里没有相应路由；StaticFiles 静态页能更新但 API 路由不会热加载。',
          solution: '先 netstat / ps -W 查 8900 占用 → taskkill 旧 PID → 重启 uvicorn。教训固化为约定：每次新增/修改 router 后必须重启 uvicorn。',
          status: '已解决（约定固化）'
        },
        {
          id: 'I-15',
          date: '2026-09-10',
          title: 'dashboard.html 被外部进程回滚，丢失 DOM 与版本号',
          symptom: '排序下拉 DOM（sort-trigger / sort-menu）与直方图容器消失，版本号从 css?v=16 退回 v=11 / js?v=10。',
          rootCause: 'dashboard.html 被外部工具/会话改动（2026-08-28 00:54）。',
          solution: '补回排序下拉与直方图容器 HTML，版本号 bump 到 css?v=18 / js?v=15。约定：后续会话改版前先核对版本号与关键 DOM 是否存在。',
          status: '已解决（有复发风险）'
        },
        {
          id: 'I-16',
          date: '2026-10-06',
          title: '移动端 K 线卡片塌缩到 20px',
          symptom: '≤768px 视口下 K 线卡片高度只有 20px（等于 padding），图表不可见。',
          rootCause: '三层叠加：① sizeProToCard() 动态预算在内容驱动卡片中自锁到 min 120px；② #kline-card{flex:1} 在无限高 flex 列中塌缩；③ 真正主因 —— #right-panel{flex:1 1 0;height:100%} 在移动端被压成 0 高。',
          solution: 'JS 移动端分支改固定 300px（横屏矮屏 200px）；ui-refinement.css 末尾（最后加载）追加 @media(max-width:768px){#right-panel{flex:0 0 auto;height:auto;min-height:0}}。实测 390px → rpH=2062 / proH=300；1440px 回归不变。',
          status: '已解决'
        },
        {
          id: 'I-17',
          date: '2026-10-06',
          title: 'AI 大屏浮层几何错位（左侧贴边、右侧漏出底页 320px）',
          symptom: '所有 .full-overlay-panel（含板块分析）在 inset:0 下左侧贴边、右侧未铺满。',
          rootCause: 'ui-refinement.css 对 .full-overlay-panel 设了 max-width:min(1120px,100vw-24px)，与 inset:0 过约束时 right 被忽略。',
          solution: 'v32 给 .full-overlay-panel 加 margin:auto 居中；AI 大屏用 ID 特异度 (#ai-fullscreen-panel) 覆盖为 max-width:none / width:100% / height:100% 实现真全屏。',
          status: '已解决'
        },
        {
          id: 'I-18',
          date: '2026-10-07',
          title: 'M3 深拷贝 bug：嵌套配置字段修改不落库',
          symptom: 'update_config 修改 trigger / email 等嵌套字段后持久化未生效（85→80 往返不一致）。',
          rootCause: 'update_config 使用浅引用，导致嵌套 dict 修改未写回。',
          solution: '改用 copy.deepcopy 合并配置；重启后验收嵌套字段往返持久化 OK。',
          status: '已解决'
        },
        {
          id: 'I-19',
          date: '2026-10-07',
          title: 'review 只增不减导致「0 ≥ 0」平凡通过放行噪声调参',
          symptom: '调参提案在没有足够样本外数据时也可能被判定为「有改善」。',
          rootCause: 'OOS 样本不足 3 笔时，0 ≥ 0 的平凡比较会通过。',
          solution: '提案 OOS 不足 3 笔时，自动改用更严格的全样本门槛（要求 +5% 边际改善才算数）。端到端测试验证：真改善自动应用、+1.1% 噪声改善被 rejected_auto。',
          status: '已解决'
        },
        {
          id: 'I-20',
          date: '2026-10-13',
          title: '北向资金接口恒定 404 —— 路由模块写了却从未挂载',
          symptom: '前端 JS 长期请求 /api/market/north，服务端恒定 404；北向资金条完全不显示。',
          rootCause: '双重原因：其一 server/routers/market.py 从未在 main.py 的 include_router 清单中注册；其二该模块 `from server.ak_market import ak_market` 而该单例并不存在（ak_market.py 实际只导出 ak_hub），因此一旦挂载就会 ImportError —— 这解释了它为何长期被搁置。',
          solution: '重写 market.py 改用真实的 ak_hub，并在 main.py 注册 app.include_router(market_router.router, prefix="/api/market")。命名冲突带来的隐性代价在此暴露：一个不存在的 import 会让整个路由模块变成死文件。',
          status: '已解决'
        },
        {
          id: 'I-21',
          date: '2026-10-13',
          title: '板块资金流是结构性死接口，且用 ok:true 掩盖失败',
          symptom: '/api/fund-flow/sector?sector=xxx 恒定返回 {"ok":true,"data":[]}，板块资金流区域永远空白；不带 sector 参数则 422。',
          rootCause: '该接口委托 westock CLI，而 westock CLI 已按设计永久不可用（skill 迁移到 MCP）。每次调用都返回 None → 被聚合为空数组 → 外层仍包 ok:true，把「取数失败」伪装成「无数据」，前端无法区分也无法诊断。',
          solution: '改用东方财富 ulist.np 批量接口（与既有 get_market_spot 同一鉴权串），新增 AkShareDataHub.get_stock_fund_flow()。返回结构设计为超集同时满足两个既有消费方（app.js 需顶层 super_large/large/main，sentiment.js 需 summary.*）。失败时返回 ok:false，不再用空数组掩盖。',
          status: '已解决'
        },
        {
          id: 'I-22',
          date: '2026-10-13',
          title: '定义了却从未被调用的死代码掩盖了断链',
          symptom: '北向资金 404 长期无人察觉，因为页面上从来看不到任何北向内容。',
          rootCause: 'updateNorthFlow() 在 app.js 中完整实现，但全项目没有任何调用点（死代码）。消费方不存在，所以数据源的故障永远不会暴露。',
          solution: '接入首屏与 30 秒定时刷新。教训：排查「数据不显示」时，先确认消费方是否真的会被调用，再怀疑数据源。',
          status: '已解决'
        },
        {
          id: 'I-23',
          date: '2026-10-13',
          title: '北向 ticker 条目被行情刷新覆盖',
          symptom: '北向条目插入后只闪现几秒就消失。',
          rootCause: 'loadQuotes 每次行情刷新都会调用 updateTickerData() → renderTickerTape()，后者用 track.innerHTML = ... 整体重写轨道，把北向条目一并抹掉。此前加的「幂等移除再插入」只防住了累积，没有防住覆盖。',
          solution: '新增 _northTickerHtml 缓存 + applyNorthTicker()，renderTickerTape() 重建轨道后自动恢复北向条目。实测重建后仍 5 条、再重建两次仍 5 条（既不丢也不累积）。教训：自检不能只问「我会不会重复」，还要问「谁会覆盖我」。',
          status: '已解决'
        },
        {
          id: 'I-24',
          date: '2026-10-13',
          title: 'flex 纵向容器把面板压成细条',
          symptom: 'survey 视图的 survey-hero / AI 主脑 / 宏观面板高度只有 30px，内容被裁掉；qlib 动态因子面板被压到 137px（内容需 544px）。',
          rootCause: '两个视图是 display:flex; flex-direction:column，其子项默认 flex-shrink:1。内容总高超出容器时，flex 会压缩子项而不是让容器滚动，于是面板被压扁。',
          solution: '在两视图上显式声明 `#qlib-dashboard-view > *, #survey-dashboard-view > * { flex: 0 0 auto }`，改由容器滚动。修复后 survey 内容高度由被压扁的 983px 恢复为正常的 1855px。该现象极易被误判为截图或画质问题。',
          status: '已解决'
        },
        {
          id: 'I-25',
          date: '2026-10-13',
          title: '主脑情报间歇性退化为空壳',
          symptom: 'AI 主脑的「影响渠道 / 全球快照 / 命中新闻」三块内容间歇性全部消失，但 AI 研判文本仍在。',
          rootCause: 'scan_intel() 对新闻与全球资产取数使用 return_exceptions=True，任一失败时 headlines / global_snapshot 静默退化为空；scan_and_persist() 随后无条件覆盖 brain_intel，把上一次的富情报整体顶掉。',
          solution: '在 scan_and_persist 加防退化护栏：新扫描为空、上次非空的 global_snapshot / headlines / channels 沿用上次值并记录 carried_forward + carried_from_ts；brief / risk 等结论性字段始终用本次结果。前端对该标记显示「本次扫描取数不全，以下沿用上次扫描（时间）：字段名」。单元测试双向验证：退化输入正确沿用、正常输入零干预。',
          status: '已解决'
        },
        {
          id: 'I-26',
          date: '2026-10-13',
          title: '粒子图节点堆叠不可读（分布极度偏斜）',
          symptom: '线性横轴下约 90% 的板块节点挤在最左侧、标签相互遮挡，粒子流几乎看不清。',
          rootCause: '板块净流入分布极度偏斜：实测中位数 8.23 亿，而头部可达 158 亿，比值接近 20:1。线性轴下绝大多数节点必然堆积。',
          solution: '横轴改为带符号对数刻度 sign(f)·log10(1+|f|)，轴标签用逆变换 sign(v)·(10^|v|−1) 还原为真实亿元值并注明「对数刻度」；同时加 labelLayout.hideOverlap 自动避让标签、dataZoom 支持滚轮放大低值区。节点分布空间占比由约 13% 提升到 35.6%（约 2.7 倍）。',
          status: '已解决'
        },
        {
          id: 'I-27',
          date: '2026-10-13',
          title: '退化行情下粒子流全部消失（0 条粒子）',
          symptom: '盘后某些时刻「板块资金迁移粒子图」只剩下节点，一条粒子流都画不出来，且没有任何提示，看起来像功能坏了。',
          rootCause: '分组用「净流入 < 中位数 / ≥ 中位数」划分强弱两半。当快照里 24 个板块的 net_flow 全部相同（实测盘后同一快照均为 12.5 亿）时，低于中位数的集合为空，pairN = min(8, 0, 24) = 0，循环一次都不执行。',
          solution: '改为「按净流入排序后的上下半区」分组，彻底摆脱中位数并列退化；并加入退化检测 (flowMax − flowMin) < max(0.01, |flowMax|×0.5%)，真退化时如实给出警示「本轮板块净流入无差异…故不绘制粒子流」，而不是静默空白。',
          status: '已解决'
        },
        {
          id: 'I-28',
          date: '2026-10-13',
          title: '粒子元素显示不全：曲线弧顶被裁 + 节点互相遮挡',
          symptom: '部分粒子线画到一半突然消失（弧顶被切）；退化行情下 24 个节点叠成一个球，只看得见最上面一个。',
          rootCause: '两个独立原因：① 曲线用 curveness 外扩贝塞尔控制点，而序列默认 clip:true，超出 grid 的弧顶被裁掉；② 多个板块落在同一（净流入, 涨跌幅）坐标上，散点完全重合。',
          solution: '① 全部粒子序列设 clip:false，curveness 由 0.32 收敛到 0.22，并按数据实测值给坐标轴留白；② 新增像素距离聚类 + 环形错开（symbolOffset，只改渲染位置不改数据值）+ 斥力松弛迭代，保证任意两节点中心距 ≥ 1.02×半径和；③ 视口尺寸变化时按新宽度重跑去重叠（原先只 resize 不重算，桌面↔移动端切换后偏移失配）。',
          status: '已解决'
        },
        {
          id: 'I-29',
          date: '2026-10-14',
          title: '接口信封谎报数据来源（真实缺陷：降级数据被伪装成实时行情）',
          symptom: '板块排行接口返回的数据被标注为"东方财富行业板块实时行情"，但 24 个板块的 chg/amount/turnover/net_flow 全部相同（chg 全=1.85、amount 全=284 亿、net_flow 全=12.5 亿）。',
          rootCause: 'stable_market_provider.py 的三个取数方法在真实请求失败后返回写死样本，行级 source 是诚实的"系统快照"；但 server/routers/market_data.py 的接口信封无条件把 source 覆盖为"东方财富行业板块实时行情"，前端因此把编造常量当成实时数据。',
          solution: '兜底数据强制自证身份：信封级 degraded:true + 真实来源名；行级 synthetic:true / stale:true（含 stale_from）。前端对降级数据显示醒目横幅。这也解释了此前"粒子图全部板块净流入都是 12.5 亿"之谜——不是分布退化，是数据根本不存在。',
          status: '已解决'
        },
        {
          id: 'I-30',
          date: '2026-10-14',
          title: '龙虎榜把 2015 年的数据伪装成实时',
          symptom: '龙虎榜展示的内容实为 2015 年救市日的旧数据，却被当作"今日龙虎榜"呈现。',
          rootCause: '交易日探测失败 → 触发无日期过滤的全历史查询 → 按净买额降序 → 早至 2015 年、净买额极大的救市日数据霸榜 → 被当作实时结果。',
          solution: '排序改为多列 sortColumns=TRADE_DATE,BILLBOARD_NET_AMT + sortTypes=-1,-1（先交易日倒序，再净买额倒序）；新增陈旧护栏 _EM_LHB_STALE_DAYS=10，最新交易日距今超过 10 天即判定陈旧并降级。教训：排序字段决定"最近的"还是"最大的"排最前。',
          status: '已解决'
        },
        {
          id: 'I-31',
          date: '2026-10-14',
          title: 'push2 一断就整屏硬编码样本（全局降级过于粗糙）',
          symptom: '东财 push2 一旦不可达，板块/热门股/龙虎榜立即整体退回写死样本，前端呈现"能看但全是假数"。',
          rootCause: '降级策略是"网络失败 → 直接落到硬编码样本"的二级结构，缺少中间层；网络抖动频繁时假数据出现频率过高。',
          solution: '改为四级降级链：真实 → 历史快照（stale）→ 腾讯聚合（真实推导）→ 本地样本（synthetic）。成功即原子落盘（.tmp → fsync → os.replace），失败回放盘缓存并标 stale；本地样本只在"首次部署/缓存被删"时出现。',
          status: '已解决'
        },
        {
          id: 'I-32',
          date: '2026-10-14',
          title: '板块 net_flow 与多周期涨跌幅是由真实数据推算出的"伪真实"',
          symptom: 'net_flow 与 5/20/60 日涨跌幅看似合理，实为推算值。',
          rootCause: 'net_flow = amt*(up/(up+dn+1)-0.5)*0.4（涨跌家数比推算）；5/20/60 日% = chg*1.85 / *3.42 / *5.80（当日涨幅乘写死系数）。与真实资金流/多周期收益无因果关系，用户据此判断方向会得出错误结论。',
          solution: '停止推算：net_flow 改为来自实测接口，取不到返回 null；多周期涨跌幅改为从日 K 聚合。东财不可达时用腾讯成分股聚合出真实板块指标，net_flow 如实返回 null。',
          status: '已解决'
        },
        {
          id: 'I-33',
          date: '2026-10-14',
          title: '因子引擎 IC/IR 是编造的，回测参数是空壳',
          symptom: 'qlib 因子回测返回的 IC/IR 看似正常，实为编造；weights/selected_factor_ids/start_date 改了不起作用。',
          rootCause: 'combined_ic = 动量分均值 × 0.15；IR = IC × 18；Q1~Q5 为写死比例；参数仅回显不接线。IC/IR 是因子研究核心指标，编造会误导策略判断。',
          solution: 'IC 改为真实截面相关系数时间序列；IR 改为 mean(IC)/std(IC)；Q1~Q5 改为真实分位分组；weights/selected_factor_ids/start_date 真接线生效。并要求元数据声明的因子集合与实算集合完全一致。',
          status: '已解决'
        },
        {
          id: 'I-34',
          date: '2026-10-14',
          title: 'quant_core 回测链路一跑即 NameError（链路完全是死的）',
          symptom: 'make_stock_features 一运行就抛 NameError，量化回测链路长期不可用。',
          rootCause: '两类命名错误：① 指标名（如 SMAIndicator）只在某个函数体内导入，模块级只有 _ta_* 系列，裸名未绑定到模块级全局；② 调 ta 库 MACD 时参数名写错，应为 window_sign 而非 window_signals。',
          solution: '修正指标名绑定与参数名；修复后合成数据冒烟通过，8 个关键列非全 NaN。',
          status: '已解决'
        },
        {
          id: 'I-35',
          date: '2026-10-14',
          title: '文档体系混乱（98 个文件混放、根目录多份过期报告）',
          symptom: 'docs/ 下 98 个文件，md 与大量 PNG 截图混放；项目根目录 5 个零散 md，其中 2 份 v4.0 报告内容重复且已过期；文档站缺"战略定位/模块边界"章节。',
          rootCause: '长期增量交付未治理信息架构；截图与文档未分离；历史版本未归档。',
          solution: '建立 docs/ 编号目录规范（00-strategy / 10-architecture / 20-prd / 30-data / 40-ops / 90-archive + assets）；重复报告合并归档；根目录只保留 README.md 与 CHANGELOG.md；文档站新增战略/模块/数据三章并重构排版（分组树形导航 + 小节锚点 + 面包屑 + 长表格横向滚动）。',
          status: '已解决'
        },
        {
          id: 'I-36',
          date: '2026-10-22',
          title: '缓存 43% 条目过期却无人回收，且满容时优先淘汰有效数据',
          symptom: '线上实例 68 条缓存里 29 条已过期（43%）；服务停止写入的时段（夜间/周末/低峰）过期条目长期占容量；新写入时反而把仍有效的数据淘汰掉。',
          rootCause: '_maybe_cleanup() 只在 set() 内被调用，与写入强耦合 —— 没有写入就没有回收；且 set() 满容时直接 popitem 淘汰 LRU，不先看有没有过期垃圾可清。',
          solution: 'server/cache.py 重写：新增 background_cleanup() 后台任务由 lifespan 启动（周期 30s，短于最短业务 TTL 60s），与写入解耦；set() 改为「先 purge_expired(force=True) 回收过期，仍有缺口才淘汰 LRU」；补齐 get_entry / iter_entries / purge_expired / stats / contains 公共接口；clear() 重置清理游标；max_size 归一化避免空表 popitem。',
          status: '已解决'
        },
        {
          id: 'I-37',
          date: '2026-10-22',
          title: '缓存并发去重形同虚设：失败时 N 个等待者各自重试',
          symptom: '同一 key 的 8 个并发请求在上游失败时打出 8 次上游调用，与「请求去重」的设计承诺相反（缓存击穿放大）。',
          rootCause: 'get_or_fetch 的等待者分支在 leader 失败后再次调用 _do_fetch，而不是复用 leader 的结果。',
          solution: '等待者改为 await event.wait() 后直接 return self.get(key)（失败即 None），不再各自重试。回归用例：8 并发失败场景只打 1 次上游（修复前为 8）。',
          status: '已解决'
        },
        {
          id: 'I-38',
          date: '2026-10-22',
          title: '/api/quotes fast 模式的「过期也用」降级是死代码',
          symptom: '行情接口超时后本应回落到上一份缓存，实际从未生效，fast 模式仍然返回空数据。',
          rootCause: '先调用 cache.get()（遇过期条目会立即 pop 删除），紧接着再读 cache._store —— 此时条目已被删掉，必然取不到。',
          solution: '改用 cache.get_entry()（保留过期条目）读取，并把 expired / age 打进日志。',
          status: '已解决'
        },
        {
          id: 'I-39',
          date: '2026-10-22',
          title: '管理端仪表盘一个未定义函数废掉三块内容',
          symptom: '板块卡片永久空白、收入趋势图与订单饼图永不渲染；页面只弹一条「仪表盘加载失败」toast，看起来像「没数据」。',
          rootCause: 'loadDashboard() 中调用 startEventStream()，而该函数全项目 0 处定义，抛 ReferenceError 被 catch 吞掉，后续 dash-sectors 渲染与两个图表调用全部跳过。',
          solution: '移除死调用（实时刷新本就由 _liveEngine 30s 轮询、document.hidden 时暂停、覆盖 8 个页面负责）；顺带让 loadOrdersPieChart() 复用 loadDashboard() 已取到的 d.orders，每 30s 少一次全量仪表盘请求。',
          status: '已解决'
        },
        {
          id: 'I-40',
          date: '2026-10-22',
          title: '一封邮件的网络抖动会撑爆整轮信号扫描',
          symptom: '日志出现 TimeoutError 堆栈，堆栈最后一帧停在 _dispatch → send_signal_batch → to_thread；本轮剩余标的全部漏扫并触发连续失败退避。',
          rootCause: '邮件推送走 asyncio.to_thread + 重试，单次最长可达数十秒，却嵌在 SCAN_ROUND_TIMEOUT_SECONDS=90s 的 wait_for 预算内，且 _dispatch 未对通知失败做超时隔离。',
          solution: '改为 asyncio.wait_for(..., timeout=NOTIFY_TIMEOUT_SECONDS=8.0)，超时/异常一律落 PushStatus.FAILED 不再向上冒泡 —— 通知慢不该拖垮监控。',
          status: '已解决'
        },
        {
          id: 'I-41',
          date: '2026-10-22',
          title: '情绪页与仪表盘的板块体系无法互通（50 个东财板块 vs 26 个自选板块）',
          symptom: '情绪页看到某板块走强，想去看成分股时无从下手：两边名字与代码都对不上（BK1500 医美耗材 / semiconductor 半导体），此前所有热力格都是 pointer 却点不动。',
          rootCause: '两个页面用了两套板块口径，且从未建立映射；前端也没有跨页上下文传递（链接一律裸跳）。',
          solution: 'sentiment.js 新增 SECTOR_ALIAS（26 组关键词别名）+ mapToSector()：命中则热力格可点并跳 dashboard.html?sector=<key>，未命中不做假跳转、title 注明「未纳入自选板块」；app.js 新增 _syncContextLinks() 在 init/switchSector/selectStock 三处刷新跨页入口的上下文参数；K 线浮层底部新增 9 个 Tab 共用的「相关信息」出口条。',
          status: '已解决'
        },
        {
          id: 'I-42',
          date: '2026-10-22',
          title: '文档图谱：数据过期、分组被打散、30 个节点只显示 8 个名字',
          symptom: 'static/docs-graph.json 停在 10-21，CHANGELOG.md 节点仍记 566 行；力导向把分组打散成颜色混杂的圆环；大部分圆点没有标签，必须逐个悬停才知道是什么。',
          rootCause: '图谱数据需手动重新生成；布局只在初始位置按分组分扇区，FR 迭代随后把簇打散；常显标签固定为 Top5 枢纽。',
          solution: '重新生成数据（31 节点 / 80 连线 / 0 孤立）；computeLayout 加入分组凝聚力（向组质心收拢，COHESION=0.26）；初始分布由正圆改椭圆以填满宽幅画布，仪器环同步椭圆化；标签覆盖按 40% 自适应（下限 5 / 上限 14），截断由尾部截断改为中段省略（保留区分度更高的尾部）。',
          status: '已解决'
        },
      ]
    },

    /* =====================================================================
     * plan —— G. 开发计划
     * ===================================================================*/
    plan: {
      title: '开发计划',
      intro: '整体闭环的细化设计与 P0-P2 顺序见「整体规划 · 三系统闭环」；本阶段划分与优先级取自 docs/00-strategy/product-roadmap-2026.md，长期演进取自 docs/00-strategy/capability-evolution-plan-2026.md。勾选状态为源文档中的真实状态。',
      priorityLegend: [
        { key: 'P0', desc: '低风险高确定性、直接影响「看得清」的收尾工作' },
        { key: 'P1', desc: '交易闭环与信任根基，北极星关键路径' },
        { key: 'P2', desc: '智能与扩展，可延后' }
      ],
      phases: [
        {
          id: 'phase1',
          name: 'Phase 1 · 把「看得清」做完',
          window: '1-2 个月',
          goal: 'P0 体验收尾：界面清晰度、AI 可解释呈现、移动端全链路适配。',
          priority: 'P0',
          status: '进行中（核心已交付，余 2 项待办）',
          northStarLink: '看得清是「可回溯」的前提：决策卡上的入场/止损/仓位必须肉眼可核验。',
          tasks: [
            { text: 'K 线界面视觉与布局优化（配色 / 网格 / 最新价标签 / 十字线 / 窗格高度预算）', done: true, priority: 'P0' },
            { text: 'AI 分析内联摘要 + 全屏大屏模式（含浮层几何与网格填充修复）', done: true, priority: 'P0' },
            { text: '移动端 390px 全链路适配（K 线卡 508px 未塌缩、AI 大屏单列退化，双视口验证通过）', done: true, priority: 'P0' },
            { text: 'P0 前置：配置 DEEPSEEK_API_KEY 并重启服务（2026-10-07 完成，旧 key 402 欠费已换）', done: true, priority: 'P0' },
            { text: '大屏模式补充：技术指标明细表（KDJ 的 K/D/J 数值、MACD 柱值）、历史研判对比', done: false, priority: 'P0' },
            { text: '异常兜底统一：行情源断流占位与重试文案（AI 缺失占位 v5.1 已完成）', done: false, priority: 'P1' }
          ]
        },
        {
          id: 'phase2',
          name: 'Phase 2 · 把「信得过」做实',
          window: '3-4 个月',
          goal: 'P1 交易闭环 —— 北极星关键路径：让「规则 + AI」先在纸面上被验证。',
          priority: 'P1',
          status: '进行中（复盘系统 MVP 已交付）',
          northStarLink: '本阶段全部任务直接对应北极星 5 项指标中的 4 项。',
          tasks: [
            { text: '复盘系统（关键路径，最先做）：✅ MVP 2026-10-07 交付 —— /api/review/summary|equity|weekly 三端点 + 自动化中心「复盘」Tab + 每日权益快照自动累积', done: true, priority: 'P1' },
            { text: '策略超市化：策略参数可视化配置 + 回测对比矩阵（≥3 策略并排），让「先回测再上线」成为默认动作', done: false, priority: 'P1' },
            { text: '风控 2.0：日内最大亏损熔断、连续亏损降仓位、单策略回撤限额（验收 = 90 天 maxDD ≤ 10%）', done: false, priority: 'P1' },
            { text: '通知升级：企业微信 / Telegram 通道（现有邮件通道偏慢），关键节点（成交/止损/熔断）分级推送', done: false, priority: 'P1' }
          ]
        },
        {
          id: 'phase3',
          name: 'Phase 3 · 把「用得久」做深',
          window: '5-8 个月',
          goal: 'P2 智能与扩展：从单票研判走向多周期、数据资产化与执行偏差度量。',
          priority: 'P2',
          status: '未开始',
          northStarLink: '本阶段让达标从「偶然」变为「可持续」，并扩展能力边界。',
          tasks: [
            { text: 'AI 复盘助手：用 LLM 对周报做归因分析（如「本周亏损集中于追高打板」）', done: false, priority: 'P2' },
            { text: '多周期联动：日线主图 + 60/30 分钟子图联动，支持波段调仓场景', done: false, priority: 'P2' },
            { text: '数据资产化：本地化存储全量自选股行情快照，为自定义因子回测供数', done: false, priority: 'P2' },
            { text: '（可选）多账户：纸面 ↔ 实盘影子对照，量化「执行偏差」', done: false, priority: 'P2' }
          ]
        }
      ],
      longTerm: {
        title: '长期演进路线（12 个月+）',
        ladder: '决策辅助工具（现在） → 个人量化操作系统（Phase 2-3 完成后） → 可分享的半托管服务（远期，谨慎）',
        points: [
          '12 个月愿景：所有自动交易决策 100% 可回溯——每次下单能查到当时的 AI 研判、策略信号与风控快照。「可审计」是个人量化系统与炒股软件的本质分界。',
          '商业化边界：若未来对外提供服务，需评估投资顾问牌照合规问题；在此之前坚持「单机 / 自部署、无代客理财」的边界。',
          '技术债清单：根目录 dashboard.html 冗余副本清理、strategies-ui.js 与 app.js 职责再切分、行情源双通道（腾讯 + 备用）热切换。'
        ]
      },
      capabilityEvolution: {
        title: '交易能力进化路线（capability-evolution-plan-2026）',
        note: 'roadmap 管「做什么产品功能」，本路线管「交易能力怎么变强」；两者共用同一组北极星指标验收。',
        phases: [
          { name: 'Phase 0 · 基线', window: '2026-10', goal: '链路通、可度量（有了尺子）', status: 'done' },
          { name: 'Phase 1 · 自动化闭环', window: '2026 Q4', goal: '自己复盘、自己迭代（指标开始自动改善）', status: 'mostly' },
          { name: 'Phase 2 · 回测与评估增强', window: '2027 Q1', goal: '抗过拟合、批量搜索（指标变得可信）', status: 'todo' },
          { name: 'Phase 3 · 数据与因子能力', window: '2027 Q2', goal: '多维分析、环境过滤（回撤指标改善）', status: 'todo' },
          { name: 'Phase 4 · 决策智能升级', window: '2027 Q3+', goal: '状态感知、组合决策（超额收益持续达标）', status: 'todo' }
        ]
      }
    },

    /* =====================================================================
     * progress —— H. 当前进度
     * ===================================================================*/
    progress: {
      title: '当前进度',
      intro: '完成度按源文档中的任务勾选状态真实统计，不做估算美化。',
      note: '统计口径：docs/00-strategy/product-roadmap-2026.md 中带 [x] / [ ] 标记的任务项。',
      phaseProgress: [
        { name: 'Phase 1「看得清」', done: 4, total: 6, note: '核心 3 项 + P0 前置已完成' },
        { name: 'Phase 2「信得过」', done: 1, total: 4, note: '复盘系统 MVP 已交付' },
        { name: 'Phase 3「用得久」', done: 0, total: 4, note: '未开始' }
      ],
      capabilityCards: [
        {
          module: '行情与 K 线',
          status: 'KLineChart v10 双引擎（ECharts 兜底），120 根日 K + 副图指标',
          maturity: '高',
          maturityLevel: 3
        },
        {
          module: 'AI 实时分析',
          status: 'DeepSeek 增强决策卡（入场 / 止损 / 止盈 / 仓位 / 置信度）+ 内联摘要 + 大屏模式',
          maturity: '高',
          maturityLevel: 3
        },
        {
          module: '板块分析',
          status: '5/20/60 日板块排行与周期切换',
          maturity: '中',
          maturityLevel: 2
        },
        {
          module: '策略与回测',
          status: '策略面板、内置策略 5 个（ma_cross_atr / n_day_breakout / rsi_mean_reversion / macd_trend / volume_breakout）、回测门控',
          maturity: '中',
          maturityLevel: 2
        },
        {
          module: '自动化交易',
          status: 'M1-M3 后端（调度 / 风控 / 纸面撮合 / 网关）+ M4 前端四 Tab 浮层 + 迭代层 + 闭环调度器',
          maturity: '中',
          maturityLevel: 2
        },
        {
          module: '复盘系统',
          status: '北极星 6 指标 + 净值 vs 沪深300 曲线 + 周报自动生成（MVP 2026-10-07）',
          maturity: '中',
          maturityLevel: 2
        },
        {
          module: '通知告警',
          status: 'QQ 邮件通道（SMTP 已实测 sent:true）+ 异动提醒',
          maturity: '中',
          maturityLevel: 2
        },
        {
          module: 'AI 主脑',
          status: 'DAPENG Brain：3 个时段感知任务 + 分层记忆（洞察 30 / 经验 20）+ LLM 研报复用历史经验',
          maturity: '中',
          maturityLevel: 2
        },
        {
          module: '动态市场调查',
          status: '真实三段式（收集 → 规则分析 → LLM 汇总），10 分钟缓存 + 历史档案 50 条',
          maturity: '中',
          maturityLevel: 2
        },
        {
          module: '开源融合',
          status: 'P0 策略实验室 / 模拟持仓已接入；P2 AKShare 适配层待做',
          maturity: '低',
          maturityLevel: 1
        },
        {
          module: '板块轮动可视化',
          status: '资金迁移粒子图（lines+effect 分档粒子 / 渐变光球节点 / 涟漪脉冲）+ 规则引擎自动总结（资金集中度、迁移方向、阶段结构、量价背离提示）',
          maturity: '中高',
          maturityLevel: 3
        },
        {
          module: '项目文档站',
          status: '单页文档站（12 章节），内容与版式分离，全量内容集中于 project-docs-data.js，更新无需触碰 HTML',
          maturity: '中高',
          maturityLevel: 3
        },
      ],
      delivered: [
        '北极星指标体系可从 /api/review/summary 真实产出（6 项指标 + pass/fail/na）',
        '自动化闭环：日结（快照 → 单笔复盘 → 迭代决策）+ 周五周报，无人值守、KV 幂等',
        '门禁状态机 + broker_adapter=null，实盘物理不可达',
        '策略从 2 个扩充到 5 个，全部可回测并自动 draft → backtested',
        'QQ 邮件通道打通（信号 / 周报 / 熔断告警）',
        'AI 决策卡上线（4 只验证股票均返回结构化 ai_decision）',
        '动态市场调查由假数据改为真实数据 + LLM 汇总',
        '看门狗自启 + 崩溃自愈 7 秒（服务不再随会话被回收）'
      ],
      limitations: [
        'GET /api/screen 的 min_turnover / min_vol / max_pe 参数后端可能未完全实现，需验证',
        '板块资金流 API 常返回空，前端仅本地估算 fallback',
        '/api/sector-stocks 仅返回 {code, name, priority}，缺涨跌幅 / 换手率等字段',
        'QQ SMTP 通道偏慢，企业微信 / Telegram 通道未做',
        'websocket 与 SSE 之外无其他实时通道；轮询仍用于部分降级场景'
      ],
      techDebt: [
        '根目录 dashboard.html 为冗余副本，uvicorn 只服务 static/，存在双份漂移风险（曾发生版本号回滚）',
        'strategies-ui.js 与 app.js 职责需再切分（两者均已 20 万字符级）',
        '行情源双通道（腾讯 + 备用）热切换未实现',
        '项目根存在误操作产生的垃圾目录（形如 server" && cp …），经核查非空（内含 alipay/config/service/wechat.py），未删除，待人工确认',
        'agents.py / payment 模块功能不完整，属历史遗留',
        'workspace 沙箱内无法留下跨会话存活进程，持久服务依赖用户手动在沙箱外启动'
      ]
    },

    /* =====================================================================
     * apiIndex —— 核心 API 索引（对应 HANDOFF 第 3 节）
     * ===================================================================*/
    apiIndex: {
      title: '核心 API 索引',
      intro: '摘录自 docs/90-archive/HANDOFF-v5.4.md 第 3 节与后续迭代新增端点，便于排查时快速定位。',
      groups: [
        {
          name: 'v3.0 ~ v4.0 核心',
          items: [
            ['GET /api/health', '健康检查'],
            ['GET /api/quotes?sector=xxx', '板块行情 + 策略评分'],
            ['GET /api/kline?code=xxx&period=day', 'K 线 + MA/MACD/RSI/ATR'],
            ['GET /api/global', '全球指数'],
            ['GET /api/search?keyword=xxx', '跨板块搜索'],
            ['GET /api/screen?min_score=60&min_chg=0&min_turnover=1&max_pe=50&limit=30', '条件选股'],
            ['GET /api/analyze?code=xxx', 'DeepSeek AI 实时分析'],
            ['GET /api/scan?sectors=xx&price_min=10&price_max=200', '全市场智能选股']
          ]
        },
        {
          name: 'v5.0+ 腾讯自选股数据',
          items: [
            ['GET /api/sector-ranking?page=1&limit=50', '行业板块排行（东方财富行业板块实时行情；返回 chg / amount / turnover / net_flow 净流入 / up_count / down_count 与 5日·20日·60日 %）。注意：limit 最小 5，传 1~4 会 422'],
            ['GET /api/sector-stocks?sector=power_equip', '板块成分股（需传英文 key）'],
            ['GET /api/fund-flow/sector?sector=xxx', '板块资金流（2026-10-13 起改为东方财富个股资金流批量聚合，返回 super_large/large/medium/small/main + summary + stocks；取数失败返回 ok:false 便于前端区分失败与无数据）'],
            ['GET /api/hot-stocks?type=stock|etf&limit=50', '热门股 / 热门 ETF 排行'],
            ['GET /api/lhb?tab=jg|yzb|yyb|gslmr|gslxw', '龙虎榜五类'],
            ['GET /api/market-overview', '市场涨跌分布'],
            ['GET /api/profile?code=xxx', '个股概况'],
            ['GET /api/finance?code=xxx&ftype=income', '财务报表'],
            ['GET /api/report?code=xxx', '研报列表'],
            ['GET /api/consensus?code=xxx', '分析师评级']
          ]
        },
        {
          name: 'v5.1 后台管理',
          items: [
            ['GET /api/admin/stats', 'API 调用统计'],
            ['GET /api/admin/calls?page=1', '调用明细分页'],
            ['GET /api/admin/error-ranking', '错误率排行']
          ]
        },
        {
          name: '自动化交易 / 复盘 / 迭代（2026-10）',
          items: [
            ['GET/POST /api/automation/*', '自动化配置、门闸、回测、策略启用、持仓、通知测试'],
            ['GET /api/review/summary', '北极星 6 项指标 + pass/fail/na 判定'],
            ['GET /api/review/equity', '净值 vs 沪深300 归一化曲线'],
            ['GET /api/review/weekly', '周复盘中文叙述报告'],
            ['GET /api/evolution/status|iterations|trade-reviews|library|proposals', '迭代层状态与记录（含 8 端点）'],
            ['POST /api/evolution/run', '手动跑一轮闭环迭代'],
            ['POST /api/evolution/proposals/{id}/apply|reject', '调参提案人工应用 / 否决'],
            ['GET /api/brain/status|insights|survey', 'AI 主脑状态与分层记忆'],
            ['POST /api/brain/run/{task}', '手动触发主脑任务'],
            ['GET /api/analyze/chart-summary', '图表解读摘要（2026-10-08 修复双重前缀）']
          ]
        },
        {
          name: '2026-11-02 决策闭环与全市场数据覆盖',
          items: [
            ['GET /api/decision/snapshot', '共享 MarketPacket：trace_id / as_of / 全市场覆盖 / 质量门禁 / 核心与全市场双时间戳'],
            ['GET /api/decision/sentiment', 'sentiment.v1 六维情绪分：宽度 30% / 动量 20% / 涨跌停 15% / 量能 15% / 全球 10% / 事件 10%；缺失维度重归一'],
            ['GET /api/decision/sentiment/history?limit=120', '情绪历史序列（append-only；同日按 trace 去重，默认 4 分钟一条）'],
            ['GET /api/decision/experts', '六专家规则 v1：六份独立意见 + 证据 / 反证 / 失效条件 + C/D 共识与风控否决'],
            ['GET /api/decision/coverage', '全 A 股 5581 只行情 + PE/PB/市值 + 5759 条季度基本面；全球指数 / 国家与行业 ETF；19 项全球宏观'],
            ['GET /api/decision/universe?limit=50&sort=amount', '全市场分页查询（amount / chg / market_cap_yi / roe / pe）'],
            ['GET /api/decision/coverage/stream', 'SSE 持续推送覆盖快照与来源健康（前端只读缓存，不触发上游抓取）'],
            ['GET /api/decision/lhb-review', '龙虎榜复盘（lhb-review.v1）：默认只读落盘结果，?refresh=true 立即重算（约 5–10s）；含 context / summary / records / caveats'],
            ['GET /api/decision/lhb-review/{date}', '指定交易日复盘（YYYY-MM-DD）；无缓存时提示传 refresh=true 生成'],
            ['GET /api/decision/lhb-review/recent?limit=10', '近期复盘索引：日期 / 记录数 / 主因分布 / 生成时间'],
            ['GET /lhb-review.html', '龙虎榜复盘展示页（不在 /api 下）：一句话结论 / KPI / 市场底座 / 关键发现 / 动因分布 / 标的表 / 逐笔明细 / 席位 / 题材；入口在左侧栏与决策层摘要卡'],
            ['GET /api/intelligent/market/survey', '调查已优先消费全 A 覆盖；返回 data_scope=full_a_share 与 data_coverage 证据包']
          ]
        },
        {
          name: '2026-10-13 数据链路修复与新增',
          items: [
            ['GET /api/market/north', '沪深港通资金流向（AkShare stock_hsgt_fund_flow_summary_em）。此前该路由模块从未挂载导致恒定 404；现返回 data.disclosed 标记（北向净买额自 2024-08 起停止实时披露，为 false 时前端改呈现成分股涨跌分布与相关指数）'],
            ['GET /api/market/all?sort_by=chg&limit=50', '全市场 A 股实时排行（由 ak_hub.get_market_spot() 支撑，同一模块修复后一并可用）'],
            ['GET /api/fund-flow?code=xxx', '个股资金流（东财批量接口单只查询，含主力/超大单/大单/中单/小单）'],
            ['GET /api/intelligent/ak/index', '主要宽基指数实时行情（7 个指数 + 涨跌家数）。此前后端可用但前端从未接线，已接入动态市场调查视图的指数条'],
            ['GET /api/intelligent/ak/spot', '全市场实时快照（约 20KB / 6000 只）。已接入作为板块轮动的潜在数据源'],
            ['GET /api/brain/intel', '主脑情报快照。现完整返回 brief / risk / channels / global_snapshot / headlines / sectors / watch_points，并可能带 carried_forward + carried_from_ts（表示部分结构化字段沿用上次扫描）']
          ]
        },
      ]
    },

    /* =====================================================================
     * glossary —— 术语表
     * ===================================================================*/
    glossary: {
      title: '术语表',
      items: [
        { term: '北极星目标', desc: '本系统唯一的衡量标准：纸面账户在 90 天滚动窗口内持续跑赢沪深300（≥基准 +3pct），且每笔交易 100% 可回溯。' },
        { term: '门禁状态机（GateState）', desc: '每策略独立的状态：draft → backtested → paper_validating → paper_passed → live_enabled。服务端强制，改参数自动回退 draft。' },
        { term: '纸面交易（paper）', desc: '用真实行情在模拟账户（初始资金 ¥1,000,000）内撮合成交，验证策略但不触及券商通道。' },
        { term: 'broker_adapter = null', desc: '架构级保证实盘物理不可达——不是 UI 灰掉按钮，而是服务端没有可用的券商通道实现。' },
        { term: '可回溯率', desc: '每笔成交流水能否查到当时的策略信号 + AI 研判 + 风控快照。达标线 100%。' },
        { term: 'MFE / MAE', desc: 'Maximum Favorable Excursion / Maximum Adverse Excursion：持仓期间最大浮盈 / 最大浮亏，用于评估出场时点质量。' },
        { term: '样本外验证（OOS）', desc: '用最近 20% 数据复测调参提案；样本不足 3 笔时改用更严格的全样本门槛（+5% 边际改善）。防过拟合硬约束。' },
        { term: '单变量原则', desc: '一轮迭代只改一个参数维度，且幅度不超过原值 ±30%（实际实现为 ±20%），否则无法归因。' },
        { term: 'RankIC / IC', desc: '因子值与未来收益的相关性 / 秩相关信息系数，用于评估因子有效性（Phase 3 因子库规划）。' },
        { term: 'walk-forward 验证', desc: '滚动窗口样本外验证（如 250 日训练 / 60 日样本外），Phase 2 将用它替代单次全样本回测。' },
        { term: 'SSE', desc: 'Server-Sent Events：服务端单向推送。本项目用 5 通道 5s 推送替代 60s 轮询，零新依赖。' },
        { term: 'TTLCache', desc: '进程内带过期时间的缓存 + get_or_fetch 包裝，减少上游调用。' },
        { term: '看门狗（watchdog）', desc: 'scripts/server_watchdog.py：守护 uvicorn，崩溃后 5s→60s 退避重启（稳定 120s 重置），保证服务跨会话存活。' },
        { term: '?v=N 版本号约定', desc: '静态资源用查询参数控制浏览器缓存；改动 HTML/CSS/JS 后必须递增版本号，否则用户会继续用旧代码。' },
        { term: 'runtime_config.json', desc: '运行时可变配置的持久化文件，null 值 fallback 到 config.py 默认，原子写入，不污染源码。' }
      ]
    },

    /* =====================================================================
     * changelogSources —— 文档索引（含文档与代码同步约定）
     * 注意：docs/ 目录未被 StaticFiles 挂载，直接访问 /docs/xxx.md 会 404。
     *       因此这里只渲染为「文件名 + 说明」的展示卡片，不生成可点击链接。
     * ===================================================================*/
    changelogSources: {
      title: '文档索引',
      intro: '项目内的设计与规划文档清单，按信息架构目录分组。docs/ 已通过 FastAPI 静态挂载在 /docs/ 下，条目可直接点击打开原始 Markdown。',
      notice: '点击条目将在新标签页打开原始 Markdown（由 /docs/ 静态挂载提供）。根目录的 README.md / CHANGELOG.md 不在 /docs/ 下，故仅作路径索引。',
      sync: {
        title: '文档与代码同步约定',
        points: [
          '单一数据源：本页所有内容（含本节）均来自 static/js/project-docs-data.js，HTML 只负责渲染骨架，不硬编码文案。',
          '每次功能变更必须同步更新 meta.uiVersion 与 meta.updatedAt，并在「重要里程碑 / 问题与解决方案」补一条对应记录。',
          '新增一个模块说明只需改 project-docs-data.js 一处（对应 sec-modules 的 items 数组），无需触碰 HTML。',
          '运行时静态资源版本号：static/project-docs.html 引用 js/project-docs-data.js?v=N，改动数据文件后需递增该 N，避免浏览器缓存旧数据。',
          '一致性自检：文档站无死锚点、控制台无异常、无过期版本号（历史归档中的旧版本号允许保留，但"最新状态"处必须写对）。'
        ]
      },
      groups: [
        {
          name: '文档导航 · 文档地图',
          items: [
            { name: 'docs/00-strategy/README.md', path: 'docs/00-strategy/README.md', note: '★ 文档地图：按「现状 / 计划（living）/ 历史设计 / 归档」四类导航全部文档，一眼判断该信哪一篇' }
          ]
        },
        {
          name: '项目根目录',
          items: [
            { name: 'README.md', path: 'README.md', note: '项目定位一句话、启动方式、目录导航、指向 MASTER-PLAN-2026Q4.md 的入口' },
            { name: 'CHANGELOG.md', path: 'CHANGELOG.md', note: '历史更新日志（最新条目 2026-11-03 龙虎榜复盘聚合层 + 展示页 lhb-insight.v1；按轮次记录背景 / 改动 / 验证结果，更早进展见本页里程碑）' }
          ]
        },
        {
          name: 'docs/00-strategy · 战略与规划',
          items: [
            { name: 'MASTER-PLAN-2026Q4.md', path: 'docs/00-strategy/MASTER-PLAN-2026Q4.md', note: '★ 总纲：战略定位、三层价值主张、7 项北极星指标、M1-M9 模块目标/范围/验收标准、路线图、数据真实性红线' },
            { name: 'DECISION-CLOSED-LOOP-PLAN-2026Q4.md', path: 'docs/00-strategy/DECISION-CLOSED-LOOP-PLAN-2026Q4.md', note: '★ 决策闭环整体规划：动态调查数据契约 / 六专家互评与冲突裁决 / 情绪六维公式 / 三系统 I/O、依赖与 P0-P2 优先级' },
            { name: 'product-roadmap-2026.md', path: 'docs/00-strategy/product-roadmap-2026.md', note: '产品路线图 v1.3：北极星目标、系统定位、用户群体、Phase 1/2/3 任务勾选与长期路线' },
            { name: 'capability-evolution-plan-2026.md', path: 'docs/00-strategy/capability-evolution-plan-2026.md', note: '交易能力进化路线 v1.1：自动化模拟盘迭代机制 + 开源融合路线 + Phase 0-4' },
            { name: 'OPEN_SOURCE_INTEGRATION_PLAN.md', path: 'docs/00-strategy/OPEN_SOURCE_INTEGRATION_PLAN.md', note: '开源能力融合规划：9 项目核验表 + 五把尺子 + 许可证红线 + P0-P4 分阶段' }
          ]
        },
        {
          name: 'docs/10-architecture · 架构设计',
          items: [
            { name: 'A-SHARE-REALTIME-DECISION-DESK.md', path: 'docs/10-architecture/A-SHARE-REALTIME-DECISION-DESK.md', note: '★ 面向全 A 股的实时分析决策台架构规范（decision-desk-arch.v1）：分层与进程模型、三层职责边界与数据契约、资源图与五道闸门、九步联动、配置升级/回滚、延迟预算与实测基线、扩展路径、A1–A8 验收标准（每条给命令与阈值）' },
            { name: 'DECISION-ROUTING.md', path: 'docs/10-architecture/DECISION-ROUTING.md', note: '★ 决策分流（decision-routing.v1）：8 项指标的接入方式/字段/单位/频率、归一化公式与权重、6 条路由的优先级与阈值表、时效与 stale 语义、七种降级情形、配置清单与四条校验命令' },
            { name: 'LHB-REVIEW.md', path: 'docs/10-architecture/LHB-REVIEW.md', note: '★ 龙虎榜复盘（lhb-review.v1 · lhb-insight.v1）：数据源与三个口径坑（TOTAL_BUYRIO 是小数、涨停池须按复盘日、个股级接口无成交量）、席位五分类顺序、五维归因（情绪打折 0.55 + 龙头效应）、聚合三视角（合力判定 / 通道不等于交易者 / 按金额加权）、推断等级 seal-time 与 day-level、展示页结构与三处入口、html,body 滚动锁的样式坑、配置项与验收基线' },
            { name: 'LAYER-SYNC-CONTRACT.md', path: 'docs/10-architecture/LAYER-SYNC-CONTRACT.md', note: '★ 层间同步契约（layer-sync.v1）：事实→判断→决策各自同步哪些字段（含单位/口径/必填）、更新频率与传递方式；全市场覆盖实测（沪 98.2% / 深 98.7% / 京 74.4%，5782 只）；lineage.v1 结论溯源（节点/指纹/回放命令）与 S1–S9 验收' },
            { name: 'openbb-dsa-integration-design.md', path: 'docs/10-architecture/openbb-dsa-integration-design.md', note: '★ OpenBB/DSA 接入架构设计（1215 行）：模块清单、接口契约、类图与时序图、P0 任务分解 T01–T13（45 人天）、MVP 子集 21 人天、风险与回滚；含 3 条推翻 PRD 的实跑发现（东财系 AkShare 接口 403、单端点覆盖 5/6 指标、依赖清单与实际环境不一致）' },
            { name: 'SYSTEM-WORKFLOW.md', path: 'docs/10-architecture/SYSTEM-WORKFLOW.md', note: '★ 系统工作流与模块地图：六条主链路（数据接入 → 选股信号 → 9 关风控+决策闸 → 成交 → 复盘迭代 → 实时推送）、存储落盘、接口↔链路映射、自检命令、已知边界' },
            { name: 'auto-trading-architecture.md', path: 'docs/10-architecture/auto-trading-architecture.md', note: '★ 自动交易架构唯一权威蓝图（12 章）：模块地图、门禁状态机、SQLite 五表、已确认决策' },
            { name: 'DECISION-HUB.md', path: 'docs/10-architecture/DECISION-HUB.md', note: '★ 决策台 = 主脑中心（decision-hub.v1）：三个并列工作区合一的三层结构（事实 / 判断 / 决策）、hub 条互斥与 hash 路由、情绪口径统一、兼容入口与轮询收敛' },
            { name: 'LAYER-CROSSCHECK.md', path: 'docs/10-architecture/LAYER-CROSSCHECK.md', note: '★ 三层对证与数据配置（layer-crosscheck.v1 · brain-config.v1）：九项检查八类判据、五档状态语义（pass / warn / conflict / incomparable / unavailable）、可比率与 blocker、survey.facts、闭环与页面接入，含对证当场抓到的 5 个真实问题' },
            { name: 'SENTIMENT-SURVEY-BRAIN.md', path: 'docs/10-architecture/SENTIMENT-SURVEY-BRAIN.md', note: '★ 情绪 × 调查 × 主脑三层一体化：sentiment-flow.v1（变化量 / 斜率 / 分位 / 拐点 / 归因）、survey-pipeline.v1（采集→诊断→结论 + 结构↔情绪背离）、brain-capability.v1 15 项能力契约 + 备忘录 + 周期' },
            { name: 'MARKET-SENTIMENT-MERGE.md', path: 'docs/10-architecture/MARKET-SENTIMENT-MERGE.md', note: '大盘纵览 × 市场情绪追踪合并设计：重合清单、统一情绪口径、六维 + 追踪 + 三锚点；注：已进一步并入决策台事实层，见 DECISION-HUB.md' },
            { name: 'DATA-PLATFORM.md', path: 'docs/10-architecture/DATA-PLATFORM.md', note: '数据平台与数据源治理：数据源注册表（实测）、可信度分级、降级链，任何面板/指标都要能追溯到来源与时间' },
            { name: 'SYSTEM-OPTIMIZATION-2026Q4.md', path: 'docs/10-architecture/SYSTEM-OPTIMIZATION-2026Q4.md', note: '架构与数据连接优化方案：先用 tools/arch_report.py 静态测绘（AST，不跑业务）再按依赖方向治理；含问题清单与验收记录，随代码同步维护' },
            { name: 'ADR-001-contract-envelope.md', path: 'docs/10-architecture/ADR-001-contract-envelope.md', note: 'ADR：统一响应信封 contract-envelope.v1（ok / code / message / data + X-Schema-Version），前端不再各自判空' },
            { name: 'ADR-002-module-split-facade.md', path: 'docs/10-architecture/ADR-002-module-split-facade.md', note: 'ADR：超大模块「门面 + 分片」拆分（tools/split_module.py 可复现切分，原路径保留门面，10 个 >900 行模块清零）' },
            { name: 'REPO-HYGIENE.md', path: 'docs/10-architecture/REPO-HYGIENE.md', note: '仓库卫生与提交前守卫 repo-hygiene.v1：目录约定、调试产物归档、ci_guard 三道闸门' },
            { name: 'AXON-GRAPH-DESIGN.md', path: 'docs/10-architecture/AXON-GRAPH-DESIGN.md', note: 'AXON 文档关系图版设计记录（1855 行）：现行界面设计、星系浑天仪演进、逐版版本表' },
            { name: 'ARMILLARY-DESIGN.md', path: 'docs/10-architecture/ARMILLARY-DESIGN.md', note: '浑天仪星系设计代码全稿（v53–v59 权威底稿），含完整可运行实现与恢复方式' },
            { name: 'IMPECCABLE-INTEGRATION.md', path: 'docs/10-architecture/IMPECCABLE-INTEGRATION.md', note: 'Impeccable 设计流程与设计检测器接入说明：让 UI 改动有可复现的设计上下文与机械检查' },
            { name: 'UI-VISUALIZATION-ROADMAP.md', path: 'docs/10-architecture/UI-VISUALIZATION-ROADMAP.md', note: '界面与可视化路线（living）：下一步界面往哪走、如何与文档节奏同步' },
            { name: 'SENTIMENT-REDESIGN.md', path: 'docs/10-architecture/SENTIMENT-REDESIGN.md', note: '情绪页面板重设计规范（早期联合稿）：现状缺陷与视觉统一要求，结论已由合并方案落地' },
            { name: 'system_design.md', path: 'docs/10-architecture/system_design.md', note: '2026-05 早期系统设计（项目起步阶段的架构基线）' },
            { name: 'architecture-v54-sidebar-pro.md', path: 'docs/10-architecture/architecture-v54-sidebar-pro.md', note: 'v5.4 侧边栏 5 工具架构设计' },
            { name: 'architecture-sidebar-tools.md', path: 'docs/10-architecture/architecture-sidebar-tools.md', note: 'v5.3 侧边栏工具架构设计' },
            { name: 'architecture-tab-admin.md', path: 'docs/10-architecture/architecture-tab-admin.md', note: 'v5.1 Tab 与管理后台架构设计' },
            { name: 'architecture-westock-mcp.md', path: 'docs/10-architecture/architecture-westock-mcp.md', note: 'westock-data 数据接入架构设计' },
            { name: 'class-diagram.mermaid / class-diagram-v54.mermaid / class-diagram-westock-mcp.mermaid', path: 'docs/10-architecture/class-diagram-v54.mermaid', note: '类图（Mermaid）：早期 / v5.4 / westock 接入' },
            { name: 'sequence-diagram.mermaid / sequence-diagram-v54.mermaid / sequence-diagram-westock-mcp.mermaid', path: 'docs/10-architecture/sequence-diagram-v54.mermaid', note: '时序图（Mermaid）：早期 / v5.4 / westock 接入' }
          ]
        },
        {
          name: 'docs/20-prd · 需求文档',
          items: [
            { name: 'openbb-dsa-integration-prd.md', path: 'docs/20-prd/openbb-dsa-integration-prd.md', note: '★ OpenBB / DailyStockAnalysis 调研与增量 PRD：许可证结论（两者均非 AGPL）、可复用能力 Top 8、四类缺口清单、P0 7 项 / P1 6 项 / P2 5 项、联动路径与改造优先级总表（619 行，结论可溯源到仓库路径）' },
            { name: 'auto-trading-prd.md', path: 'docs/20-prd/auto-trading-prd.md', note: '自动化交易增量 PRD：P0-1 ~ P0-10 需求池 + User Stories + P1/P2 远期' },
            { name: 'incremental-prd-v54-sidebar-pro.md', path: 'docs/20-prd/incremental-prd-v54-sidebar-pro.md', note: 'v5.4 侧边栏工具增量 PRD' },
            { name: 'incremental-prd-sidebar-tools.md', path: 'docs/20-prd/incremental-prd-sidebar-tools.md', note: '侧边栏工具增量 PRD' },
            { name: 'incremental-prd-tab-admin.md', path: 'docs/20-prd/incremental-prd-tab-admin.md', note: 'Tab 与后台管理增量 PRD' },
            { name: 'incremental-prd-westock-mcp.md', path: 'docs/20-prd/incremental-prd-westock-mcp.md', note: 'westock-data 接入增量 PRD' }
          ]
        },
        {
          name: 'docs/30-data · 数据口径',
          items: [
            { name: 'DATA-DICTIONARY.md', path: 'docs/30-data/DATA-DICTIONARY.md', note: '★ 数据口径字典：数据源清单与可用性、四级降级链、字段单位、来源标注约定、已被治理的伪造字段' }
          ]
        },
        {
          name: 'docs/40-ops · 部署与运维',
          items: [
            { name: 'API-REFERENCE.md', path: 'docs/40-ops/API-REFERENCE.md', note: '★ 接口参考：185 个端点按功能分组，由运行中服务的 /openapi.json 自动生成（tools/gen_api_doc.py）；注意生成器读的是 artifacts/openapi.json 快照，刷新接口后必须先重下快照再生成，否则会像本轮一样少 44 个端点；在线可搜索版见 /api-reference.html' },
            { name: 'DEPLOYMENT.md', path: 'docs/40-ops/DEPLOYMENT.md', note: '启动方式、解释器自动探测、看门狗自愈、端口与单实例锁、停止脚本、健康检查与日志' },
            { name: 'DEBUG-AUDIT-2026-11-02.md', path: 'docs/40-ops/DEBUG-AUDIT-2026-11-02.md', note: '★ 系统调试与修复记录：缓存 4 处缺陷（字节账 / 超预算删旧值 / 异常不回落 / 并发无锁）+ 3 项安全加固（Host 校验 / 安全头 / 手机号脱敏）+ 34 项自动体检的定位与验证' },
            { name: 'BOARD-REVIEW.md', path: 'docs/40-ops/BOARD-REVIEW.md', note: '★ 打板复盘工具链：交割单需要哪些列（含别名）、两条命令、FIFO 配对与费用分摊口径、离场四类判定、风格维度与已知边界' }
          ]
        },
        {
          name: 'docs/90-archive · 归档（过期 / 已废弃）',
          items: [
            { name: 'SYSTEM-OPTIMIZATION-v4.0.md', path: 'docs/90-archive/SYSTEM-OPTIMIZATION-v4.0.md', note: '⚠️ v4.0 优化报告（两份重复报告合并归档），已被 MASTER-PLAN-2026Q4.md 取代' },
            { name: 'HANDOFF-v5.4.md', path: 'docs/90-archive/HANDOFF-v5.4.md', note: '⚠️ v5.4 AI 接驳手包（版本已落后，仅作早期架构快照）' },
            { name: 'auto-trading-arch-v0-deprecated.md', path: 'docs/90-archive/auto-trading-arch-v0-deprecated.md', note: '⚠️ 已废弃草案（JSON 存储 / pandas / /api/auto 前缀方案被推翻），仅供考古' }
          ]
        }
      ]
    }
  };

  global.DAPENG_DOCS = DAPENG_DOCS;
})(window);
