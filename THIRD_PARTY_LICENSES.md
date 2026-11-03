# 第三方组件与许可证

本项目自身以 **Apache-2.0** 发布（见 `LICENSE`）。以下是随仓库分发的第三方组件及其许可证。
所有组件均与 Apache-2.0 兼容；没有引入 AGPL/SSPL 等强传染性许可的代码。

## 前端（随仓库分发）

| 组件 | 文件 | 许可证 | 说明 |
|---|---|---|---|
| Apache ECharts | `static/js/echarts.min.js` | Apache-2.0 | 图表渲染；保留原始版权声明 |
| Font Awesome Free | `static/vendor/fontawesome-all.min.css`、`static/vendor/webfonts/*` | 代码 MIT / 图标 CC BY 4.0 / 字体 SIL OFL 1.1 | 图标与字体分属三种许可，分发时需同时保留 |
| Tailwind CSS（CDN 构建） | `static/vendor/tailwind-cdn.js` | MIT | 仅用于样式工具类 |
| Vanilla-tilt.js | `static/vendor/vanilla-tilt.min.js` | MIT | 卡片悬浮效果 |

> 建议：发布前在上述文件头部保留或补充版权与许可注释；若上游未附，可在本文件注明来源链接。

## 后端（Python 依赖，不随仓库分发）

| 依赖 | 许可证 | 用途 |
|---|---|---|
| FastAPI | MIT | Web 框架 |
| uvicorn | BSD-3-Clause | ASGI 服务器 |
| numpy | BSD-3-Clause | 数值计算 |
| pandas | BSD-3-Clause | 数据处理 |
| httpx | BSD-3-Clause | HTTP 客户端 |
| psutil | BSD-3-Clause | 进程/系统指标 |
| aiofiles | Apache-2.0 | 异步文件 IO |
| akshare | MIT | 可选数据源适配 |

安装依赖即由使用者自行接受上述许可证；`requirements.txt` 仅声明版本范围。

## 数据来源与使用边界

本项目**不随仓库分发任何行情数据**。运行时通过公开接口访问：

- 腾讯财经行情（`qt.gtimg.cn` / `web.ifzq.gtimg.cn`）
- 新浪财经行情（`hq.sinajs.cn`）
- 东方财富（`push2.eastmoney.com`、`datacenter-web.eastmoney.com`）
- AkShare（聚合公开数据源，MIT）

使用者需自行遵守各数据源的服务条款与访问频率限制。本仓库不包含、也不再分发这些数据的快照。

## 文档与图片

`docs/assets/` 下的截图由本项目生成，随 Apache-2.0 一并授权。若截图包含第三方界面元素，
仅作说明用途，不代表对其商标或内容的授权。

## 合规检查清单

- [ ] 新增前端库时，在本文件登记名称、版本、许可证、来源链接
- [ ] 不引入 AGPL/SSPL 代码；如需参考，只借鉴思路、不复制实现
- [ ] 依赖升级后复核许可证是否变化
- [ ] 发布包中保留 `LICENSE`、`NOTICE`（本文件）与第三方版权声明
