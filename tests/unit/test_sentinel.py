from unittest.mock import patch

import pytest

from web_app.app import app
from web_app.config import ConfigManager
from web_app.helpers import limiter, register_all_blueprints
from web_app.sentinel import _limit_from_request, _parse_batch_payload, _report_payload, _validate_additional_domains
from web_app.sentinel.actions import ActionValidationError, parse_agent_action
from web_app.sentinel.providers import (
    _build_codex_cmd,
    _codex_text,
    _get_provider,
    _system_prompt,
)
from web_app.sentinel.runner import (
    _add_finding,
    _agent_prompt,
    _annotate_screenshot,
    _apply_action,
    _classify_run_verdict,
    _clean_title,
    _detect_click_loop,
    _detect_login_failure,
    _ensure_summary_heading,
    _fallback_title,
    _final_report_prompt,
    _generate_title,
    _host_allowed,
    _navigation_host_allowed,
    _observe_page,
    _parse_picker_payload,
    _parse_verdict_payload,
    _pick_final_report_screenshots,
    _request_agent_action,
    _screenshot_manifest,
    ensure_screenshot_thumbnail,
)
from web_app.sentinel.target_policy import TargetValidationError, ValidatedTarget, validate_public_web_url
from web_app.users import User


@pytest.fixture
def client():
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    app.secret_key = "test-secret"
    limiter.enabled = False
    if "sentinel" not in app.blueprints:
        register_all_blueprints(app)
    with app.test_client() as client:
        yield client


def _addrinfo(ip: str):
    return [(2, 1, 6, "", (ip, 443))]


def test_validate_public_web_url_rejects_local_and_private_targets():
    bad_urls = [
        "ftp://example.com",
        "http://localhost",
        "http://127.0.0.1",
        "http://10.0.0.5",
        "http://192.168.1.2",
        "http://[::1]",
    ]

    for url in bad_urls:
        with pytest.raises(TargetValidationError):
            validate_public_web_url(url)

    with patch("web_app.sentinel.target_policy.socket.getaddrinfo", return_value=_addrinfo("93.184.216.34")):
        assert validate_public_web_url("https://example.com/path").url == "https://example.com/path"
        assert validate_public_web_url("example.com/path").url == "https://example.com/path"
        assert (
            validate_public_web_url("https://user:pass@example.com/path").url
            == "https://user:pass@example.com/path"
        )


def test_validate_additional_domains_normalizes_public_urls_and_domains():
    with patch("web_app.sentinel.target_policy.socket.getaddrinfo", return_value=_addrinfo("93.184.216.34")):
        assert _validate_additional_domains("recruitment.macquarie.com\nhttps://careers.macquarie.com/jobs") == [
            "recruitment.macquarie.com",
            "careers.macquarie.com",
        ]
        assert _validate_additional_domains(["https://EXAMPLE.com/path", "example.com"]) == ["example.com"]


def test_additional_domains_allow_specific_external_navigation(client):
    admin = User(username="admin", password="pass", folder="af", is_admin=True)
    captured = {}

    def fake_start_run(target, prompt, limit_s, **kwargs):
        captured.update(kwargs)
        return {"run_id": "rdom", "status": "queued"}

    with patch("web_app.helpers.DataInterface") as mock_users:
        mock_users.return_value.load_users.return_value = {"admin": admin}
        with client.session_transaction() as sess:
            sess["_user_id"] = "admin"

        with patch("web_app.sentinel.validate_public_web_url") as mock_validate, patch(
            "web_app.sentinel.start_run", side_effect=fake_start_run
        ):
            mock_validate.side_effect = [
                ValidatedTarget("https://example.com/", "example.com"),
                ValidatedTarget("https://jobs.example.org/apply", "jobs.example.org"),
            ]
            res = client.post(
                "/sentinel/api/runs",
                json={
                    "url": "https://example.com",
                    "prompt": "find the application form",
                    "additional_domains": "https://jobs.example.org/apply",
                    "allow_external": False,
                },
            )

    assert res.status_code == 202
    assert captured["additional_domains"] == ["jobs.example.org"]


def test_parse_agent_action_allows_only_known_actions_and_elements():
    action = parse_agent_action('{"action": "click", "element_id": "e1", "reason": "Open nav"}', {"e1"})
    assert action.action == "click"
    assert action.element_id == "e1"

    scroll = parse_agent_action('{"action": "scroll", "value": "down", "reason": "See products"}', set())
    assert scroll.action == "scroll"
    assert scroll.value == "down"
    assert scroll.element_id is None

    select = parse_agent_action('{"action": "select", "element_id": "e2", "value": "Price: Low - High"}', {"e2"})
    assert select.action == "select"
    assert select.element_id == "e2"
    assert select.value == "Price: Low - High"

    with pytest.raises(ActionValidationError):
        parse_agent_action('{"action": "delete_database"}', {"e1"})

    with pytest.raises(ActionValidationError):
        parse_agent_action('{"action": "click", "element_id": "missing"}', {"e1"})

    with pytest.raises(ActionValidationError):
        parse_agent_action('{"action": "scroll", "element_id": "e1"}', {"e1"})


def test_parse_agent_action_picks_last_json_when_response_has_thinking_prose():
    # Real failure mode from run 3b201325: model emits multiple JSON candidates
    # separated by reasoning prose. We treat the last brace-balanced object as
    # the model's final answer.
    text = (
        '{"action":"click","element_id":"e1","reason":"first try"}\n\n'
        'Wait, e1 is wrong. Let me reconsider.\n\n'
        '{"action":"click","element_id":"e2","reason":"second try"}\n\n'
        'Actually:\n\n'
        '{"action":"scroll","value":"down","reason":"final answer"}'
    )
    action = parse_agent_action(text, {"e1", "e2"})
    assert action.action == "scroll"
    assert action.value == "down"
    assert action.reason == "final answer"


def test_parse_agent_action_handles_braces_inside_string_values():
    # { and } inside reason strings must not confuse the brace-depth tracker.
    text = 'preamble {"action":"click","element_id":"e1","reason":"saw {placeholder} text"}'
    action = parse_agent_action(text, {"e1"})
    assert action.action == "click"
    assert action.reason == "saw {placeholder} text"


