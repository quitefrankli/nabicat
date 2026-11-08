import base64
import gzip
import json
import logging
from functools import wraps
from io import BytesIO

import flask
import flask_login
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from flask import request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from web_app.config import ConfigManager
from web_app.data_interface import DataInterface
from web_app.errors import APIError, AuthenticationError
from web_app.logging_utils import log_event
from web_app.redis_client import get_redis
from web_app.users import User


_EPHEMERAL_KEY_PREFIX = "nabicat:ephkey:"


def _store_ephemeral_key(session_id: str, private_key) -> None:
    """Persist an ephemeral RSA private key in Redis with a TTL.

    Redis-backed (not in-process) so the handshake and the follow-up encrypted
    request can be served by different gunicorn workers.
    """
    der = private_key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    get_redis().set(
        _EPHEMERAL_KEY_PREFIX + session_id,
        der,
        ex=ConfigManager().ephemeral_key_ttl_s,
    )


def _load_ephemeral_key(session_id: str):
    """Return the stored RSA private key for session_id, or None if absent/expired."""
    der = get_redis().get(_EPHEMERAL_KEY_PREFIX + session_id)
    if not der:
        return None
    return serialization.load_der_private_key(der, password=None)


def _delete_ephemeral_key(session_id: str) -> None:
    get_redis().delete(_EPHEMERAL_KEY_PREFIX + session_id)


login_manager = flask_login.LoginManager()
@login_manager.user_loader
def user_loader(username: str) -> User | None:
    users = DataInterface().load_users()
    return users.get(username, None)

@login_manager.request_loader
def request_loader(request: flask.Request) -> User | None:
    username = request.form.get('username')
    if not username:
        return None
    existing_users = DataInterface().load_users()
    return existing_users.get(username, None)

@login_manager.unauthorized_handler
def unauthorized_handler():
    flask.flash('Log in required', category='error')
    return flask.redirect(flask.url_for('account_api.login', 
                                        next=flask.request.path))

limiter = Limiter(
    get_remote_address,
    default_limits=["3 per second"],
    strategy="fixed-window",
)


def init_auth(app) -> None:
    """Attach authentication and rate limiting extensions to a Flask app."""
    login_manager.init_app(app)
    app.config.setdefault("RATELIMIT_STORAGE_URI", ConfigManager().redis_url)
    limiter.init_app(app)

def from_req(key: str) -> str:
    val = request.form[key] if key in request.form else request.args[key]
    return val.encode('ascii', 'ignore').decode('ascii')

def admin_only(failure_redirect: str):
    def _admin_only(func):
        @wraps(func)
        def decorated_view(*args, **kwargs):
            if not flask_login.current_user.is_admin:
                flask.flash('You must be an admin to access this page', category='error')
                return flask.redirect(flask.url_for(failure_redirect))

            return func(*args, **kwargs)

        return decorated_view
    return _admin_only



def get_ip() -> str:
    if request.headers.getlist("X-Forwarded-For"):
        return request.headers.getlist("X-Forwarded-For")[0]
    else:
        return request.remote_addr if request.remote_addr else "Unknown IP"
    
def cur_user() -> User:
    if not isinstance(flask_login.current_user, User):
        raise TypeError("Current user is not an instance of User")
    return flask_login.current_user

def authenticate_user(username: str, password: str, require_admin: bool = True) -> bool:
    if not username or not password:
        return False

    user = DataInterface().load_users_local().get(username)
    return user is not None and user.verify_password(password) and (
        not require_admin or user.is_admin
    )

def generate_ephemeral_keypair() -> tuple[str, str]:
    """
    Generate an ephemeral RSA key pair for hybrid encryption.
    
    Returns:
        tuple: (session_id, public_key_pem)
        The private key is stored in memory with TTL.
    """
    import uuid
    
    # Generate 2048-bit RSA key pair
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    
    # Generate unique session ID
    session_id = str(uuid.uuid4())
    
    # Serialize public key to PEM
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode('utf-8')
    
    # Store private key in Redis (TTL from config)
    _store_ephemeral_key(session_id, private_key)
    
    log_event(
        "api", "api.ephemeral_key_generated",
        level=logging.DEBUG, session_id=session_id,
    )
    return session_id, public_pem


