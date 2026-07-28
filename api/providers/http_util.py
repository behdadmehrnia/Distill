from __future__ import annotations

import ssl
from typing import Optional

import aiohttp


def ssl_connector() -> aiohttp.TCPConnector:
    """Build an aiohttp connector with a reliable CA bundle"""
    try:
        import certifi

        ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        ctx = ssl.create_default_context()
    return aiohttp.TCPConnector(ssl=ctx)


def client_session(timeout: Optional[aiohttp.ClientTimeout] = None) -> aiohttp.ClientSession:
    return aiohttp.ClientSession(connector=ssl_connector(), timeout=timeout)
