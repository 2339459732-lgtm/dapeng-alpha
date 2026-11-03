"""对外契约层（contract-envelope.v1）。

只放「跨模块共享的响应/错误契约」，不放业务逻辑；上层（routers）可以依赖，
下层（core/data/engine）不得反向依赖本包。
"""
from server.contracts.envelope import (
    SCHEMA_HEADER,
    SCHEMA_VERSION,
    ErrorCode,
    code_for_status,
    degraded_payload,
    fail,
    ok,
)

__all__ = [
    "SCHEMA_VERSION",
    "SCHEMA_HEADER",
    "ErrorCode",
    "code_for_status",
    "ok",
    "fail",
    "degraded_payload",
]