def decode_decrypt_decompress(encrypted_payload: dict) -> dict:
    """
    Decrypt hybrid-encrypted payload.
    
    Expected payload format:
    {
        "session_id": "<uuid>",
        "encrypted_key": "<base64>",  # AES key encrypted with RSA public key
        "encrypted_data": "<base64>", # gzip-compressed JSON encrypted with AES-GCM
        "nonce": "<base64>"           # AES-GCM nonce
    }
    """
    try:
        session_id = encrypted_payload['session_id']
        encrypted_key_b64 = encrypted_payload['encrypted_key']
        encrypted_data_b64 = encrypted_payload['encrypted_data']
        nonce_b64 = encrypted_payload['nonce']
    except KeyError as e:
        raise APIError(f"Missing required field in encrypted payload: {e}")
    
    # Retrieve ephemeral private key
    private_key = _load_ephemeral_key(session_id)
    if not private_key:
        raise AuthenticationError("Invalid or expired session ID")
    
    try:
        # Decode base64
        encrypted_key = base64.b64decode(encrypted_key_b64)
        encrypted_data = base64.b64decode(encrypted_data_b64)
        nonce = base64.b64decode(nonce_b64)
        
        # Decrypt AES key using RSA-OAEP
        aes_key = private_key.decrypt(
            encrypted_key,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None
            )
        )
        
        # Decrypt data using AES-GCM
        aesgcm = AESGCM(aes_key)
        compressed_data = aesgcm.decrypt(nonce, encrypted_data, None)
        
        # Decompress and parse JSON
        with gzip.GzipFile(fileobj=BytesIO(compressed_data)) as gz:
            json_data = gz.read()
        
        # Clean up: delete the ephemeral key after successful decryption
        _delete_ephemeral_key(session_id)
        
        return json.loads(json_data.decode('utf-8'))
        
    except Exception as e:
        log_event(
            "api", "api.decryption_failed",
            level=logging.WARNING, session_id=session_id, exc_info=e,
            error_type=type(e).__name__,
        )
        raise APIError(f"Failed to decrypt request: {str(e)}")

def parse_request(require_login: bool = True, require_admin: bool = True) -> dict:
    if require_admin:
        require_login = True
    
    content_type = request.headers.get('Content-Type', '')
    if content_type.startswith('application/json'):
        request_body = request.get_json(silent=True)
        if request_body is None:
            raise APIError("Invalid JSON request_body")
    elif content_type.startswith('application/x-www-form-urlencoded'):
        request_body = request.form.to_dict()
    elif content_type.startswith('multipart/form-data'):
        request_body = {key: value for key, value in request.form.items()}
    else:
        raise APIError("Unsupported content type")
    
    if not isinstance(request_body, dict):
        raise APIError("Invalid request_body")

    # Check if this is a hybrid-encrypted request (new protocol)
    encrypted_payload = request_body.get("req")
    if encrypted_payload:
        if isinstance(encrypted_payload, dict):
            # New hybrid encryption format
            try:
                request_body = decode_decrypt_decompress(encrypted_payload)
            except Exception as e:
                raise APIError(f"Failed to decrypt request: {str(e)}")
        elif isinstance(encrypted_payload, str):
            # Legacy format - deprecated, will be removed
            raise APIError("Legacy encryption format is no longer supported. Please use hybrid encryption.")
    
    if not isinstance(request_body, dict):
        raise APIError("Invalid request_body")

    if require_login:
        authorization = request.headers.get("Authorization")
        if authorization:
            from web_app.api_auth import authenticate_api_token

            user = authenticate_api_token(authorization, require_admin=require_admin)
            return request_body | {"username": user.id}
        username = request_body.get("username", "")
        password = request_body.get("password", "")
        if not isinstance(username, str) or not isinstance(password, str):
            raise AuthenticationError("Invalid credentials")
        if not authenticate_user(username, password, require_admin=require_admin):
            raise AuthenticationError("Invalid credentials")

    return request_body

