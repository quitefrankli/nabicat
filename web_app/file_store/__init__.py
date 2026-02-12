import logging

from werkzeug.datastructures import FileStorage
from flask import Blueprint, render_template, request, send_file, redirect, url_for, flash
from flask_login import login_required

from web_app.helpers import cur_user
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
def upload_file():
    if 'file' not in request.files:
        flash('No file part', 'error')
        return redirect(url_for('.index'))
    file: FileStorage = request.files['file']
    if not file.filename:
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
        flash(f'Upload failed: Storage limit of {max_label} exceeded. '
              f'Current usage: {format_file_size(current_size)}, '
              f'File size: {format_file_size(file_size)}', 'error')
        return redirect(url_for('.index'))
    
    data_interface.save_file(file, user)
    logging.info(f"user {user.id} uploaded file: {file.filename}")
    flash('File uploaded successfully!', 'success')
    return redirect(url_for('.index'))


@file_store_api.route('/download/<filename>')
def download_file(filename: str):
    file_path = DataInterface().get_file_path(filename, cur_user())
    return send_file(file_path, as_attachment=True)


@file_store_api.route('/thumbnail/<filename>')
def thumbnail(filename: str):
    """Serve a thumbnail for an image file."""
    data_interface = DataInterface()
    thumbnail_path = data_interface.get_thumbnail_for_file(filename, cur_user())

    if thumbnail_path and thumbnail_path.exists():
        return send_file(thumbnail_path, mimetype='image/jpeg')

    # If thumbnail generation failed, return the original file
    try:
        file_path = data_interface.get_file_path(filename, cur_user())
        return send_file(file_path)
    except FileNotFoundError:
        flash('File not found', 'error')
        return redirect(url_for('.index'))


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
