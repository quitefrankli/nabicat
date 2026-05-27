from dataclasses import dataclass, field
from os import getenv
from pathlib import Path
from datetime import timedelta
from typing import Callable, Literal
from dotenv import load_dotenv

LLMSource = Literal["meridian", "codex", "bedrock"]

# Load environment variables from .env file
env_path = Path(__file__).parent.parent / '.env'
load_dotenv(env_path)


@dataclass
class LLMConfig:
    api_source: LLMSource = "codex"

    # Meridian (local Claude proxy) transport
    meridian_default_port: int = 3456
    meridian_models: dict = field(default_factory=lambda: {
        "weak":   "claude-haiku-4-5-20251001",
        "medium": "claude-sonnet-4-6",
        "strong": "claude-opus-4-7",
    })

    # Codex CLI transport (shared by all apps that shell out to codex).
    # Empty model strings mean "let codex CLI pick its native default".
    codex_cli_command: str = "codex"
    codex_cli_approval_policy: str = "never"
    codex_cli_sandbox: str = "read-only"
    codex_models: dict = field(default_factory=lambda: {
        "weak":   "",
        "medium": "",
        "strong": "",
    })

    # Bedrock (Anthropic-on-AWS via boto3 / anthropic[bedrock] SDK).
    # AWS auth comes from the standard AWS credential chain; AWS_REGION must be set.
    # Values may be inference-profile ARNs or anthropic.<model> IDs.
    # Bedrock model IDs / inference-profile ARNs. Inference-profile ARNs are
    # required when your IAM policy only grants InvokeModel on the profile,
    # not on the bare foundation-model ID — set BEDROCK_{TIER}_MODEL in .env
    # to override per-environment without committing the ARN.
    bedrock_models: dict = field(default_factory=lambda: {
        "weak":   getenv("BEDROCK_WEAK_MODEL")   or "anthropic.claude-haiku-4-5",
        "medium": getenv("BEDROCK_MEDIUM_MODEL") or "anthropic.claude-sonnet-4-6",
        "strong": getenv("BEDROCK_STRONG_MODEL") or "anthropic.claude-opus-4-7",
    })

    @property
    def meridian_url(self) -> str:
        return f"http://127.0.0.1:{self.meridian_default_port}/v1/messages"

    def model_for(self, tier: str) -> str:
        """Resolve a tier (``weak``/``medium``/``strong``) to a concrete model
        name for the currently-configured ``api_source``. Unknown tiers fall
        back to ``medium``.
        """
        models = {
            "meridian": self.meridian_models,
            "codex":    self.codex_models,
            "bedrock":  self.bedrock_models,
        }.get(self.api_source, self.codex_models)
        return models.get(tier, models.get("medium", ""))


@dataclass
class TubioConfig:
    _save_data_path: Callable[[], Path] = field(repr=False)
    search_prefix: str = ""
    max_results: int = 10
    max_search_pages: int = 3
    max_video_length: timedelta = timedelta(minutes=10)
    test_video_id: str = "dQw4w9WgXcQ"

    @property
    def cookie_path(self) -> Path:
        return self._save_data_path() / "cookies.txt"


@dataclass
class TodoistConfig:
    default_page_size: int = 8
    goal_drag_hold_ms: int = 350
    goal_drag_move_threshold_px: int = 8
    goal_drag_hover_expand_ms: int = 650


@dataclass
class DiaryConfig:
    default_tags: list[str] = field(default_factory=lambda: ["personal", "work", "reflection"])
    tag_dropdown_limit: int = 5


@dataclass
class DevConfig:
    terminal_shell: str = "/bin/bash"
    terminal_max_sessions: int = 4
    terminal_idle_timeout_s: int = 1800
    terminal_buffer_bytes: int = 1_048_576
    terminal_read_chunk: int = 4096
    log_rotation_backup_count: int = 20
    log_viewer_file_count: int = 5
    log_viewer_max_lines: int = 5000
    map_geo_timeout_s: int = 8
    map_geo_cache_ttl_s: int = 3600
    map_geo_batch_size: int = 100
    map_max_ips: int = 500
    map_geo_url: str = "http://ip-api.com/batch"


