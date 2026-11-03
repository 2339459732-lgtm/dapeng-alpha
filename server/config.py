"""DAPENG Alpha v5.0 - 板块配置、股票池、常量定义

板块覆盖：26 个板块（24 A股 + 1 ETF + 1 港股通），434 只去重个股
数据源：腾讯财经 / 东方财富 / Westock CLI 三源冗余
"""

import os
import re
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# .env 由 main.py 在应用启动时统一加载，此处不再重复

# ============================================================
# 板块配置：26 个板块，涵盖 A 股 + 港股，每个板块 8-23 只代表性个股
# 格式：[(代码, 名称), ...]
# ============================================================
SECTORS: Dict[str, dict] = {
    "semiconductor": {
        "name": "半导体",
        "icon": "💾",
        "sector_code": "pt01801081",
        "stocks": [
            ("sh688981", "中芯国际", 1), ("sh688012", "中微公司", 1), ("sz002371", "北方华创", 1),
            ("sh688041", "海光信息", 1), ("sh688256", "寒武纪", 1), ("sh603501", "韦尔股份", 1),
            ("sz300782", "卓胜微", 1), ("sh603986", "兆易创新", 1), ("sh600584", "长电科技", 1),
            ("sz002156", "通富微电", 2), ("sh688072", "拓荆科技", 2), ("sh688037", "芯原股份", 2),
            ("sh600703", "三安光电", 2), ("sh600460", "士兰微", 2), ("sz002185", "华天科技", 2),
            ("sz002129", "TCL中环", 2), ("sh600171", "上海贝岭", 2), ("sh600360", "华微电子", 2),
            ("sz300077", "国民技术", 3), ("sz300046", "台基股份", 3), ("sz002409", "雅克科技", 2),
            ("sh603690", "至纯科技", 2), ("sh603078", "江化微", 3), ("sz300706", "阿石创", 3),
            ("sh688126", "沪硅产业", 2), ("sh603005", "晶方科技", 2), ("sz300655", "晶瑞电材", 3),
            ("sz002079", "苏州固锝", 3),
        ],
    },
    "new_energy": {
        "name": "新能源",
        "icon": "⚡",
        "sector_code": "pt02GN2207",
        "stocks": [
            ("sz300750", "宁德时代", 1), ("sz002594", "比亚迪", 1), ("sz300274", "阳光电源", 1),
            ("sh601012", "隆基绿能", 1), ("sh600438", "通威股份", 2), ("sz002466", "天齐锂业", 2),
            ("sz002460", "赣锋锂业", 2), ("sh603659", "璞泰来", 2), ("sz002709", "天赐材料", 2),
            ("sz300207", "欣旺达", 2), ("sz002074", "国轩高科", 2), ("sh688005", "容百科技", 2),
            ("sh603787", "新日股份", 3), ("sz002340", "格林美", 2), ("sz300484", "蓝海华腾", 3),
            ("sz002660", "茂硕电源", 3), ("sz002580", "圣阳股份", 3), ("sh600884", "杉杉股份", 2),
            ("sz300014", "亿纬锂能", 1), ("sz002812", "恩捷股份", 1), ("sh688567", "孚能科技", 2),
            ("sz300568", "星源材质", 2), ("sh603026", "石大胜华", 2), ("sh603799", "华友钴业", 1),
            ("sz300037", "新宙邦", 2), ("sh688599", "天合光能", 1),
        ],
    },
    "pharma": {
        "name": "医药",
        "icon": "💊",
        "sector_code": "sw1_pt01801140",
        "stocks": [
            ("sh600276", "恒瑞医药", 1), ("sh603259", "药明康德", 1), ("sz300122", "智飞生物", 2),
            ("sz000661", "长春高新", 2), ("sh600436", "片仔癀", 1), ("sz000538", "云南白药", 1),
            ("sh600196", "复星医药", 2), ("sz300347", "泰格医药", 2), ("sz002821", "凯莱英", 2),
            ("sz000963", "华东医药", 1), ("sh600535", "天士力", 2), ("sz300003", "乐普医疗", 2),
            ("sh600566", "济川药业", 2), ("sz002603", "以岭药业", 2), ("sh600750", "江中药业", 2),
            ("sz000999", "华润三九", 1), ("sz300026", "红日药业", 3), ("sz002317", "众生药业", 3),
            ("sz002001", "新和成", 1), ("sz002294", "信立泰", 2), ("sh600380", "健康元", 2),
        ],
    },
    "consumer": {
        "name": "消费",
        "icon": "🛒",
        "sector_code": "sw1_pt01801170",
        "stocks": [
            ("sh600887", "伊利股份", 1), ("sh603288", "海天味业", 1), ("sz000651", "格力电器", 1),
            ("sz000895", "双汇发展", 2), ("sz002714", "牧原股份", 2), ("sh600690", "海尔智家", 1),
            ("sz000799", "酒鬼酒", 3), ("sh603589", "口子窖", 2), ("sz002304", "洋河股份", 1),
            ("sh603369", "今世缘", 2), ("sz002568", "百润股份", 2), ("sh600298", "安琪酵母", 2),
            ("sz002557", "洽洽食品", 2), ("sh600702", "舍得酒业", 2), ("sz000729", "燕京啤酒", 2),
            ("sh600597", "光明乳业", 2), ("sz000858", "五粮液", 1), ("sh600519", "贵州茅台", 1),
            ("sz000568", "泸州老窖", 1), ("sh600809", "山西汾酒", 1), ("sh603345", "安井食品", 2),
        ],
    },
    "finance": {
        "name": "金融",
        "icon": "🏦",
        "sector_code": "sw1_pt01801020",
        "stocks": [
            ("sh601318", "中国平安", 1), ("sh600036", "招商银行", 1), ("sh600030", "中信证券", 1),
            ("sh601688", "华泰证券", 1), ("sz300059", "东方财富", 1), ("sh601211", "国泰海通", 1),
            ("sh601995", "中金公司", 1), ("sz000776", "广发证券", 1), ("sh601066", "中信建投", 1),
            ("sz002142", "宁波银行", 1), ("sh601398", "工商银行", 1), ("sh601939", "建设银行", 1),
            ("sh601288", "农业银行", 1), ("sh601988", "中国银行", 1), ("sh601166", "兴业银行", 1),
            ("sh601328", "交通银行", 1), ("sh600016", "民生银行", 2), ("sh601601", "中国太保", 1),
            ("sh601628", "中国人寿", 1), ("sz000001", "平安银行", 1), ("sh600837", "海通证券", 1),
            ("sh600926", "杭州银行", 2), ("sh601009", "南京银行", 1), ("sz002673", "西部证券", 2),
        ],
    },
    "military": {
        "name": "军工",
        "icon": "✈️",
        "sector_code": "sw1_pt01801130",
        "stocks": [
            ("sh600760", "中航沈飞", 1), ("sh600893", "航发动力", 1), ("sz002179", "中航光电", 2),
            ("sz000768", "中航西飞", 2), ("sz000733", "振华科技", 2), ("sh603678", "火炬电子", 3),
            ("sh603267", "鸿远电子", 3), ("sz002025", "航天电器", 2), ("sh600765", "中航重机", 2),
            ("sh600862", "中航高科", 2), ("sz300034", "钢研高纳", 3), ("sz002414", "高德红外", 3),
            ("sh600391", "航发科技", 3), ("sz300101", "振芯科技", 3), ("sz002013", "中航机电", 2),
            ("sh600685", "中船防务", 2), ("sh600482", "中国动力", 2), ("sz002465", "海格通信", 2),
            ("sz300527", "中船应急", 3), ("sz300114", "中航电测", 2), ("sh600184", "光电股份", 3),
            ("sz300397", "天和防务", 3), ("sz300008", "天海防务", 3),
        ],
    },
    "ai_compute": {
        "name": "AI算力",
        "icon": "🤖",
        "sector_code": "pt02GN2328",
        "stocks": [
            ("sz300308", "中际旭创", 1), ("sz300502", "新易盛", 1), ("sz300394", "天孚通信", 1),
            ("sh601138", "工业富联", 1), ("sh688041", "海光信息", 1), ("sh688256", "寒武纪", 1),
            ("sz000977", "浪潮信息", 2), ("sz000938", "紫光股份", 2), ("sz000063", "中兴通讯", 1),
            ("sz002230", "科大讯飞", 2), ("sh688008", "澜起科技", 2), ("sz300474", "景嘉微", 2),
            ("sh688111", "金山办公", 1), ("sh603019", "中科曙光", 1), ("sz002261", "拓维信息", 2),
            ("sz300496", "中科创达", 2), ("sz002415", "海康威视", 2), ("sz002236", "大华股份", 2),
            ("sh600588", "用友网络", 2), ("sz300383", "光环新网", 2), ("sh600536", "中国软件", 2),
            ("sz300166", "东方国信", 3), ("sh600845", "宝信软件", 2), ("sz002065", "东华软件", 2),
        ],
    },
    "photovoltaic": {
        "name": "光伏",
        "icon": "☀️",
        "sector_code": "pt02GN2029",
        "stocks": [
            ("sh688599", "天合光能", 2), ("sh688303", "大全能源", 2), ("sh603806", "福斯特", 2),
            ("sz300763", "锦浪科技", 2), ("sz002459", "晶澳科技", 2), ("sz300861", "美畅股份", 3),
            ("sz002006", "精功科技", 3), ("sz300118", "东方日升", 2), ("sh688680", "海优新材", 3),
            ("sh688598", "金博股份", 3), ("sz002056", "横店东磁", 2), ("sh603105", "芯能科技", 3),
            ("sh601222", "林洋能源", 2), ("sh603628", "清源股份", 3),
        ],
    },
    "internet": {
        "name": "互联网",
        "icon": "🌐",
        "sector_code": "sw1_pt01801200",
        "stocks": [
            ("sz300418", "昆仑万维", 3), ("sz002602", "世纪华通", 3), ("sh600570", "恒生电子", 2),
            ("sz300458", "全志科技", 3), ("sz002555", "三七互娱", 3), ("sz300413", "芒果超媒", 3),
            ("sz002405", "四维图新", 3), ("sz002027", "分众传媒", 1), ("sz002624", "完美世界", 2),
            ("sz300251", "光线传媒", 2), ("sh600633", "浙数文化", 2),
            ("sz300133", "华策影视", 3), ("sz002558", "巨人网络", 2), ("sz300017", "网宿科技", 2),
            ("sz300113", "顺网科技", 3), ("sz300031", "宝通科技", 3), ("sz002174", "游族网络", 3),
        ],
    },
    "real_estate": {
        "name": "房地产",
        "icon": "🏠",
        "sector_code": "sw1_pt01801010",
        "stocks": [
            ("sz000002", "万科A", 1), ("sh600048", "保利发展", 1), ("sz001979", "招商蛇口", 2),
            ("sh601155", "新城控股", 2), ("sz002244", "滨江集团", 2), ("sz001872", "招商港口", 2),
            ("sh600823", "世茂能源", 3), ("sh600736", "苏州高新", 3), ("sh600848", "上海临港", 2),
            ("sh600773", "西藏城投", 3), ("sh600064", "南京高科", 3), ("sz000006", "深振业A", 3),
            ("sh600639", "浦东金桥", 3), ("sz000014", "沙河股份", 3), ("sz000011", "深物业A", 3),
            ("sz000029", "深深房A", 3), ("sz000042", "中洲控股", 3),
        ],
    },
    "steel_material": {
        "name": "钢铁有色",
        "icon": "🔩",
        "sector_code": "sw1_pt01801030",
        "stocks": [
            ("sh600019", "宝钢股份", 2), ("sh601899", "紫金矿业", 1), ("sh601600", "中国铝业", 2),
            ("sh603993", "洛阳钼业", 2), ("sh600362", "江西铜业", 2), ("sz000878", "云南铜业", 2),
            ("sh600219", "南山铝业", 2), ("sz000933", "神火股份", 2), ("sz000807", "云铝股份", 2),
            ("sz002497", "驰宏锌锗", 3), ("sh600547", "山东黄金", 2), ("sh600489", "中金黄金", 2),
            ("sz000630", "铜陵有色", 2), ("sh601212", "白银有色", 3), ("sh600392", "盛和资源", 3),
            ("sh600111", "北方稀土", 2), ("sz000831", "中国稀土", 2), ("sz000960", "锡业股份", 2),
            ("sh601958", "金钼股份", 3), ("sh600531", "豫光金铅", 3), ("sh600456", "宝钛股份", 2),
        ],
    },
    "auto": {
        "name": "汽车",
        "icon": "🚗",
        "sector_code": "sw1_pt01801100",
        "stocks": [
            ("sh601633", "长城汽车", 2), ("sh600104", "上汽集团", 2), ("sh601238", "广汽集团", 2),
            ("sz000625", "长安汽车", 2), ("sz002920", "德赛西威", 2), ("sh600733", "北汽蓝谷", 3),
            ("sz000800", "一汽解放", 3), ("sz002284", "亚太股份", 3), ("sz000559", "万向钱潮", 3),
            ("sh600418", "江淮汽车", 2), ("sz002434", "万里扬", 3), ("sh601965", "中国汽研", 2),
            ("sz002126", "银轮股份", 3), ("sh603960", "克来机电", 3), ("sz300507", "苏奥传感", 3),
            ("sz000951", "中国重汽", 2), ("sz002488", "金固股份", 3), ("sz300428", "立中集团", 3),
        ],
    },
    "power_equip": {
        "name": "电力设备",
        "icon": "🔌",
        "sector_code": "sw1_pt01801070",
        "stocks": [
            ("sh600406", "国电南瑞", 1), ("sh600089", "特变电工", 2), ("sz000400", "许继电气", 2),
            ("sz002070", "思源电气", 2), ("sh600312", "平高电气", 2), ("sh601179", "中国西电", 2),
            ("sh688676", "金盘科技", 3), ("sh603416", "信捷电气", 3), ("sz002452", "长高电科", 3),
            ("sz000682", "东方电子", 3), ("sh600268", "国电南自", 3), ("sz002169", "智光电气", 3),
            ("sz300222", "科大智能", 3), ("sz002335", "科华数据", 3), ("sz300001", "特锐德", 2),
            ("sh601877", "正泰电器", 2), ("sh603861", "白云电器", 3), ("sz300040", "九洲集团", 3),
            ("sz002276", "万马股份", 3), ("sh600869", "远东股份", 3),
        ],
    },
    "telecom": {
        "name": "通信",
        "icon": "📡",
        "sector_code": "sw1_pt01801060",
        "stocks": [
            ("sh600498", "烽火通信", 2), ("sh600487", "亨通光电", 2), ("sh600522", "中天科技", 2),
            ("sz002281", "光迅科技", 2), ("sz300502", "新易盛", 2), ("sh603803", "瑞斯康达", 3),
            ("sz002396", "星网锐捷", 3), ("sz300265", "通光线缆", 3), ("sz300638", "广和通", 2),
            ("sz002491", "通鼎互联", 3), ("sh600105", "永鼎股份", 3), ("sz300504", "天邑股份", 3),
            ("sh600776", "东方通信", 2), ("sz002583", "海能达", 2), ("sh603496", "恒为科技", 3),
            ("sz002313", "日海智能", 3), ("sz300050", "世纪鼎利", 3), ("sh688100", "威胜信息", 2),
            ("sh603118", "共进股份", 3), ("sz002161", "远望谷", 3),
        ],
    },
    "etf": {
        "name": "ETF基金",
        "icon": "📊",
        "sector_code": "etf",
        "stocks": [
            ("sh510300", "沪深300ETF", 1), ("sh510500", "中证500ETF", 1), ("sz159915", "创业板ETF", 1),
            ("sh560010", "证券ETF", 2), ("sh512480", "半导体ETF", 2), ("sh512760", "芯片ETF", 2),
            ("sh515790", "光伏ETF", 2), ("sh512690", "酒ETF", 2), ("sh512660", "军工ETF", 2),
            ("sh512800", "银行ETF", 2), ("sh515030", "新能源车ETF", 2), ("sh588000", "科创50ETF", 2),
            ("sh512100", "中证1000ETF", 1), ("sz159845", "中证1000ETF", 1), ("sz159949", "创业板50", 1),
            ("sh513050", "中概互联ETF", 2), ("sz159766", "旅游ETF", 2), ("sh516160", "新能源ETF", 2),
        ],
    },
    "cyber": {
        "name": "网络安全",
        "icon": "🔐",
        "sector_code": "pt02GN2429",
        "stocks": [
            ("sh688023", "安恒信息", 2), ("sh688561", "奇安信", 2), ("sz002212", "天融信", 2),
            ("sh688030", "山石网科", 3), ("sz300730", "科创信息", 3), ("sh600410", "华胜天成", 3),
            ("sz002439", "启明星辰", 2), ("sz300454", "深信服", 1), ("sz002788", "鼎信通讯", 3),
            ("sh688418", "震有科技", 3), ("sz300311", "任子行", 3), ("sh688588", "凌志软件", 3),
            ("sz300768", "迪普科技", 2), ("sh600756", "浪潮软件", 3),
        ],
    },
    "consumer_electronics": {
        "name": "消费电子",
        "icon": "⚙️",
        "sector_code": "sw1_pt01801160",
        "stocks": [
            ("sz002475", "立讯精密", 1), ("sh601231", "环旭电子", 2), ("sz002938", "鹏鼎控股", 2),
            ("sz300136", "信维通信", 2), ("sz002241", "歌尔股份", 2), ("sz002273", "水晶光电", 3),
            ("sh688289", "圣邦股份", 2), ("sz002916", "深南电路", 2), ("sz002009", "天奇股份", 3),
            ("sz002635", "安洁科技", 3), ("sz300115", "长盈精密", 2), ("sz300433", "蓝思科技", 2),
            ("sz002600", "领益智造", 2), ("sz002456", "欧菲光", 2), ("sz300088", "长信科技", 3),
        ],
    },
    # ========== v4.5 新增板块 ==========
    "chemical": {
        "name": "化工新材料",
        "icon": "🧪",
        "sector_code": "sw1_pt01801040",
        "stocks": [
            ("sh600309", "万华化学", 1), ("sh600346", "恒力石化", 2), ("sz002648", "卫星化学", 2),
            ("sh600426", "华鲁恒升", 2), ("sz000830", "鲁西化工", 2), ("sz002601", "龙佰集团", 2),
            ("sh600989", "宝丰能源", 2), ("sh603225", "新凤鸣", 3), ("sh600141", "兴发集团", 3),
            ("sz002250", "联化科技", 3), ("sh600596", "新安股份", 3), ("sh600352", "浙江龙盛", 2),
            ("sz000422", "湖北宜化", 3), ("sh600409", "三友化工", 2), ("sh600160", "巨化股份", 2),
            ("sz002092", "中泰化学", 2), ("sh603260", "合盛硅业", 1), ("sz002408", "齐翔腾达", 2),
        ],
    },
    "machinery": {
        "name": "机械设备",
        "icon": "⚙️",
        "sector_code": "sw1_pt01801080",
        "stocks": [
            ("sh600031", "三一重工", 1), ("sz000157", "中联重科", 2), ("sz002444", "巨星科技", 2),
            ("sz002747", "埃斯顿", 2), ("sh603185", "弘元绿能", 3), ("sh688165", "埃夫特", 3),
            ("sz300024", "机器人", 2), ("sz002698", "博实股份", 3), ("sz300415", "伊之密", 3),
            ("sz002527", "新时达", 3), ("sh603012", "创力集团", 3), ("sz300161", "华中数控", 3),
            ("sh603131", "上海沪工", 3), ("sh600320", "振华重工", 2), ("sh601100", "恒立液压", 1),
            ("sz300124", "汇川技术", 1), ("sz002008", "大族激光", 2), ("sh688017", "绿的谐波", 2),
        ],
    },
    "environmental": {
        "name": "环保节能",
        "icon": "♻️",
        "sector_code": "sw1_pt01801240",
        "stocks": [
            ("sh600323", "瀚蓝环境", 2), ("sh600388", "龙净环保", 2), ("sh603588", "高能环境", 2),
            ("sh600217", "中再资环", 3), ("sh601200", "上海环境", 3), ("sh603686", "福龙马", 3),
            ("sh600292", "远达环保", 3), ("sh601330", "绿色动力", 3),
            ("sh603568", "伟明环保", 2), ("sz300070", "碧水源", 2), ("sz000826", "启迪环境", 3),
            ("sh600008", "首创环保", 1), ("sz002573", "清新环境", 3), ("sz300388", "节能国祯", 3),
            ("sh603903", "中持股份", 3), ("sz000967", "盈峰环境", 2), ("sz300631", "久吾高科", 3),
        ],
    },
    "textile": {
        "name": "纺织服装",
        "icon": "👕",
        "sector_code": "sw1_pt01801190",
        "stocks": [
            ("sz002029", "七匹狼", 3), ("sh603001", "奥康国际", 3), ("sh603116", "红蜻蜓", 3),
            ("sh603877", "太平鸟", 3),
            ("sh600398", "海澜之家", 1), ("sz002563", "森马服饰", 2), ("sz002293", "罗莱生活", 3),
            ("sh603365", "水星家纺", 3), ("sz002154", "报喜鸟", 3), ("sh603839", "安正时尚", 3),
            ("sz002832", "比音勒芬", 2), ("sh603587", "地素时尚", 3), ("sh603555", "贵人鸟", 3),
            ("sz300005", "探路者", 3), ("sh603301", "振德医疗", 2), ("sz002003", "伟星股份", 3),
            ("sh601566", "九牧王", 2), ("sz002763", "汇洁股份", 3),
        ],
    },
    "agriculture": {
        "name": "农业",
        "icon": "🌾",
        "sector_code": "sw1_pt01801050",
        "stocks": [
            ("sz000998", "隆平高科", 2), ("sh600598", "北大荒", 2), ("sz002041", "登海种业", 2),
            ("sz300087", "荃银高科", 3),
            ("sz002385", "大北农", 2), ("sz002311", "海大集团", 1), ("sz300498", "温氏股份", 1),
            ("sz000876", "新希望", 2), ("sh600195", "中牧股份", 2), ("sz000713", "丰乐种业", 3),
            ("sh600354", "敦煌种业", 3), ("sz300189", "神农科技", 3), ("sh600371", "万向德农", 3),
            ("sz002100", "天康生物", 3),
        ],
    },
    "transport": {
        "name": "交通运输",
        "icon": "🚚",
        "sector_code": "sw1_pt01801090",
        "stocks": [
            ("sh600009", "上海机场", 1), ("sz002352", "顺丰控股", 1), ("sh601111", "中国国航", 2),
            ("sz000089", "深圳机场", 2),
            ("sh601006", "大秦铁路", 1), ("sh601816", "京沪高铁", 1), ("sh601919", "中远海控", 1),
            ("sh600018", "上港集团", 1), ("sh601018", "宁波港", 2), ("sh601872", "招商轮船", 2),
            ("sh600029", "南方航空", 2), ("sh600004", "白云机场", 2), ("sh601021", "春秋航空", 2),
            ("sh601866", "中远海发", 2), ("sh601598", "中国外运", 2),
        ],
    },
    "building_materials": {
        "name": "建材",
        "icon": "🧱",
        "sector_code": "sw1_pt01801045",
        "stocks": [
            ("sh600585", "海螺水泥", 1), ("sz000786", "北新建材", 1), ("sh600176", "中国巨石", 2),
            ("sz002271", "东方雨虹", 2), ("sh600801", "华新水泥", 2), ("sh603737", "三棵树", 2),
            ("sz002372", "伟星新材", 2), ("sh603601", "再升科技", 3),
            ("sh601992", "金隅集团", 2), ("sz000012", "南玻A", 2), ("sh601636", "旗滨集团", 2),
            ("sz002791", "坚朗五金", 2), ("sz002043", "兔宝宝", 3), ("sz002918", "蒙娜丽莎", 3),
            ("sz002398", "垒知集团", 3), ("sh603208", "江山欧派", 3),
        ],
    },
    # ========== v5.0 新增板块 ==========
    "media_entertainment": {
        "name": "传媒娱乐",
        "icon": "🎬",
        "sector_code": "sw1_pt01801210",
        "stocks": [
            ("sh603000", "人民网", 2), ("sh603888", "新华网", 2), ("sz000681", "视觉中国", 3),
            ("sz002739", "万达电影", 2), ("sh600977", "中国电影", 2), ("sz300058", "蓝色光标", 2),
            ("sz002400", "省广集团", 3), ("sh603533", "掌阅科技", 3), ("sz301025", "读客文化", 3),
            ("sh600373", "中文传媒", 2), ("sh601928", "凤凰传媒", 2), ("sz300182", "捷成股份", 2),
            ("sh600637", "东方明珠", 2), ("sz300251", "光线传媒", 2), ("sz300133", "华策影视", 3),
        ],
    },
    "hk_stocks": {
        "name": "港股通",
        "icon": "🇭🇰",
        "sector_code": "hk",
        "stocks": [
            ("hk00700", "腾讯控股", 1), ("hk09988", "阿里巴巴", 1), ("hk03690", "美团", 1),
            ("hk01810", "小米集团", 2), ("hk01024", "快手", 2), ("hk09618", "京东集团", 1),
            ("hk09999", "网易", 2), ("hk09888", "百度集团", 2), ("hk01211", "比亚迪股份", 1),
            ("hk02015", "理想汽车", 2), ("hk09866", "蔚来", 3), ("hk09868", "小鹏汽车", 3),
            ("hk02269", "药明生物", 2), ("hk02382", "舜宇光学", 2), ("hk00981", "中芯国际", 2),
            ("hk00388", "港交所", 1), ("hk00005", "汇丰控股", 1), ("hk02318", "中国平安", 1),
        ],
    },
}



