import base64
import gzip
import json
import time
import threading
import flask
import flask_login
import logging

from io import BytesIO
from flask import request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from functools import wraps
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from web_app.app import app
from web_app.data_interface import DataInterface
from web_app.users import User
from web_app.errors import *


def get_all_data_interfaces() -> list[DataInterface]:
    from web_app.api.data_interface import DataInterface as APIDataInterface
    from web_app.todoist2.data_interface import DataInterface as Todoist2DataInterface
    from web_app.metrics.data_interface import DataInterface as MetricsDataInterface
    from web_app.jswipe.data_interface import DataInterface as JSwipeDataInterface
    from web_app.tubio.data_interface import DataInterface as TubioDataInterface
    from web_app.file_store.data_interface import DataInterface as FileStoreDataInterface
    from web_app.hammock.data_interface import DataInterface as HammockDataInterface

    return [
        APIDataInterface,
        Todoist2DataInterface,
        MetricsDataInterface,
        JSwipeDataInterface,
        TubioDataInterface,
        FileStoreDataInterface,
        HammockDataInterface,
    ]


def register_all_blueprints(app):
    from web_app.crosswords import crosswords_api
    from web_app.todoist2 import todoist2_api
    from web_app.tubio import tubio_api
    from web_app.metrics import metrics_api
    from web_app.account_api import account_api
    from web_app.file_store import file_store_api
    from web_app.api import api_api
    from web_app.jswipe import jswipe_api
    from web_app.proxy import proxy_api
    from web_app.hammock import hammock_api

    blueprints = [
        todoist2_api,
        crosswords_api,
        tubio_api,
        metrics_api,
        account_api,
        file_store_api,
        api_api,
        jswipe_api,
        proxy_api,
        hammock_api,
    ]

    for blueprint in blueprints:
        app.register_blueprint(blueprint)


class TimedDict:
    """Thread-safe dict with TTL for ephemeral key storage."""
    
    def __init__(self, default_ttl_seconds: int = 300):
        self._data = {}
        self._lock = threading.RLock()
        self._default_ttl = default_ttl_seconds
        self._last_cleanup = time.time()
    
    def _cleanup_expired(self):
        """Remove expired entries."""
        now = time.time()
        # Run cleanup at most once every 60 seconds
        if now - self._last_cleanup < 60:
            return
        
        expired_keys = [
            k for k, v in self._data.items()
            if v['expires'] < now
        ]
        for k in expired_keys:
            del self._data[k]
        self._last_cleanup = now
    
    def set(self, key: str, value, ttl: int = None):
        """Store value with TTL."""
        with self._lock:
            self._cleanup_expired()
            self._data[key] = {
                'value': value,
                'expires': time.time() + (ttl or self._default_ttl)
            }
    
    def get(self, key: str):
        """Get value if not expired, else return None and delete."""
        with self._lock:
            self._cleanup_expired()
            entry = self._data.get(key)
            if not entry:
                return None
            if entry['expires'] < time.time():
                del self._data[key]
                return None
            return entry['value']
    
    def delete(self, key: str):
        """Delete a key."""
        with self._lock:
            if key in self._data:
                del self._data[key]


# Ephemeral RSA key storage (session_id -> private_key)
_ephemeral_keys = TimedDict(default_ttl_seconds=300)


login_manager = flask_login.LoginManager()
login_manager.init_app(app)
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
    app=app,
    default_limits=["3 per second"],
    storage_uri="memory://",
    strategy="fixed-window", # or "moving-window"
)

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
    users = DataInterface().load_users()
    user = users.get(username)
    if not user or user.password != password:
        return False

    if require_admin and not user.is_admin:
        return False
    
    return True

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
    
    # Store private key in memory (5 minute TTL)
    _ephemeral_keys.set(session_id, private_key, ttl=300)
    
    logging.debug(f"Generated ephemeral keypair, session_id: {session_id}")
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
    private_key = _ephemeral_keys.get(session_id)
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
        _ephemeral_keys.delete(session_id)
        
        return json.loads(json_data.decode('utf-8'))
        
    except Exception as e:
        logging.warning(f"Decryption failed for session {session_id}: {e}")
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
    
    if require_login:
        username = request_body.get("username", "")
        password = request_body.get("password", "")
        if not authenticate_user(username, password, require_admin=require_admin):
            raise AuthenticationError("Invalid credentials")
    
    return request_body