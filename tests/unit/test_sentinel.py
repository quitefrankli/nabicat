from unittest.mock import patch

import pytest

from web_app.app import app
from web_app.config import ConfigManager
from web_app.helpers import limiter, register_all_blueprints
from web_app.sentinel import _limit_from_request, _report_payload
from web_app.sentinel.actions import ActionValidationError, parse_agent_action
from web_app.sentinel.runner import (
    _add_finding,
    _agent_prompt,
    _annotate_screenshot,
    _apply_action,
    _BedrockProvider,
    _build_codex_cmd,
    _classify_run_verdict,
    _clean_title,
    _codex_text,
    _CodexProvider,
    _ensure_summary_heading,
    _fallback_title,
    _final_report_prompt,
    _generate_title,
    _get_provider,
    _host_allowed,
    _MeridianProvider,
    _observe_page,
    _parse_verdict_payload,
    _system_prompt,
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


def test_runner_allows_only_exact_host_or_www_redirect_variant():
    assert _host_allowed("google.com", "google.com")
    assert _host_allowed("www.google.com", "google.com")
    assert _host_allowed("google.com", "www.google.com")
    assert not _host_allowed("accounts.google.com", "google.com")
    assert not _host_allowed("example.com", "google.com")


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

    with patch("web_app.sentinel.runner.tempfile.NamedTemporaryFile", return_value=DummyOutput()), patch(
        "web_app.sentinel.runner.subprocess.run"
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
        assert isinstance(_get_provider(), _MeridianProvider)
        cfg.llm.api_source = "codex"
        assert isinstance(_get_provider(), _CodexProvider)
        cfg.llm.api_source = "bedrock"
        assert isinstance(_get_provider(), _BedrockProvider)
    finally:
        cfg.llm.api_source = original


def test_meridian_provider_calls_meridian_text_with_screenshots(tmp_path):
    image = tmp_path / "step.png"
    image.write_bytes(b"png-bytes")

    with patch("web_app.sentinel.runner.meridian_text", return_value="agent reply") as mock_meridian:
        result = _MeridianProvider().agent_text("user prompt", image_paths=[image])

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


def test_sentinel_routes_require_admin_and_start_run(client):
    non_admin = User(username="user", password="pass", folder="uf", is_admin=False)
    admin = User(username="admin", password="pass", folder="af", is_admin=True)

    with patch("web_app.helpers.DataInterface") as mock_users:
        mock_users.return_value.load_users.return_value = {"user": non_admin, "admin": admin}
        with client.session_transaction() as sess:
            sess["_user_id"] = "user"
        assert client.get("/sentinel/").status_code == 403

        with client.session_transaction() as sess:
            sess["_user_id"] = "admin"

        with patch("web_app.sentinel.validate_public_web_url") as mock_validate, patch(
            "web_app.sentinel.start_run"
        ) as mock_start:
            mock_validate.return_value.url = "https://example.com"
            mock_start.return_value = {"run_id": "run-123", "status": "queued"}

            res = client.post("/sentinel/api/runs", json={"url": "https://example.com", "limit": 5})

    assert res.status_code == 202
    assert res.get_json()["run_id"] == "run-123"
    mock_start.assert_called_once()


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
    assert page.waits == [ConfigManager().sentinel.wait_action_ms]


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
    assert page.waits == [ConfigManager().sentinel.wait_action_ms]


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