# ============================================================
# 全局市场指数配置
# ============================================================
GLOBAL_INDICES = {
    "indices": {
        "sh": {"code": "sh000001", "name": "上证指数"},
        "cyb": {"code": "sz399006", "name": "创业板指"},
        "sz": {"code": "sz399001", "name": "深证成指"},
    },
    "overseas": {
        # 旧码 us.IXIC / hf_GC 已废弃（global_market 取不到就写 0 → 与
        # data.global_assets 自相矛盾）。改复用 server/global_assets.py 实测可用的
        # 标的：asset_key 指向 global_assets 的 key（ndx=usNDX 腾讯；gold=hf_GC 新浪）。
        "ndx": {"code": "usNDX", "name": "纳斯达克100", "asset_key": "ndx"},
        "gold": {"code": "hf_GC", "name": "黄金", "asset_key": "gold"},
    },
}


# ============================================================
# 缓存 TTL（秒）— 优化后的时长
# ============================================================
CACHE_TTL_QUOTES = 60        # 行情缓存 60 秒
CACHE_TTL_KLINE = 600        # K 线缓存 10 分钟
CACHE_TTL_GLOBAL = 120        # 全球市场缓存 120 秒
CACHE_TTL_SCREEN = 120        # 选股条件缓存 120 秒

