import click
from git import Repo
from werkzeug.serving import is_running_from_reloader

from web_app.app import app
from web_app.config import ConfigManager
from web_app.logging_utils import configure_logging, log_event
from web_app.redis_client import ensure_local_redis

@click.command()
@click.option('--debug', is_flag=True, default=False)
@click.option('--port', default=ConfigManager().server_default_port, type=int)
@click.option('--llm-source', type=click.Choice(['meridian', 'codex', 'bedrock']), default=None,
              help='Override the default LLM provider for this process.')
def cli_start(
    debug: bool,
    port: int,
    llm_source: str | None,
):
    configure_logging(debug=debug)
    cfg = ConfigManager()
    cfg.debug_mode = debug
    if llm_source:
        cfg.llm.api_source = llm_source
    app.secret_key = cfg.flask_secret_key
    app.config["SESSION_COOKIE_NAME"] = cfg.flask_session_cookie_name

    ensure_local_redis()

    mode = "debug" if debug else "development"
    if not debug or is_running_from_reloader():
        log_event("system", "server.started", llm_source=cfg.llm.api_source, mode=mode)
    # Under --debug, Werkzeug's reloader re-execs this whole module in a child
    # process, so everything from here up runs twice: once in the supervising
    # parent and once in the serving child (WERKZEUG_RUN_MAIN=true). The parent
    # spawns redis-server; the child then finds it already up. That's why cold
    # debug starts log both "Starting local redis-server" and "already running".
    app.run(
        host=cfg.server_host,
        port=port,
        debug=debug,
    )

def prod_entry():
    configure_logging(debug=False)
    config = ConfigManager()
    config.debug_mode = False
    app.secret_key = config.flask_secret_key
    app.config["SESSION_COOKIE_NAME"] = config.flask_session_cookie_name
    app.config["DEPLOY_COMMIT"] = Repo(".").head.commit.hexsha

    ensure_local_redis()

    log_event("system", "worker.started", mode="production")
    return app


if __name__ == '__main__':
    cli_start()
