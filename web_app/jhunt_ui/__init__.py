from flask import Blueprint, render_template

from web_app.blueprint_registration import register_app_name, require_admin_blueprint


jhunt_ui_api = Blueprint(
    "jhunt_ui",
    __name__,
    template_folder="templates",
    static_folder="static",
    url_prefix="/jhunt-ui",
)

require_admin_blueprint(jhunt_ui_api)
register_app_name(jhunt_ui_api, "JHunt")


@jhunt_ui_api.get("/")
def index():
    return render_template("jhunt_index.html")
