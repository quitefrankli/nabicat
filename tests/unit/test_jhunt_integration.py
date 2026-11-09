"""Isolated browser acceptance against a real local JHunt API and worker writes."""

import json
import os
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest
import requests
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

BACKEND = Path(os.environ.get("JHUNT_BACKEND_CHECKOUT", "/nonexistent"))
pytestmark = pytest.mark.skipif(
    not os.environ.get("JHUNT_BACKEND_CHECKOUT"),
    reason="Set JHUNT_BACKEND_CHECKOUT for opt-in cross-repository browser acceptance",
)
PYTHON = str(BACKEND / ".venv/bin/python")
SEED = """
import json,sys
from pathlib import Path
from jhunt.runtime import Runtime
from jhunt.config import Settings
r=Runtime(Settings(_env_file=None, data_dir=Path(sys.argv[1]), api_token='t'*32))
job=r.db.put('vacancy_revision', {'description':'Fixture systems job'})
vacancy=r.db.put('vacancy', {'revision_id':job['id']})
assessment=r.db.put('assessment', {'rationale':'Fixture relevance rationale'})
pdf=r.artifacts.write(b'%PDF-1.4 fixture download')
package=r.db.put('package', {'vacancy_id':vacancy['id'], 'vacancy_revision':job['id'], 'assessment_id':assessment['id'], 'resume':{'pdf':pdf}, 'manifest_hash':'a'*64, 'answers':{'name':'Fixture person'}, 'validation':{'valid':True}}, state='awaiting_approval')
task=r.db.put('agent_task', {'question':{'text':'Fixture question?'},'continuation':{'kind':'prepare','data':{}}}, state='needs_input')
print(json.dumps({'task':task['id'],'package':package['id'],'pdf':pdf}))
"""


SEARCH_SETUP = """
import sys
from pathlib import Path
from jhunt.config import Settings
from jhunt.runtime import Runtime
from jhunt.discovery import SourcePage, SourceError
class SearchSource:
    name = 'fixture'
    query_independent = True
    async def search(self, query, cursor=None):
        return SourcePage(listings=[])
class OtherSource(SearchSource):
    name = 'other'
    async def search(self, query, cursor=None):
        if (Path(sys.argv[1]) / 'fail-source').exists():
            raise SourceError('Fixture source unavailable', retry_after=0)
        return SourcePage(listings=[])
r=Runtime(Settings(_env_file=None, data_dir=Path(sys.argv[1]), api_token='t'*32,
                   tunnel_ssh_host='', max_attempts=2), source=SearchSource())
r.discovery.sources=[r.discovery.source, OtherSource()]
"""
SERVE = SEARCH_SETUP + """
import uvicorn
from jhunt.api import create_app
uvicorn.run(create_app(r), host='127.0.0.1', port=int(sys.argv[2]), log_level='error', timeout_graceful_shutdown=1)
"""
RUN_SEARCH = SEARCH_SETUP + """
import asyncio
from jhunt.worker import tick
asyncio.run(tick(r))
"""


