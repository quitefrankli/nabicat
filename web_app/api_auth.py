"""Issue and validate API access tokens independently of browser sessions."""

import hmac
import logging
from datetime import datetime, timezone

import jwt
from flask import current_app, g

from web_app.config import ConfigManager
from web_app.data_interface import DataInterface
from web_app.errors import AuthenticationError
from web_app.logging_utils import log_event
from web_app.users import User


def _signing_key() -> bytes:
    secret = current_app.secret_key
    if not secret:
        raise RuntimeError("API token signing requires the application secret key")
    if isinstance(secret, str):
        secret = secret.encode()
    return hmac.digest(secret, ConfigManager().api_jwt_key_context, "sha256")


def issue_api_token(username: str) -> str:
    config = ConfigManager()
    issued_at = int(datetime.now(timezone.utc).timestamp())
    return jwt.encode({
        "sub": username,
        "iat": issued_at,
        "exp": issued_at + config.api_jwt_lifetime_s,
        "iss": config.api_jwt_issuer,
        "aud": config.api_jwt_audience,
    }, _signing_key(), algorithm=config.api_jwt_algorithm)


def authenticate_api_token(authorization: str, *, require_admin: bool) -> User:
    config = ConfigManager()
    scheme, _, token = authorization.partition(" ")
    try:
        if scheme.lower() != "bearer" or not token:
            raise jwt.InvalidTokenError("Missing bearer token")
        claims = jwt.decode(
            token, _signing_key(), algorithms=[config.api_jwt_algorithm],
            issuer=config.api_jwt_issuer, audience=config.api_jwt_audience,
            options={"require": ["sub", "iat", "exp", "iss", "aud"]},
        )
    except jwt.InvalidTokenError as error:
        log_event("api", "api.token_rejected", level=logging.WARNING,
                  reason="invalid_or_expired_token", error_type=type(error).__name__,
                  exc_info=True)
        raise AuthenticationError("Invalid or expired token. Please log in again.", status=401) from None
    user = DataInterface().load_users().get(claims["sub"])
    if user is None:
        raise AuthenticationError("Invalid or expired token. Please log in again.", status=401)
    g.request_user = user.id
    if require_admin and not user.is_admin:
        raise AuthenticationError("Admin access required", status=403)
    return user
