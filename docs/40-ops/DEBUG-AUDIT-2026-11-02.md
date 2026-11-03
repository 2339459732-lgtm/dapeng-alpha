# 系统调试与修复记录（debug-audit 2026-11-02）

> 范围：服务与配置、缓存处理逻辑（读写/失效/并发/异常回退）、已暴露的安全面
> 结果：**修复 8 项缺陷**，新增 34 项自动体检（**0 FAIL**；PASS/WARN 随工作负载在 31–32 / 2–3 之间浮动，WARN 项固定为「ADMIN_TOKEN 未配置 / 链路新鲜度瞬时陈旧 / OpenAPI 本机可达」）
> 基线：`pytest 357 passed`、`ci_guard` 三闸门 PASS、5 个 Playwright 验收全绿

---

## 一、体检入口

```bash
python scripts/verify/system_audit.py          # 34 项，按领域分组
python scripts/verify/system_audit.py --gate   # 出现 FAIL 即退出码 1
python scripts/verify/system_audit.py --json data/audit_baseline.json
```

覆盖五块：**服务**（端口/绑定地址/健康）、**配置**（JSON 可解析、运行期校验、密钥存在性）、
**上游**（新浪/东财×2/腾讯/LLM 端点）、**缓存**（账实一致、水位、过期堆积、命中率、链路新鲜度、耗时可观测）、
**存储与一致性**（data 可写、SQLite quick_check、关键文件新鲜度、8 个端点信封齐备）、
**安全**（响应头、Host 校验、CORS、备份文件、用户信息脱敏、OpenAPI 暴露面）。

---

## 二、缓存：四个缺陷

### C1 过期条目被取走时漏扣字节（账实不符）

| 项 | 内容 |
|---|---|
| 定位 | `server/cache.py::get()` 中过期分支直接 `self._store.pop(key, None)`，没有 `self._bytes -= entry.nbytes` |
| 影响 | 每次「读到过期条目」都会把该条目的字节永久留在账上。运行久了 `bytes` 虚高，触发本不该发生的 LRU 淘汰，表现为「缓存没满却开始丢热点数据」「命中率莫名下降」。这类故障不会报错，只会慢慢变慢 |
| 修复 | 新增 `_drop(key)` 作为**所有删除路径的唯一出口**（pop + 扣字节），`get()/purge_expired()/delete()` 全部改走它 |
| 验证 | `test_expired_get_keeps_byte_accounting`；线上 `累计 2024764 B / 实算 2024764 B`（账实一致） |

### C2 单条超大对象写入会先删掉旧值

| 项 | 内容 |
|---|---|
| 定位 | `set()` 先 `pop` 旧条目再判断 `nbytes > max_bytes`，超预算时直接 `return` |
| 影响 | 上游偶发返回一个异常大 payload（例如全市场明细被塞进聚合接口），写入被拒——但**上一份好数据已经被删了**。结果是一个坏响应清空了热点 key，后续所有请求重新打上游 |
| 修复 | 把体积估算提到最前：超预算时记 `oversize_rejected` 并直接返回，**保留旧值**；真正写入逻辑移到 `_set_locked()` |
| 验证 | `test_oversize_does_not_destroy_previous_value`（2MB 对象写不进 1MB 预算的缓存，旧值仍可读） |

### C3 上游抛异常时不回落旧值

| 项 | 内容 |
|---|---|
| 定位 | `get_or_fetch_ex()` 只处理 `fetch_fn` 返回 `None` 的情况；`fetch_fn` **抛异常**时异常直接冒泡 |
| 影响 | 文档承诺「取数失败 → 沿用上一份缓存并标 stale」，但真实上游超时/403 大多是以异常形式出现。结果是：明明有一份 3 分钟前的数据可用，接口却返回 500 或空 |
| 修复 | `try/except` 包住取数：异常一律按「失败」处理，有旧值则返回 `stale+degraded`（reason 里带异常类型），无旧值返回 `(None, degraded)` |
| 验证 | `test_fetch_exception_falls_back_to_stale`、`test_fetch_exception_without_stale_returns_degraded_none` |

### C4 并发无锁

| 项 | 内容 |
|---|---|
| 定位 | `OrderedDict` 的 `move_to_end/pop/popitem` 与 `_bytes` 自增自减没有同步；缓存会被事件循环与线程池（`asyncio.to_thread` 里的同步抓取）共同访问 |
| 影响 | 条目与字节计数可能交错更新，出现「条目在、字节数错」或 `RuntimeError: OrderedDict mutated during iteration` |
| 修复 | 引入 `threading.RLock`（可重入，避免 `set → purge → _drop` 之间自锁），读方法加锁；`iter_entries()` 改为**先快照再迭代**，不把锁跨在 `yield` 上 |
| 验证 | `test_concurrent_mutation_keeps_books_straight`（8 线程 × 120 次混合读写删，结束账实一致、无异常）、`test_iter_entries_snapshot_survives_delete_during_iteration` |

> 附带：`stats()` 新增 `recomputed_bytes`（把条目实际占用重新加一遍），让「账实一致」变成可观测项——
> 否则上面这类缺陷只能靠肉眼发现。

---

## 三、安全：三个加固项

### S1 缺少 Host 头校验（DNS 重绑定）

