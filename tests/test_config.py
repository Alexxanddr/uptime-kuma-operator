import pytest
from kuma_operator.config import InvalidConfig, Settings, effective_annotations, parse, status_codes


def test_precedence(body):
    body["spec"] = {
        "template": {
            "metadata": {
                "annotations": {
                    "uptime-kuma.io/name": "template",
                    "uptime-kuma.io/url": "https://template.test",
                }
            }
        }
    }
    body["metadata"]["annotations"]["uptime-kuma.io/name"] = "top"
    assert parse(body)["name"] == "top"
    assert parse(body)["url"] == "https://example.com"
    del body["metadata"]["annotations"]["uptime-kuma.io/url"]
    assert effective_annotations(body)["uptime-kuma.io/url"] == "https://template.test"


@pytest.mark.parametrize("kind", ["http", "tcp", "port", "ping", "dns"])
def test_types_and_legacy(body, kind):
    body["metadata"]["annotations"].update(
        {
            "uptime-kuma.io/type": kind,
            "uptime-kuma.io/url": "https://example.com" if kind == "http" else "redis.database.svc:6379",
        }
    )
    cfg = parse(body)
    if kind == "http":
        assert cfg["accepted_statuscodes"] == ["200-299"]
    else:
        assert cfg["hostname"] == "redis.database.svc"
        if kind in ("tcp", "port"):
            assert cfg["port"] == 6379
            assert cfg["type"] == "port"


@pytest.mark.parametrize(
    "key,value",
    [
        ("interval", "oops"),
        ("interval", "0"),
        ("retries", "-1"),
        ("type", "unknown"),
        ("enabled", "tru"),
        ("url", "relative"),
        ("status-codes", "foo"),
    ],
)
def test_invalid_never_disabled(body, key, value):
    body["metadata"]["annotations"][f"uptime-kuma.io/{key}"] = value
    with pytest.raises(InvalidConfig):
        parse(body)


def test_disabled(body):
    body["metadata"]["annotations"] = {}
    assert parse(body) is None


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("201-250", [str(n) for n in range(201, 251)]),
        ("200-299,301,301", ["200-299", "301"]),
        ('["200-299", 301]', ["200-299", "301"]),
        (None, ["200-299"]),
    ],
)
def test_codes(raw, expected):
    assert status_codes(raw) == expected


@pytest.mark.parametrize("raw", ["", "[]", "{}", "[null]", "99", "300-200", "200-no", "200-300-400", "1000"])
def test_codes_reject_invalid(raw):
    with pytest.raises(InvalidConfig):
        status_codes(raw)


def test_settings(monkeypatch):
    for key in ["KUMA_URL", "KUMA_USER", "KUMA_PASS"]:
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(InvalidConfig):
        Settings.from_env()
    for key, value in [("KUMA_URL", "https://kuma.test"), ("KUMA_USER", "user"), ("KUMA_PASS", "secret")]:
        monkeypatch.setenv(key, value)
    assert Settings.from_env().timeout == 10
    monkeypatch.setenv("KUMA_TIMEOUT", "0")
    with pytest.raises(InvalidConfig):
        Settings.from_env()


@pytest.mark.parametrize("url,port", [("host.test:42", 42), ("[::1]:443", 443)])
def test_tcp_legacy_endpoints(body, url, port):
    body["metadata"]["annotations"].update({"uptime-kuma.io/type": "port", "uptime-kuma.io/url": url})
    assert parse(body)["port"] == port


@pytest.mark.parametrize("target", ["http://[invalid", "space host"])
def test_non_http_invalid_host(body, target):
    body["metadata"]["annotations"].update(
        {"uptime-kuma.io/type": "tcp", "uptime-kuma.io/url": "", "uptime-kuma.io/hostname": target}
    )
    with pytest.raises(InvalidConfig):
        parse(body)


def test_explicit_hostname_port_precedence(body):
    body["metadata"]["annotations"].update(
        {
            "uptime-kuma.io/type": "port",
            "uptime-kuma.io/url": "legacy:33",
            "uptime-kuma.io/hostname": "explicit",
            "uptime-kuma.io/port": "44",
        }
    )
    cfg = parse(body)
    assert (cfg["hostname"], cfg["port"]) == ("explicit", 44)


def test_bad_legacy_port(body):
    body["metadata"]["annotations"].update(
        {"uptime-kuma.io/type": "tcp", "uptime-kuma.io/url": "host:invalid"}
    )
    with pytest.raises(InvalidConfig):
        parse(body)


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "bad"])
def test_resync_validation(monkeypatch, value):
    from kuma_operator.config import resync_interval

    monkeypatch.setenv("KUMA_RESYNC_INTERVAL", value)
    with pytest.raises(InvalidConfig):
        resync_interval()


@pytest.mark.parametrize("value", ["http://[invalid", "http://example.com:bad", "http://example.com:99999"])
def test_invalid_http_urls_are_configuration_errors(body, value):
    body["metadata"]["annotations"]["uptime-kuma.io/url"] = value
    with pytest.raises(InvalidConfig):
        parse(body)


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("tcp", "port"),
        ("port", "port"),
        ("tcp-port", "port"),
        ("tcpport", "port"),
        ("ping", "ping"),
        ("icmp", "ping"),
        ("dns", "dns"),
        ("http", "http"),
    ],
)
def test_upstream_default_targets_and_aliases(body, kind, expected):
    body["metadata"]["annotations"] = {"uptime-kuma.io/enabled": "true", "uptime-kuma.io/type": kind}
    cfg = parse(body)
    assert cfg["type"] == expected
    if kind == "http":
        assert cfg["url"] == "http://app.test.svc.cluster.local"
    else:
        assert cfg["hostname"] == "app.test.svc.cluster.local"


@pytest.mark.parametrize(
    "key,value,host,port",
    [
        ("hostname", "tcp://db:5432", "db", 5432),
        ("hostname", "db:5432", "db", 5432),
        ("host", "db:5432", "db", 5432),
        ("hostname", "::1", "::1", 80),
    ],
)
def test_upstream_target_formats(body, key, value, host, port):
    body["metadata"]["annotations"] = {
        "uptime-kuma.io/enabled": "true",
        "uptime-kuma.io/type": "tcp",
        f"uptime-kuma.io/{key}": value,
    }
    cfg = parse(body)
    assert (cfg["hostname"], cfg["port"]) == (host, port)


def test_upstream_dns_options_and_unprefixed_port(body):
    body["metadata"]["annotations"] = {
        "uptime-kuma.io/enabled": "true",
        "uptime-kuma.io/type": "dns",
        "uptime-kuma.io/dns-server": "9.9.9.9",
        "uptime-kuma.io/port": "5353",
    }
    cfg = parse(body)
    assert (cfg["dns_resolve_server"], cfg["port"], cfg["dns_resolve_type"]) == ("9.9.9.9", 5353, "A")
    body["metadata"]["annotations"] = {
        "uptime-kuma.io/enabled": "true",
        "uptime-kuma.io/type": "tcp",
        "port": "5432",
    }
    assert parse(body)["port"] == 5432