def test_runner_allows_only_exact_host_or_www_redirect_variant():
    assert _host_allowed("google.com", "google.com")
    assert _host_allowed("www.google.com", "google.com")
    assert _host_allowed("google.com", "www.google.com")
    assert not _host_allowed("accounts.google.com", "google.com")
    assert not _host_allowed("example.com", "google.com")


def test_navigation_host_allowed_accepts_additional_domains():
    assert _navigation_host_allowed("jobs.example.org", "example.com", ["jobs.example.org"])
    assert _navigation_host_allowed("www.jobs.example.org", "example.com", ["jobs.example.org"])
    assert not _navigation_host_allowed("evil.example.org", "example.com", ["jobs.example.org"])


def test_codex_command_includes_screenshot_images(tmp_path):
    output = tmp_path / "out.txt"
    image = tmp_path / "step.png"
    image.write_bytes(b"png")

    cmd = _build_codex_cmd(str(output), [image])

    assert "--image" in cmd
    assert str(image) in cmd
    assert cmd.index("--image") < cmd.index("--output-last-message")


def test_codex_command_disables_project_docs_and_uses_minimal_permissions(tmp_path):
    cmd = _build_codex_cmd(str(tmp_path / "out.txt"))

    assert "-c" in cmd
    assert "project_doc_max_bytes=0" in cmd
    assert "project_doc_fallback_filenames=[]" in cmd
    assert 'default_permissions="sentinel_qa"' in cmd
    assert 'permissions.sentinel_qa.filesystem.:minimal="read"' in cmd
    assert "--sandbox" not in cmd
    assert "--ignore-rules" in cmd


def test_codex_runs_from_project_dir_with_project_docs_disabled():
    class DummyOutput:
        name = "/tmp/sentinel-output.txt"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def seek(self, _pos):
            return None

        def read(self):
            return "done"

    with patch("web_app.sentinel.providers.tempfile.NamedTemporaryFile", return_value=DummyOutput()), patch(
        "web_app.sentinel.providers.subprocess.run"
    ) as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stderr = ""
        mock_run.return_value.stdout = ""

        _codex_text("system", "prompt", image_paths=None, timeout_s=1)

    assert mock_run.call_args.kwargs["cwd"] == str(ConfigManager().project_dir)
    assert "project_doc_max_bytes=0" in mock_run.call_args.args[0]


def test_provider_switch_follows_global_llm_api_source():
    cfg = ConfigManager()
    original = cfg.llm.api_source
    try:
        cfg.llm.api_source = "meridian"
        assert _get_provider().name == "meridian"
        cfg.llm.api_source = "codex"
        assert _get_provider().name == "codex"
        cfg.llm.api_source = "bedrock"
        assert _get_provider().name == "bedrock"
        cfg.llm.api_source = "unknown-source"
        assert _get_provider().name == "codex"
    finally:
        cfg.llm.api_source = original


def test_meridian_provider_calls_meridian_text_with_screenshots(tmp_path):
    image = tmp_path / "step.png"
    image.write_bytes(b"png-bytes")

    cfg = ConfigManager()
    original = cfg.llm.api_source
    try:
        cfg.llm.api_source = "meridian"
        with patch("web_app.sentinel.providers.meridian_text", return_value="agent reply") as mock_meridian:
            result = _get_provider().agent_text("user prompt", image_paths=[image])
    finally:
        cfg.llm.api_source = original

    assert result == "agent reply"
    kwargs = mock_meridian.call_args.kwargs
    assert kwargs["user_message"] == "user prompt"
    assert kwargs["image_paths"] == [image]
    assert kwargs["agent"] == "sentinel"
    assert "QA tester" in kwargs["system"]


def test_time_limit_input_is_minutes_capped_at_ten():
    assert _limit_from_request("1") == 60
    assert _limit_from_request("10") == 600
    assert _limit_from_request("99") == 600


def test_finding_details_are_truncated_and_single_line():
    report = {"findings": []}
    _add_finding(report, "info", "Console", "x" * 1000 + "\n" + "y" * 1000)

    detail = report["findings"][0]["detail"]
    assert "\n" not in detail
    assert len(detail) <= 503
    assert detail.endswith("...")


def test_final_report_prompt_directly_includes_original_prompt():
    prompt = _final_report_prompt(
        {
            "prompt": "Does checkout work?",
            "target_url": "https://example.com",
            "status": "completed",
            "steps": [{"action": "click", "reason": "Open cart", "result": {"ok": True}}],
            "findings": [{"severity": "error", "title": "Broken", "detail": "Button failed"}],
            "screenshots": ["screenshots/step-01.png"],
        }
    )

    assert "Does checkout work?" in prompt
    assert "Button failed" in prompt


def test_report_payload_renders_final_report_markdown_without_html():
    payload = _report_payload(
        {
            "run_id": "a" * 32,
            "screenshots": [],
            "final_report": "## Summary\n\n- **Works**\n<script>alert(1)</script>",
        }
    )

    assert "<h2>Summary</h2>" in payload["final_report_html"]
    assert "<strong>Works</strong>" in payload["final_report_html"]
    assert "<script>" not in payload["final_report_html"]
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in payload["final_report_html"]
    assert payload["screenshot_load_stagger_ms"] == ConfigManager().sentinel.screenshot_load_stagger_ms
    assert payload["screenshot_load_max_retries"] == ConfigManager().sentinel.screenshot_load_max_retries
    assert payload["screenshot_load_retry_delay_ms"] == ConfigManager().sentinel.screenshot_load_retry_delay_ms


def test_generate_title_uses_provider_when_title_blank_and_falls_back_on_error():
    report = {
        "target_url": "https://example.com",
        "target_hostname": "example.com",
        "prompt": "Test the checkout flow",
    }

    class _OkProvider:
        def title_text(self, _user):
            return '"Checkout flow smoke test"\n'

    with patch("web_app.sentinel.runner._get_provider", return_value=_OkProvider()):
        assert _generate_title(report) == "Checkout flow smoke test"

    class _BadProvider:
        def title_text(self, _user):
            raise RuntimeError("nope")

    with patch("web_app.sentinel.runner._get_provider", return_value=_BadProvider()):
        assert _generate_title(report) == _fallback_title(report) == "example.com"


