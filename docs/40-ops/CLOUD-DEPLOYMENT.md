# 云服务器部署与成本（2026-10-30）

> 回答两个问题：云服务器能不能联网跑这套系统？大概多少钱？

## 一、结论先说

**能。** 国内主流「轻量应用服务器 / 云服务器」默认带公网 IP + 出网带宽，24×7 在线，可运行本系统（Python/FastAPI + SQLite + 静态页面），并主动访问腾讯/新浪/东财/Tushare 等行情接口；手机可通过公网/Tailscale/Cloudflare Tunnel 访问。

但要注意两点：

1. **入站需在安全组放行端口**（或更推荐：不暴露公网，用 Tailscale / Cloudflare Tunnel）；
2. **不是所有数据源在 IDC 都通**：东财 push2 在国内部分机房也会被阻断（本机都如此），腾讯/新浪通常可用；买之前先买 1 个月实测。

## 二、价格参考（2026 年，按常规价/活动价，实际以官网为准）

| 厂商/规格 | 配置 | 参考价 | 适合 |
|---|---|---|---|
| 腾讯云轻量 2C2G | 3–4M 带宽，40–60G SSD | 常规约 ¥60–90/月；新用户活动常见 ¥99–199/年 | **推荐起步**，够跑本系统 |
| 腾讯云轻量 1C2G | 3M，40G | 约 ¥40–60/月 | 只跑服务+手机访问，勉强够 |
| 阿里云轻量 2C2G | 3–5M，40–60G | 常规约 ¥70–110/月；活动价更低 | 同上 |
| 阿里云 ECS 突发 t6 2C4G | 按量/包年 | 活动约 ¥99–300/年（限新用户） | 想跑回测/多任务 |
| 华为云/其他 | 2C2G | 约 ¥50–100/月 | 备选 |

补充成本：

- 域名（可选）：¥30–80/年；
- TLS：Let's Encrypt 免费；
- Tushare 积分：基础免费，进阶按需；
- 流量：本系统页面小、行情请求在服务端，3–5M 带宽足够；注意月流量包（轻量通常 200–1000GB/月）。

**最低可行预算：约 ¥5–10/月（活动机）到 ¥60–90/月（常规 2C2G）。**

## 三、推荐架构（云 + 本地双模）

```
本地 Windows（主控/开发/通达信数据）
   │  同步代码/配置（Git 或 rsync）
   ▼
轻量云 Linux（24×7 在线）
   ├─ FastAPI (uvicorn) + systemd 守护
   ├─ SQLite（trading/api 数据，定期备份）
   ├─ Caddy/Nginx（可选 TLS）
   ├─ Tailscale / Cloudflare Tunnel（手机安全访问）
   └─ 定时任务：盘前推送、盘后复盘、备份
手机：PWA/mobile.html + 推送
```

- 本地保留：通达信客户端 + `vipdoc` 数据准备、回测、开发；
- 云端负责：24h 在线、行情采集、决策推送、手机访问；
- 两边通过 Git/备份同步文档与配置，**不要求实时双写**（先跑通单向）。

## 四、部署步骤（Linux 云服务器）

```bash
# 1. 基础环境（Ubuntu 22.04）
sudo apt update && sudo apt install -y python3.11 python3.11-venv git sqlite3
# 2. 取代码（Git 或 scp 上传项目）
git clone <your-repo> dapeng && cd dapeng
python3.11 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
# 3. 配置 .env（HOST=127.0.0.1, PORT=8900, 数据源 token）
# 4. systemd
sudo tee /etc/systemd/system/dapeng.service >/dev/null <<'EOF'
[Unit]
Description=DAPENG Alpha
After=network.target
[Service]
User=dapeng
WorkingDirectory=/home/dapeng/dapeng
ExecStart=/home/dapeng/dapeng/.venv/bin/python -m uvicorn server.main:app --host 127.0.0.1 --port 8900
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
EOF
sudo systemctl enable --now dapeng
# 5. 健康检查
curl http://127.0.0.1:8900/healthz
```

## 五、安全（必须做）

1. **不要**把 8900 直接暴露公网且无鉴权（当前 dashboard 无登录）；
2. 优先 Tailscale：手机/电脑加入同一 tailnet，用私有 IP 访问；
3. 或用 Cloudflare Tunnel + Cloudflare Access（邮箱/OTP 登录）；
4. 防火墙只放行 SSH（限制来源 IP）与必要端口；
5. 每日备份 SQLite 与配置到对象存储/本地；
6. 系统 `.env` 含 API key，权限 600，不提交 Git。

## 六、迁移检查清单

- [ ] 实测云端能否访问：腾讯行情、新浪列表、东财数据中心、东财 push2（决定是否保留板块实时）
- [ ] `NO_PROXY=*` 已设置（服务端不走代理）
- [ ] 时区设为 Asia/Shanghai
- [ ] 定时任务：08:40 盘前推送、15:10 盘后复盘、每日备份
- [ ] 手机端 `mobile.html` 可访问、推送可收到
- [ ] 备份恢复演练一次

## 七、本地 vs 云：怎么选

| 场景 | 建议 |
|---|---|
| 只在宿舍用、能保证电脑常开 | 本地即可，0 成本 |
| 人不在电脑旁、需要 24h 在线/推送 | 云服务器 + Tailscale（推荐） |
| 要跑通达信盘后数据 | 本地 Windows 保留；云端用 Tushare/腾讯/新浪替代 |
| 预算极低 | 活动轻量机 ¥99/年 先跑起来，再按需升级 |