# ---- v5.0 MCP 数据源缓存 TTL（秒）----
CACHE_TTL_FUND_FLOW = 60          # 资金流向 60 秒
CACHE_TTL_SECTOR_RANK = 60        # 板块排行 60 秒
CACHE_TTL_LHB = 300               # 龙虎榜 5 分钟
CACHE_TTL_MARKET_OVERVIEW = 60    # 市场涨跌分布 60 秒
CACHE_TTL_HOT = 120               # 热门股 2 分钟
CACHE_TTL_PROFILE = 3600          # 个股简况 1 小时
CACHE_TTL_FINANCE = 3600          # 财务报表 1 小时
CACHE_TTL_REPORT = 1800           # 研报列表 30 分钟
CACHE_TTL_NORTH = 300             # 沪深股通 5 分钟
CACHE_TTL_NEWS = 300              # 新闻资讯 5 分钟
CACHE_TTL_SECTOR_STOCKS = 86400   # 板块成分股 24 小时

# ---- T01 新增：OpenBB/DSA 集成各域 TTL（秒）----
# 分层依据见 docs/10-architecture/openbb-dsa-integration-design.md §3.9：
# 实时价只走 QuoteHub（L1），非价格数据走 cache（L2），派生指标走 L3 不缓存。
CACHE_TTL_FINANCE_STATEMENT = 21600   # 财报（三表）6 小时：季频数据，盘中无需刷新
CACHE_TTL_FINANCE_INDICATOR = 21600   # 财务指标（ROE/毛利率等）6 小时
CACHE_TTL_VALUATION = 43200           # 估值分位（PE/PB 历史序列）12 小时
CACHE_TTL_DIVIDEND = 43200            # 分红记录 12 小时
CACHE_TTL_INDICATORS = 300            # 技术指标聚合 5 分钟
CACHE_TTL_RESEARCH = 1800             # 研报 / 一致预期 30 分钟
CACHE_TTL_RISK_METRICS = 900          # 风险指标（Beta/波动/回撤）15 分钟
CACHE_TTL_LINKAGE = 600               # 联动/传导矩阵 10 分钟