def test_clean_title_strips_quotes_collapses_whitespace_and_truncates():
    assert _clean_title('  "Hello   world"  ') == "Hello world"
    long_input = "word " * 100
    assert len(_clean_title(long_input)) <= ConfigManager().sentinel.title_max_chars


def test_ensure_summary_heading_prepends_when_missing_and_keeps_when_present():
    assert _ensure_summary_heading("Some body without a heading.").startswith("## Summary\n\n")
    assert _ensure_summary_heading("## Summary\n\nAlready here.") == "## Summary\n\nAlready here."
    assert _ensure_summary_heading("# summary\n\nany level").startswith("# summary")
    assert _ensure_summary_heading("") == "## Summary\n\nNo report content was generated."


def test_final_report_inlines_allowlisted_screenshots_and_drops_other_images():
    run_id = "b" * 32
    payload = _report_payload(
        {
            "run_id": run_id,
            "screenshots": ["screenshots/step-01.png", "screenshots/step-02.png"],
            "final_report": (
                "Login worked: ![login screen](step-01.png)\n\n"
                "Bad: ![evil](https://evil.example.com/x.png)\n\n"
                "Missing: ![missing](step-99.png)"
            ),
        }
    )

    html = payload["final_report_html"]
    assert f'src="/sentinel/report/{run_id}/screenshots/thumb/step-01.png"' in html
    assert f'data-full="/sentinel/report/{run_id}/screenshots/step-01.png"' in html
    assert "evil.example.com" not in html
    assert "step-99.png" not in html
    assert 'class="sentinel-final-report-img"' in html
    assert 'loading="lazy"' in html


def test_sentinel_thumbnail_generation_creates_small_copy(tmp_path):
    from PIL import Image

    run_id = "c" * 32
    screenshots_dir = tmp_path / "screenshots"
    screenshots_dir.mkdir()
    Image.new("RGB", (1200, 800), (255, 255, 255)).save(screenshots_dir / "step-01.png")

    class DummyDataInterface:
        def screenshots_dir(self, _run_id):
            return screenshots_dir

        def screenshot_thumbnail_path(self, _run_id, filename):
            return screenshots_dir / "thumbs" / filename

    with patch("web_app.sentinel.runner.DataInterface", return_value=DummyDataInterface()):
        thumb = ensure_screenshot_thumbnail(run_id, "step-01.png")

    assert thumb is not None
    assert thumb.exists()
    with Image.open(thumb) as img:
        assert max(img.size) <= ConfigManager().sentinel.screenshot_thumb_max_px


def test_sentinel_index_accessible_to_elevated_non_admin(client):
    elevated = User(username="eve", password="pass", folder="ef", is_admin=False, is_elevated=True)

    with patch("web_app.helpers.DataInterface") as mock_users:
        mock_users.return_value.load_users.return_value = {"eve": elevated}
        with client.session_transaction() as sess:
            sess["_user_id"] = "eve"

        with patch("web_app.sentinel.DataInterface") as mock_data:
            mock_data.return_value.list_reports.return_value = []
            assert client.get("/sentinel/").status_code == 200


def test_sentinel_routes_require_admin_and_start_run_for_admin(client):
    admin = User(username="admin", password="pass", folder="af", is_admin=True)
    non_admin = User(username="user", password="pass", folder="uf", is_admin=False)

    with patch("web_app.helpers.DataInterface") as mock_users:
        mock_users.return_value.load_users.return_value = {"admin": admin, "user": non_admin}

        assert client.get("/sentinel/").status_code == 302

        with client.session_transaction() as sess:
            sess["_user_id"] = "user"
        assert client.get("/sentinel/").status_code == 403
        assert client.post("/sentinel/api/runs", json={"url": "https://example.com"}).status_code == 403

        with client.session_transaction() as sess:
            sess["_user_id"] = "admin"

        with patch("web_app.sentinel.DataInterface") as mock_data, patch(
            "web_app.sentinel.validate_public_web_url"
        ) as mock_validate, patch("web_app.sentinel.start_run") as mock_start:
            mock_data.return_value.list_reports.return_value = []
            assert client.get("/sentinel/").status_code == 200
            mock_validate.return_value.url = "https://example.com"
            mock_start.return_value = {"run_id": "run-123", "status": "queued"}

            res = client.post("/sentinel/api/runs", json={"url": "https://example.com", "limit": 5})

    assert res.status_code == 202
    assert res.get_json()["run_id"] == "run-123"
    mock_start.assert_called_once()
    assert mock_start.call_args.kwargs["owner"] == "admin"


def test_sentinel_card_details_validate_and_never_persist(client, tmp_path):
    """allow_financial round-trips card details into start_run, validates them,
    keeps them in memory only, and never writes them to disk."""
    admin = User(username="admin", password="pass", folder="af", is_admin=True)

    captured = {}

    def fake_start_run(target, prompt, limit_s, **kwargs):
        captured.update(kwargs)
        return {"run_id": "rfin", "status": "queued"}

    with patch("web_app.helpers.DataInterface") as mock_users:
        mock_users.return_value.load_users.return_value = {"admin": admin}
        with client.session_transaction() as sess:
            sess["_user_id"] = "admin"

        with patch("web_app.sentinel.validate_public_web_url") as mock_validate, patch(
            "web_app.sentinel.start_run", side_effect=fake_start_run
        ):
            mock_validate.return_value.url = "https://example.com"

            res_bad = client.post(
                "/sentinel/api/runs",
                json={
                    "url": "https://example.com",
                    "prompt": "buy something",
                    "allow_financial": True,
                    "card_number": "12",
                    "card_expiry": "13/99",
                    "card_cvv": "1",
                },
            )
            assert res_bad.status_code == 400

            res_ok = client.post(
                "/sentinel/api/runs",
                json={
                    "url": "https://example.com",
                    "prompt": "buy something",
                    "allow_financial": True,
                    "card_number": "4242 4242 4242 4242",
                    "card_expiry": "12/30",
                    "card_cvv": "123",
                },
            )
    assert res_ok.status_code == 202
    assert captured["allow_financial"] is True
    assert captured["card_details"] == {
        "card_number": "4242424242424242",
        "expiry": "12/30",
        "cvv": "123",
    }


