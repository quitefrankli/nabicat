from flask import Blueprint, render_template

from web_app.config import ConfigManager
from web_app.helpers import register_app_name


qread_api = Blueprint(
    "qread", __name__, url_prefix="/qread",
    template_folder="templates", static_folder="static",
)
register_app_name(qread_api, "QRead")


@qread_api.get("/")
def index():
    config = ConfigManager()
    return render_template(
        "qread.html",
        max_image_bytes=config.qread_max_image_bytes,
        scan_max_dimension=config.qread_scan_max_dimension,
        scan_interval_ms=config.qread_scan_interval_ms,
        qr_quiet_modules=config.qread_qr_quiet_modules,
        qr_module_pixels=config.qread_qr_module_pixels,
    )