# ---- T01 新增：能力角标四态阈值 ----
# /api/meta/capability 的 state 由 (age, ttl, market_open, degraded) 推导，
# 阈值集中在此，避免 meta.py 里散落魔法数字。
CAPABILITY_REALTIME_AGE = 3.0         # ≤3 秒视为「实时」
CAPABILITY_STALE_FACTOR = 1.0         # age > ttl × 该系数 → stale（默认即过期）
# 休市容忍窗口（秒）：收盘/节假日期间数据本就不刷新，24h 内的旧数据判
# ``closed`` 而不是 ``unavailable`` —— 否则用户会把「休市」误读成「行情坏了」。
CAPABILITY_CLOSED_TOLERANCE = 86400.0


# ---- ICT 模型（融入 ICT Trading Model，MIT）默认参数 ----
# 依据：A 股日线实测（见 server/ict_model.py 顶部注释与交付说明）。
# 运行期可用 KV ``ict_config`` 覆盖（不改代码调参），键名与此处一致。
ICT_DEFAULTS = {
    "swing_width": 5,                  # 摆动点宽度（原 skill=2，A 股日线噪音过大）
    "sweep_lookback": 80,              # 扫单回看的摆动点数量
    "setup_lookahead": 12,             # 扫单后等待 MSS/位移的窗口
    "atr_period": 14,
    "displacement_body_multiple": 1.5,  # 位移实体 ≥ 前 20 根实体中位数 × 该倍数
    "displacement_atr_multiple": 1.0,   # 位移振幅 ≥ ATR × 该倍数
    "minimum_rr": 2.0,                 # 最小盈亏比
    "session_timezone": "Asia/Shanghai",  # 修正原 skill 的 America/New_York
}


