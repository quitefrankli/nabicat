import logging

from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename
from flask import Blueprint, render_template, request, send_file, redirect, url_for, flash
from flask_login import login_required
from pathlib import Path

from web_app.api.data_interface import DataInterface
from web_app.helpers import cur_user
from web_app.users import User


# Maximum total storage size for non-admin users (in bytes)
NON_ADMIN_MAX_STORAGE = 100 * 1024 * 1024  # 100 MB

file_store_api = Blueprint(
    'file_store',
    __name__,
    template_folder='templates',
    static_folder='static',
    url_prefix='/file_store'
)


class FileStoreDataInterface(DataInterface):
    # Shares same directory as api.data_interface.DataInterface
    def __init__(self) -> None:
        super().__init__()

    def save_file(self, file_storage: FileStorage, user: User) -> None:
        user_dir = self._get_user_dir(user)
        # Sanitize filename to prevent path traversal
        safe_filename = secure_filename(file_storage.filename)
        file_path = user_dir / safe_filename

        self.atomic_write(file_path, stream=file_storage.stream, mode="wb")

    def get_file_path(self, filename: str, user: User) -> Path:
        # Sanitize filename to prevent path traversal
        safe_filename = secure_filename(filename)
        return self._get_user_dir(user) / safe_filename

    def get_total_storage_size(self, user: User) -> int:
        """Get total storage size used by a user in bytes"""
        user_dir = self._get_user_dir(user)
        if not user_dir.exists():
            return 0
        total_size = 0
        for file_path in user_dir.iterdir():
            if file_path.is_file():
                total_size += file_path.stat().st_size
        return total_size


@file_store_api.context_processor
def inject_app_name():
    return dict(app_name='File Store')


@file_store_api.route('/')
@login_required
def index():
    files = FileStoreDataInterface().list_files(cur_user()) if cur_user() else []
    return render_template("file_store_index.html", files=files)


@file_store_api.route('/upload', methods=['POST'])
@login_required
def upload_file():
    if 'file' not in request.files:
        flash('No file part', 'error')
        return redirect(url_for('.index'))
    file: FileStorage = request.files['file']
    if not file.filename:
        flash('No selected file', 'error')
        return redirect(url_for('.index'))
    
    user = cur_user()
    data_interface = FileStoreDataInterface()
    
    # Check storage limit for non-admin users
    if not user.is_admin:
        current_size = data_interface.get_total_storage_size(user)
        # Get file size from the stream
        file.seek(0, 2)  # Seek to end
        file_size = file.tell()
        file.seek(0)  # Reset to beginning
        
        if current_size + file_size > NON_ADMIN_MAX_STORAGE:
            flash(f'Upload failed: Storage limit of {NON_ADMIN_MAX_STORAGE / (1024*1024):.0f}MB exceeded. '
                  f'Current usage: {current_size / (1024*1024):.1f}MB, '
                  f'File size: {file_size / (1024*1024):.1f}MB', 'error')
            return redirect(url_for('.index'))
    
    data_interface.save_file(file, user)
    logging.info(f"user {user.id} uploaded file: {file.filename}")
    flash('File uploaded successfully!', 'success')
    return redirect(url_for('.index'))


@file_store_api.route('/download/<filename>')
@login_required
def download_file(filename: str):
    file_path = FileStoreDataInterface().get_file_path(filename, cur_user())
    return send_file(file_path, as_attachment=True)


@file_store_api.route('/files_list')
@login_required
def files_list():
    files = FileStoreDataInterface().list_files(cur_user())
    return {'files': files}


@file_store_api.route('/delete/<filename>', methods=['POST'])
@login_required
def delete_file(filename):
    try:
        FileStoreDataInterface().delete_data(filename, cur_user())
    except FileNotFoundError:
        flash('File not found or could not be deleted.', 'error')
        return redirect(url_for('.index'))
    
    flash('File deleted successfully!', 'success')

    return redirect(url_for('.index'))
