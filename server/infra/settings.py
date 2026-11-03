"""环境配置集中解析（护城河 · 配置层）

设计原则：
1. 所有运行期参数只从环境变量/`.env` 读一次，进程内共享同一份不可变 Settings；
2. 默认值以“仅本机、最小暴露”为准（127.0.0.1 + 严格安全头）；
3. 启动期做校验，暴露问题而不是带病运行；`issues()` 在 /readyz 里可见。

新增环境变量（均可选，不填走默认）：
    HOST / PORT                     监听地址（默认 127.0.0.1:8900）
    MOAT_RATE_LIMIT                 每个 IP 每分钟 /api 请求上限（默认 600）
    MOAT_MAX_BODY                   请求体上限字节（默认 2 MiB）
    MOAT_ADMIN_TOKEN                系统端点令牌；非回环监听时必须设置
    MOAT_SECURITY                   是否加安全响应头（默认 1）
    MOAT_BACKUP_KEEP                备份保留份数（默认 14）
    MOAT_LOG                        是否写滚动文件日志（默认 1）
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
STATIC_DIR = PROJECT_ROOT / "static"
DOCS_DIR = PROJECT_ROOT / "docs"
DATA_DIR = PROJECT_ROOT / "data"
LOGS_DIR = PROJECT_ROOT / "logs"
BACKUP_DIR = PROJECT_ROOT / "backups"
DOCS_GRAPH = STATIC_DIR / "docs-graph.json"

_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


CSP = (
    "default-src 'self'; "
    "img-src 'self' data: blob:; "
    "font-src 'self' data:; "
    "style-src 'self' 'unsafe-inline'; "
    "script-src 'self' 'unsafe-inline'; "
    "connect-src 'self'; "
    "media-src 'self' data:; "
    "frame-ancestors 'self'; "
    "base-uri 'self'; "
    "form-action 'self'"
)


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 8900
    rate_limit_per_min: int = 600
    max_body_bytes: int = 2 * 1024 * 1024
    admin_token: str | None = None
    security_headers: bool = True
    backup_keep: int = 14
    log_to_file: bool = True
    project_root: Path = PROJECT_ROOT
    static_dir: Path = STATIC_DIR
    docs_dir: Path = DOCS_DIR
    data_dir: Path = DATA_DIR
    logs_dir: Path = LOGS_DIR
    backup_dir: Path = BACKUP_DIR
    docs_graph: Path = DOCS_GRAPH
    csp: str = CSP
    # issues = 会影响可用性的硬故障；warnings = 值得知道但不阻断的配置风险。
    # 分开的原因：弱口令这类「能跑但不够安全」的项如果混进 issues，会让体检长期 FAIL，
    # 值班很快就学会忽略红色，真正的故障反而被淹没。
    issues: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def bind_local_only(self) -> bool:
        return self.host in _LOOPBACK

    def as_public(self) -> dict:
        return {
            "host": self.host,
            "port": self.port,
            "rate_limit_per_min": self.rate_limit_per_min,
            "max_body_bytes": self.max_body_bytes,
            "security_headers": self.security_headers,
            "backup_keep": self.backup_keep,
            "log_to_file": self.log_to_file,
            "admin_token_configured": bool(self.admin_token),
            "bind_local_only": self.bind_local_only,
            "issues": list(self.issues),
            "warnings": list(self.warnings),
        }


def _validate(s: Settings) -> tuple[tuple[str, ...], tuple[str, ...]]:
    issues: list[str] = []
    warnings: list[str] = []
    if not s.static_dir.is_dir():
        issues.append(f"static 目录不存在：{s.static_dir}")
    if not s.docs_graph.is_file():
        issues.append(f"docs-graph.json 不存在：{s.docs_graph}")
    if not s.bind_local_only and not s.admin_token:
        issues.append("监听非回环地址但未设置 MOAT_ADMIN_TOKEN，系统端点缺少保护")
    if not s.bind_local_only and not (os.getenv("ACCESS_KEY") or os.getenv("DAPENG_ACCESS_KEY")):
        issues.append("监听非回环地址但未设置 ACCESS_KEY，任何能访问该地址的人都可以直接使用本系统")
    _key = (os.getenv("ACCESS_KEY") or os.getenv("DAPENG_ACCESS_KEY") or "").strip()
    _require = (os.getenv("REQUIRE_ACCESS_KEY") or "").strip().lower() in ("1", "true", "yes", "on")
    try:
        _floor = int((os.getenv("ACCESS_KEY_MIN_LEN") or "32").strip() or 32)
    except ValueError:
        _floor = 32
    if _require and len(_key) < _floor:
        issues.append(f"REQUIRE_ACCESS_KEY=1 但 ACCESS_KEY 缺失或短于 {_floor} 位")
    elif _key and len(_key) < 16:
        warnings.append(f"ACCESS_KEY 仅 {len(_key)} 字符，抗爆破能力有限；建议换成长随机串")
    if (os.getenv("AUTH_DISABLED") or "").strip().lower() in ("1", "true", "yes", "on") and not s.bind_local_only:
        issues.append("AUTH_DISABLED=1 且监听非回环地址：任何人都能无密钥进入")
    if s.rate_limit_per_min < 60:
        issues.append("MOAT_RATE_LIMIT 低于 60，正常面板轮询会被限流")
    if s.backup_keep < 1:
        issues.append("MOAT_BACKUP_KEEP 必须 >= 1")
    return tuple(issues), tuple(warnings)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    host = (os.getenv("HOST") or "127.0.0.1").strip() or "127.0.0.1"
    port = _env_int("PORT", 8900)
    token = (os.getenv("MOAT_ADMIN_TOKEN") or os.getenv("ADMIN_TOKEN") or "").strip() or None
    s = Settings(
        host=host,
        port=port,
        rate_limit_per_min=_env_int("MOAT_RATE_LIMIT", 600),
        max_body_bytes=_env_int("MOAT_MAX_BODY", 2 * 1024 * 1024),
        admin_token=token,
        security_headers=_env_bool("MOAT_SECURITY", True),
        backup_keep=_env_int("MOAT_BACKUP_KEEP", 14),
        log_to_file=_env_bool("MOAT_LOG", True),
    )
    issues, warnings = _validate(s)
    return Settings(**{**s.__dict__, "issues": issues, "warnings": warnings})
