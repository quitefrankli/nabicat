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
        self.todoist_default_page_size = 8
        self.diary_default_tags = ['personal', 'work', 'reflection']
        self.diary_tag_dropdown_limit = 5
        self.backup_max_count = 8
        # Requests matching these prefixes are silently dropped (404, no log) — automated bots/scanners probing for common vulnerabilities
        self.known_bot_prefixes = {
            '/.env',        # env file harvesting (.env, .env.local, .env.production, etc.)
            '/.git/',       # git config/object exposure
            '/wp-',         # WordPress scanners (wp-admin, wp-login, wp-includes, xmlrpc)
            '/xmlrpc',      # WordPress XML-RPC
            '/phpmyadmin',  # phpMyAdmin probes
            '/.mist/',      # Juniper/Mist IoT probes
            '/dns-query',   # DNS-over-HTTPS probes
        }
        self.known_bot_methods = {'PROPFIND', 'TRACK', 'TRACE'}
        self.cache_max_age = 606461 # Default cache max age (1 week) in seconds, can be overridden by environment variable
        self.smtp_port = 587
        self.project_dir = Path.cwd()

        # LLM API
        self.llm_meridian_default_port = 3456
        self.llm_meridian_url = f"http://127.0.0.1:{self.llm_meridian_default_port}/v1/messages"
        self.llm_meridian_model = "claude-opus-4-7"
        self.llm_api_source = "codex"  # meridian | codex | hardcoded

        # Dev terminal (in-browser shell)
        self.dev_terminal_shell = "/bin/bash"
        self.dev_terminal_max_sessions = 4
        self.dev_terminal_idle_timeout_s = 1800
        self.dev_terminal_buffer_bytes = 1_048_576
        self.dev_terminal_read_chunk = 4096
        self.dev_map_geo_timeout_s = 8
        self.dev_map_geo_cache_ttl_s = 3600
        self.dev_map_geo_batch_size = 100
        self.dev_map_max_ips = 500
        self.dev_map_geo_url = "http://ip-api.com/batch"

        # Crosswords
        self.crosswords_model = "claude-sonnet-4-6"
        self.crosswords_codex_model = ""
        self.crosswords_codex_cli_command = "codex"
        self.crosswords_codex_cli_sandbox = "read-only"
        self.crosswords_codex_cli_approval_policy = "never"
        self.crosswords_word_count = 7
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

        # Sentinel
        self.sentinel_default_limit_mins = 1
        self.sentinel_min_limit_mins = 1
        self.sentinel_max_limit_mins = 10
        self.sentinel_max_steps = 20
        self.sentinel_max_screenshots = 10
        self.sentinel_max_retained_runs = 25
        self.sentinel_prompt_max_chars = 4000
        self.sentinel_browser_width_px = 1366
        self.sentinel_browser_height_px = 900
        self.sentinel_browser_default_timeout_ms = 15000
        self.sentinel_navigation_timeout_ms = 30000
        self.sentinel_post_click_load_timeout_ms = 5000
        self.sentinel_wait_action_ms = 1000
        self.sentinel_observation_max_elements = 80
        self.sentinel_observation_text_max_chars = 3000
        self.sentinel_observation_element_text_max_chars = 140
        self.sentinel_finding_detail_max_chars = 500
        self.sentinel_final_report_max_chars = 4000
        self.sentinel_final_report_max_images = 4
        self.sentinel_final_report_timeout_s = 60.0
        self.sentinel_codex_cli_command = "codex"
        self.sentinel_codex_model = ""
        self.sentinel_codex_cli_approval_policy = "never"
        self.sentinel_codex_permissions_profile = "sentinel_qa"
        self.sentinel_codex_step_timeout_s = 45.0

        # Hammock
        self.hammock_non_admin_quota_bytes = 50 * 1024 * 1024  # 50 MB
        self.hammock_admin_quota_bytes = 10 * 1024 * 1024 * 1024  # 10 GB
        self.hammock_gallery_thumb_max_px = 1400
        self.hammock_gallery_thumb_quality = 80
        self.hammock_max_image_pixels = 40_000_000  # decoded pixel cap (~40 MP) to bound RAM
        self.hammock_gallery_video_max_upload_bytes = 100 * 1024 * 1024
        self.hammock_gallery_video_max_duration_s = 60
        self.hammock_gallery_video_max_height_px = 720
        self.hammock_gallery_video_transcode_timeout_s = 180
        self.hammock_gallery_image_stagger_ms = 200
        self.hammock_gallery_image_max_retries = 3
        self.hammock_gallery_image_retry_delay_ms = 1000
        self.hammock_title_max_chars = 120
        self.hammock_description_max_chars = 2048
        self.hammock_markdown_max_chars = 256 * 1024
        self.hammock_project_slug_max_chars = 64

        # File Store
        self.file_store_non_admin_quota_bytes = 30 * 1024 * 1024  # 30 MB
        self.file_store_admin_quota_bytes = 10 * 1024 * 1024 * 1024  # 10 GB

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
