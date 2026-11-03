#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""验证脚本的登录助手（access-key.v1）。

为什么需要它
------------
开启 `ACCESS_KEY` 后，所有 `/api/*` 都会返回 401，审计与性能脚本如果直接打接口
只会得到一片 HTTP-ERR——「安全措施把自检工具也挡在门外」是典型的维护性退化。
这里统一从环境变量或 `.env` 读密钥、登录一次、复用 Cookie。

用法
----
```python
from _auth import auth_headers
headers = auth_headers(BASE)      # {'Cookie': 'dapeng_session=...'} 或 {}
```
未配置密钥的实例返回 `{}`，脚本行为与以前完全一致。
"""
from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path
from typing import Dict, Optional

COOKIE_NAME = "dapeng_sid"
_ROOT = Path(__file__).resolve().parents[2]


def load_key() -> Optional[str]:
    """优先环境变量，其次项目根 `.env`；取不到返回 None。"""
    for name in ("ACCESS_KEY", "DAPENG_ACCESS_KEY"):
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    env_file = _ROOT / ".env"
    try:
        for line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() in ("ACCESS_KEY", "DAPENG_ACCESS_KEY"):
                v = v.strip().strip('"').strip("'")
                if v:
                    return v
    except OSError:
        pass
    return None


def login(base: str, key: Optional[str] = None, timeout: float = 10.0) -> Optional[str]:
    """登录并返回 Cookie 头的值；失败返回 None（调用方按未登录继续跑）。"""
    secret = (key or load_key() or "").strip()
    if not secret:
        return None
    body = json.dumps({"key": secret}).encode("utf-8")
    req = urllib.request.Request(
        base.rstrip("/") + "/api/auth/login", data=body, method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.headers.get("Set-Cookie") or ""
    except Exception:
        return None
    for part in raw.split(";"):
        part = part.strip()
        if part.startswith(COOKIE_NAME + "="):
            return part
    return None


def auth_headers(base: str) -> Dict[str, str]:
    cookie = login(base)
    return {"Cookie": cookie} if cookie else {}


__all__ = ["load_key", "login", "auth_headers", "COOKIE_NAME"]
