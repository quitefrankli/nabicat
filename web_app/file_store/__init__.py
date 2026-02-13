import logging

from werkzeug.datastructures import FileStorage
from flask import Blueprint, render_template, request, send_file, redirect, url_for, flash
from flask_login import login_required
import flask_login

from web_app.helpers import cur_user
from web_app.helpers import limiter
from web_app.config import ConfigManager
from web_app.file_store.data_interface import DataInterface, format_file_size, NON_ADMIN_MAX_STORAGE, ADMIN_MAX_STORAGE


file_store_api = Blueprint(
    'file_store',
    __name__,
    template_folder='templates',
    static_folder='static',
    url_prefix='/file_store'
)


@file_store_api.before_request
@login_required
def before_request():
    # This ensures all routes in this blueprint require login
    pass


@file_store_api.context_processor
def inject_app_name():
    return dict(app_name='File Store')


@file_store_api.route('/')
def index():
    user = cur_user()
    data_interface = DataInterface()
    mode = request.args.get('mode', 'list')
    files = data_interface.list_files_with_metadata(user) if user else []

    # Calculate storage info for all users
    storage_info = None
    if user:
        total_used = data_interface.get_total_storage_size(user)
        max_storage = ADMIN_MAX_STORAGE if user.is_admin else NON_ADMIN_MAX_STORAGE
        usage_percent = (total_used / max_storage) * 100 if max_storage > 0 else 0
        storage_info = {
            'used': total_used,
            'used_formatted': format_file_size(total_used),
            'max': max_storage,
            'max_formatted': format_file_size(max_storage),
            'usage_percent': min(usage_percent, 100),  # Cap at 100%
            'remaining': max_storage - total_used,
            'remaining_formatted': format_file_size(max(0, max_storage - total_used))
        }

    return render_template("file_store_index.html", files=files, storage_info=storage_info, mode=mode)


@file_store_api.route('/upload', methods=['POST'])
@limiter.limit(
    "10/second",
    key_func=lambda: flask_login.current_user.id,
    exempt_when=lambda: flask_login.current_user.is_admin,
)
def upload_file():
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    if 'file' not in request.files:
        if is_ajax:
            return {'error': 'No file part'}, 400
        flash('No file part', 'error')
        return redirect(url_for('.index'))
    file: FileStorage = request.files['file']
    if not file.filename:
        if is_ajax:
            return {'error': 'No selected file'}, 400
        flash('No selected file', 'error')
        return redirect(url_for('.index'))
    
    user = cur_user()
    data_interface = DataInterface()
    
    # Check storage limit for all users
    current_size = data_interface.get_total_storage_size(user)
    # Get file size from the stream
    file.seek(0, 2)  # Seek to end
    file_size = file.tell()
    file.seek(0)  # Reset to beginning
    
    max_storage = ADMIN_MAX_STORAGE if user.is_admin else NON_ADMIN_MAX_STORAGE
    
    if current_size + file_size > max_storage:
        max_label = f'{max_storage / (1024*1024*1024):.0f}GB' if user.is_admin else f'{max_storage / (1024*1024):.0f}MB'
        error_msg = (f'Upload failed: Storage limit of {max_label} exceeded. '
                     f'Current usage: {format_file_size(current_size)}, '
                     f'File size: {format_file_size(file_size)}')
        if is_ajax:
            return {'error': error_msg}, 413
        flash(error_msg, 'error')
        return redirect(url_for('.index'))
    
    data_interface.save_file(file, user)
    logging.info(f"user {user.id} uploaded file: {file.filename}")

    if is_ajax:
        return {'ok': True}, 200

    flash('File uploaded successfully!', 'success')
    return redirect(url_for('.index'))


@file_store_api.route('/download/<filename>')
def download_file(filename: str):
    file_path = DataInterface().get_file_path(filename, cur_user())
    response = send_file(file_path, as_attachment=True)

    # Cache files (immutable, identified by CRC)
    response.cache_control.max_age = ConfigManager().cache_max_age
    response.cache_control.public = True

    # ETag using CRC for cache validation
    crc = file_path.name  # filename is the CRC
    response.set_etag(crc)

    return response


@file_store_api.route('/thumbnail/<filename>')
@limiter.limit("30/second", key_func=lambda: flask_login.current_user.id)
def thumbnail(filename: str):
    """Serve a thumbnail for an image file."""
    data_interface = DataInterface()
    thumbnail_path = data_interface.get_thumbnail_for_file(filename, cur_user())

    if not thumbnail_path or not thumbnail_path.exists():
        flash('Thumbnail not available', 'error')
        return redirect(url_for('.index'))
    
    response = send_file(thumbnail_path, mimetype='image/jpeg')

    # Cache thumbnails
    response.cache_control.max_age = ConfigManager().cache_max_age
    response.cache_control.public = True

    return response


@file_store_api.route('/files_list')
def files_list():
    files = DataInterface().list_files(cur_user())
    return {'files': files}


@file_store_api.route('/delete/<filename>', methods=['POST'])
def delete_file(filename):
    try:
        DataInterface().delete_file(filename, cur_user())
    except FileNotFoundError:
        flash('File not found or could not be deleted.', 'error')
        return redirect(url_for('.index'))
    
    flash('File deleted successfully!', 'success')

    return redirect(url_for('.index'))


@file_store_api.route('/delete_all', methods=['POST'])
def delete_all_files():
    user = cur_user()
    data_interface = DataInterface()
    filenames = data_interface.list_files(user)

    for filename in filenames:
        data_interface.delete_file(filename, user)

    flash('All files deleted successfully!', 'success')
    return redirect(url_for('.index'))