def test_save_strips_underscore_keys_before_disk_write():
    """Runtime-only fields (like _card_details) must not reach the persisted report."""
    from web_app.sentinel import runner

    captured = {}

    class FakeData:
        def save_report(self, report):
            captured["report"] = report

    with patch.object(runner, "DataInterface", return_value=FakeData()):
        runner._save({
            "run_id": "r1",
            "status": "running",
            "_card_details": {"card_number": "4242", "expiry": "12/30", "cvv": "123"},
        })

    assert "_card_details" not in captured["report"]
    assert captured["report"]["run_id"] == "r1"


def test_sentinel_account_credentials_round_trip_and_invalid_field_name(client):
    """allow_accounts with credentials forwards them to start_run; bad field
    names get rejected; underscore-prefixed runtime field never persists."""
    admin = User(username="admin", password="pass", folder="af", is_admin=True)

    captured = {}

    def fake_start_run(target, prompt, limit_s, **kwargs):
        captured.update(kwargs)
        return {"run_id": "racc", "status": "queued"}

    with patch("web_app.helpers.DataInterface") as mock_users:
        mock_users.return_value.load_users.return_value = {"admin": admin}
        with client.session_transaction() as sess:
            sess["_user_id"] = "admin"

        with patch("web_app.sentinel.validate_public_web_url") as mock_validate, patch(
            "web_app.sentinel.start_run", side_effect=fake_start_run
        ):
            mock_validate.return_value.url = "https://example.com"

            res_bad = client.post(
                "/sentinel/api/runs",
                json={
                    "url": "https://example.com",
                    "prompt": "log in to the site",
                    "allow_accounts": True,
                    "account_credentials": {
                        "username": "qa",
                        "password": "p",
                        "extras": {"!!bad name": "x"},
                    },
                },
            )
            assert res_bad.status_code == 400

            res_ok = client.post(
                "/sentinel/api/runs",
                json={
                    "url": "https://example.com",
                    "prompt": "log in to the site",
                    "allow_accounts": True,
                    "account_credentials": {
                        "username": "qatester",
                        "password": "hunter2",
                        "extras": {"email": "qa@example.com"},
                    },
                },
            )
    assert res_ok.status_code == 202
    assert captured["account_credentials"] == {
        "username": "qatester",
        "password": "hunter2",
        "extras": {"email": "qa@example.com"},
    }


def test_system_prompt_injects_account_credentials_and_login_fail_directive():
    prompt = _system_prompt(
        allow_accounts=True,
        demographic="",
        allow_external=False,
        card_details=None,
        account_credentials={"username": "qatester", "password": "hunter2", "extras": {"email": "qa@example.com"}},
    )
    assert "qatester" in prompt
    assert "hunter2" in prompt
    assert "email" in prompt and "qa@example.com" in prompt
    assert "login failed:" in prompt


def test_detect_login_failure_marks_run_failed_only_for_login_finish_reason():
    base_steps = [{"index": 1, "action": "click", "reason": "", "result": {"url": "https://x/"}}]
    fail_report = {
        "allow_accounts": True,
        "steps": base_steps + [{"index": 2, "action": "finish", "reason": "login failed: invalid password"}],
    }
    assert _detect_login_failure(fail_report) == "login failed: invalid password"

    other_finish = {
        "allow_accounts": True,
        "steps": base_steps + [{"index": 2, "action": "finish", "reason": "All checks passed."}],
    }
    assert _detect_login_failure(other_finish) == ""

    accounts_off = {
        "allow_accounts": False,
        "steps": base_steps + [{"index": 2, "action": "finish", "reason": "login failed: x"}],
    }
    assert _detect_login_failure(accounts_off) == ""


def test_sentinel_rejects_account_prompt_when_accounts_disallowed(client):
    admin = User(username="admin", password="pass", folder="af", is_admin=True)

    with patch("web_app.helpers.DataInterface") as mock_users:
        mock_users.return_value.load_users.return_value = {"admin": admin}
        with client.session_transaction() as sess:
            sess["_user_id"] = "admin"

        with patch("web_app.sentinel.validate_public_web_url") as mock_validate, patch(
            "web_app.sentinel.start_run"
        ) as mock_start:
            mock_validate.return_value.url = "https://example.com"

            res = client.post(
                "/sentinel/api/runs",
                json={
                    "url": "https://example.com",
                    "prompt": "check that an account can be created and a metric logged",
                    "limit": 5,
                    "allow_accounts": False,
                },
            )

    assert res.status_code == 400
    assert "account" in res.get_json()["error"].lower()
    mock_start.assert_not_called()


def test_sentinel_index_prefills_form_from_clone_query_params(client):
    admin = User(username="admin", password="pass", folder="af", is_admin=True)

    with patch("web_app.helpers.DataInterface") as mock_users, patch(
        "web_app.sentinel.DataInterface"
    ) as mock_sentinel_data:
        mock_users.return_value.load_users.return_value = {"admin": admin}
        mock_sentinel_data.return_value.list_reports.return_value = []
        with client.session_transaction() as sess:
            sess["_user_id"] = "admin"

        res = client.get(
            "/sentinel/?url=https://example.com&prompt=Test+checkout&limit=5"
            "&device=small_phone&demographic=senior"
        )

    assert res.status_code == 200
    body = res.get_data(as_text=True)
    assert 'value="https://example.com"' in body
    assert "Test checkout" in body
    assert 'value="5"' in body
    assert 'value="small_phone" selected' in body
    assert 'value="senior" selected' in body


