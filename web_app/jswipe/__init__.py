import requests

from flask import Blueprint, render_template, request, flash
from flask_login import login_required

from web_app.config import ConfigManager
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
    url = "https://active-jobs-db.p.rapidapi.com/active-ats-7d"
    params = {
        "limit": "1",
        "offset": "0",
        "advanced_title_filter": f"'{job_type}'",
        "location_filter": location,
        "description_type": "text",
    }
    headers = {
        "X-RapidAPI-Key": ConfigManager().jswipe_api_key,
        "X-RapidAPI-Host": "active-jobs-db.p.rapidapi.com"
    }
    
    try:
        flash("Fetching jobs from the API... This may take a moment.", "info")
        response = requests.get(url, 
                                headers=headers, 
                                params=params, 
                                timeout=30)
        response.raise_for_status()  # Raise an exception for bad status codes
        data: list[dict] = response.json()
        
        jobs = [JobPost(id=job['id'],
                        title=job["title"],
                        company=job["organization"],
                        location=job.get("location", location),
                        description=job["description_text"],
                        url=job["url"],
                        post_date=job["date_posted"]) for job in data]
        if not jobs:
            flash("No jobs found for your search criteria.", "warning")
        return jobs
    except requests.exceptions.RequestException as e:
        flash(f"Error fetching jobs from API: {e}", "error")
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