| 项 | 内容 |
|---|---|
| 定位 | 只监听 `127.0.0.1`，管理接口用 `request.client.host` 判「本机来源」；没有任何 Host 校验 |
| 影响 | 浏览器里的恶意页面可把自有域名解析到 `127.0.0.1` 再发请求：连接确实来自本机，`client.host` 也是 `127.0.0.1`，回环判断被绕过。CORS 只能阻止**读取响应**，阻止不了副作用（POST/PUT 仍会执行） |
| 修复 | 增加 `TrustedHostMiddleware`，白名单 `127.0.0.1/localhost/[::1]/testserver`（可用 `ALLOWED_HOSTS` 覆盖）；攻击者域名的 Host 头直接被 400 拒掉 |
| 验证 | 审计项「Host 头校验」：`Host: evil.example.com → 400` |

### S2 缺少基础安全响应头

| 项 | 内容 |
|---|---|
| 定位 | 全站只设置了 `Cache-Control`，没有 MIME 嗅探防护与防套壳 |
| 影响 | 静态文件被浏览器猜成脚本执行（nosniff 缺失）；页面可被第三方站点 iframe 套壳诱导点击（本地管理台尤其敏感）；外链会把本机地址带进 Referer |
| 修复 | `SecurityHeadersMiddleware` 统一注入 `X-Content-Type-Options: nosniff`、`X-Frame-Options: DENY`、`Referrer-Policy: no-referrer`、`Permissions-Policy`。**CSP 暂不启用**：本系统页面大量内联脚本/样式，一条不精确的 CSP 会直接打白，且无法在无构建环境下验证 |
| 验证 | 审计项「响应头 ×3」全部 PASS |

### S3 用户列表回传完整手机号

| 项 | 内容 |
|---|---|
| 定位 | `GET /api/admin/users` 直接返回 `users.json` 原始行，含 `guardian_phone` |
| 影响 | 管理接口虽限本机，但列表页只需核对身份，不需要完整号码；一旦日志/截图/远程协助外泄即为个人信息泄露 |
| 修复 | 列表接口对手机号脱敏（`138****8000`）；完整值仍可本机直接读文件获得，不经 HTTP |
| 验证 | 审计项「用户信息脱敏」PASS |

---

## 四、逐项核对（无缺陷，但要留证）

| 领域 | 核对项 | 结果 |
|---|---|---|
| 服务 | 主服务 8900 / 看门狗 8899 均**只监听 127.0.0.1** | PASS |
| 服务 | 健康检查 HTTP 200 · 18ms；看门狗近 2 小时仅 2 次启动（无崩溃循环） | PASS |
| 配置 | `brain_config.json` / `decision_config.json` 可解析；运行期校验 `issues=0 warnings=0`，sha=`7486c6…` | PASS |
| 配置 | `.env` 八项键位齐全；LLM Key 已配置（仅报存在性，不回显） | PASS |
| 上游 | 新浪计数 332ms · 东财北交所 254ms · 腾讯行情 204ms · 东财基本面 313ms · LLM 端点 TCP 可达 | PASS |
| 存储 | `data/` 可写；`api_calls.db`(21MB) 与 `trading.db`(9MB) `quick_check=ok` | PASS |
| 一致性 | 8 个端点（行情/快照/情绪/追踪/调查/专家/对证/配置）信封字段齐备 | PASS |
| 缓存 | 账实一致、水位 3.0%（122/800 条）、过期堆积 0、后台回收运行、命中率 80.7% | PASS |
| 缓存 | 链路 7/7 fresh，全部资源有耗时上报 | PASS |
| 安全 | CORS 仅白名单来源（恶意 Origin 无 ACAO）；`static` 备份文件 404 | PASS |
| 代码 | 无 `os.system` / `eval` / `exec` / `pickle.loads`；`subprocess` 仅一处且用 `create_subprocess_exec`（无 shell） | PASS |

### 仍存在的两项 WARN（有意保留）

| 项 | 说明 | 何时必须处理 |
|---|---|---|
| `ADMIN_TOKEN` 未配置 | 管理接口退化为「仅本机回环可用」。当前是单机个人工具，可接受；但若改为对外提供服务，必须先配令牌 | 任何公网/局域网暴露之前 |
| `/openapi.json` 可达 | 本机调试与 `tools/gen_api_doc.py` 依赖它 | 对外部署时关闭（`openapi_url=None`） |

---

## 五、回归结果

```
pytest tests -q                     357 passed（新增 8 项缓存审计用例）
python scripts/ci_guard.py          架构测绘 / 拆分计划 / 全量测试 三 PASS
python scripts/verify/system_audit.py --gate     34 项：0 FAIL，退出码 0（WARN 2–3 项，均为已知姿态）
python scripts/verify/perf_budget.py --gate      10 条读路径 p95 全部达标，并发错误率 0
verify_layer_crosscheck / hub_merge / market_merge / armillary_drag / split_round
                                    18 / 21 / 19 / 17 / 13 全 PASS
```

---

## 六、方法说明（为什么这类缺陷能藏这么久）

1. **账实不符不会报错**：字节虚高只会让缓存提前淘汰，症状是「慢」「命中率低」，没有任何异常日志。
   → 对策：把「实算字节」做成一等观测字段（`recomputed_bytes`），并写进体检。
2. **异常路径没人测**：契约写的是「失败回落旧值」，但只有返回 `None` 被覆盖，抛异常没人管。
   → 对策：测试直接 `raise`，而不是 `return None`。
3. **并发问题靠压测才现形**：单线程跑一切正常。
   → 对策：8 线程混合读写删的小压测 + 结束后的账实断言。
4. **「本机来源」不等于「本机用户」**：DNS 重绑定把这两件事拆开了。
   → 对策：再加一层 Host 校验，两个条件同时满足才算本机。
