"""Configuration parsing without remote side effects."""

import ipaddress
import json
import os
import math
from dataclasses import dataclass, field
from urllib.parse import urlsplit

PREFIX = os.getenv("KUMA_ANNOTATION_PREFIX", "uptime-kuma.io")


class InvalidConfig(ValueError):
    """Desired configuration is invalid; never treat it as disabled."""


@dataclass(frozen=True)
class Settings:
    url: str
    username: str
    password: str = field(repr=False)
    timeout: float = 10

    @classmethod
    def from_env(cls):
        values = [os.getenv(key, "") for key in ("KUMA_URL", "KUMA_USER", "KUMA_PASS")]
        if not all(values):
            raise InvalidConfig("KUMA_URL, KUMA_USER and KUMA_PASS are required")
        if urlsplit(values[0]).scheme not in ("http", "https"):
            raise InvalidConfig("KUMA_URL requires http or https")
        try:
            timeout = float(os.getenv("KUMA_TIMEOUT", "10"))
            if not 0 < timeout <= 300:
                raise ValueError
        except ValueError:
            raise InvalidConfig("KUMA_TIMEOUT must be in (0, 300]") from None
        return cls(*values, timeout=timeout)


def resync_interval():
    try:
        value = float(os.getenv("KUMA_RESYNC_INTERVAL", "300"))
        if not math.isfinite(value) or not 1 <= value <= 86400:
            raise ValueError
        return value
    except ValueError:
        raise InvalidConfig("KUMA_RESYNC_INTERVAL must be in [1, 86400]") from None


def effective_annotations(body):
    template = body.get("spec", {}).get("template", {}).get("metadata", {})
    return {**template.get("annotations", {}), **body.get("metadata", {}).get("annotations", {})}


def status_codes(value):
    if value is None:
        return ["200-299"]
    try:
        tokens = json.loads(value) if value.strip().startswith("[") else value.split(",")
        if not isinstance(tokens, list) or not tokens:
            raise ValueError
        result = []
        for item in tokens:
            token = str(item).strip().strip("'\"")
            parts = token.split("-")
            if len(parts) not in (1, 2):
                raise ValueError
            start, end = int(parts[0]), int(parts[-1])
            if not 100 <= start <= end <= 999:
                raise ValueError
            # Exact ranges, never round to a wider hundred.
            codes = (
                [token]
                if len(parts) == 1 or (start % 100 == 0 and end == start + 99)
                else [str(c) for c in range(start, end + 1)]
            )
            result.extend(c for c in codes if c not in result)
        return result
    except (ValueError, TypeError, AttributeError):
        raise InvalidConfig("Invalid accepted HTTP status codes") from None


def parse(body):
    annotations = effective_annotations(body)

    def get(key, default=None):
        return annotations.get(f"{PREFIX}/{key}", default)

    enabled = get("enabled", "false").strip().lower()
    if enabled not in ("true", "false"):
        raise InvalidConfig("enabled must be true or false")
    if enabled == "false":
        return None
    kind = get("type", "http").strip().lower()
    kind = {"port": "tcp", "tcp-port": "tcp", "tcpport": "tcp", "icmp": "ping"}.get(kind, kind)
    if kind not in ("http", "tcp", "ping", "dns"):
        raise InvalidConfig("type must be http, tcp/port, ping or dns")

    def number(key, default, minimum, maximum):
        try:
            value = int(get(key, str(default)))
            if not minimum <= value <= maximum:
                raise ValueError
            return value
        except ValueError:
            raise InvalidConfig(f"{key} must be in [{minimum}, {maximum}]") from None

    meta = body["metadata"]
    config = dict(
        name=get("name", "").strip() or f"k8s-{meta['namespace']}-{meta['name']}",
        type={"tcp": "port"}.get(kind, kind),
        interval=number("interval", 60, 20, 86400),
        maxretries=number("retries", 3, 0, 1000),
        notifications=[n.strip() for n in get("notifications", "").split(",") if n.strip()],
        group=get("group", "").strip(),
    )
    default_host = f"{meta['name']}.{meta['namespace']}.svc.cluster.local"
    url = get("url", "").strip()
    if kind == "http":
        if not url:
            target = get("hostname") or get("host") or default_host
            url = target if "://" in target else f"http://{target}"
        try:
            parsed = urlsplit(url)
            # Accessing port also validates numeric/range syntax.
            parsed.port
        except ValueError:
            raise InvalidConfig("Invalid HTTP URL") from None
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise InvalidConfig("HTTP monitor requires an http(s) URL")
        raw = next(
            (
                get(key)
                for key in ("status-codes", "accepted-statuscodes", "accepted-status-codes", "statuscodes")
                if get(key) is not None
            ),
            None,
        )
        config.update(url=url, accepted_statuscodes=status_codes(raw))
    else:
        target = (get("hostname") or get("host") or url or default_host).strip()
        legacy_port = None
        try:
            # A bare IPv6 address is a hostname, not a host:port pair.
            host = str(ipaddress.ip_address(target))
        except ValueError:
            try:
                parsed = urlsplit(target if "://" in target else f"//{target}")
                host, legacy_port = parsed.hostname, parsed.port
            except ValueError:
                raise InvalidConfig("Invalid hostname/port target") from None
        if not host or any(c.isspace() for c in host) or "/" in host:
            raise InvalidConfig("Non-HTTP monitor requires a hostname")
        config["hostname"] = host
        if kind == "tcp":
            config["port"] = number("port", annotations.get("port") or legacy_port or 80, 1, 65535)
        elif kind == "dns":
            config["port"] = number("port", 53, 1, 65535)
            resolver = (get("dns-server") or get("dns-resolve-server") or "1.1.1.1").strip()
            if not resolver or any(c.isspace() for c in resolver):
                raise InvalidConfig("Invalid DNS resolver")
            config.update(dns_resolve_server=resolver, dns_resolve_type="A")
    return config
