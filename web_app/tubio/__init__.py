import logging
import json
import time

from typing import *
from pathlib import Path
from flask import Blueprint, render_template, request, send_file, redirect, url_for, flash, Response
from flask_login import login_required

from web_app.tubio.data_interface import DataInterface, AudioMetadata
from web_app.tubio.audio_downloader import AudioDownloader, VideoTooLongError, get_download_progress, clear_download_progress
from web_app.config import ConfigManager
from web_app.helpers import cur_user, parse_request
from web_app.users import User
from web_app.helpers import limiter


tubio_api = Blueprint(
    'tubio',
    __name__,
    template_folder='templates',
    static_folder='static',
    url_prefix='/tubio'
)


@tubio_api.before_request
@login_required
def before_request():
    # This ensures all routes in this blueprint require login
    pass


@tubio_api.context_processor
def inject_app_name():
    return dict(app_name='Tubio')

def get_cached_yt_vid_ids(user: User|None = None) -> Set[str]:
    metadata = DataInterface().get_metadata()
    if user is None:
        return {audio.yt_video_id for audio in metadata.audios.values()}
    else:
        user_metadata = DataInterface().get_user_metadata(user)
        return {metadata.audios[crc].yt_video_id for crc in user_metadata.get_playlist().audio_crcs}

def get_playlists_data(user: User) -> list[tuple[str, list[tuple[int, str, bool, str]]]]:
    user_metadata = DataInterface().get_user_metadata(user)
    playlists = []
    metadata = DataInterface().get_metadata()
    for playlist in user_metadata.get_playlists():
        playlist_data = []
        for crc in playlist.audio_crcs:
            if crc in metadata.audios:
                audio = metadata.audios[crc]
                has_thumbnail = DataInterface().has_thumbnail(crc)
                playlist_data.append((crc, audio.title, has_thumbnail, audio.source_url))
        playlists.append((playlist.name, playlist_data))

    return playlists

@tubio_api.route('/')
def index():
    return render_template("index.html", playlists=get_playlists_data(cur_user()))

