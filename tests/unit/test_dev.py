from collections import Counter
from unittest.mock import patch

from web_app.app import app
from web_app.dev.map import (
    _build_hit_series,
    _collect_client_ip_counts,
    _extract_client_ip,
    _extract_request_path,
    _extract_timestamp,
    _matching_log_events,
    _parse_ip_filters,
    _path_matches_filter,
    map_data,
)


def test_extract_client_ip_accepts_ipv4_and_ipv6():
    assert _extract_client_ip("INFO Processing request: client=1.145.63.44, path=/") == "1.145.63.44"
    assert _extract_client_ip("INFO Processing request: client=2001:4860:4860::8888, path=/") == "2001:4860:4860::8888"
    assert _extract_client_ip("INFO Processing request: client=not-an-ip, path=/") is None


def test_extract_request_path_and_glob_filter():
    line = "INFO Processing request: client=1.1.1.1, path=/hammock/cats, method=GET"

    assert _extract_request_path(line) == "/hammock/cats"
    assert _path_matches_filter("/hammock/cats", "/hammock/*")
    assert _path_matches_filter("/hammock/cats", "/hammock/cats")
    assert not _path_matches_filter("/metrics/", "/hammock/*")


def test_build_hit_series_buckets_events_by_hour():
    events = [
        (_extract_timestamp("2026-05-12 10:15:00,000 INFO Processing request: client=1.1.1.1, path=/"), "1.1.1.1"),
        (_extract_timestamp("2026-05-12 10:45:00,000 INFO Processing request: client=1.1.1.1, path=/"), "1.1.1.1"),
        (_extract_timestamp("2026-05-12 12:05:00,000 INFO Processing request: client=8.8.8.8, path=/"), "8.8.8.8"),
    ]

    series = _build_hit_series(events)

    assert series["bucket"] == "hour"
    assert [point["count"] for point in series["points"]] == [2, 0, 1]
    assert series["points"][0]["ips"] == [{"ip": "1.1.1.1", "count": 2}]


def test_collect_client_ip_counts_reads_all_rotated_logs(tmp_path):
    (tmp_path / "web_app.log").write_text(
        "INFO Processing request: client=1.1.1.1, path=/\n"
        "INFO Processing request: client=1.1.1.1, path=/dev\n"
    )
    (tmp_path / "web_app.log.1").write_text(
        "INFO Processing request: client=8.8.8.8, path=/\n"
        "INFO Processing request: client=bad-ip, path=/\n"
    )
    (tmp_path / "other.log").write_text("INFO Processing request: client=9.9.9.9, path=/\n")

    assert _collect_client_ip_counts(tmp_path) == Counter({"1.1.1.1": 2, "8.8.8.8": 1})


def test_collect_client_ip_counts_can_filter_by_path_glob(tmp_path):
    (tmp_path / "web_app.log").write_text(
        "INFO Processing request: client=1.1.1.1, path=/hammock/cats, method=GET\n"
        "INFO Processing request: client=1.1.1.1, path=/metrics/, method=GET\n"
        "INFO Processing request: client=8.8.8.8, path=/hammock/dogs, method=GET\n"
    )

    assert _collect_client_ip_counts(tmp_path, "/hammock/*") == Counter({"1.1.1.1": 1, "8.8.8.8": 1})


def test_matching_log_events_can_filter_by_range_and_ip_glob(tmp_path):
    (tmp_path / "web_app.log").write_text(
        "2026-05-12 09:00:00,000 INFO Processing request: client=1.1.1.1, path=/hammock/cats, method=GET\n"
        "2026-05-12 10:00:00,000 INFO Processing request: client=8.8.8.8, path=/hammock/cats, method=GET\n"
        "2026-05-12 11:00:00,000 INFO Processing request: client=1.145.63.44, path=/hammock/cats, method=GET\n"
    )

    events = _matching_log_events(
        tmp_path,
        "/hammock/*",
        _extract_timestamp("2026-05-12 09:30:00,000 INFO x"),
        _extract_timestamp("2026-05-12 11:30:00,000 INFO x"),
        _parse_ip_filters("1.145.*"),
    )

    assert events == [(_extract_timestamp("2026-05-12 10:00:00,000 INFO x"), "8.8.8.8")]


def test_map_data_returns_located_public_ips_and_summary(tmp_path):
    (tmp_path / "web_app.log").write_text(
        "2026-05-12 10:00:00,000 INFO Processing request: client=8.8.8.8, path=/\n"
        "2026-05-12 10:30:00,000 INFO Processing request: client=8.8.8.8, path=/dev\n"
        "2026-05-12 11:00:00,000 INFO Processing request: client=127.0.0.1, path=/dev\n"
    )
    geo = {
        "8.8.8.8": {
            "country": "United States",
            "country_code": "US",
            "region": "California",
            "city": "Mountain View",
            "lat": 37.4056,
            "lon": -122.0775,
            "isp": "Google",
            "proxy": False,
            "hosting": True,
        }
    }

    with app.test_request_context(), patch("web_app.dev.map._LOGS_DIR", tmp_path), patch("web_app.dev.map._geolocate_ips", return_value=geo):
        payload = map_data().get_json()

    assert payload["summary"]["unique_ips"] == 2
    assert payload["summary"]["public_ips"] == 1
    assert payload["summary"]["located_ips"] == 1
    assert payload["summary"]["request_count"] == 3
    assert payload["series"]["points"]
    assert payload["points"][0]["ip"] == "8.8.8.8"
    assert payload["points"][0]["count"] == 2


def test_map_data_applies_path_filter(tmp_path):
    (tmp_path / "web_app.log").write_text(
        "INFO Processing request: client=8.8.8.8, path=/hammock/cats, method=GET\n"
        "INFO Processing request: client=1.1.1.1, path=/metrics/, method=GET\n"
    )
    geo = {
        "8.8.8.8": {
            "country": "United States",
            "country_code": "US",
            "region": "California",
            "city": "Mountain View",
            "lat": 37.4056,
            "lon": -122.0775,
            "isp": "Google",
            "proxy": False,
            "hosting": True,
        }
    }

    with app.test_request_context("/dev/map-data?path=/hammock/*"), patch("web_app.dev.map._LOGS_DIR", tmp_path), patch("web_app.dev.map._geolocate_ips", return_value=geo):
        payload = map_data().get_json()

    assert payload["summary"]["path_filter"] == "/hammock/*"
    assert payload["summary"]["unique_ips"] == 1
    assert payload["points"][0]["ip"] == "8.8.8.8"
