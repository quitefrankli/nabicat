"""Wait for the public server to serve a release's exact commit."""

import json
import sys
import time
from urllib.error import URLError
from urllib.request import Request, urlopen

from web_app.config import ConfigManager


def wait_for_deployment(expected_commit: str, config: ConfigManager) -> bool:
    deadline = time.monotonic() + config.deployment_verification_timeout_s
    request = Request(
        f"{config.site_url.rstrip('/')}/api/health",
        headers={"Cache-Control": "no-cache"},
    )
    observed_commit = "unknown"
    print(f"Waiting for public deployment of {expected_commit}", flush=True)
    while (remaining := deadline - time.monotonic()) > 0:
        try:
            with urlopen(
                request,
                timeout=min(config.deployment_verification_request_timeout_s, remaining),
            ) as response:
                health = json.load(response)
            if isinstance(health, dict):
                observed_commit = health.get("commit", "unknown")
                if health.get("status") == "ok" and observed_commit == expected_commit:
                    print(f"Verified public deployment of {expected_commit}", flush=True)
                    return True
        except (URLError, OSError, ValueError) as error:
            print(f"Health check unavailable ({type(error).__name__}); retrying", flush=True)
        time.sleep(min(
            config.deployment_verification_interval_s,
            max(0, deadline - time.monotonic()),
        ))
    print(
        f"Timed out waiting for {expected_commit}; last observed commit: {observed_commit}",
        flush=True,
    )
    return False


if __name__ == "__main__":
    sys.exit(0 if wait_for_deployment(sys.argv[1], ConfigManager()) else 1)
