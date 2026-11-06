import base64
import json
import subprocess
import tempfile
from pathlib import Path

import requests as http_requests

from web_app.config import ConfigManager


def _build_message_content(user_message: str, image_paths: list | None):
    """Build an Anthropic message content value from text + optional images.

    Returns the plain string when no images are supplied; otherwise a list of
    content blocks (text first, then a base64 image block per existing file).
    Missing files are silently skipped.
    """
    if not image_paths:
        return user_message
    content: list = [{"type": "text", "text": user_message}]
    for path in image_paths:
        p = Path(path)
        if not p.exists():
            continue
        ext = p.suffix.lower()
        media_type = "image/jpeg" if ext in (".jpg", ".jpeg") else "image/png"
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": base64.b64encode(p.read_bytes()).decode("ascii"),
            },
        })
    return content


class MeridianError(RuntimeError):
    """Raised when the Meridian proxy is unreachable or returns an error."""


class CodexCLIError(RuntimeError):
    """Raised when the local Codex CLI is unavailable or returns an error."""

    def __init__(self, message: str, *, reason: str = "execution_failed"):
        super().__init__(message)
        self.reason = reason


class BedrockError(RuntimeError):
    """Raised when the AWS Bedrock Anthropic call fails."""


_bedrock_clients: dict = {}


def _get_bedrock_client(read_timeout_s: float):
    """Return a cached boto3 bedrock-runtime client with bounded socket timeouts.

    We use boto3 directly rather than the anthropic SDK's AnthropicBedrock
    because the SDK double-URL-encodes inference-profile ARNs in the request
    path, which breaks SigV4 signing. boto3 handles ARN model IDs correctly.

    Region is read from AWS_REGION (or AWS_DEFAULT_REGION); we pass it
    explicitly because profile-level ``region`` in ~/.aws/config takes
    precedence over the AWS_REGION env var otherwise — which would point
    the client at the wrong region for inference-profile ARNs.

    Clients are cached per read-timeout: botocore sets the socket read timeout
    on the client (not per call), so callers with different timeouts get
    distinct clients. Without a read_timeout a stalled connection can hang a
    request thread indefinitely.
    """
    key = round(float(read_timeout_s), 3)
    client = _bedrock_clients.get(key)
    if client is None:
        import os
        import boto3
        from botocore.config import Config

        cfg = ConfigManager().llm
        region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
        boto_cfg = Config(
            connect_timeout=cfg.bedrock_connect_timeout_s,
            read_timeout=read_timeout_s,
            retries={"max_attempts": cfg.bedrock_max_attempts, "mode": "standard"},
        )
        client = boto3.client("bedrock-runtime", region_name=region, config=boto_cfg)
        _bedrock_clients[key] = client
    return client


def bedrock_text(
    user_message: str,
    system: str,
    model: str,
    max_tokens: int = 1024,
    timeout_s: float = 120.0,
    image_paths: list | None = None,
) -> str:
    """Invoke AWS Bedrock via boto3 with an Anthropic Messages payload.

    Uses the standard AWS credential chain (AWS_REGION + AWS_PROFILE / env
    keys / instance role). ``model`` is a Bedrock foundation-model ID or an
    inference-profile ARN.
    """
    message_content = _build_message_content(user_message, image_paths)

    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": message_content}],
    })

    try:
        resp = _get_bedrock_client(timeout_s).invoke_model(modelId=model, body=body)
    except Exception as e:
        msg = str(e)
        if (
            e.__class__.__name__ in {"ReadTimeoutError", "ConnectTimeoutError", "ConnectionError"}
            or "Read timeout" in msg
            or "Connect timeout" in msg
        ):
            raise BedrockError(f"bedrock call timed out after {timeout_s}s") from e
        if "Unable to locate credentials" in msg or "could not resolve credentials" in msg:
            raise BedrockError(
                "AWS credentials not found. Set AWS_PROFILE in .env (and run aws sso login if needed)."
            ) from e
        if "ExpiredToken" in msg or ("security token" in msg.lower() and "expired" in msg.lower()):
            raise BedrockError("AWS session expired. Refresh your SSO credentials.") from e
        if "AccessDeniedException" in msg or "is not authorized to perform" in msg:
            raise BedrockError(f"Bedrock access denied for model {model!r}: {msg[:300]}") from e
        raise BedrockError(f"bedrock call failed: {e}") from e

    payload = json.loads(resp["body"].read())
    return "".join(
        b.get("text", "") for b in payload.get("content", []) if b.get("type") == "text"
    ).strip()


