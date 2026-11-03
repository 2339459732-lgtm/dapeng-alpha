"""对外契约层：统一响应信封与错误码（contract-envelope.v1）。

为什么需要这一层
----------------
2026-11 之前各路由各自写 ``_ok`` / ``_fail``，字段名与语义漂移：

* 成功信封有 ``{"ok","data","message"}`` 也有 ``{"ok","data"}``；
* 失败信封有 ``{"ok":false,"message"}`` 也有 ``{"ok":false,"reason"}``；
* 失败**没有稳定错误码**，前端只能靠中文文案判断分支。

本模块把「传输层信封」收敛为一处：

* :func:`ok` —— 成功信封；
* :func:`fail` —— 失败信封（HTTP 4xx/5xx + 错误码）；
* :func:`degraded_payload` —— 「HTTP 200 但 ok:false」的**降级信封**，
  用于行情不可得等「正常返回、数据不可用」场景（与传输失败区分开）。

领域字段（``source`` / ``as_of`` / ``age`` 等）仍由调用方通过 ``extra`` 追加，
契约层不猜测业务语义。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi.responses import JSONResponse

#: 信封版本。字段增删必须同步 +0.1；语义变更 +1.0。
SCHEMA_VERSION = "1.0"

#: 便于客户端做兼容判断的响应头名
SCHEMA_HEADER = "X-Schema-Version"


class ErrorCode:
    """稳定错误码。前端按码分支，不按文案分支。"""

    OK = "ok"
    BAD_REQUEST = "bad_request"
    UNAUTHORIZED = "unauthorized"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    RATE_LIMITED = "rate_limited"
    UPSTREAM_TIMEOUT = "upstream_timeout"
    UPSTREAM_ERROR = "upstream_error"
    INSUFFICIENT_DATA = "insufficient_data"
    DATA_DEGRADED = "data_degraded"
    UNSUPPORTED = "unsupported"
    INTERNAL = "internal_error"

    #: HTTP 状态码 → 默认错误码
    DEFAULT_FOR_STATUS: Dict[int, str] = {
        400: BAD_REQUEST,
        401: UNAUTHORIZED,
        403: FORBIDDEN,
        404: NOT_FOUND,
        409: CONFLICT,
        422: BAD_REQUEST,
        429: RATE_LIMITED,
        500: INTERNAL,
        502: UPSTREAM_ERROR,
        503: UPSTREAM_ERROR,
        504: UPSTREAM_TIMEOUT,
    }


def code_for_status(status_code: int) -> str:
    """状态码兜底映射；未知状态码一律归 ``internal_error``。"""
    return ErrorCode.DEFAULT_FOR_STATUS.get(int(status_code), ErrorCode.INTERNAL)


def ok(data: Any = None, message: str = "ok", **extra: Any) -> Dict[str, Any]:
    """成功信封。``extra`` 里的键平铺到顶层（保持既有响应形状）。"""
    payload: Dict[str, Any] = {"ok": True, "data": data, "message": message,
                               "code": ErrorCode.OK, "schema_version": SCHEMA_VERSION}
    payload.update(extra)
    return payload


def fail(
    message: str,
    status_code: int = 400,
    *,
    code: Optional[str] = None,
    detail: Any = None,
    **extra: Any,
) -> JSONResponse:
    """失败信封：HTTP 状态码 + 稳定错误码。"""
    payload: Dict[str, Any] = {
        "ok": False,
        "data": None,
        "message": message,
        "code": code or code_for_status(status_code),
        "schema_version": SCHEMA_VERSION,
    }
    if detail is not None:
        payload["detail"] = detail
    payload.update(extra)
    return JSONResponse(status_code=status_code, content=payload)


def degraded_payload(
    reason: str,
    *,
    source: Optional[str] = None,
    as_of: Optional[str] = None,
    age: Optional[float] = None,
    code: Optional[str] = None,
    **extra: Any,
) -> Dict[str, Any]:
    """降级信封（HTTP 200 + ``ok:false``）。

    「取不到数据」不是传输失败，不该用 5xx；但也不能把空结果当成功返回。
    该信封强制带上 ``degraded: true`` 与 ``reason``。
    """
    payload: Dict[str, Any] = {
        "ok": False,
        "data": None,
        "message": reason,
        "reason": reason,
        "code": code or ErrorCode.DATA_DEGRADED,
        "schema_version": SCHEMA_VERSION,
        "degraded": True,
        "as_of": as_of,
        "age": age,
    }
    if source is not None:
        payload["source"] = source
    payload.update(extra)
    return payload


__all__ = [
    "SCHEMA_VERSION",
    "SCHEMA_HEADER",
    "ErrorCode",
    "code_for_status",
    "ok",
    "fail",
    "degraded_payload",
]
