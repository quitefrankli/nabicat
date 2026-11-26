from flask import Blueprint, render_template

from web_app.config import ConfigManager
from web_app.blueprint_registration import register_app_name


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
        stream_qr_version=config.qread_stream_qr_version,
        stream_qr_level=config.qread_stream_qr_level,
        stream_chunk_bytes=config.qread_stream_chunk_bytes,
        stream_frame_ms=config.qread_stream_frame_ms,
        stream_scan_interval_ms=config.qread_stream_scan_interval_ms,
        stream_scan_max_dimension=config.qread_stream_scan_max_dimension,
        stream_max_file_bytes=config.qread_stream_max_file_bytes,
        stream_soliton_c=config.qread_stream_soliton_c,
        stream_soliton_delta=config.qread_stream_soliton_delta,
    )