def test_sentinel_index_defaults_to_desktop_adult_australia(client):
    admin = User(username="admin", password="pass", folder="af", is_admin=True)

    with patch("web_app.helpers.DataInterface") as mock_users, patch(
        "web_app.sentinel.DataInterface"
    ) as mock_sentinel_data:
        mock_users.return_value.load_users.return_value = {"admin": admin}
        mock_sentinel_data.return_value.list_reports.return_value = []
        with client.session_transaction() as sess:
            sess["_user_id"] = "admin"

        res = client.get("/sentinel/")

    assert res.status_code == 200
    body = res.get_data(as_text=True)
    assert 'value="desktop" selected' in body
    assert 'value="adult" selected' in body
    assert 'id="sentinel-region"' in body
    assert 'value="australia" selected' in body
    assert 'value="china"' in body
    assert 'value="us"' in body
    assert 'value="uk"' in body
    assert 'value="japan"' in body
    assert 'placeholder="nabicat.site"' in body
    assert "recruitment.macquarie.com" not in body
    assert 'id="sentinel-additional-domain-fields" class="sentinel-card-fields" hidden' in body
    assert '<div class="sentinel-advanced-body"></div>' in body


def test_annotate_screenshot_draws_boxes_and_writes_png(tmp_path):
    from PIL import Image

    raw = tmp_path / "step-01.png"
    Image.new("RGB", (200, 100), (255, 255, 255)).save(raw)
    out = tmp_path / "step-01-annot.png"

    elements = [
        {"id": "e1", "rect": {"x": 10, "y": 20, "w": 50, "h": 30}},
        {"id": "e2", "rect": {"x": 80, "y": 40, "w": 60, "h": 25}},
    ]
    written = _annotate_screenshot(raw, out, elements, viewport={"w": 200, "h": 100})

    assert written == out and out.exists()
    img = Image.open(out)
    assert img.size == (200, 100)
    # Annotated image must differ from the blank original somewhere.
    assert list(img.convert("RGB").getdata()) != list(Image.open(raw).getdata())


def test_agent_prompt_omits_body_text_and_rects():
    report = {
        "run_id": "a" * 32,
        "target_url": "https://example.com",
        "prompt": "test it",
        "steps": [],
    }
    observation = {
        "url": "https://example.com",
        "title": "Example",
        "text": "Lots of body text the model should never see",
        "elements": [
            {"id": "e1", "tag": "button", "type": "", "text": "Sign up",
             "href": "", "rect": {"x": 1, "y": 2, "w": 3, "h": 4}},
        ],
        "viewport": {"w": 800, "h": 600},
    }
    out = _agent_prompt(report, observation)

    assert "Lots of body text" not in out
    assert "rect" not in out
    assert "viewport" not in out
    assert '"id": "e1"' in out
    assert '"label": "Sign up"' in out


def test_system_prompt_prepends_demographic_persona():
    cfg = ConfigManager()
    senior_persona = cfg.sentinel.demographic_personas["senior"]
    with_persona = _system_prompt(allow_accounts=False, demographic="senior")
    without = _system_prompt(allow_accounts=False, demographic="")
    assert with_persona.startswith(senior_persona)
    assert senior_persona not in without
    # Unknown demographic falls back to no persona.
    assert _system_prompt(allow_accounts=False, demographic="bogus") == without


def test_scroll_action_moves_page_and_waits():
    class DummyMouse:
        def __init__(self):
            self.calls = []

        def wheel(self, x, y):
            self.calls.append((x, y))

    class DummyPage:
        url = "https://example.com/"

        def __init__(self):
            self.mouse = DummyMouse()
            self.waits = []

        def wait_for_timeout(self, ms):
            self.waits.append(ms)

    page = DummyPage()
    action = parse_agent_action('{"action": "scroll", "value": "down"}', set())
    result = _apply_action(page, action, ValidatedTarget("https://example.com/", "example.com"))

    assert result == {"ok": True, "url": "https://example.com/"}
    assert page.mouse.calls == [(0, ConfigManager().sentinel.scroll_action_delta_px)]
    assert page.waits == [ConfigManager().sentinel.post_scroll_settle_ms]


def test_click_action_reports_external_links_blocked_when_not_allowed():
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        pytest.skip("Playwright unavailable")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.set_content('<a data-sentinel-id="e1" href="https://jobs.example.org/apply">Apply now</a>')
            action = parse_agent_action('{"action": "click", "element_id": "e1"}', {"e1"})
            result = _apply_action(page, action, ValidatedTarget("https://example.com/", "example.com"))
            browser.close()
    except Exception as e:
        pytest.skip(f"Playwright browser unavailable: {e}")

    assert result["ok"] is False
    assert result["error"] == "Navigation outside target host blocked"
    assert result["blocked_url"] == "https://jobs.example.org/apply"


def test_select_action_sets_option_and_waits():
    class DummyLocator:
        def __init__(self):
            self.calls = []

        def select_option(self, **kwargs):
            self.calls.append(kwargs)

    class DummyPage:
        url = "https://example.com/"

        def __init__(self):
            self.locator_obj = DummyLocator()
            self.waits = []

        def locator(self, selector):
            self.selector = selector
            return type("LocatorHandle", (), {"first": self.locator_obj})()

        def wait_for_timeout(self, ms):
            self.waits.append(ms)

    page = DummyPage()
    action = parse_agent_action('{"action": "select", "element_id": "e2", "value": "Price: Low - High"}', {"e2"})
    result = _apply_action(page, action, ValidatedTarget("https://example.com/", "example.com"))

    assert result == {"ok": True, "url": "https://example.com/"}
    assert page.selector == '[data-sentinel-id="e2"]'
    assert page.locator_obj.calls == [{"label": "Price: Low - High"}]
    assert page.waits == [ConfigManager().sentinel.post_select_settle_ms]


def test_observe_page_ignores_offscreen_hidden_and_covered_elements():
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        pytest.skip("Playwright unavailable")

    html = """
    <style>
      body { margin: 0; }
      #cover { position: absolute; left: 0; top: 90px; width: 160px; height: 60px; background: white; z-index: 2; }
      #covered { position: absolute; left: 0; top: 90px; width: 160px; height: 60px; z-index: 1; }
      #ok { position: absolute; left: 0; top: 180px; width: 160px; height: 60px; }
      #offscreen { position: absolute; left: 0; top: -500px; }
    </style>
    <a id="offscreen" href="#">Offscreen</a>
    <a id="hidden" aria-hidden="true" href="#">Hidden</a>
    <button id="covered">Covered</button>
    <div id="cover"></div>
    <select id="ok"><option>Best Match</option><option>Price: Low - High</option></select>
    """

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 400, "height": 300})
            page.set_content(html)
            observation = _observe_page(page)
            browser.close()
    except Exception as e:
        pytest.skip(f"Playwright browser unavailable: {e}")

    labels = [el["text"] for el in observation["elements"]]
    assert labels == ["Best Match\nPrice: Low - High"]