@tubio_api.route('/search', methods=['GET', 'POST'])
def search():
    results = []
    query = ''
    if request.method == 'POST':
        query = request.form.get('youtube_query', '')
        if not query:
            flash("No search query provided.", 'error')
            return redirect(url_for('.index') + '#search')

        try:
            decorated_query = f"{ConfigManager().tudio_search_prefix}{query}"
            user_favourites = get_cached_yt_vid_ids(cur_user())
            results = AudioDownloader.search_youtube(decorated_query, user_favourites)
            # assume AJAX POST request
            return {'results': results, 'query': query}

        except VideoTooLongError as e:
            max_mins = int(e.max_duration.total_seconds() // 60)
            return {'error': f'Video exceeds maximum length of {max_mins} minutes', 'query': query}, 400

        except Exception:
            logging.exception("Error searching YouTube")
            flash("Error: Search Failed!", 'error')
            redirect(url_for('.index') + '#search')
    
    return redirect(url_for('.index') + '#search')

@tubio_api.route('/youtube_download', methods=['POST'])
def youtube_download():
    req = parse_request(require_login=False, require_admin=False)
    video_id = req.get('video_id')
    title = req.get('title')
    
    is_ajax = (request.headers.get('X-Requested-With') == 'XMLHttpRequest' or 
              'application/json' in request.headers.get('Accept', ''))
    if not is_ajax:
        logging.error("Non-AJAX request to /youtube_download")
        flash("Invalid request.", 'error')
        return redirect(url_for('.index') + '#playlists')

    # for rest of function assume we are dealing with AJAX request
    
    if not video_id or not title:
        return {'error': 'No video ID or title provided'}, 400

    if video_id in get_cached_yt_vid_ids(cur_user()):
        return {'error': 'Already in playlist', 'type': 'info'}, 400

    if video_id in get_cached_yt_vid_ids():
        # check if audio is already downloaded on the server but not in user's playlists
        existing_audio_metadata = DataInterface().get_audio_metadata(yt_video_id=video_id)
        user_metadata = DataInterface().get_user_metadata(cur_user())
        user_metadata.add_to_playlist(existing_audio_metadata.crc)
        DataInterface().save_user_metadata(cur_user(), user_metadata)
        
        return {
            'success': True,
            'message': f'Added {existing_audio_metadata.title} to playlist',
            'playlists': get_playlists_data(cur_user())
        }
        
    try:
        AudioDownloader.download_youtube_audio(video_id, title, cur_user())
        return {
            'success': True,
            'message': f'Audio downloaded for: {title}',
            'playlists': get_playlists_data(cur_user())
        }
    except Exception:
        logging.exception("Error downloading audio")
        return {'error': 'Error downloading audio'}, 500


@tubio_api.route('/download_progress/<video_id>')
def download_progress(video_id: str):
    def generate():
        while True:
            progress = get_download_progress(video_id)
            if progress is None:
                yield f"data: {json.dumps({'status': 'not_found'})}\n\n"
                break

            data = {
                'status': progress.status,
                'percent': round(progress.percent, 1),
                'error': progress.error
            }
            yield f"data: {json.dumps(data)}\n\n"

            if progress.status in ('complete', 'error'):
                clear_download_progress(video_id)
                break

            time.sleep(0.3)

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@tubio_api.route('/upload', methods=['POST'])
@limiter.limit("20 per minute")
def upload_audio():
    try:
        # Check if file is in the request
        if 'audio_file' not in request.files:
            flash('No file provided.', 'error')
            return redirect(url_for('.index'))
        
        file = request.files['audio_file']
        
        if file.filename == '':
            flash('No file selected.', 'error')
            return redirect(url_for('.index'))
        
        # Get the custom title or use filename
        title = request.form.get('audio_title', '').strip()
        if not title:
            # Use filename without extension as title
            title = Path(file.filename).stem
        
        # Validate file extension
        allowed_extensions = {'mp3', 'mp4', 'm4a'}
        file_ext = Path(file.filename).suffix.lower()[1:]
        
        if file_ext not in allowed_extensions:
            flash(f"Unsupported file format: {file_ext}. Allowed: {', '.join(allowed_extensions)}", "error")
            return redirect(url_for('.index'))
        
        crc = DataInterface().save_audio(title, file.read(), file_ext)
        audio_metadata = DataInterface().get_audio_metadata(crc=crc)
        user_metadata = DataInterface().get_user_metadata(cur_user())
        user_metadata.add_to_playlist(audio_metadata.crc)
        DataInterface().save_user_metadata(cur_user(), user_metadata)
        
        flash(f'Successfully uploaded: {title}', 'success')
        
    except Exception as e:
        logging.exception("Error uploading audio", exc_info=e)
        flash('Error uploading audio. Please try again.', 'error')
    
    return redirect(url_for('.index'))

def redownload_audio(audio_metadata: AudioMetadata) -> None:
    # TODO: redownload might take sometime so it could be jarring for end user
    # make it more obvious what is going on in the background

    file_path = DataInterface().get_audio_path(audio_metadata.crc)

    if file_path.exists():
        logging.warning(f"Audio file {file_path} exists but metadata indicates not cached. Updating metadata.")
        audio_metadata.is_cached = True
        DataInterface().save_audio_metadata(audio_metadata)
        return
    
    if not audio_metadata.yt_video_id:
        logging.error(f"Cannot redownload audio with crc {audio_metadata.crc} as it has no associated YouTube video ID.")
        raise ValueError("No YouTube video ID associated with this audio.")

    logging.info(f"Redownloading audio for YT video ID: {audio_metadata.yt_video_id}")
    AudioDownloader.download_youtube_audio(audio_metadata.yt_video_id,
                                           audio_metadata.title,
                                           cur_user(),
                                           crc=audio_metadata.crc)
    
    logging.info(f"Redownloaded audio for YT video ID: {audio_metadata.yt_video_id}")

@limiter.limit("100 per second") # TODO: only 1 should be loaded at a time temporary fix
@tubio_api.route('/audio/<int:crc>')
def serve_audio(crc: int):
    try:
        metadata = DataInterface().get_audio_metadata(crc=crc)
    except ValueError:
        flash(f'Error: no such audio: {crc: int}', 'error')
        logging.exception("Error serving audio")
        return redirect(url_for('.index'))

    if not metadata.is_cached:
        redownload_audio(metadata)

    file_path = DataInterface().get_audio_path(crc)
    file_size = file_path.stat().st_size
    range_header = request.headers.get("Range", None)
    logging.info(f"Serving audio {file_path}: {metadata.title} to user {cur_user()} with size {file_size} bytes, Range header: {range_header}")

    if not range_header:
        response = send_file(
            file_path,
            mimetype='audio/mp4',
            as_attachment=False,
            download_name=f"{crc}.m4a"
        )
        response.headers['Accept-Ranges'] = 'bytes'
        response.cache_control.max_age = ConfigManager().cache_max_age
        response.cache_control.public = True
        response.set_etag(str(crc))
        return response

    # Example: "Range: bytes=12345-"
    range_header = range_header.strip()[len("bytes="):]
    splitted = range_header.split("-")
    if len(splitted) > 2:
        logging.error(f"Invalid Range header format: {range_header}")
        raise ValueError("Invalid Range header format")
    if range_header[0] == '-':
        # Example: "Range: bytes=-12345" (last N bytes)
        suffix_length = int(splitted[1])
        byte1 = max(0, file_size - suffix_length)
        byte2 = file_size
    elif range_header[-1] == '-':
        # Example: "Range: bytes=12345-"
        byte1 = int(splitted[0])
        byte2 = file_size
    else:
        # Example: "Range: bytes=12345-67890"
        byte1 = int(splitted[0])
        byte2 = int(splitted[1]) + 1

    length = byte2 - byte1
    with open(file_path, "rb") as f:
        f.seek(byte1)
        data = f.read(length)

    response = Response(
        data,
        206,
        mimetype="audio/mp4",
        content_type="audio/mp4",
        direct_passthrough=True,
        headers={
            "Content-Type": "audio/mp4",
            "Content-Range": f"bytes {byte1}-{byte2-1}/{file_size}",
            "Accept-Ranges": "bytes",
        }
    )
    response.headers.set("Content-Length", str(length))

    # Cache audio ranges
    response.cache_control.max_age = ConfigManager().cache_max_age
    response.cache_control.public = True
    response.set_etag(str(crc))

    return response


@tubio_api.route('/thumbnail/<int:crc>')
def serve_thumbnail(crc: int):
    thumbnail_path = DataInterface().get_thumbnail_path(crc)
    if not thumbnail_path.exists():
        # Return a placeholder or 404
        return '', 404

    response = send_file(thumbnail_path, mimetype='image/jpeg')

    # Cache thumbnails
    response.cache_control.max_age = ConfigManager().cache_max_age
    response.cache_control.public = True
    response.set_etag(str(crc))

    return response


@tubio_api.route('/delete_audio/<int:crc>', methods=['POST'])
def delete_audio(crc: int):
    try:
        user = cur_user()
        user_metadata = DataInterface().get_user_metadata(user)
        
        # Check if user has this audio in their playlists
        if crc not in user_metadata.get_playlist().audio_crcs:
            flash('Audio not found in your playlists.', 'error')
            return redirect(url_for('.index'))
        
        # Remove from user's playlists
        user_metadata.remove_from_playlist(crc)
        DataInterface().save_user_metadata(user, user_metadata)
        
        # Check if any other users have this audio in their playlists
        metadata = DataInterface().get_metadata()
        other_users_have_audio = any(
            crc in user_metadata.get_playlist().audio_crcs 
            for user_metadata in metadata.users.values() 
        )
        
        # If no other users have this audio, delete it completely
        if not other_users_have_audio:
            DataInterface().delete_audio(crc)
            flash('Audio deleted successfully.', 'success')
        else:
            flash('Audio removed from your playlists.', 'info')
            
    except Exception as e:
        logging.exception("Error deleting audio")
        flash('Error deleting audio.', 'error')
    
    return redirect(url_for('.index'))

@tubio_api.route('/create_playlist', methods=['POST'])
@limiter.limit("10 per minute")
def create_playlist():
    try:
        playlist_name = request.form.get('playlist_name', '').strip()
        
        if not playlist_name:
            flash('Playlist name cannot be empty.', 'error')
            return redirect(url_for('.index'))
        
        user = cur_user()
        user_metadata = DataInterface().get_user_metadata(user)
        
        # Check if playlist already exists
        if playlist_name in user_metadata.playlists:
            flash(f'Playlist "{playlist_name}" already exists.', 'warning')
            return redirect(url_for('.index'))
        
        # Create new playlist
        user_metadata.get_playlist(playlist_name)
        DataInterface().save_user_metadata(user, user_metadata)
        
        flash(f'Playlist "{playlist_name}" created successfully!', 'success')
        
    except Exception as e:
        logging.exception("Error creating playlist")
        flash('Error creating playlist.', 'error')
    
    return redirect(url_for('.index'))

@tubio_api.route('/move_tracks_to_playlist', methods=['POST'])
@limiter.limit("20 per minute")
def move_tracks_to_playlist():
    try:
        target_playlist = request.form.get('target_playlist', '').strip()
        song_crcs_str = request.form.get('song_crcs', '')
        
        if not target_playlist:
            flash('Please select a target playlist.', 'error')
            return redirect(url_for('.index'))
        
        if not song_crcs_str:
            flash('No songs selected.', 'warning')
            return redirect(url_for('.index'))
        
        # Parse CRCs
        song_crcs = [int(crc) for crc in song_crcs_str.split(',') if crc.strip()]
        if not song_crcs:
            flash('No valid songs selected.', 'warning')
            return redirect(url_for('.index'))
        
        user = cur_user()
        user_metadata = DataInterface().get_user_metadata(user)
        
        for crc in song_crcs:
            user_metadata.remove_from_all_playlists(crc)
            user_metadata.add_to_playlist(crc, target_playlist)

        DataInterface().save_user_metadata(user, user_metadata)
        
    except Exception as e:
        logging.exception("Error moving songs to playlist", exc_info=e)
        flash('Error moving songs to playlist.', 'error')
    
    return redirect(url_for('.index'))


@tubio_api.route('/delete_selected_songs', methods=['POST'])
@limiter.limit("10 per minute")
def delete_selected_songs():
    try:
        song_crcs_str = request.form.get('song_crcs', '')
        
        if not song_crcs_str:
            flash('No songs selected.', 'warning')
            return redirect(url_for('.index'))
        
        song_crcs = [int(crc) for crc in song_crcs_str.split(',') if crc.strip()]
        user = cur_user()
        user_metadata = DataInterface().get_user_metadata(user)
        
        for crc in song_crcs:
            user_metadata.remove_from_all_playlists(crc)
        DataInterface().save_user_metadata(user, user_metadata)
        
        DataInterface().cleanup_unused_tracks()
        DataInterface().cleanup_unused_thumbnails()
    except Exception as e:
        logging.exception("Error deleting selected songs", exc_info=e)
        flash('Error deleting songs.', 'error')
    
    return redirect(url_for('.index'))

@tubio_api.route('/delete_playlist', methods=['POST'])
@limiter.limit("10 per minute")
def delete_playlist():
    try:
        playlist_name = request.form.get('playlist_name', '').strip()
        
        if not playlist_name:
            flash('Playlist name cannot be empty.', 'error')
            return redirect(url_for('.index'))
        
        # Prevent deletion of Favourites playlist
        if playlist_name == "Favourites":
            flash('Cannot delete the Favourites playlist.', 'error')
            return redirect(url_for('.index'))
        
        user = cur_user()
        user_metadata = DataInterface().get_user_metadata(user)
        
        # Check if playlist exists
        if playlist_name not in user_metadata.playlists:
            flash(f'Playlist "{playlist_name}" does not exist.', 'warning')
            return redirect(url_for('.index'))
        
        # Delete the playlist
        del user_metadata.playlists[playlist_name]
        DataInterface().save_user_metadata(user, user_metadata)
        
        flash(f'Playlist "{playlist_name}" deleted successfully!', 'success')
        
    except Exception as e:
        logging.exception("Error deleting playlist")
        flash('Error deleting playlist.', 'error')
    
    return redirect(url_for('.index'))