@dataclass
class CrosswordsConfig:
    # Provider-agnostic capability tier; LLMConfig.model_for() resolves it
    # to a concrete model for the active api_source.
    llm_tier: str = "medium"  # weak | medium | strong
    word_count: int = 7
    min_placed_words: int = 3
    llm_generation_max_tokens: int = 1024
    llm_generation_timeout_s: float = 20.0
    llm_theme_check_max_tokens: int = 4
    llm_theme_check_timeout_s: float = 10.0
    default_theme: str = "cats"
    default_difficulty: int = 2
    difficulty_min: int = 1
    difficulty_max: int = 5
    theme_min_len: int = 2
    theme_max_len: int = 13


@dataclass
class SentinelConfig:
    default_limit_mins: int = 5
    min_limit_mins: int = 1
    max_limit_mins: int = 10
    max_steps: int = 50
    max_screenshots: int = 50
    max_retained_runs: int = 25
    prompt_max_chars: int = 4000
    browser_width_px: int = 1366
    browser_height_px: int = 900
    browser_default_timeout_ms: int = 15000
    navigation_timeout_ms: int = 30000
    post_click_load_timeout_ms: int = 5000
    # Settle delay after a click. Gives modals/menus/transitions a moment to
    # finish before the next observation runs.
    post_click_settle_ms: int = 600
    # Settle delay after a fill. Mostly debounced JS validators / autocomplete.
    post_fill_settle_ms: int = 200
    # Settle delay after a select. Native dropdowns can re-render the page.
    post_select_settle_ms: int = 1000
    # Settle delay after a scroll. Lazy-loaded content / IntersectionObservers.
    post_scroll_settle_ms: int = 1000
    # Pause for the explicit "wait" action.
    wait_action_ms: int = 1000
    scroll_action_delta_px: int = 650
    observation_max_elements: int = 80
    observation_text_max_chars: int = 3000
    observation_element_text_max_chars: int = 140
    finding_detail_max_chars: int = 500
    final_report_max_chars: int = 4000
    final_report_max_images: int = 4
    final_report_timeout_s: float = 60.0
    title_max_chars: int = 80
    llm_title_max_tokens: int = 80
    llm_title_timeout_s: float = 15.0
    llm_verdict_max_tokens: int = 200
    llm_verdict_timeout_s: float = 20.0
    annotation_box_width_px: int = 3
    annotation_label_font_px: int = 14
    annotation_label_pad_px: int = 4
    screenshot_load_stagger_ms: int = 200
    screenshot_load_max_retries: int = 3
    screenshot_load_retry_delay_ms: int = 1000
    screenshot_thumb_max_px: int = 360
    # Provider-agnostic capability tier; LLMConfig.model_for() resolves it
    # to a concrete model for the active api_source.
    llm_tier: str = "strong"  # weak | medium | strong
    # Provider-agnostic LLM behavior knobs.
    llm_step_timeout_s: float = 45.0
    llm_step_max_tokens: int = 1024
    llm_final_report_max_tokens: int = 2048
    # Codex-only quirk: sandbox profile name passed via -c default_permissions=...
    codex_permissions_profile: str = "sentinel_qa"
    # Friendly device key -> Playwright devices registry name. Empty string
    # means "no emulation; use browser_width/height_px viewport".
    device_profiles: dict = field(default_factory=lambda: {
        "desktop":     "",
        "tablet":      "iPad (gen 7)",
        "large_phone": "iPhone 13 Pro Max",
        "small_phone": "iPhone SE",
    })
    device_labels: dict = field(default_factory=lambda: {
        "desktop":     "Desktop",
        "tablet":      "Tablet",
        "large_phone": "Large Phone",
        "small_phone": "Small Phone",
    })
    default_device: str = "desktop"
    # Demographic key -> persona sentence prepended to the agent system prompt.
    demographic_personas: dict = field(default_factory=lambda: {
        "child":  "You are an 8-year-old child using a website for the first time; you click colorful things, get bored fast, and cannot read long text.",
        "adult":  "You are a typical adult web user with average tech literacy who skims interfaces and expects standard web conventions.",
        "senior": "You are a senior in your 70s with limited tech experience; small targets, jargon, and unexpected layouts confuse you, and you prefer obvious, labeled controls.",
        "techie": "You are a power user comfortable with developer tools, keyboard shortcuts, and dense UIs; you probe edge cases and unusual flows.",
    })
    demographic_labels: dict = field(default_factory=lambda: {
        "child":  "Child",
        "adult":  "Adult",
        "senior": "Senior",
        "techie": "Techie",
    })
    default_demographic: str = "adult"
    # Keywords that imply the prompt depends on auth flows. If any appear in
    # the prompt while allow_accounts=false, the run is rejected up-front so
    # the agent doesn't immediately self-abort. Matched as whole words
    # (case-insensitive) against the prompt.
    account_keywords: tuple = (
        "account", "accounts",
        "sign up", "signup", "sign-up",
        "sign in", "signin", "sign-in",
        "log in", "login", "log-in",
        "register", "registration",
    )
    region_labels: dict = field(default_factory=lambda: {
        "australia": "Australia",
        "china":     "China",
        "us":        "US",
        "uk":        "UK",
        "japan":     "Japan",
    })
    default_region: str = "australia"