def test_sentinel_cancel_signals_active_run(client):
    admin = User(username="admin", password="pass", folder="af", is_admin=True)
    active_report = {"run_id": "r1", "status": "running"}
    finished_report = {"run_id": "r2", "status": "completed"}

    with patch("web_app.helpers.DataInterface") as mock_users:
        mock_users.return_value.load_users.return_value = {"admin": admin}
        with client.session_transaction() as sess:
            sess["_user_id"] = "admin"

        with patch("web_app.sentinel.get_run", return_value=active_report), patch(
            "web_app.sentinel.request_cancel", return_value=True
        ) as mock_cancel:
            res = client.post("/sentinel/api/runs/r1/cancel")
        assert res.status_code == 200
        assert res.get_json() == {"run_id": "r1", "cancelled": True}
        mock_cancel.assert_called_once_with("r1")

        with patch("web_app.sentinel.get_run", return_value=finished_report), patch(
            "web_app.sentinel.request_cancel"
        ) as mock_cancel_completed:
            res = client.post("/sentinel/api/runs/r2/cancel")
        assert res.status_code == 200
        assert res.get_json()["cancelled"] is False
        mock_cancel_completed.assert_not_called()


def test_sentinel_delete_run_removes_finished_runs_and_rejects_active_runs(client):
    admin = User(username="admin", password="pass", folder="af", is_admin=True)
    active_report = {"run_id": "r1", "status": "running"}
    finished_report = {"run_id": "r2", "status": "completed"}

    with patch("web_app.helpers.DataInterface") as mock_users:
        mock_users.return_value.load_users.return_value = {"admin": admin}
        with client.session_transaction() as sess:
            sess["_user_id"] = "admin"

        with patch("web_app.sentinel.get_run", return_value=active_report), patch(
            "web_app.sentinel.delete_run"
        ) as mock_delete:
            res = client.post("/sentinel/api/runs/r1/delete")
        assert res.status_code == 409
        mock_delete.assert_not_called()

        with patch("web_app.sentinel.get_run", return_value=finished_report), patch(
            "web_app.sentinel.delete_run", return_value=True
        ) as mock_delete:
            res = client.post("/sentinel/api/runs/r2/delete")
        assert res.status_code == 200
        assert res.get_json() == {"run_id": "r2", "deleted": True}
        mock_delete.assert_called_once_with("r2")


def test_parse_verdict_payload_accepts_pass_fail_and_rejects_garbage():
    assert _parse_verdict_payload('{"verdict":"pass","reason":"Looked good."}') == ("pass", "Looked good.")
    assert _parse_verdict_payload('preamble {"verdict":"fail","reason":"Blocked."} trailing') == (
        "fail",
        "Blocked.",
    )
    assert _parse_verdict_payload("") is None
    assert _parse_verdict_payload("not json at all") is None
    assert _parse_verdict_payload('{"verdict":"maybe","reason":"x"}') is None


def test_classify_run_verdict_downgrades_to_failed_and_records_reason():
    report = {
        "run_id": "r1",
        "prompt": "create an account and log a metric",
        "target_url": "https://example.com",
        "steps": [{"action": "finish", "reason": "blocked", "result": {"ok": True}}],
        "findings": [],
        "final_report": "## Summary\n\nNothing tested.",
    }
    fake_provider = type("P", (), {"verdict_text": lambda self, _: '{"verdict":"fail","reason":"Agent self-aborted on step 1."}'})()
    with patch("web_app.sentinel.runner._get_provider", return_value=fake_provider):
        outcome = _classify_run_verdict(report)
    assert outcome == "failed"
    assert report["verdict_reason"] == "Agent self-aborted on step 1."
    assert any(f["title"] == "Run did not fulfill prompt" for f in report["findings"])


def test_classify_run_verdict_keeps_completed_on_pass_or_provider_failure():
    report = {"run_id": "r2", "prompt": "click around", "steps": [], "findings": [], "final_report": ""}
    pass_provider = type("P", (), {"verdict_text": lambda self, _: '{"verdict":"pass","reason":"Looked fine."}'})()
    with patch("web_app.sentinel.runner._get_provider", return_value=pass_provider):
        assert _classify_run_verdict(report) == "completed"
    assert "verdict_reason" not in report

    raising_provider = type("P", (), {"verdict_text": lambda self, _: (_ for _ in ()).throw(RuntimeError("x"))})()
    with patch("web_app.sentinel.runner._get_provider", return_value=raising_provider):
        assert _classify_run_verdict(report) == "completed"


def test_detect_click_loop_flags_repeated_clicks_on_same_url():
    report = {
        "steps": [
            {"index": 1, "action": "click", "result": {"url": "https://x.test/a"}, "reason": "go"},
            {"index": 2, "action": "click", "result": {"url": "https://x.test/a"}, "reason": "again"},
            {"index": 3, "action": "click", "result": {"url": "https://x.test/a"}, "reason": "still"},
        ],
        "findings": [],
    }
    _detect_click_loop(report)
    titles = [f["title"] for f in report["findings"]]
    assert "Repeated click with no navigation" in titles

    # Idempotent: a second call with no new steps must not duplicate the finding.
    _detect_click_loop(report)
    assert titles.count("Repeated click with no navigation") == 1