def call_meridian(
    messages: list,
    system: str,
    model: str | None = None,
    max_tokens: int = 1024,
    tools: list | None = None,
    timeout_s: float = 120.0,
    agent: str = "nabicat",
) -> dict:
    """Send a single /v1/messages request to the local Meridian proxy.

    Returns the parsed JSON response on success. Raises ``MeridianError``
    on network failure or non-2xx response. Does not retry.
    """
    config = ConfigManager()
    headers = {"Content-Type": "application/json", "x-meridian-agent": agent}
    body = {
        "model": model or config.llm.meridian_models.get("medium", ""),
        "max_tokens": max_tokens,
        "system": system,
        "messages": messages,
    }
    if tools is not None:
        body["tools"] = tools

    try:
        resp = http_requests.post(config.llm.meridian_url, headers=headers, json=body, timeout=timeout_s)
    except http_requests.RequestException as e:
        raise MeridianError(f"meridian unreachable: {e}") from e

    if not resp.ok:
        raise MeridianError(f"meridian {resp.status_code}: {resp.text[:500]}")

    try:
        return resp.json()
    except ValueError as e:
        raise MeridianError(f"meridian returned non-JSON: {e}") from e


def meridian_text(
    user_message: str,
    system: str,
    model: str | None = None,
    max_tokens: int = 1024,
    timeout_s: float = 120.0,
    agent: str = "nabicat",
    image_paths: list | None = None,
) -> str:
    """Convenience wrapper around ``call_meridian`` for text/multimodal prompts.

    When ``image_paths`` is provided, each existing PNG/JPEG file is base64-
    encoded and attached as an Anthropic image content block alongside the
    user text. Concatenates all ``text`` blocks from the response and returns
    the stripped result. Raises ``MeridianError`` on transport failures.
    """
    message_content = _build_message_content(user_message, image_paths)

    result = call_meridian(
        messages=[{"role": "user", "content": message_content}],
        system=system,
        model=model,
        max_tokens=max_tokens,
        timeout_s=timeout_s,
        agent=agent,
    )
    blocks = result.get("content", []) or []
    return "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()


def codex_cli_text(
    user_message: str,
    instructions: str = "",
    model: str | None = None,
    timeout_s: float = 120.0,
    image_paths: list[Path] | None = None,
    reasoning_effort: str | None = None,
    permissions_profile: str | None = None,
    working_directory: Path | None = None,
    standalone: bool = False,
) -> str:
    """Run Codex CLI non-interactively and return its final message.

    This uses the local Codex installation and its existing ChatGPT/Codex
    authentication, not an OpenAI API key.
    """
    config = ConfigManager()
    prompt = f"{instructions}\n\nUser request:\n{user_message}" if instructions else user_message
    with tempfile.NamedTemporaryFile("r+", encoding="utf-8") as output:
        cmd = [
            config.llm.codex_cli_command,
            "-a",
            config.api_llm_approval_policy if standalone else config.llm.codex_cli_approval_policy,
            "exec",
            "--ephemeral",
            "--skip-git-repo-check",
        ]
        if standalone:
            cmd.extend(["--ignore-rules", "-c", "project_doc_max_bytes=0",
                        "-c", "project_doc_fallback_filenames=[]"])
        if permissions_profile:
            cmd.extend(
                [
                    "--ignore-rules",
                    "-c",
                    "project_doc_max_bytes=0",
                    "-c",
                    "project_doc_fallback_filenames=[]",
                    "-c",
                    f"default_permissions={json.dumps(permissions_profile)}",
                    "-c",
                    f"permissions.{permissions_profile}.filesystem.:minimal=\"read\"",
                ]
            )
        else:
            cmd.extend(["--sandbox", config.api_llm_sandbox if standalone else config.llm.codex_cli_sandbox])
        if reasoning_effort:
            cmd.extend(
                ["-c", f"model_reasoning_effort={json.dumps(reasoning_effort)}"]
            )
        if model:
            cmd.extend(["--model", model])
        for image_path in image_paths or []:
            if image_path.is_file():
                cmd.extend(["--image", str(image_path)])
        cmd.extend(["--output-last-message", output.name])
        cmd.append("-")
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(working_directory if working_directory is not None else config.project_dir),
                input=prompt,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
            )
        except FileNotFoundError:
            raise CodexCLIError("Codex CLI is unavailable", reason="unavailable") from None
        except subprocess.TimeoutExpired:
            raise CodexCLIError("Codex CLI timed out", reason="timeout") from None

        if proc.returncode != 0:
            raise CodexCLIError("Codex CLI execution failed")

        output.seek(0)
        text = output.read().strip()
        if not text:
            raise CodexCLIError("Codex CLI returned an empty response", reason="empty_response")
        return text
