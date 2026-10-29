"""Deploy current main once, then verify the public commit while CI holds its lock."""

import json
import os
import subprocess
import sys
from urllib.error import URLError
from urllib.request import Request, urlopen

from scripts.verify_deployment import wait_for_deployment
from web_app.config import ConfigManager


def deploy_main(expected_commit: str, config: ConfigManager, username: str, password: str) -> bool:
    subprocess.check_output(
        ['git', 'fetch', 'origin', 'main'], stderr=subprocess.STDOUT,
        timeout=config.deployment_git_timeout_s,
    )
    current_main = subprocess.check_output(
        ['git', 'rev-parse', 'origin/main'], text=True,
        timeout=config.deployment_git_timeout_s,
    ).strip()
    if current_main != expected_commit:
        print('Skipping deployment: main has advanced beyond this commit.', flush=True)
        return True

    health_request = Request(
        f"{config.site_url.rstrip('/')}/api/health",
        headers={'Cache-Control': 'no-cache'},
    )
    try:
        with urlopen(health_request, timeout=config.deployment_verification_request_timeout_s) as response:
            health = json.load(response)
        if isinstance(health, dict) and health.get('status') == 'ok' and health.get('commit') == expected_commit:
            print(f'Skipping deployment: {expected_commit} is already live.', flush=True)
            return True
    except (URLError, OSError, ValueError) as error:
        print(f'Public health unavailable ({type(error).__name__}); requesting deployment.', flush=True)

    update_request = Request(
        f"{config.site_url.rstrip('/')}/api/update",
        data=json.dumps({'username': username, 'password': password}).encode(),
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    with urlopen(update_request, timeout=config.deployment_trigger_timeout_s) as response:
        result = json.load(response)
    if not isinstance(result, dict) or result.get('success') is not True:
        print('Deployment request was rejected.', flush=True)
        return False
    return wait_for_deployment(expected_commit, config)


if __name__ == '__main__':
    try:
        succeeded = deploy_main(
            sys.argv[1], ConfigManager(), os.environ['ADMIN_USER'], os.environ['ADMIN_PASS'],
        )
    except (URLError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(f'Deployment failed ({type(error).__name__}).', flush=True)
        succeeded = False
    sys.exit(0 if succeeded else 1)
