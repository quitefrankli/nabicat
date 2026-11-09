"""Pair a local JHunt review API with this production Nabicat instance."""

from __future__ import annotations

import json
import sys
import uuid

from web_app.config import ConfigManager
from web_app.jhunt_ui.bridge import BridgeError, JHuntClient, valid_store_id, valid_token
from web_app.jhunt_ui.connection_store import save_verified_connection
from web_app.logging_utils import log_event


def main(stdin=None, stdout=None) -> int:
    stdin = stdin if stdin is not None else sys.stdin
    stdout = stdout if stdout is not None else sys.stdout
    config = ConfigManager()
    config.debug_mode = False

    try:
        raw = stdin.read(config.jhunt_pair_input_max_bytes + 1)
        if len(raw.encode("utf-8")) > config.jhunt_pair_input_max_bytes:
            raise ValueError("input_too_large")
        body = json.loads(raw)
        if (
            not isinstance(body, dict)
            or set(body) != {"token", "expected_store_id", "remote_port"}
            or not valid_token(body.get("token"))
            or not valid_store_id(body.get("expected_store_id"))
            or type(body.get("remote_port")) is not int
            or not config.jhunt_pair_port_min <= body["remote_port"] <= config.jhunt_pair_port_max
        ):
            log_event("jhunt_ui", "jhunt_ui.ssh_pairing", user="ssh",
                      status="rejected", reason="invalid_input")
            print("Pairing failed.", file=stdout)
            return 1
        candidate = {
            "enabled": True,
            "base_url": f"http://127.0.0.1:{body['remote_port']}",
            "token": body["token"],
            "generation": uuid.uuid4().hex,
        }
        with JHuntClient(candidate) as client:
            snapshot = client.load_inbox()
            if snapshot["store_id"] != body["expected_store_id"]:
                log_event("jhunt_ui", "jhunt_ui.ssh_pairing", user="ssh",
                          status="rejected", reason="store_mismatch")
                print("Pairing failed.", file=stdout)
                return 1
            save_verified_connection(client.settings, snapshot["store_id"])
    except BridgeError as error:
        safe_error = RuntimeError("JHunt verification failed")
        log_event("jhunt_ui", "jhunt_ui.ssh_pairing", user="ssh", status="failure",
                  reason="verification_failed", exc_info=safe_error,
                  error_type=type(error).__name__)
        print("Pairing failed.", file=stdout)
        return 1
    except Exception as error:
        safe_error = RuntimeError("JHunt pairing failed")
        log_event("jhunt_ui", "jhunt_ui.ssh_pairing", user="ssh", status="failure",
                  reason="handled_failure", exc_info=safe_error,
                  error_type=type(error).__name__)
        print("Pairing failed.", file=stdout)
        return 1

    log_event("jhunt_ui", "jhunt_ui.ssh_pairing", user="ssh", status="success",
              store_id=snapshot["store_id"])
    print("Pairing complete.", file=stdout)
    return 0


if __name__ == "__main__":
    from web_app.logging_utils import configure_logging

    configure_logging(debug=False)
    raise SystemExit(main())