# ---- T01 新增：功能灰度开关 ----
# 语义：开关控制「新链路是否对外生效」；回退用同名环境变量（大小写不敏感），
# 例如 ``FINANCE_V2_ENABLED=0`` 可在不改代码的情况下关闭财务新链路。
def _env_flag(env_name: str, default: bool) -> bool:
    """读环境变量覆盖开关；未设置则用默认值。``0/false/no/off`` 视为关闭。"""
    raw = os.environ.get(env_name.upper())
    if raw is None:
        return default
    return str(raw).strip().lower() not in ("0", "false", "no", "off", "")


FEATURE_FLAGS = {
    # 基本面/财务新链路（T02）。旧 westock 链路已废弃（CLI 迁 MCP），
    # fail-closed 等于保持全 null，故默认启用新链路；紧急回退用 FINANCE_V2_ENABLED=0。
    "finance_v2_enabled": _env_flag("FINANCE_V2_ENABLED", True),
    # 估值分位（T02）：链路已就绪，但该开关目前无代码读取（占位文档化），保持关闭待 T02 明确
    "valuation_percentile_enabled": _env_flag("VALUATION_PERCENTILE_ENABLED", False),
    "linkage_v2_enabled": _env_flag("LINKAGE_V2_ENABLED", False),      # 联动/传导矩阵（L4/L5 同期）
    "risk_flags_enabled": _env_flag("RISK_FLAGS_ENABLED", False),      # 持仓 6 旗标（T07）
    # 复盘 7 指标：后端已交付（T04，/api/automation/review/metrics），前端 T11 接；开关仅作文档化
    "review_metrics_v2_enabled": _env_flag("REVIEW_METRICS_V2_ENABLED", True),
    "capability_badge_enabled": _env_flag("CAPABILITY_BADGE_ENABLED", False),   # 前端能力角标（T08）
    # ICT 模型（融入 ICT Trading Model）：默认关闭（fail-closed），
    # 打开方式：环境变量 ICT_MODEL_ENABLED=1 后重启（不改代码）。
    "ict_model_enabled": _env_flag("ICT_MODEL_ENABLED", False),
}