def backend_command(data, code):
    return subprocess.run(
        [PYTHON, "-c", code, str(data)],
        cwd=BACKEND,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout


def test_debug_browser_with_live_backend(app, auth_loader, monkeypatch, tmp_path):
    from web_app.config import ConfigManager
    from web_app.users import User
    from web_app.__main__ import cli_start
    import web_app.__main__ as entry
    from click.testing import CliRunner

    data = tmp_path / "backend"
    ids = json.loads(backend_command(data, SEED).splitlines()[-1])
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    backend_port = sock.getsockname()[1]
    sock.close()
    env = {
        **os.environ,
        "JHUNT_DATA_DIR": str(data),
        "JHUNT_API_TOKEN": "t" * 32,
        "JHUNT_CODEX_ENABLED": "false",
        "JHUNT_JSEARCH_ENABLED": "false",
        "JHUNT_CAREER_OPS_ENABLED": "false",
        "JHUNT_TUNNEL_SSH_HOST": "",
    }
    backend = subprocess.Popen(
        [
            PYTHON, "-c", SERVE, str(data), str(backend_port),
        ],
        cwd=BACKEND,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    server = None
    thread = None
    try:
        deadline = time.monotonic() + 8
        while True:
            try:
                if (
                    requests.get(
                        f"http://127.0.0.1:{backend_port}/health",
                        headers={"Authorization": "Bearer " + "t" * 32},
                        timeout=0.3,
                    ).status_code
                    == 200
                ):
                    break
            except requests.RequestException:
                pass
            assert backend.poll() is None and time.monotonic() < deadline
            time.sleep(0.05)
        for key, value in {
            "ENABLED": "false",
            "BASE_URL": f"http://127.0.0.1:{backend_port}",
            "TOKEN": "",
            "GENERATION": "fixture",
        }.items():
            monkeypatch.setenv("JHUNT_DEBUG_" + key, value)
        config = ConfigManager()
        monkeypatch.setattr(config, "jhunt_stream_max_lifetime_s", 3)
        app.config.update(WTF_CSRF_ENABLED=True, SESSION_COOKIE_SECURE=False)
        user = User(
            username="fixture-admin", password="pw", folder="fixture", is_admin=True
        )
        auth_loader(user)
        monkeypatch.setattr(entry, "app", app)
        monkeypatch.setattr(entry, "ensure_local_redis", lambda: None)

        # Exercise the actual --debug flag with an isolated threaded HTTP listener.
        def run(host, port, debug):
            nonlocal server, thread
            assert debug is True
            app.debug = debug
            server = make_server("127.0.0.1", 0, app, threaded=True)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()

        monkeypatch.setattr(app, "run", run)
        result = CliRunner().invoke(cli_start, ["--debug", "--port", "12345"])
        assert result.exit_code == 0, result.output
        assert config.debug_mode
        origin = f"http://127.0.0.1:{server.server_port}"
        serializer = app.session_interface.get_signing_serializer(app)
        cookie = serializer.dumps({"_user_id": user.id, "_fresh": True})
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                **(
                    {"executable_path": os.environ["JHUNT_BROWSER_EXECUTABLE"]}
                    if os.environ.get("JHUNT_BROWSER_EXECUTABLE")
                    else {}
                ),
            )
            context = browser.new_context()
            context.add_cookies(
                [
                    {
                        "name": app.config["SESSION_COOKIE_NAME"],
                        "value": cookie,
                        "url": origin,
                    }
                ]
            )
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.set_viewport_size({"width": 375, "height": 812})
            page.goto(origin + "/jhunt-ui/")
            assert page.locator("#jhunt-connection").inner_text().strip() == "Not connected"
            assert page.get_by_role("button", name="Connect JHunt", exact=True).count() == 0
            assert page.locator("input[type=password]").count() == 0
            # Simulate automatic SSH registration while the page retries its inbox.
            from web_app.jhunt_ui.bridge import JHuntClient
            from web_app.jhunt_ui.connection_store import save_verified_connection

            with JHuntClient({
                "enabled": True,
                "base_url": f"http://127.0.0.1:{backend_port}",
                "token": "t" * 32,
                "generation": "fixture",
            }) as bridge:
                verified = bridge.load_inbox()
                save_verified_connection(bridge.settings, verified["store_id"])
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            assert "t" * 32 not in page.content()
            page.wait_for_function(
                "document.querySelector('#jhunt-connection').textContent.includes('Connected')"
            )
            assert (
                "Fixture question?" in page.locator("#jhunt-questions").text_content()
            )
            assert (
                "Fixture relevance rationale"
                in page.locator("#jhunt-applications").text_content()
            )
            # Search controls exercise the real bridge, API and separate worker.
            criteria = page.locator('#jhunt-criteria')
            criteria.fill('First systems search')
            page.get_by_role('button', name='Save criteria', exact=True).click()
            page.wait_for_function("document.querySelector('#jhunt-message').textContent.includes('Criteria saved')")
            assert page.locator('#jhunt-active-searches').inner_text() == 'No active searches.'
            criteria.fill('Updated systems search')
            page.get_by_role('button', name='Search now', exact=True).click()
            page.wait_for_function("document.querySelector('#jhunt-active-searches').textContent.includes('Queued')")
            criteria.fill('Unsent criteria draft')
            criteria.focus()
            backend_command(data, RUN_SEARCH)
            page.wait_for_function("document.querySelector('#jhunt-latest-search strong')?.textContent==='completed'")
            assert criteria.input_value() == 'Unsent criteria draft'
            assert criteria.evaluate('(node)=>node===document.activeElement')
            assert 'Assessment and preparation continue separately' in page.locator('#jhunt-latest-search').inner_text()
            second = context.new_page()
            second.goto(origin + '/jhunt-ui/')
            second.wait_for_function("document.querySelector('#jhunt-connection').textContent==='Connected'")
            second.locator('#jhunt-criteria').fill('Other tab criteria')
            second.get_by_role('button', name='Save criteria', exact=True).click()
            page.get_by_role('button', name='Compare with saved criteria').wait_for()
            assert criteria.input_value() == 'Unsent criteria draft'
            assert page.get_by_role('button', name='Save criteria', exact=True).is_disabled()
            page.get_by_role('button', name='Compare with saved criteria').click()
            assert 'Other tab criteria' in page.locator('#jhunt-search-conflict').inner_text()
            page.get_by_role('button', name='Reload saved criteria', exact=True).click()
            assert criteria.input_value() == 'Other tab criteria'
            second.close()
            # Lose a response after acceptance: reconcile the receipt, never replay.
            search_attempts = []
            def lose_search_response(route):
                search_attempts.append(route.request.url)
                route.fetch()
                route.abort('failed')
            page.route('**/api/search/requests', lose_search_response)
            page.get_by_role('button', name='Search now', exact=True).click()
            page.wait_for_function("document.querySelector('#jhunt-active-searches').textContent.includes('Queued')")
            page.wait_for_function("!document.querySelector('#jhunt-search-now').disabled")
            assert len(search_attempts) == 1
            page.unroute('**/api/search/requests', lose_search_response)
            (data / 'fail-source').write_text('fixture failure')
            backend_command(data, RUN_SEARCH)
            page.wait_for_function("document.querySelector('#jhunt-latest-search').textContent.includes('partial-failure')")
            assert 'Fixture source unavailable' in page.locator('#jhunt-latest-search').inner_text()

            # Manual and automatic searches share one live list and cancellation.
            page.get_by_role('button', name='Search now', exact=True).click()
            page.wait_for_function("document.querySelector('#jhunt-active-searches').textContent.includes('Manual search')")
            backend_command(data, SEARCH_SETUP + "\nr.discovery.save_criteria('Automatic fixture criteria')\nr.db.schedule(86400, accept=r.searches.accept_scheduled_in_session)\n")
            page.wait_for_function("document.querySelector('#jhunt-active-searches').textContent.includes('Automatic search')")
            assert page.get_by_role('heading', name='Searches in progress', exact=True).count() == 1
            active = page.locator('#jhunt-active-searches article')
            assert active.count() == 2
            assert '0 of 2 sources finished' in active.first.inner_text()
            cancellation_attempts = []
            def lose_cancel_response(route):
                cancellation_attempts.append(route.request.url)
                route.fetch()
                route.abort('failed')
            page.route('**/api/search/requests/*/cancel', lose_cancel_response)
            active.filter(has_text='Manual search').get_by_role('button', name='Cancel search', exact=True).click()
            page.wait_for_function("document.querySelectorAll('#jhunt-active-searches article').length===1")
            assert len(cancellation_attempts) == 1
            page.unroute('**/api/search/requests/*/cancel', lose_cancel_response)
            page.get_by_role('button', name='Cancel search', exact=True).click()
            page.wait_for_function("document.querySelector('#jhunt-active-searches').textContent.includes('No active searches')")
            page.wait_for_function("document.querySelector('#jhunt-latest-search strong')?.textContent==='cancelled'")

            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            field = page.locator("#jhunt-questions textarea")
            field.fill("Unsent fixture answer")
            field.focus()
            update = "from pathlib import Path; import sys; from jhunt.storage import Database; db=Database(Path(sys.argv[1])); db.put('notification',{'event':'fixture_update'})"
            started = time.monotonic()
            backend_command(data, update)
            page.wait_for_function(
                "document.querySelector('#jhunt-notifications').textContent.includes('fixture update')"
            )
            assert time.monotonic() - started < 2
            assert field.input_value() == "Unsent fixture answer"
            assert field.evaluate("(node)=>node===document.activeElement")
            # Unrelated updates preserve open review disclosures and focus.
            page.locator("#jhunt-applications details").first.evaluate(
                "(node)=>node.open=true"
            )
            confirm = page.locator("#jhunt-applications input[type=checkbox]")
            confirm.check()
            update_package = (
                "from pathlib import Path; import sys; from jhunt.storage import Database,Record; db=Database(Path(sys.argv[1])); "
                + "\nwith db.transaction() as s:\n r=s.get(Record,"
                + repr(ids["package"])
                + "); r.data={**r.data,'manifest_hash':'b'*64}"
            )
            backend_command(data, update_package)
            page.wait_for_function(
                "document.querySelector('#jhunt-applications').textContent.includes('"
                + "b" * 64
                + "')"
            )
            assert not confirm.is_checked()
            assert page.get_by_role("button", name="Approve submission").is_disabled()
            assert field.input_value() == "Unsent fixture answer"
            # A changed question cannot receive the earlier draft.
            update_question = (
                "from pathlib import Path; import sys; from jhunt.storage import Database,Record; db=Database(Path(sys.argv[1])); "
                + "\nwith db.transaction() as s:\n r=s.get(Record,"
                + repr(ids["task"])
                + "); r.data={**r.data,'question':{'text':'Changed fixture question?'}}"
            )
            backend_command(data, update_question)
            page.wait_for_function(
                "document.querySelector('#jhunt-questions').textContent.includes('Changed fixture question?')"
            )
            assert field.input_value() == ""
            field.fill("Fresh answer")
            download = page.locator("a", has_text="Open resume PDF").get_attribute(
                "href"
            )
            pdf = context.request.get(origin + download)
            assert pdf.status == 200 and pdf.body().startswith(b"%PDF-1.4")
            denied = context.request.post(
                origin + "/jhunt-ui/api/tasks/" + ids["task"] + "/input",
                data={"answer": {"text": "No CSRF"}},
            )
            assert denied.status == 400
            # The page retains a draft while its backend is unavailable and reconnects.
            backend.terminate()
            backend.wait(timeout=5)
            page.wait_for_function(
                "document.querySelector('#jhunt-connection').textContent.includes('Stale')"
            )
            assert page.get_by_role("button", name="Send answer").is_disabled()
            backend = subprocess.Popen(
                [
                PYTHON, "-c", SERVE, str(data), str(backend_port),
                ],
                cwd=BACKEND,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            page.wait_for_function(
                "document.querySelector('#jhunt-connection').textContent==='Connected'"
            )
            assert field.input_value() == "Fresh answer"
            assert 'cancelled' in page.locator('#jhunt-latest-search').inner_text()
            # Reject the fixture package; this never schedules an external submission.
            page.get_by_role("button", name="Reject package").click()
            page.wait_for_function(
                "document.querySelector('#jhunt-applications').textContent.includes('rejected')"
            )
            attempts = []

            def lose_answer_response(route):
                attempts.append(route.request.url)
                route.fetch()
                route.abort("failed")

            page.route("**/api/tasks/*/input", lose_answer_response)
            page.get_by_role("button", name="Send answer").click()
            page.wait_for_function(
                "!document.querySelector('#jhunt-questions textarea')"
            )
            page.wait_for_function(
                "document.querySelector('#jhunt-message').textContent.includes('Outcome unconfirmed')"
            )
            assert len(attempts) == 1
            # A first-load outage must recover without a manual reload, too.
            recovered = context.new_page()
            inbox_calls = []

            def initial_outage(route):
                inbox_calls.append(1)
                if len(inbox_calls) == 1:
                    route.fulfill(
                        status=503,
                        content_type="application/json",
                        body='{"error":"fixture outage"}',
                    )
                else:
                    route.continue_()

            recovered.route("**/api/inbox", initial_outage)
            recovered.goto(origin + "/jhunt-ui/")
            recovered.wait_for_function(
                "document.querySelector('#jhunt-connection').textContent==='Connected'",
                timeout=10000,
            )
            assert len(inbox_calls) > 1
            recovered.close()
            # A replacement backend clears old drafts and pending mutation context.
            criteria.fill('Draft for previous backend')
            def replace_backend(route):
                response = route.fetch()
                inbox = response.json()
                inbox.update(store_id='replacement-store', connection_generation='replacement-generation')
                inbox['search'] = {'criteria': None, 'sources': [], 'active_requests': [], 'latest_request': None}
                route.fulfill(response=response, json=inbox)
            page.route('**/api/inbox', replace_backend)
            backend_command(data, update)
            page.wait_for_function("document.querySelector('#jhunt-criteria').value===''")
            assert page.get_by_role('button', name='Search now', exact=True).is_disabled()
            assert 'No active searches' in page.locator('#jhunt-active-searches').inner_text()
            assert not errors, errors
            context.close()
            browser.close()
    finally:
        if server:
            server.shutdown()
            thread.join(timeout=3)
        backend.terminate()
        try:
            backend.wait(timeout=5)
        except subprocess.TimeoutExpired:
            backend.kill()
            backend.wait(timeout=5)