def test_detect_click_loop_returns_true_after_max_warnings():
    """When the loop detector has fired enough times, signal that the run
    should be force-stopped instead of just adding another warning."""
    report = {"findings": [], "steps": []}
    cfg = ConfigManager().sentinel
    threshold = cfg.click_loop_threshold
    max_warnings = cfg.click_loop_max_warnings

    # Build a series of distinct loop "events" — each must end on a different
    # step index so the dedup-by-last-step check doesn't suppress the warning.
    stuck = False
    for warn_idx in range(max_warnings):
        for k in range(threshold):
            step_index = warn_idx * threshold + k + 1
            report["steps"].append({
                "index": step_index,
                "action": "click",
                "result": {"url": f"https://x.test/page{warn_idx}"},
                "reason": "click",
            })
        stuck = _detect_click_loop(report)
        if warn_idx + 1 < max_warnings:
            assert stuck is False
    assert stuck is True
    assert sum(
        1 for f in report["findings"] if f["title"] == "Repeated click with no navigation"
    ) == max_warnings


def test_detect_click_loop_no_flag_when_url_changes_or_action_differs():
    moved = {
        "steps": [
            {"index": 1, "action": "click", "result": {"url": "https://x.test/a"}, "reason": ""},
            {"index": 2, "action": "click", "result": {"url": "https://x.test/b"}, "reason": ""},
            {"index": 3, "action": "click", "result": {"url": "https://x.test/a"}, "reason": ""},
        ],
        "findings": [],
    }
    _detect_click_loop(moved)
    assert moved["findings"] == []

    mixed = {
        "steps": [
            {"index": 1, "action": "click", "result": {"url": "https://x.test/a"}, "reason": ""},
            {"index": 2, "action": "fill", "result": {"url": "https://x.test/a"}, "reason": ""},
            {"index": 3, "action": "click", "result": {"url": "https://x.test/a"}, "reason": ""},
        ],
        "findings": [],
    }
    _detect_click_loop(mixed)
    assert mixed["findings"] == []


def test_request_agent_action_retries_once_on_parse_failure():
    """First call returns garbage, second returns valid JSON — should succeed."""
    report = {
        "target_url": "https://x.test/",
        "prompt": "go",
        "steps": [],
        "findings": [],
        "allow_accounts": False,
        "demographic": "",
    }
    observation = {"url": "https://x.test/", "title": "X", "elements": [{"id": "e1", "text": "Go"}]}

    responses = iter(["not json", '{"action":"click","element_id":"e1","reason":"go"}'])
    fake_provider = type("P", (), {"agent_text": lambda self, *a, **kw: next(responses)})()

    with patch("web_app.sentinel.runner._get_provider", return_value=fake_provider):
        action = _request_agent_action(report, observation, [], False, {"e1"})

    assert action is not None
    assert action.action == "click"
    assert report["findings"] == []  # success on retry, no warning


def test_request_agent_action_records_invalid_step_when_retry_also_fails():
    report = {
        "target_url": "https://x.test/",
        "prompt": "go",
        "steps": [],
        "findings": [],
        "allow_accounts": False,
        "demographic": "",
    }
    observation = {"url": "https://x.test/", "title": "X", "elements": []}
    fake_provider = type("P", (), {"agent_text": lambda self, *a, **kw: "still not json"})()

    with patch("web_app.sentinel.runner._get_provider", return_value=fake_provider):
        action = _request_agent_action(report, observation, [], False, set())

    assert action is None
    assert report["steps"] and report["steps"][0]["action"] == "invalid"
    assert any(f["title"] == "Agent response unparseable" for f in report["findings"])


def test_screenshot_manifest_pairs_step_filename_with_action():
    report = {
        "target_url": "https://x.test/",
        "screenshots": ["screenshots/step-00.png", "screenshots/step-01.png", "screenshots/step-02.png"],
        "steps": [
            {"index": 1, "action": "click", "reason": "View Chart", "result": {"url": "https://x.test/c"}},
            {"index": 2, "action": "finish", "reason": "done", "result": {"url": "https://x.test/c"}},
        ],
    }
    manifest = _screenshot_manifest(report)
    assert manifest[0] == {"filename": "step-00.png", "produced_by": "initial", "url": "https://x.test/"}
    assert manifest[1]["filename"] == "step-01.png"
    assert "click" in manifest[1]["produced_by"] and "View Chart" in manifest[1]["produced_by"]
    assert manifest[1]["url"] == "https://x.test/c"


def test_pick_final_report_screenshots_uses_picker_then_falls_back():
    report = {
        "run_id": "r",
        "prompt": "verify chart shows",
        "target_url": "https://x.test/",
        "screenshots": [f"screenshots/step-{i:02d}.png" for i in range(0, 5)],
        "steps": [{"index": i, "action": "click", "reason": "x", "result": {"url": ""}} for i in range(1, 5)],
        "findings": [],
    }

    picker_provider = type("P", (), {
        "screenshot_picker_text": lambda self, _: '{"screenshots":["step-04.png","step-02.png"],"reason":"chart and form"}'
    })()
    with patch("web_app.sentinel.runner._get_provider", return_value=picker_provider):
        chosen = _pick_final_report_screenshots(report)
    assert chosen == ["step-04.png", "step-02.png"]

    raising = type("P", (), {"screenshot_picker_text": lambda self, _: (_ for _ in ()).throw(RuntimeError("x"))})()
    with patch("web_app.sentinel.runner._get_provider", return_value=raising):
        chosen = _pick_final_report_screenshots(report)
    # fallback = last `budget` filenames; budget defaults to 6 so all 5 returned
    assert chosen == ["step-00.png", "step-01.png", "step-02.png", "step-03.png", "step-04.png"]


def test_parse_picker_payload_filters_to_allowed_and_respects_budget():
    allowed = {"step-00.png", "step-04.png", "step-09.png"}
    picked = _parse_picker_payload(
        '{"screenshots":["step-04.png","step-99.png","step-09.png","step-04.png"],"reason":"x"}',
        allowed,
        budget=2,
    )
    assert picked == ["step-04.png", "step-09.png"]
    assert _parse_picker_payload("not json", allowed, budget=3) == []
    assert _parse_picker_payload('{"foo":"bar"}', allowed, budget=3) == []


def _login_admin(client, mock_users):
    admin = User(username="admin", password="pass", folder="af", is_admin=True)
    mock_users.return_value.load_users.return_value = {"admin": admin}
    with client.session_transaction() as sess:
        sess["_user_id"] = "admin"


