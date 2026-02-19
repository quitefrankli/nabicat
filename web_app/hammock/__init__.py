from flask import Blueprint, render_template, send_file, abort, request, redirect
from web_app.hammock.data_interface import DataInterface

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

@hammock_api.route('/<project>/<post>/<path:filename>')
def post_asset(project: str, post: str, filename: str):
    data_interface = DataInterface()
    asset_path = data_interface.get_asset_path(project, post, filename)
    if not asset_path or not asset_path.exists():
        abort(404)
    return send_file(asset_path)