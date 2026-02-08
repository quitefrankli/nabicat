import flask
import requests
import logging
import flask_login

from datetime import date
from flask import Blueprint, render_template, request, flash, jsonify

from web_app.config import ConfigManager
from web_app.jswipe.endpoints import RapidAPIActiveJobsDB
from web_app.jswipe.debug_data import DEBUG_JOBS
from web_app.jswipe.data_interface import DataInterface, JobPost, JobPostState


AUSTRALIAN_CITIES = [
    'Sydney', 'Melbourne', 'Brisbane', 'Perth', 'Adelaide',
    'Hobart', 'Canberra', 'Gold Coast', 'Newcastle', 'Wollongong'
]


jswipe_api = Blueprint(
    'jswipe', __name__,
    template_folder='templates',
    static_folder='static',
    url_prefix='/jswipe'
)


@jswipe_api.before_request
@flask_login.login_required
def require_admin():
    if not flask_login.current_user.is_admin:
        flask.flash('You must be an admin to access this page', category='error')
        return flask.redirect(flask.url_for('home'))


@jswipe_api.context_processor
def inject_app_name():
    return dict(app_name='JSwipe', cities=AUSTRALIAN_CITIES)


def search_jobs(job_type: str, location: str):
    # Return hardcoded jobs in debug mode
    if ConfigManager().debug_mode:
        flash("Debug mode: Using test jobs", "info")
        return DEBUG_JOBS
    else:
        api = RapidAPIActiveJobsDB()
        return api.search(job_type=job_type, location=location, limit=10)


@jswipe_api.route('/', methods=['GET', 'POST'])
def index():
    jobs = []
    if request.method == 'POST':
        job_type = request.form.get('job-type')
        location = request.form.get('location')
        if job_type and location:
            try:
                flash("Fetching jobs...", "info")
                jobs = search_jobs(job_type, location)
                
                if not jobs:
                    flash("No jobs found.", "warning")
            except requests.exceptions.RequestException as e:
                logging.error(f"API error: {e}")
                flash("Error fetching jobs.", "error")
    return render_template("jswipe_index.html", jobs=jobs)


@jswipe_api.route('/api/job/<job_id>/<action>', methods=['POST'])
def job_action(job_id: str, action: str):
    """Record save/reject/apply action on a job."""
    action_map = {
        'save': JobPostState.SAVED,
        'reject': JobPostState.REJECTED,
        'apply': JobPostState.APPLIED
    }
    
    if action not in action_map:
        return jsonify({'error': 'Invalid action'}), 400
    
    data = request.get_json() or {}
    
    try:
        DataInterface().record_job_action(
            user=cur_user(),
            job_id=job_id,
            state=action_map[action],
            job_data={k: data.get(k) for k in ('title', 'company', 'location', 'url') if data.get(k)}
        )
        return jsonify({'success': True})
    except Exception as e:
        logging.error(f"Error recording job action: {e}")
        return jsonify({'error': 'Failed'}), 500
