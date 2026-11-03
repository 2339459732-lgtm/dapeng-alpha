# 私钥管理（不改变现有口令）

> 当前部署：`ACCESS_KEY` 为所有者指定的 12 位口令，**只存在于 `.env`，不写入任何文档或代码**；运维不得擅自更换。
> 本文只规定"怎么存、怎么查、怎么轮换、泄露了怎么办"，不改动口令本身。

## 一、存放位置与权限

| 项 | 位置 | 要求 |
|---|---|---|
| 主私钥 | `.env` 的 `ACCESS_KEY` | 仅所有者账户可读；`.env` 已被 `.gitignore` 忽略 |
| 备选 | `ACCESS_KEY_FILE=/run/secrets/dapeng_key` | Docker/K8s secret 挂载，适合服务器 |
| 备份 | `backups/.env.bak-*` | 同机备份，目录已被忽略；不要上传网盘 |

Windows 收紧权限（只保留所有者、SYSTEM、Administrators）：

```text
icacls .env /inheritance:r /grant:r "%USERNAME%:F" "SYSTEM:F" "Administrators:F"
```

Linux：

```bash
chmod 600 .env && chown "$USER" .env
```

## 二、日常自检

```bash
python scripts/ops/key_status.py          # 指纹/长度/来源/是否泄露/备份数，不显示明文
python scripts/ops/key_status.py --json   # 给监控或流水线用
```

返回码 `0` = 正常；`1` = 有问题（缺失、过短、泄露、`.env` 未忽略等），可直接接入定时任务。

## 三、轮换（需要所有者同意才做）

```bash
# 1) 生成新私钥（不覆盖旧值，先并存）
python -c "import secrets;print(secrets.token_urlsafe(48))"
# 2) 写入 .env 的 ACCESS_KEY，并同步 ACCESS_KEY_MIN_LEN（若长度变化）
# 3) 重启服务（计划任务/看门狗会自动拉起）
# 4) 所有人重新登录；旧会话因服务端会话表清空而立即失效
```

若希望"先让新私钥生效、再下线旧私钥"的平滑过渡，本项目**不支持双私钥并存**
（单租户闸门的设计取舍）；如需该能力，升级为多密钥方案。

## 四、泄露处置（按顺序）

1. **立即换私钥**（同上）并重启；
2. `POST /api/auth/revoke-all` 清空所有会话；
3. 查看 `logs/auth.log`，确认是否有异常 IP 登录成功；
4. 若私钥曾进入 git 历史：重写历史（`git filter-repo`）并强推，然后视为已公开；
5. 检查 `data/` 是否被未授权访问（持仓、复盘、用户表）。

## 五、为什么测试里不能写真实私钥

`tests/` 会随开源仓库公开。历史上有一次把线上私钥写进用例，已改为合成口令
`test-key-0712-abcd`。规则：**任何进入仓库的文件都不得包含真实私钥**，
由 `scripts/ops/scan_secrets.py` 与 pre-commit 钩子强制。

## 六、相关命令

```bash
python scripts/ops/install_hooks.py     # 安装提交前扫描钩子
python scripts/ops/scan_secrets.py      # 手动扫描已跟踪文件
python scripts/ops/key_status.py        # 私钥状态
```
