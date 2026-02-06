"""
Job API Endpoint Abstractions for JSwipe

This module provides abstracted interfaces to various job search APIs.
"""

import requests

from web_app.config import ConfigManager
from web_app.jswipe.data_interface import JobPost


class BaseEndPoint():
    pass


class DummyEndPoint(BaseEndPoint):
    """
    Dummy API endpoint for testing and development.
    
    This class simulates an API endpoint by returning hardcoded job data.
    """
    
    def search(self, job_type: str, location: str, limit: int = 1) -> list[JobPost]:
        """Simulate a job search by returning hardcoded job posts."""
        return [
            JobPost(
                id=1,
                title=f"{job_type} at Dummy Company",
                company="Dummy Company",
                location=location,
                description="This is a dummy job description.",
                url="https://dummycompany.com/job/1",
                post_date="2024-01-01"
            )
        ] * limit

class RapidAPIActiveJobsDB(BaseEndPoint):
    """
    RapidAPI Active Jobs DB API client.
    
    Provides access to the Active Jobs Database via RapidAPI.
    API Docs: https://rapidapi.com/Pat92/api/active-jobs-db
    """
    
    BASE_URL: str = "https://active-jobs-db.p.rapidapi.com"
    HOST: str = "active-jobs-db.p.rapidapi.com"
    

    def _get_headers(self) -> dict:
        """Get the required headers for API requests."""
        return {
            "X-RapidAPI-Key": ConfigManager().jswipe_api_key,
            "X-RapidAPI-Host": self.HOST,
        }
    
    def search(self, 
               job_type: str, 
               location: str, 
               limit: int = 1,
               offset: int = 0,
               description_type: str = "text"
    ) -> list[JobPost]:
        endpoint = f"{self.BASE_URL}/active-ats-7d"
        
        params = {
            "limit": str(limit),
            "offset": str(offset),
            "advanced_title_filter": f"'{job_type}'",
            "location_filter": location,
            "description_type": description_type,
        }
        
        response = requests.get(
            endpoint,
            headers=self._get_headers(),
            params=params,
            timeout=30
        )
        response.raise_for_status()
        
        data: list[dict] = response.json()
        
        return [
            JobPost(
                id=job['id'],
                title=job["title"],
                company=job["organization"],
                location=job.get("location", location),
                description=job["description_text"],
                url=job["url"],
                post_date=job["date_posted"]
            )
            for job in data
        ]
