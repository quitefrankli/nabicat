import base64
import gzip
import logging
import zipfile
from io import BytesIO

from flask import Blueprint, render_template, send_file, abort, request, redirect, jsonify
from web_app.hammock.data_interface import DataInterface
from web_app.helpers import limiter, parse_request, get_ip
from web_app.errors import APIError
from web_app.app import csrf

hammock_api = Blueprint(
    'hammock',
    __name__,
    template_folder='templates',
    static_folder='static',
    url_prefix='/hammock'
)

@hammock_api.context_processor
def inject_app_name():
    return dict(app_name='Hammock')

@hammock_api.route('/')
def index():
    posts_by_project = DataInterface().get_posts_by_project()

    return render_template("hammock_index.html", 
                           posts_by_project=posts_by_project)

@hammock_api.route('/<project>/<post>/')
def view_post(project: str, post: str):
    data_interface = DataInterface()
    posts_by_project = data_interface.get_posts_by_project()
    post_content = data_interface.get_post_content(project, post)
    return render_template("hammock_post.html",
                           project_name=project,
                           post_name=post,
                           posts_by_project=posts_by_project,
                           post_content=post_content)

@hammock_api.route('/api/upload_post', methods=['POST'])
@csrf.exempt
def upload_post():
    try:
        request_body = parse_request(require_login=True, require_admin=True)
    except APIError as e:
        return jsonify({"error": str(e)}), 400

    project = request_body.get("project")
    post_name = request_body.get("post_name")
    data = request_body.get("data")

    if not project or not post_name or not data:
        return jsonify({"error": "Missing required fields: project, post_name, data"}), 400

    try:
        decoded_data = base64.b64decode(data)
        plain_data = gzip.decompress(decoded_data)
    except Exception as e:
        return jsonify({"error": f"Failed to decode data: {e}"}), 400

    data_interface = DataInterface()
    post_dir = data_interface.projects_dir / project / post_name
    post_dir.mkdir(parents=True, exist_ok=True)

    try:
        with zipfile.ZipFile(BytesIO(plain_data), 'r') as zf:
            for member in zf.namelist():
                target = (post_dir / member).resolve()
                if not target.is_relative_to(post_dir.resolve()):
                    return jsonify({"error": "Zip contains path traversal"}), 400
            zf.extractall(post_dir)
    except zipfile.BadZipFile:
        return jsonify({"error": "Invalid zip data"}), 400

    logging.info(f"Post uploaded: {project}/{post_name} from {get_ip()}")
    return jsonify({"success": True, "message": f"Post {project}/{post_name} uploaded"}), 200

@hammock_api.route('/<project>/<post>/<path:filename>')
@limiter.limit("20/second")
def post_asset(project: str, post: str, filename: str):
    data_interface = DataInterface()
    asset_path = data_interface.get_asset_path(project, post, filename)
    if not asset_path or not asset_path.exists():
        abort(404)
    return send_file(asset_path)