def test_parse_batch_payload_generates_name_when_blank():
    payload = {"name": " ", "items": [{"url": "https://example.com", "prompt": "check checkout"}]}

    with patch("web_app.sentinel.validate_public_web_url") as mock_validate, patch(
        "web_app.sentinel._generate_title", return_value="Checkout smoke test"
    ):
        mock_validate.return_value = ValidatedTarget("https://example.com/", "example.com")
        name, items = _parse_batch_payload(payload)

    assert name == "Checkout smoke test"
    assert items[0]["url"] == "https://example.com"


def test_create_batch_queues_runs_inline_without_persisting_a_batch(client, tmp_path):
    """A single POST queues one run per item, all sharing a new batch_id and the
    batch label. Inline per-item credentials are forwarded to start_run (held in
    memory only); NO batch entity is written to disk."""
    from web_app.sentinel.data_interface import DataInterface as SentinelData

    calls = []

    def fake_start_run(target, prompt, limit_s, **kwargs):
        calls.append({"target": target, "prompt": prompt, "limit_s": limit_s, **kwargs})
        return {"run_id": f"{len(calls):032x}", "status": "queued"}

    def make_data():
        d = SentinelData()
        d.sentinel_dir = tmp_path / "sentinel"
        d.runs_dir = d.sentinel_dir / "runs"
        return d

    with patch("web_app.helpers.DataInterface") as mock_users:
        _login_admin(client, mock_users)
        with patch("web_app.sentinel.DataInterface", side_effect=make_data), patch(
            "web_app.sentinel.validate_public_web_url"
        ) as mock_validate, patch("web_app.sentinel.start_run", side_effect=fake_start_run):
            mock_validate.return_value = ValidatedTarget("https://example.com/", "example.com")

            res = client.post(
                "/sentinel/api/batches",
                json={
                    "name": "Device sweep",
                    "items": [
                        {"url": "https://example.com", "prompt": "explore", "device": "small_phone"},
                        {"url": "https://example.com", "prompt": "explore", "device": "desktop",
                         "allow_accounts": True,
                         "account_credentials": {"username": "qatester", "password": "hunter2", "extras": {}}},
                    ],
                },
            )

    assert res.status_code == 202
    body = res.get_json()
    batch_id = body["batch_id"]
    assert len(body["run_ids"]) == 2

    # One start_run per item, all sharing the batch_id and the batch label.
    assert len(calls) == 2
    assert {c["batch_id"] for c in calls} == {batch_id}
    assert {c["batch_label"] for c in calls} == {"Device sweep"}
    assert calls[0]["device"] == "small_phone"
    # Credentials forwarded only for the item that supplied them.
    assert calls[0]["account_credentials"] is None
    assert calls[1]["account_credentials"] == {"username": "qatester", "password": "hunter2", "extras": {}}

    # No saved batch entity is written anywhere.
    assert not (tmp_path / "sentinel" / "batches").exists()


def test_batch_status_groups_runs_by_batch_id(client):
    """GET /api/batch/<id> returns the runs sharing that batch_id; 404 when none."""
    runs = [
        {"run_id": "a" * 32, "batch_id": "bid1", "batch_label": "B1", "status": "running",
         "title": "Run A", "target_url": "https://example.com", "run_outcome": None},
        {"run_id": "b" * 32, "batch_id": "bid1", "batch_label": "B1", "status": "completed",
         "title": "Run B", "target_url": "https://example.com", "run_outcome": "completed"},
        {"run_id": "c" * 32, "batch_id": "", "status": "completed", "title": "Solo",
         "target_url": "https://example.com", "run_outcome": "completed"},
    ]
    with patch("web_app.helpers.DataInterface") as mock_users:
        _login_admin(client, mock_users)
        with patch("web_app.sentinel.DataInterface") as mock_data:
            mock_data.return_value.list_reports.return_value = runs
            res = client.get("/sentinel/api/batch/bid1")
            missing = client.get("/sentinel/api/batch/nope")

    assert res.status_code == 200
    children = res.get_json()["child_runs"]
    assert {c["run_id"] for c in children} == {"a" * 32, "b" * 32}
    assert missing.status_code == 404


def test_delete_batch_removes_child_runs_and_rejects_active_children(client):
    active_runs = [
        {"run_id": "a" * 32, "batch_id": "bid1", "status": "completed", "batch_label": "B1"},
        {"run_id": "b" * 32, "batch_id": "bid1", "status": "running", "batch_label": "B1"},
    ]
    finished_runs = [
        {"run_id": "a" * 32, "batch_id": "bid1", "status": "completed", "batch_label": "B1"},
        {"run_id": "b" * 32, "batch_id": "bid1", "status": "cancelled", "batch_label": "B1"},
    ]
    with patch("web_app.helpers.DataInterface") as mock_users:
        _login_admin(client, mock_users)
        with patch("web_app.sentinel.DataInterface") as mock_data, patch(
            "web_app.sentinel.delete_run"
        ) as mock_delete:
            mock_data.return_value.list_reports.return_value = active_runs
            res = client.post("/sentinel/api/batch/bid1/delete")
            assert res.status_code == 409
            mock_delete.assert_not_called()

            mock_data.return_value.list_reports.return_value = finished_runs
            mock_delete.return_value = True
            res = client.post("/sentinel/api/batch/bid1/delete")

    assert res.status_code == 200
    assert res.get_json() == {"batch_id": "bid1", "deleted": True, "run_ids": ["a" * 32, "b" * 32]}
    assert [call.args[0] for call in mock_delete.call_args_list] == ["a" * 32, "b" * 32]


def test_create_batch_rejects_invalid_item_url(client):
    """A batch with an item whose URL fails validation is rejected with 400."""
    with patch("web_app.helpers.DataInterface") as mock_users:
        _login_admin(client, mock_users)
        with patch("web_app.sentinel.validate_public_web_url",
                   side_effect=TargetValidationError("bad url")):
            res = client.post(
                "/sentinel/api/batches",
                json={"name": "Bad", "items": [{"url": "http://localhost"}]},
            )
    assert res.status_code == 400
    assert "bad url" in res.get_json()["error"]
