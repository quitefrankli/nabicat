import requests
import logging

from datetime import date
from flask import Blueprint, render_template, request, flash, jsonify
from flask_login import login_required

from web_app.config import ConfigManager
from web_app.jswipe.endpoints import RapidAPIActiveJobsDB
from web_app.jswipe.data_interface import DataInterface, JobPost, JobPostState
from web_app.helpers import cur_user


AUSTRALIAN_CITIES = [
    'Sydney', 'Melbourne', 'Brisbane', 'Perth', 'Adelaide',
    'Hobart', 'Canberra', 'Gold Coast', 'Newcastle', 'Wollongong'
]

# Hardcoded jobs for debug mode
DEBUG_JOBS = [
    JobPost(
        id='debug-1',
        title='Software Engineer',
        company='TechCorp Australia',
        location='Sydney',
        description='We are looking for a skilled Software Engineer to join our team. You will work on exciting projects using Python, JavaScript, and cloud technologies. Experience with web frameworks like Flask or Django preferred.',
        url='https://example.com/job1',
        post_date=date.today()
    ),
    JobPost(
        id='debug-2',
        title='Senior Python Developer',
        company='DataFlow Systems',
        location='Melbourne',
        description='Join our data engineering team! We need a Senior Python Developer with experience in data processing, ETL pipelines, and machine learning. Remote work options available.',
        url='https://example.com/job2',
        post_date=date.today()
    ),
    JobPost(
        id='debug-3',
        title='Full Stack Developer',
        company='StartupXYZ',
        location='Brisbane',
        description='Fast-growing startup seeking a Full Stack Developer. Tech stack: React, Node.js, PostgreSQL. Must be comfortable with rapid iteration and agile development.',
        url='https://example.com/job3',
        post_date=date.today()
    ),
    JobPost(
        id='debug-4',
        title='DevOps Engineer',
        company='CloudNative Solutions',
        location='Perth',
        description='Looking for a DevOps Engineer with AWS, Kubernetes, and Terraform experience. You will help build and maintain our cloud infrastructure and CI/CD pipelines.',
        url='https://example.com/job4',
        post_date=date.today()
    ),
    JobPost(
        id='debug-5',
        title='Product Manager',
        company='FinTech Innovations',
        location='Sydney',
        description='Join our fintech team as a Product Manager. You will drive product strategy, work with engineering teams, and deliver features that help our customers. Finance background a plus.',
        url='https://example.com/job5',
        post_date=date.today()
    ),
]

jswipe_api = Blueprint(
    'jswipe', __name__,
    template_folder='templates',
    static_folder='static',
    url_prefix='/jswipe'
)


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
@login_required
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
@login_required
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