def feature_enabled(name: str) -> bool:
    """读取灰度开关；未定义的开关一律视为关闭（fail-closed）。"""
    return bool(FEATURE_FLAGS.get(str(name or "").strip(), False))


# ============================================================
# westock-data CLI 配置（路径惰性解析，避免写死失效路径）
# ============================================================
# 不再把 node / index.js 路径写死为常量，而是运行期按候选列表探测
# （风格对齐 scripts/server_watchdog.py 的 resolve_python）。
# 通过模块级 __getattr__（PEP 562）暴露 WESTOCK_NODE / WESTOCK_SCRIPT：
#   - `from server.config import WESTOCK_NODE` 仍可用（导入时触发解析一次）；
#   - `config.WESTOCK_NODE` 每次访问都惰性解析。
_HOME_DIR = Path.home()
_WORKBUDDY_HOME = Path(os.environ.get("WORKBUDDY_HOME") or (_HOME_DIR / ".workbuddy"))
_NODE_VERSIONS_DIR = _WORKBUDDY_HOME / "binaries" / "node" / "versions"

# 这些目录自身即为 ...\skills（其下按 skill 名探测 scripts\index.js）
_WESTOCK_SKILL_ROOTS = [
    _WORKBUDDY_HOME / "plugins" / "marketplaces" / "cb_teams_marketplace"
    / "plugins" / "finance-data" / "skills",
    _WORKBUDDY_HOME / "plugins" / "marketplaces" / "experts"
    / "plugins" / "stock-partner-team" / "skills",
]
# 缓存版本目录：...\finance-data\<version>\skills\...（按版本号倒序探测）
_WESTOCK_CACHE_ROOT = (
    _WORKBUDDY_HOME / "plugins" / "cache" / "cb_teams_marketplace" / "finance-data"
)
# 仅探测 westock-data（本项目实际数据源）。
# 【永久排除 westock-tool —— 请勿再添加回候选】原因：
#   1) westock-tool 是"选股器"技能，其子命令集为 filter/strategy/label/event/ranking，
#      与本项目 westock.py 需要的 18 个数据子命令（quote/search/sector/profile/news/
#      asfund/changedist/hot/report/rating/consensus/lgt/shareholder/dividend ...）完全不同；
#   2) 它对未知命令返回 rc=0（不走非零退出），会绕过 westock.py 的 `rc != 0` 判断，
#      导致每次调用都真的拉起一个 ~3MB 的 node 进程、拿回"未知命令"文本、解析出空表再返回 None，
#      把"廉价立即失败"退化为"每次白起进程 + 语义失败"，并污染日志与耗时。
# 本项目实际使用的 westock-data v2.0 已迁移到 MCP Tool，其 skills 目录下已无 scripts\index.js，
# 故正常情况下 resolve_westock_script() 会返回 None，从而优雅回退 AkShare。
_WESTOCK_SKILL_NAMES = ("westock-data",)


