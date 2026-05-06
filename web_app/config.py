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
        self.tubio_max_search_pages = 3
        self.tudio_max_video_length = timedelta(minutes=10)
        self.tubio_test_video_id = "dQw4w9WgXcQ" # Default video ID for testing (Rick Astley - Never Gonna Give You Up)
        self.todoist2_default_page_size = 8
        self.cache_max_age = 606461 # Default cache max age (1 week) in seconds, can be overridden by environment variable
        self.smtp_port = 587
        self.project_dir = Path.cwd()

        # Assistant
        self.assistant_meridian_default_port = 3456
        self.assistant_meridian_url = f"http://127.0.0.1:{self.assistant_meridian_default_port}/v1/messages"
        self.assistant_model = "claude-opus-4-7"
        self.assistant_max_tokens = 4096

        # Crosswords
        self.crosswords_model = "claude-sonnet-4-6"
        self.crosswords_word_count = 10
        self.crosswords_min_placed_words = 3
        self.crosswords_generation_max_tokens = 1024
        self.crosswords_generation_timeout_s = 20.0
        self.crosswords_theme_check_max_tokens = 4
        self.crosswords_theme_check_timeout_s = 10.0
        self.crosswords_default_theme = "cats"
        self.crosswords_default_difficulty = 2
        self.crosswords_difficulty_min = 1
        self.crosswords_difficulty_max = 5
        self.crosswords_theme_min_len = 2
        self.crosswords_theme_max_len = 13

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

    @property
    def smtp_host(self) -> str:
        return getenv('SMTP_HOST', '')

    @property
    def smtp_user(self) -> str:
        return getenv('SMTP_USER', '')

    @property
    def smtp_password(self) -> str:
        return getenv('SMTP_PASSWORD', '')

    @property
    def alert_email_to(self) -> str:
        return getenv('ALERT_EMAIL_TO', '')