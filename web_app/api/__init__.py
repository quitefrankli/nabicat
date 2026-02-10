import base64
import subprocess
import logging

from flask import request, jsonify, Blueprint

from web_app.data_interface import DataInterface
from web_app.api.data_interface import DataInterface as APIDataInterface
from web_app.todoist2.data_interface import DataInterface as Todoist2DataInterface
from web_app.metrics.data_interface import DataInterface as MetricsDataInterface
from web_app.tubio.data_interface import DataInterface as TubioDataInterface
from web_app.helpers import get_ip, parse_request, authenticate_user
from web_app.config import ConfigManager
from web_app.errors import *


GITHUB_EVENT_HEADER = "X-GitHub-Event"
api_api = Blueprint("api_api", __name__, url_prefix="/api")

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
def api_update():
    logging.info(f"Received update request from {get_ip()}")
    
    if GITHUB_EVENT_HEADER in request.headers:
        return handle_github_webhook()
    
    try:
        request_body = parse_request()
    except APIError as e:
        logging.exception("Error processing request")
        return jsonify({"error": str(e)}), 400

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
def api_backup():
    logging.info(f"Received backup request from {get_ip()}")

    try:
        parse_request()
    except APIError as e:
        logging.exception("Error processing request")
        return jsonify({"error": str(e)}), 400

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
def api_push():
    logging.info(f"Received push request from {get_ip()}")

    try:
        request_body = parse_request(require_login=True, require_admin=False)
    except APIError as e:
        logging.exception("Error processing request")
        return jsonify({"error": str(e)}), 400

    try:
        name: str = request_body["name"]
        data: str = request_body["data"]
    except KeyError as e:
        logging.exception("push request rejected missing required field(s)")
        return jsonify({"error": f"Missing required field: {str(e)}"}), 400

    username = request_body["username"]
    user = DataInterface().load_users()[username]
    APIDataInterface().write_data(name, data.encode('utf-8'), user)

    return jsonify({"success": True, "message": "Data pushed successfully"}), 200

@api_api.route("/pull", methods=["POST"])
def api_pull():
    logging.info(f"Received pull request from {get_ip()}")

    try:
        request_body = parse_request(require_login=True, require_admin=False)
    except APIError as e:
        logging.exception("Error processing request")
        return jsonify({"error": str(e)}), 400

    try:
        name: str = request_body["name"]
    except KeyError as e:
        logging.exception("pull request rejected missing required field(s)")
        return jsonify({"error": f"Missing required field: {str(e)}"}), 400

    username = request_body["username"]
    user = DataInterface().load_users()[username]

    try:
        data = APIDataInterface().read_data(name, user)
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404

    if "raw" in request_body:
        return data.decode('utf-8'), 200, {'Content-Type': 'text/plain'}
    
    return jsonify({"success": True, "data": data.decode('utf-8')}), 200

@api_api.route("/delete", methods=["POST"])
def api_delete():
    logging.info(f"Received delete request from {get_ip()}")

    try:
        request_body = parse_request(require_login=True, require_admin=False)
    except APIError as e:
        logging.exception("Error processing request")
        return jsonify({"error": str(e)}), 400

    try:
        name: str = request_body["name"]
    except KeyError as e:
        logging.exception("delete request rejected missing required field(s)")
        return jsonify({"error": f"Missing required field: {str(e)}"}), 400

    username = request_body["username"]
    user = DataInterface().load_users()[username]

    try:
        APIDataInterface().delete_data(name, user)
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404

    return jsonify({"success": True, "message": "Data deleted successfully"}), 200

@api_api.route("/list", methods=["POST"])
def api_list():
    logging.info(f"Received list request from {get_ip()}")

    try:
        request_body = parse_request(require_login=True, require_admin=False)
    except APIError as e:
        logging.exception("Error processing request")
        return jsonify({"error": str(e)}), 400

    username = request_body["username"]
    user = DataInterface().load_users()[username]
    files = APIDataInterface().list_files(user)

    return jsonify({"success": True, "files": files}), 200

@api_api.route("/push_cookie", methods=["POST"])
def api_upload_cookie():
    logging.info(f"Received cookie upload request from {get_ip()}")

    try:
        request_body = parse_request(require_login=True, require_admin=True)
    except APIError as e:
        logging.exception("Error processing request")
        return jsonify({"error": str(e)}), 400

    cookie: str = request_body.get("cookie")
    if not cookie:
        return jsonify({"error": "Missing cookie data"}), 400

    APIDataInterface().atomic_write(ConfigManager().tubio_cookie_path, 
                                    data=cookie.encode('utf-8'), 
                                    mode="wb")

    return jsonify({"success": True, "message": "Cookies uploaded successfully"}), 200