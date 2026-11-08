"""Outbound network helpers for predictable GitHub-hosted runner behavior."""

from __future__ import annotations

import os
import urllib.request


_PROXY_ENV_KEYS = (
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
    "http_proxy", "https_proxy", "all_proxy",
)


def proxy_enabled() -> bool:
    return os.getenv("OUTBOUND_PROXY_ENABLED", "").strip().lower() in {"1", "true", "yes"}


def build_direct_opener(*handlers):
    """Build a urllib opener that ignores ambient proxy variables by default."""
    if proxy_enabled():
        return urllib.request.build_opener(*handlers)
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), *handlers)


def direct_subprocess_env() -> dict[str, str]:
    """Return an environment for subprocess network clients with proxies disabled by default."""
    env = os.environ.copy()
    if not proxy_enabled():
        for key in _PROXY_ENV_KEYS:
            env.pop(key, None)
    return env
