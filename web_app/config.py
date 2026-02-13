import os

from os import getenv
from pathlib import Path
from datetime import timedelta
from dotenv import load_dotenv

# Load environment variables from .env file
env_path = Path(__file__).parent.parent / '.env'
load_dotenv(env_path)


class ConfigManager:
    _instance = None  # Class-level variable to store the single instance

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            # If no instance exists, create a new one
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        # __init__ will be called every time, even for existing instances,
        # but the configuration loading logic should only run once.
        if hasattr(self, '_initialized'):
            return

        self._initialized = True
        self.use_offline_syncer = True
        self.debug_mode = False
        self.tudio_search_prefix = "" # helps narrow down search results
        self.tudio_max_results = 10
        self.tudio_max_video_length = timedelta(minutes=10)
        self.todoist2_default_page_size = 8
        self.cache_max_age = 606461 # Default cache max age (1 week) in seconds, can be overridden by environment variable

    @property
    def project_name(self) -> str:
        return "nabicat" if not self.debug_mode else "nabicat_debug"

    @property
    def save_data_path(self) -> Path:
        return Path.home() / f".{self.project_name}" / "data"
    
    @property
    def tubio_cookie_path(self) -> Path:
        return self.save_data_path / "cookies.txt"
    
    @property
    def temp_dir(self) -> Path:
        return self.save_data_path / "temp"

    @property
    def jswipe_api_key(self) -> str:
        api_key = getenv('X_RAPID_API_KEY')
        if api_key:
            return api_key
        
        if self.debug_mode:
            return "DEBUG_X_RAPID_API_KEY"
        
        raise ValueError("API key for JSwipe is not set. Please set the 'X_RAPID_API_KEY' environment variable.")
    
    @property
    def flask_secret_key(self) -> str:
        key = getenv('FLASK_SECRET_KEY')
        if key:
            return key

        if self.debug_mode:
            return "DEBUG_FLASK_SECRET_KEY"

        raise ValueError("Flask secret key is not set. Please set the 'FLASK_SECRET_KEY' environment variable.")