def _version_key(name: str) -> tuple:
    """把版本目录名转成可比较的元组：纯数字段排在前且按数值比较。

    数字段（版本号）标记为 (1, 数值, "")，非数字段标记为 (0, 0, 文本)；
    这样倒序排序时数字版本目录优先，版本号越大越靠前，非版本目录（如
    current / .locks）排在最后。
    """
    key = []
    for token in re.split(r"[.\-]", name):
        if token.isdigit():
            key.append((1, int(token), ""))
        else:
            key.append((0, 0, token))
    return tuple(key)


def _iter_node_candidates() -> List[str]:
    """按优先级产出 node.exe 候选路径。"""
    candidates: List[str] = []
    try:
        if _NODE_VERSIONS_DIR.is_dir():
            version_dirs = [p for p in _NODE_VERSIONS_DIR.iterdir() if p.is_dir()]
            # 版本号目录按数值倒序，取版本号最大者（如 22.22.2-6）
            version_dirs.sort(key=lambda p: _version_key(p.name), reverse=True)
            for d in version_dirs:
                candidates.append(str(d / "node.exe"))
    except OSError:
        pass
    which_node = shutil.which("node")
    if which_node:
        candidates.append(which_node)
    return candidates


def resolve_westock_node() -> str:
    """惰性解析 node 可执行文件路径，返回第一个存在的候选。

    优先级：① 托管版本目录中版本号最大者 → ② PATH 中的 node。
    全部候选都不存在时返回空字符串 ""（不再回退写死的失效路径），
    以便上层守卫 `not WESTOCK_NODE` 能正确判定并触发优雅降级；函数签名仍为 -> str。
    """
    for cand in _iter_node_candidates():
        try:
            if cand and os.path.exists(cand):
                return cand
        except OSError:
            continue
    return ""


