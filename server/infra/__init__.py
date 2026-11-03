"""DAPENG Alpha · 运行期基础设施（护城河）

模块划分：
- settings   ：环境配置集中解析与启动期校验
- security   ：纯 ASGI 安全中间件（请求 ID / 限流 / 体积上限 / 安全响应头）
- health     ：存活、就绪、系统状态、数据完整性、手动备份端点
- backup     ：状态文件 + SQLite 的一致性备份与留存
- logging_setup：滚动文件日志
"""
