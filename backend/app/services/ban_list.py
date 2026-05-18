"""IP ban list.

A small module that parses the BANNED_IPS env var (via Settings) into a
set of individual IPs plus a list of CIDR networks, then answers
`is_banned(ip)` lookups for the request middleware and WebSocket
handlers.

The list is loaded lazily on first call and cached for the process
lifetime. To change it, update the env var and restart the container —
there's no admin endpoint for hot-add, intentionally (keeps the surface
small; the deploy cycle is the moderator's tool).

IPv4 and IPv6 are both supported. Malformed entries are logged and
skipped so a typo doesn't take down the app.
"""
from __future__ import annotations

import ipaddress
import logging
from dataclasses import dataclass, field
from threading import Lock

from app.core.config import get_settings

log = logging.getLogger(__name__)


@dataclass(slots=True)
class _BanList:
    addresses: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = field(
        default_factory=set,
    )
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = field(
        default_factory=list,
    )


_state: _BanList | None = None
_lock = Lock()


def _parse(spec: str) -> _BanList:
    bl = _BanList()
    for raw in spec.split(","):
        item = raw.strip()
        if not item:
            continue
        try:
            if "/" in item:
                bl.networks.append(ipaddress.ip_network(item, strict=False))
            else:
                bl.addresses.add(ipaddress.ip_address(item))
        except ValueError:
            log.warning("BANNED_IPS: ignoring malformed entry %r", item)
    return bl


def _get() -> _BanList:
    global _state
    if _state is None:
        with _lock:
            if _state is None:
                _state = _parse(get_settings().banned_ips)
                if _state.addresses or _state.networks:
                    log.info(
                        "ban list loaded: %d address(es), %d network(s)",
                        len(_state.addresses), len(_state.networks),
                    )
    return _state


def is_banned(ip: str | None) -> bool:
    """Return True if `ip` is on the ban list.

    `None` and unparseable strings return False — we don't reject
    requests just because the client IP couldn't be resolved.
    """
    if not ip:
        return False
    bl = _get()
    if not bl.addresses and not bl.networks:
        return False
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if addr in bl.addresses:
        return True
    return any(addr in net for net in bl.networks)


def reset_for_tests() -> None:
    """Drop the cached ban list. Tests should call this after mutating
    the env var (or settings cache) so the next is_banned() call re-reads
    the current settings."""
    global _state
    with _lock:
        _state = None