def _iter_westock_script_candidates() -> List[str]:
    """按优先级产出 westock CLI 入口 index.js 候选路径。"""
    skill_parents: List[Path] = list(_WESTOCK_SKILL_ROOTS)
    try:
        if _WESTOCK_CACHE_ROOT.is_dir():
            version_dirs = [p for p in _WESTOCK_CACHE_ROOT.iterdir() if p.is_dir()]
            version_dirs.sort(key=lambda p: _version_key(p.name), reverse=True)
            for vd in version_dirs:
                skill_parents.append(vd / "skills")
    except OSError:
        pass

    candidates: List[str] = []
    for parent in skill_parents:
        for skill in _WESTOCK_SKILL_NAMES:
            candidates.append(str(parent / skill / "scripts" / "index.js"))
    return candidates


def resolve_westock_script() -> Optional[str]:
    """惰性解析 westock CLI 入口脚本路径，返回第一个存在的 index.js。

    全部候选均不存在时返回 None —— 该 skill 已迁移到 MCP、CLI 属预期不可用时，
    上层会优雅降级回退 AkShare。
    """
    for cand in _iter_westock_script_candidates():
        try:
            if cand and os.path.exists(cand):
                return cand
        except OSError:
            continue
    return None


def __getattr__(name: str):
    """PEP 562 模块级惰性属性：让 WESTOCK_NODE / WESTOCK_SCRIPT 按需解析。"""
    if name == "WESTOCK_NODE":
        return resolve_westock_node()
    if name == "WESTOCK_SCRIPT":
        return resolve_westock_script()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


WESTOCK_TIMEOUT = 25        # CLI 超时 25 秒
KLINE_LIMIT = 120           # v4.5: K线增至120条，确保100交易日+20余量


# ============================================================
# 预热配置
# ============================================================
PRELOAD_SECTORS = ["semiconductor", "ai_compute", "new_energy", "pharma", "hk_stocks"]  # 核心板块预热
PRELOAD_DELAY = 5.0       # 启动后延迟预热秒数


# ============================================================
# 默认板块
# ============================================================
DEFAULT_SECTOR = "semiconductor"


# ============================================================
# 策略权重
# ============================================================
STRATEGY_WEIGHTS = {
    "momentum": 0.35,
    "breakout": 0.30,
    "volume": 0.35,
}


# ============================================================
# Agent 配置（v4.0 新增）
# ============================================================
AGENT_CONFIG = {
    # DeepSeek V4 Pro — 从 .env 文件或系统环境变量读取
    "deepseek_api_key": os.environ.get("DEEPSEEK_API_KEY", ""),
    "deepseek_base_url": os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
    "deepseek_model": os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
    "deepseek_timeout": int(os.environ.get("DEEPSEEK_TIMEOUT", "30")),
    "agent_poll_interval": 60,  # 监控Agent轮询间隔（秒）
    "max_concurrent_agents": 3,
}


# ============================================================
# 辅助函数
# ============================================================
def get_sector(sector_key: str) -> dict:
    """获取板块配置，不存在则返回默认板块"""
    return SECTORS.get(sector_key, SECTORS[DEFAULT_SECTOR])


def get_sector_stocks(sector_key: str) -> List[Tuple[str, str]]:
    """获取板块股票列表"""
    sector = get_sector(sector_key)
    return sector["stocks"]


def get_sector_name(sector_key: str) -> str:
    """获取板块中文名称"""
    sector = get_sector(sector_key)
    return sector["name"]


def find_stock_name(code: str) -> str:
    """根据股票代码查找名称"""
    for sector_data in SECTORS.values():
        for stock in sector_data["stocks"]:
            if stock[0] == code:
                return stock[1]
    return code


def get_all_stock_codes() -> List[str]:
    """获取所有板块的去重股票代码列表"""
    seen = set()
    result = []
    for sector_data in SECTORS.values():
        for stock in sector_data["stocks"]:
            code = stock[0]
            if code not in seen:
                seen.add(code)
                result.append(code)
    return result


def get_all_stock_list() -> List[Tuple[str, str, int]]:
    """获取所有板块的去重股票完整列表 [(code, name, priority), ...]"""
    seen = set()
    result = []
    for sector_data in SECTORS.values():
        for stock in sector_data["stocks"]:
            code = stock[0]
            if code not in seen:
                seen.add(code)
                result.append(stock)
    return result


def find_stock_sector(code: str) -> str:
    """根据股票代码查找所属板块 key"""
    for sector_key, sector_data in SECTORS.items():
        for stock in sector_data["stocks"]:
            if stock[0] == code:
                return sector_key
    return DEFAULT_SECTOR


# ============================================================
# 支付系统配置
# ============================================================
DB_DIR = Path(__file__).resolve().parent.parent / "data"
DB_DIR.mkdir(exist_ok=True)

PRICING = {
    "free": {"queries_per_day": 5, "price_fen": 0},
    "pro": {"queries_per_day": 200, "price_fen": 9900},
    "premium": {"queries_per_day": 9999, "price_fen": 29900},
    "family": {"queries_per_day": 9999, "price_fen": 19900},
}
