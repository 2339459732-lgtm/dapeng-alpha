# 贡献指南

## 环境

```bash
git clone <repo> && cd dapeng-water
python -m venv .venv
# Windows: .venv\Scripts\activate
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # 本地可不配 ACCESS_KEY
python -m uvicorn server.main:app --reload --port 8900
```

前端是原生 HTML/CSS/JS，**没有构建步骤**，改完刷新即可。静态资源引用带版本号
（`?v=N`），改 JS/CSS 后必须递增，否则浏览器会命中旧缓存。

## 提交前必须跑

```bash
python scripts/verify/checkup.py    # 一键：pytest + 架构守卫 + 运行态体检 + 性能预算
python scripts/verify/checkup.py --offline   # 服务没起时只跑前两项
```

单独跑也可以：

```bash
python -m pytest tests/ -q          # 全量测试
python scripts/ci_guard.py          # 架构测绘 + 拆分计划 + 全量测试
```

`ci_guard` 会检查三条硬约束：

1. **分层依赖**：`providers(0) → infra(0) → core(1) → data(2) → engine(3) → api(4)`，
   不允许反向 import（`tools/arch_report.py --check`）；
2. **单文件 ≤ 900 行**，超了就拆（`server/lhb_*` 的拆分就是这么来的）；
3. **路由快照**：新增/删除端点后必须更新基线：
   ```bash
   UPDATE_ROUTES=1 python -m pytest tests/test_contracts.py
   ```

## 代码约定

- 注释解释**为什么**，不复述代码在做什么；关键取舍要写清「不这么做会怎样」。
- 缺数据一律 `None` + `reason`，**不用 0 冒充**；推断要标注等级（如 `inference: seal-time`）。
- 不写死机器路径：项目内用 `Path(__file__)` 推导，用户目录用 `Path.home()` 或环境变量。
- 不提交密钥、真实持仓、用户信息；`.gitignore` 对 `data/` 是白名单制。
- 新增鉴权相关逻辑时同步更新 `docs/10-architecture/AUTH.md` 与 `SECURITY.md`。

## 新增接口

1. 路由放 `server/routers/`，用 `APIRouter(prefix=...)`；
2. 返回 `{ok, data?, message, source?, as_of?, degraded?}` 信封（见 `server/contracts/envelope.py`）；
3. 更新路由基线（见上）；
4. 若接口涉及新页面，记得在 `static/js/project-docs-data.js` 登记。

## 提交信息

```
<type>(<scope>): <subject>

type: feat | fix | refactor | docs | test | chore
```

例：`feat(auth): 增加访问密钥登录与登录审计`

## Pull Request 检查表

- [ ] `pytest` 全绿，`ci_guard` 三 PASS
- [ ] 新增逻辑有测试；边界（空值/异常/无权限）覆盖
- [ ] 无新增硬编码路径、密钥、个人信息
- [ ] 涉及 UI 的附截图；涉及接口的附请求示例
- [ ] 文档与 CHANGELOG 已更新（行为变更必须写）

## 安全

发现漏洞请走 [`SECURITY.md`](SECURITY.md) 的私下报告流程，不要开公开 issue。
