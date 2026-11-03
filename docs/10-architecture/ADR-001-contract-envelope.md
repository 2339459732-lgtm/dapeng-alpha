# ADR-001：统一响应信封（contract-envelope.v1）

- 状态：已采纳（2026-11-02）
- 决策者：架构治理轮次 P3
- 影响面：`server/routers/*`、`server/main.py`、前端 `static/js/*`

## 背景

2026-11 之前，响应信封由各路由自建，实测存在三类漂移：

| 现象 | 具体表现 |
|---|---|
| 成功信封字段不一致 | 有的 `{"ok","data","message"}`，有的只有 `{"ok","data"}` |
| 失败信封字段不一致 | `message` 与 `reason` 混用；`data` 有时缺失 |
| 失败无稳定错误码 | 前端只能匹配中文文案判断分支，文案一改即断 |

路由层还出现了 5 份重复的 `_ok` / `_fail` 实现（`automation` / `evolution` /
`etf` / `ict` / `review`），改一处漏四处。

## 决策

新增 `server/contracts/envelope.py`，只收敛**传输层信封**：

```python
ok(data=None, message="ok", **extra)          # 成功
fail(message, status_code=400, *, code=None, detail=None, **extra)   # HTTP 4xx/5xx
degraded_payload(reason, *, source=None, as_of=None, age=None, ...)  # 200 + ok:false
```

并给出：

- `SCHEMA_VERSION = "1.0"`，随信封字段增删 +0.1、语义变更 +1.0；
- `ErrorCode`（`bad_request` / `not_found` / `upstream_timeout` / `data_degraded` …）；
- `code_for_status()`：HTTP 状态码 → 默认错误码的兜底映射；
- 响应头 `X-Schema-Version`，客户端据此做兼容判断，不必改 payload。

**领域字段不进入契约层**：`source` / `as_of` / `age` / `degraded` 由调用方
通过 `extra` 追加，契约层不猜测业务语义。这一条是为 `etf` / `ict` 这类
「降级信封」（`degraded_payload`）保留的出口。

## 为什么不用全局响应拦截器改写所有 178 个路由

改写全部 payload 会同时改动前端 18 个消费点与 243 项测试的断言基线，
收益（字段更整齐）远小于回归风险。因此采用**渐进迁移**：

1. 先建契约模块与响应头（零 payload 风险）；
2. 把**完全同构**的 3 份 `_ok` / `_fail` 直接换成别名（`automation_common` /
   `evolution` / `review`），调用点一行不动；
3. `etf` / `ict` 的领域降级信封暂不动，只在其内部复用 `degraded_payload`
   的字段约定（后续按域逐个迁移）。

## 后果

- 正面：错误码可被前端稳定分支；响应头提供版本协商；重复实现从 5 份降到 3 份
  （`etf` / `ict` 仍保留领域信封）。
- 负面：`ok()` 输出比旧实现多 `code` / `schema_version` 两个键，payload 略大
  （约 40 字节/响应）。已确认前端按键取值、不受影响。
- 守卫：`tests/test_contracts.py` 冻结信封字段与状态码映射；`app.openapi()`
  可生成性单独成测（拆分模块最容易坏的隐式契约）。

## 相关

- `docs/10-architecture/SYSTEM-OPTIMIZATION-2026Q4.md`（P3 阶段）
- `tests/test_contracts.py`
