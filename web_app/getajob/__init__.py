import requests

from flask import Blueprint, render_template, request, flash
from flask_login import login_required

from web_app.config import ConfigManager


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

getajob_api = Blueprint(
    'getajob',
    __name__,
    template_folder='templates',
    static_folder='static',
    url_prefix='/getajob'
)

@getajob_api.context_processor
def inject_app_name():
    return dict(app_name='Get a Job', cities=AUSTRALIAN_CITIES)

def search_jobs(job_type, location):
    url = "https://active-jobs-db.p.rapidapi.com/active-ats-7d"
    params = {
        "limit": "1",
        "offset": "0",
        "advanced_title_filter": f"'{job_type}'",
        "location_filter": location,
        "description_type": "text",
    }
    headers = {
        "X-RapidAPI-Key": ConfigManager().getajob_api_key,
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
        
        jobs = []
        for job in data:
            print(job.keys())
            jobs.append({
                "id": job['id'],
                "date": job["date_posted"],
                "title": job["title"],
                "company": job["organization"],
                "description": job["description_text"],
                "url": job["url"],
            })
        if not jobs:
            flash("No jobs found for your search criteria.", "warning")
        return jobs
    except requests.exceptions.RequestException as e:
        flash(f"Error fetching jobs from API: {e}", "error")
        return []

@getajob_api.route('/', methods=['GET', 'POST'])
@login_required
def index():
    jobs = []
    if request.method == 'POST':
        job_type = request.form.get('job-type')
        location = request.form.get('location')
        if job_type and location:
            jobs = search_jobs(job_type, location)
    return render_template("getajob_index.html", jobs=jobs)