@dataclass
class HammockConfig:
    non_admin_quota_bytes: int = 50 * 1024 * 1024
    admin_quota_bytes: int = 10 * 1024 * 1024 * 1024
    gallery_thumb_max_px: int = 1400
    gallery_thumb_quality: int = 80
    max_image_pixels: int = 40_000_000
    gallery_video_max_upload_bytes: int = 100 * 1024 * 1024
    gallery_video_max_duration_s: int = 60
    gallery_video_max_height_px: int = 720
    gallery_video_transcode_timeout_s: int = 180
    gallery_image_stagger_ms: int = 200
    gallery_image_max_retries: int = 3
    gallery_image_retry_delay_ms: int = 1000
    title_max_chars: int = 120
    description_max_chars: int = 2048
    markdown_max_chars: int = 256 * 1024
    project_slug_max_chars: int = 64


@dataclass
class FileStoreConfig:
    non_admin_quota_bytes: int = 30 * 1024 * 1024
    admin_quota_bytes: int = 10 * 1024 * 1024 * 1024


@dataclass
class JSwipeConfig:
    _debug_mode: Callable[[], bool] = field(repr=False)

    @property
    def api_key(self) -> str:
        api_key = getenv('X_RAPID_API_KEY')
        if api_key:
            return api_key

        if self._debug_mode():
            return "DEBUG_X_RAPID_API_KEY"

        raise ValueError("API key for JSwipe is not set. Please set the 'X_RAPID_API_KEY' environment variable.")


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
        self.request_log_suppressed_paths = {
            '/dev/terminal/input',
            '/dev/terminal/output',
        }
        self.cache_max_age = 606461 # Default cache max age (1 week) in seconds, can be overridden by environment variable
        self.smtp_port = 587
        self.project_dir = Path.cwd()

        self.llm = LLMConfig()
        self.tubio = TubioConfig(lambda: self.save_data_path)
        self.todoist = TodoistConfig()
        self.diary = DiaryConfig()
        self.dev = DevConfig()
        self.crosswords = CrosswordsConfig()
        self.sentinel = SentinelConfig()
        self.hammock = HammockConfig()
        self.file_store = FileStoreConfig()
        self.jswipe = JSwipeConfig(lambda: self.debug_mode)

    @property
    def project_name(self) -> str:
        return "nabicat" if not self.debug_mode else "nabicat_debug"

    @property
    def save_data_path(self) -> Path:
        return Path.home() / f".{self.project_name}" / "data"
    
    @property
    def temp_dir(self) -> Path:
        return self.save_data_path / "temp"

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
