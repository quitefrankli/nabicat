import requests
import logging

from flask import Blueprint, render_template, request, flash
from flask_login import login_required

from web_app.jswipe.endpoints import RapidAPIActiveJobsDB
from web_app.jswipe.data_interface import JobPost


# Major Australian cities for job search
AUSTRALIAN_CITIES = [
    'Sydney',
    'Melbourne',
    'Brisbane',
    'Perth',
    'Adelaide',
    'Hobart',
    'Canberra',
    'Gold Coast',
    'Newcastle',
    'Wollongong'
]

jswipe_api = Blueprint(
    'jswipe',
    __name__,
    template_folder='templates',
    static_folder='static',
    url_prefix='/jswipe'
)

@jswipe_api.context_processor
def inject_app_name():
    return dict(app_name='JSwipe', cities=AUSTRALIAN_CITIES)

def search_jobs(job_type, location) -> list[JobPost]:
    api = RapidAPIActiveJobsDB()
    
    try:
        flash("Fetching jobs from the API... This may take a moment.", "info")
        jobs = api.search(job_type=job_type, location=location, limit=10)
        
        if not jobs:
            flash("No jobs found for your search criteria.", "warning")
        return jobs
    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching jobs from API: {e}")
        return []

@jswipe_api.route('/', methods=['GET', 'POST'])
@login_required
def index():
    jobs = []
    if request.method == 'POST':
        job_type = request.form.get('job-type')
        location = request.form.get('location')
        if job_type and location:
            jobs = search_jobs(job_type, location)
            print(jobs)            
    return render_template("jswipe_index.html", jobs=jobs)
