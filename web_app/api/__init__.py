import base64
import gzip
import subprocess
import logging
from functools import wraps

from flask import request, jsonify, Blueprint

from web_app.data_interface import DataInterface
from web_app.api.data_interface import DataInterface as APIDataInterface
from web_app.todoist2.data_interface import DataInterface as Todoist2DataInterface
from web_app.metrics.data_interface import DataInterface as MetricsDataInterface
from web_app.tubio.data_interface import DataInterface as TubioDataInterface
from web_app.helpers import get_ip, parse_request, authenticate_user, generate_ephemeral_keypair
from web_app.config import ConfigManager
from web_app.errors import APIError


GITHUB_EVENT_HEADER = "X-GitHub-Event"
api_api = Blueprint("api_api", __name__, url_prefix="/api")


def _handle_api_error(func):
    """Decorator to handle APIError exceptions consistently."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except APIError as e:
            logging.exception("Error processing request")
            return jsonify({"error": str(e)}), 400
    return wrapper


def _get_required_field(request_body: dict, field: str) -> str:
    """Get a required field from request body or raise APIError."""
    try:
        return request_body[field]
    except KeyError:
        logging.exception("Request rejected: missing required field(s)")
        raise APIError(f"Missing required field: {field}")

def update_server():
    logging.info(f"Updating server...")
    subprocess.Popen("bash update_server.sh &>> logs/shell_logs.log", shell=True, close_fds=True)

def handle_github_webhook():
    # for the webhook, login creds are supplied in the authorization header
    request_body = parse_request(require_login=False, require_admin=False)

    if request.headers.get(GITHUB_EVENT_HEADER) != "push":
        logging.info(f"Ignoring GitHub webhook event: {request.headers.get(GITHUB_EVENT_HEADER)}")
        return jsonify({"status": "ignored"}), 200
        
    ref = request_body.get("ref")
    if ref != "refs/heads/main":
        logging.info(f"Ignoring push event for non-main branch: {ref}")
        return jsonify({"status": "ignored"}), 200

    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Basic "):
        return jsonify({"error": "Missing or invalid Authorization header"}), 401

    encoded_credentials = auth_header.split(" ")[1]
    decoded_bytes = base64.b64decode(encoded_credentials)
    decoded_credentials = decoded_bytes.decode("utf-8")

    try:
        username, password = decoded_credentials.split(":", 1)
    except ValueError:
        logging.error("Error parsing credentials from Authorization header")
        return jsonify({"error": "Invalid credentials format"}), 400
    
    if not authenticate_user(username, password):
        logging.error("Invalid credentials for GitHub webhook, update rejected")
        return jsonify({"error": "Invalid credentials"}), 401
    
    logging.info(f"Applying GitHub push webhook update")
    update_server()

    return jsonify({
        "success": True, 
    }), 200

@api_api.route("/update", methods=["POST"])
@_handle_api_error
def api_update():
    logging.info(f"Received update request from {get_ip()}")
    
    if GITHUB_EVENT_HEADER in request.headers:
        return handle_github_webhook()
    
    request_body = parse_request()
    
    # check if the request contains username and password in body
    # or if the username and password are provided in the Authorization header
    patch = request_body.get("patch", None)
    if not patch:
        update_server()
        return jsonify({"success": True}), 200
    
    patch: str
    size_kb = len(patch) / 1e3
    logging.info(f"Updating with patch of size {size_kb:.2f} kB")

    # in order to prevent any issues with piping to bash, we will convert it to base64
    encoded_patch = base64.b64encode(patch.encode('utf-8')).decode('utf-8')
    subprocess.Popen(f"bash update_server.sh -p \"{encoded_patch}\" &>> logs/shell_logs.log", 
                     shell=True, 
                     close_fds=True)
    
    return jsonify({
        "success": True, 
        "patch_size": f"{size_kb:.2f} kB",
    }), 200

@api_api.route("/backup", methods=["POST"])
@_handle_api_error
def api_backup():
    logging.info(f"Received backup request from {get_ip()}")

    parse_request()

    # TODO: zip the backup and upload to s3
    # self.data_syncer.upload_file(new_backup)
    backup_dir = DataInterface().generate_backup_dir()
    DataInterface().backup_data(backup_dir)
    Todoist2DataInterface().backup_data(backup_dir)
    MetricsDataInterface().backup_data(backup_dir)
    TubioDataInterface().backup_data(backup_dir)
    APIDataInterface().backup_data(backup_dir)

    logging.info("Backup complete")

    return jsonify({"success": True, "message": "Backup complete"})

@api_api.route("/push", methods=["POST"])
@_handle_api_error
def api_push():
    logging.info(f"Received push request from {get_ip()}")

    request_body = parse_request(require_login=True, require_admin=False)
    name = _get_required_field(request_body, "name")
    data = _get_required_field(request_body, "data")

    # Decode base64 and decompress gzip to store plain data
    try:
        decoded_data = base64.b64decode(data)
        plain_data = gzip.decompress(decoded_data)
    except Exception as e:
        logging.warning(f"Failed to decode/decompress data, storing as-is: {e}")
        plain_data = data.encode('utf-8')

    username = request_body["username"]
    user = DataInterface().load_users()[username]
    APIDataInterface().write_data(name, plain_data, user)

    return jsonify({"success": True, "message": "Data pushed successfully"}), 200

@api_api.route("/pull", methods=["POST"])
@_handle_api_error
def api_pull():
    logging.info(f"Received pull request from {get_ip()}")

    request_body = parse_request(require_login=True, require_admin=False)
    name = _get_required_field(request_body, "name")

    username = request_body["username"]
    user = DataInterface().load_users()[username]

    try:
        plain_data = APIDataInterface().read_data(name, user)
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404

    # Compress and encode for client compatibility
    compressed_data = gzip.compress(plain_data)
    encoded_data = base64.b64encode(compressed_data).decode('utf-8')

    if "raw" in request_body:
        return plain_data.decode('utf-8'), 200, {'Content-Type': 'text/plain'}
    
    return jsonify({"success": True, "data": encoded_data}), 200

@api_api.route("/delete", methods=["POST"])
@_handle_api_error
def api_delete():
    logging.info(f"Received delete request from {get_ip()}")

    request_body = parse_request(require_login=True, require_admin=False)
    name = _get_required_field(request_body, "name")

    username = request_body["username"]
    user = DataInterface().load_users()[username]

    try:
        APIDataInterface().delete_data(name, user)
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404

    return jsonify({"success": True, "message": "Data deleted successfully"}), 200

@api_api.route("/list", methods=["POST"])
@_handle_api_error
def api_list():
    logging.info(f"Received list request from {get_ip()}")

    request_body = parse_request(require_login=True, require_admin=False)
    username = request_body["username"]
    user = DataInterface().load_users()[username]
    files = APIDataInterface().list_files(user)

    return jsonify({"success": True, "files": files}), 200

@api_api.route("/push_cookie", methods=["POST"])
@_handle_api_error
def api_upload_cookie():
    logging.info(f"Received cookie upload request from {get_ip()}")

    request_body = parse_request(require_login=True, require_admin=True)
    cookie: str = request_body.get("cookie")
    if not cookie:
        return jsonify({"error": "Missing cookie data"}), 400

    APIDataInterface().atomic_write(ConfigManager().tubio_cookie_path, 
                                    data=cookie.encode('utf-8'), 
                                    mode="wb")

    return jsonify({"success": True, "message": "Cookies uploaded successfully"}), 200


@api_api.route("/handshake", methods=["POST"])
def api_handshake():
    """
    Initiate ephemeral hybrid encryption handshake.
    
    Returns:
        - session_id: Unique identifier for this encryption session
        - public_key: RSA public key (PEM format) for client to encrypt the AES key
        - expires_in: Seconds until this session expires (default: 300)
    
    Client flow:
        1. Call /handshake to get public_key and session_id
        2. Generate a random 256-bit AES key
        3. Encrypt data: gzip → AES-GCM → base64
        4. Encrypt AES key: RSA-OAEP → base64
        5. Send to API endpoints with req={session_id, encrypted_key, encrypted_data, nonce}
    """
    logging.info(f"Received handshake request from {get_ip()}")
    
    session_id, public_key = generate_ephemeral_keypair()
    
    return jsonify({
        "success": True,
        "session_id": session_id,
        "public_key": public_key,
        "expires_in": 300,
        "algorithm": "RSA-2048-OAEP-SHA256/AES-256-GCM"
    }), 200