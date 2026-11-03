# DAPENG Alpha · Impeccable 接入说明

> 目的：把 [Impeccable](https://impeccable.style) 的设计流程与「设计检测器」接到这个仓库，
> 让 AXON 图版的每次 UI 改动都有可复现的设计上下文与机械检查，而不是凭手感改样式。
> 接入时间 2026-10-24；基线对应 AXON v35。

## 1 · 已接入的内容

| 项 | 位置 | 作用 |
|---|---|---|
| 技能本体 | `D:\轩\docs-graph-design\.agents\skills\impeccable\` | 从 `~\.agents\skills\impeccable` 复制进仓库（63 个文件），项目级 harness 可直接发现，不依赖全局安装 |
| 设计上下文 | `PRODUCT.md`、`DESIGN.md` | Impeccable 的 context 命令读取这两个文件；`DESIGN.md` 已记录到 v35 |
| 设计检测器 | `D:\轩\docs-graph-design\tools\impeccable-check.ps1` | 对 `src/plate.js`、`src/plate.css`、`src/index.template.html` 跑 `detect --json`，输出问题清单 |
| 检测器本体 | `~\.agents\skills\impeccable\scripts\impeccable.cmd` | 自包含二进制，不依赖 Node |

> 网络受限说明：本机访问 `github.com` 超时，`impeccable install`（下载编译版技能包）会失败；
> 因此采用「本地技能目录复制 + 本地检测器脚本」的方式接入，技能与检测器均可正常使用。

## 2 · 日常工作流

```
# 1. 动 UI 前：加载设计上下文（PRODUCT.md / DESIGN.md / 本文件 / craft-floor）
# 2. 改完 UI：跑机械检测
powershell -NoProfile -ExecutionPolicy Bypass -File D:\轩\docs-graph-design\tools\impeccable-check.ps1
# 3. 有发现时：能改就改；属于本项目世界观的，在 DESIGN.md 论证后保留
```

- 设计类请求默认按 Impeccable 的 **Operate / Read** 模式处理：精确、克制、可读优先；
- 本项目世界是 **仪器图版 / 制图手册**（`DESIGN.md` §2、§5）：深色纸面、发丝线、
  暖金只表权威与交互、钢蓝表冷数据、颜色只表可信层级；
- "星云壁纸式泛光、紫雾、无意义渐变"会被检测器或人工评审拦下。

## 3 · 当前检测基线（2026-10-24）

命令输出（2 条）：

| 级别 | 规则 | 位置 | 判定 |
|---|---|---|---|
| advisory | `codex-grid-background` | `src/plate.css:998` | **保留并论证**：这是制图图纸的测量底纹，与 7 条层级环、72 根刻度、四角角标共同构成"星图制版"语言，属 craft-floor 允许的 blueprint/measurement surface |
| warning | `flat-type-hierarchy` | `src/index.template.html` | **核实为误报**：检测器只看模板内联样式（默认 16px）；实际字号在 `plate.css`。浏览器实测：`h1.sh-title` 21px / `h2.blk-h` 11px（小字号分节标签）/ body 13px，h1:body = 1.62:1，标题层级成立 |

> 结论：零个需要修的真实问题；两项均已在 DESIGN.md / 本文件留下判定依据。

## 4 · 与其他文档的关系

- `DESIGN.md`：视觉规范与 v1–v35 变更记录（Impeccable context 读它）；
- `PRODUCT.md`：产品与读者、成功标准（旧 schema，`impeccable init` 可补齐，未执行）；
- [AXON-GRAPH-DESIGN.md](AXON-GRAPH-DESIGN.md)：AXON 图版实现版本记录（v34 起与本文互相引用）；
- [UI-VISUALIZATION-ROADMAP.md](UI-VISUALIZATION-ROADMAP.md)：界面路线与数据源登记。

## 5 · 未接入的部分（需要用户确认）

- **项目级 init 访谈**：Impeccable `init` 会多轮访谈补齐 `PRODUCT.md` 新 schema
  （Positioning / Operating Context / Evidence on Hand / Product Principles）。
  这会新增产品事实，不能凭空推断，需要你确认后再跑。
- **自动 hook**：`install` 会写 provider hook manifest，让每次 UI 编辑后自动跑检测器；
  当前用 `tools/impeccable-check.ps1` 手动触发（等价效果，少一层自动）。
