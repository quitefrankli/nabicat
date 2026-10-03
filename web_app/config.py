from dataclasses import dataclass, field
from os import getenv
from pathlib import Path
from datetime import timedelta
from math import ceil
from typing import Callable, Literal
from dotenv import load_dotenv

LLMSource = Literal["meridian", "codex", "bedrock"]

# Load environment variables from .env file
env_path = Path(__file__).parent.parent / '.env'
load_dotenv(env_path)


def default_career_ops_root() -> str:
    return str(Path.home() / ".local" / "share" / "nabicat" / "jswipe" / "career-ops")


@dataclass(frozen=True, slots=True)
class JswipeConfig:
    career_ops_root: str = field(default_factory=default_career_ops_root)
    node_command: str = "node"
    scan_script: str = "scan-ats-full.mjs"
    ats_sources: tuple[str, ...] = (
        "greenhouse",
        "lever",
        "ashby",
        "workday",
        "icims",
    )
    default_keywords: tuple[str, ...] = (
        "Software Engineer",
        "C++",
        "Platform Engineer",
        "Systems Engineer",
        "Infrastructure Engineer",
    )
    default_locations: tuple[str, ...] = (
        "Sydney",
        "New South Wales",
        "Australia",
        "Remote",
    )
    remote_preferences: tuple[str, ...] = ("any", "prefer", "require", "exclude")
    default_since_days: int = 7
    minimum_since_days: int = 1
    maximum_since_days: int = 30
    minimum_companies_per_source: int = 1
    companies_per_source: int = 100
    maximum_companies_per_source: int = 500
    companies_per_source_step: int = 1
    maximum_results: int = 250
    description_enrichment_limit: int = 50
    job_description_max_chars: int = 50_000
    maximum_retained_jobs: int = 1_000
    maximum_keywords: int = 24
    maximum_locations: int = 16
    maximum_filter_chars: int = 80
    scan_timeout_seconds: int = 240
    scan_lease_grace_seconds: int = 30
    scan_retry_after_seconds: int = 10
    user_operation_retry_after_seconds: int = 10
    scan_rate_limit_requests: int = 10
    scan_rate_limit_window_minutes: int = 60
    resume_rate_limit_requests: int = 6
    profile_rate_limit_requests: int = 30
    personalization_rate_limit_window_minutes: int = 60
    scanner_output_max_bytes: int = 2_000_000
    node_max_old_space_mb: int = 256
    resume_max_bytes: int = 5 * 1024 * 1024
    resume_max_pages: int = 20
    resume_text_max_chars: int = 50_000
    profile_model_max_tokens: int = 2_048
    profile_model_timeout_seconds: int = 60
    ranking_batch_size: int = 10
    ranking_model_max_tokens: int = 4_096
    ranking_model_timeout_seconds: int = 60
    ranking_dimension_weights: dict[str, int] = field(default_factory=lambda: {
        "role": 30,
        "skills_experience": 30,
        "seniority": 15,
        "location_remote": 10,
        "constraints": 15,
    })
    truncated_description_confidence_cap: int = 85

    def __post_init__(self) -> None:
        if not self.career_ops_root.strip():
            raise ValueError("career_ops_root must be non-empty")
        if not self.node_command.strip():
            raise ValueError("node_command must be non-empty")
        if not self.scan_script.strip():
            raise ValueError("scan_script must be non-empty")
        if not self.ats_sources or len(set(self.ats_sources)) != len(self.ats_sources):
            raise ValueError("ats_sources must be non-empty and unique")
        if not self.remote_preferences or len(set(self.remote_preferences)) != len(
            self.remote_preferences
        ):
            raise ValueError("remote_preferences must be non-empty and unique")
        if self.minimum_companies_per_source < 1:
            raise ValueError("minimum_companies_per_source must be positive")
        if self.maximum_companies_per_source < self.minimum_companies_per_source:
            raise ValueError("maximum_companies_per_source is below its minimum")
        if self.companies_per_source_step < 1:
            raise ValueError("companies_per_source_step must be positive")
        if not (
            self.minimum_companies_per_source
            <= self.companies_per_source
            <= self.maximum_companies_per_source
        ):
            raise ValueError("companies_per_source is outside the configured range")
        if not self.minimum_since_days <= self.default_since_days <= self.maximum_since_days:
            raise ValueError("default_since_days is outside the configured range")
        positive_values = (
            self.maximum_results,
            self.description_enrichment_limit,
            self.job_description_max_chars,
            self.maximum_retained_jobs,
            self.maximum_keywords,
            self.maximum_locations,
            self.maximum_filter_chars,
            self.scan_timeout_seconds,
            self.scan_lease_grace_seconds,
            self.scan_retry_after_seconds,
            self.user_operation_retry_after_seconds,
            self.scan_rate_limit_requests,
            self.scan_rate_limit_window_minutes,
            self.resume_rate_limit_requests,
            self.profile_rate_limit_requests,
            self.personalization_rate_limit_window_minutes,
            self.scanner_output_max_bytes,
            self.node_max_old_space_mb,
            self.resume_max_bytes,
            self.resume_max_pages,
            self.resume_text_max_chars,
            self.profile_model_max_tokens,
            self.profile_model_timeout_seconds,
            self.ranking_batch_size,
            self.ranking_model_max_tokens,
            self.ranking_model_timeout_seconds,
            self.truncated_description_confidence_cap,
        )
        if any(value < 1 for value in positive_values):
            raise ValueError("numeric limits must be positive")
        if self.truncated_description_confidence_cap > 100:
            raise ValueError("truncated description confidence cap must not exceed 100")

    @property
    def maximum_ranking_seconds(self) -> int:
        return (
            ceil(self.description_enrichment_limit / self.ranking_batch_size)
            * self.ranking_model_timeout_seconds
        )

    @property
    def scan_lease_seconds(self) -> int:
        return (
            self.scan_timeout_seconds + self.maximum_ranking_seconds + self.scan_lease_grace_seconds
        )

    @property
    def user_operation_lease_seconds(self) -> int:
        return (
            max(
                self.scan_timeout_seconds + self.maximum_ranking_seconds,
                self.profile_model_timeout_seconds + self.maximum_ranking_seconds,
            )
            + self.scan_lease_grace_seconds
        )


@dataclass(slots=True)
class SentinelConfig:
    """Typed, app-owned configuration for Sentinel."""

    llm_tier: str = "strong"
    codex_model: str = "gpt-5.6-sol"
    codex_reasoning_effort: str = "medium"
    codex_permissions_profile: str = "sentinel_qa"
    image_temp_prefix: str = "nabicat-sentinel-text-"
    allow_local_targets: bool = False
    request_deadline_s: int = 690
    lease_ttl_s: int = 90
    lease_retry_after_s: int = 5
    lease_recovery_grace_s: int = 5
    cancel_flag_ttl_s: int = 3600

    default_limit_mins: int = 5
    min_limit_mins: int = 1
    max_limit_mins: int = 10
    max_steps: int = 50
    max_screenshots: int = 50
    sidebar_run_limit: int = 25
    sidebar_batch_limit: int = 25
    max_batch_items: int = 1
    batch_name_max_chars: int = 80
    batch_name_fallback: str = "Sentinel batch"

    prompt_max_chars: int = 4000
    title_max_chars: int = 80
    verdict_reason_max_chars: int = 300
    finding_detail_max_chars: int = 500
    final_report_max_chars: int = 4000
    final_report_max_images: int = 4
    final_report_picker_budget: int = 6
    additional_domains_max_count: int = 10
    additional_domain_max_chars: int = 253

    browser_width_px: int = 1366
    browser_height_px: int = 900
    browser_launch_timeout_ms: int = 30000
    browser_default_timeout_ms: int = 15000
    ignore_https_errors: bool = False
    browser_desktop_user_agent: str = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )
    browser_launch_args: list[str] = field(
        default_factory=lambda: ["--disable-blink-features=AutomationControlled"]
    )
    navigation_timeout_ms: int = 30000
    post_click_load_timeout_ms: int = 5000
    post_click_settle_ms: int = 600
    post_fill_settle_ms: int = 200
    post_select_settle_ms: int = 1000
    post_scroll_settle_ms: int = 1000
    wait_action_ms: int = 1000
    scroll_action_delta_px: int = 650
    scroll_position_tolerance_px: int = 2
    full_page_scope_prompt_pattern: str = (
        r"\b(?:all|every|each|whole|entire|full)\b.{0,80}\b"
        r"(?:apps?|cards?|links?|items?|rows?|sections?|pages?|menus?|public|private)\b"
        r"|\b(?:apps?|cards?|links?|items?|rows?|sections?|pages?|menus?|public|private)\b"
        r".{0,80}\b(?:all|every|each|whole|entire|full)\b"
    )
    observation_max_elements: int = 80
    observation_text_max_chars: int = 3000
    observation_element_text_max_chars: int = 140

    agent_parse_retry_attempts: int = 1
    click_loop_threshold: int = 3
    click_loop_max_warnings: int = 3
    console_finding_title: str = "Console"
    console_finding_kind: str = "browser.console"

    llm_step_timeout_s: float = 45.0
    llm_step_max_tokens: int = 1024
    llm_title_timeout_s: float = 15.0
    llm_title_max_tokens: int = 80
    llm_verdict_timeout_s: float = 20.0
    llm_verdict_max_tokens: int = 200
    llm_picker_timeout_s: float = 20.0
    llm_picker_max_tokens: int = 300
    final_report_timeout_s: float = 60.0
    llm_final_report_max_tokens: int = 2048

    annotation_box_width_px: int = 3
    annotation_label_font_px: int = 14
    annotation_label_pad_px: int = 4
    screenshot_load_stagger_ms: int = 200
    screenshot_load_max_retries: int = 3
    screenshot_load_retry_delay_ms: int = 1000
    screenshot_thumb_max_px: int = 360
    annotation_palette: tuple[tuple[int, int, int], ...] = (
        (224, 122, 95),
        (135, 168, 120),
        (244, 162, 97),
        (233, 196, 106),
        (74, 93, 74),
        (107, 142, 90),
    )

    pdf_margin_top: str = "16mm"
    pdf_margin_bottom: str = "18mm"
    pdf_margin_left: str = "14mm"
    pdf_margin_right: str = "14mm"
    pdf_footer_label: str = "Generated by Sentinel"

    device_profiles: dict[str, str] = field(
        default_factory=lambda: {
            "desktop": "",
            "tablet": "iPad (gen 7)",
            "large_phone": "iPhone 13 Pro Max",
            "small_phone": "iPhone SE",
        }
    )
    device_labels: dict[str, str] = field(
        default_factory=lambda: {
            "desktop": "Desktop",
            "tablet": "Tablet",
            "large_phone": "Large Phone",
            "small_phone": "Small Phone",
        }
    )
    default_device: str = "desktop"
    demographic_personas: dict[str, str] = field(
        default_factory=lambda: {
            "child": (
                "You are an 8-year-old child using a website for the first time; "
                "you click colorful things, get bored fast, and cannot read long text."
            ),
            "adult": (
                "You are a typical adult web user with average tech literacy who "
                "skims interfaces and expects standard web conventions."
            ),
            "senior": (
                "You are a senior in your 70s with limited tech experience; small targets, "
                "jargon, and unexpected layouts confuse you."
            ),
            "techie": (
                "You are a power user comfortable with developer tools, keyboard shortcuts, "
                "and dense UIs; you probe edge cases and unusual flows."
            ),
        }
    )
    demographic_labels: dict[str, str] = field(
        default_factory=lambda: {
            "child": "Child",
            "adult": "Adult",
            "senior": "Senior",
            "techie": "Techie",
        }
    )
    default_demographic: str = "adult"
    account_keywords: tuple[str, ...] = (
        "account",
        "accounts",
        "sign up",
        "signup",
        "sign-up",
        "sign in",
        "signin",
        "sign-in",
        "log in",
        "login",
        "log-in",
        "register",
        "registration",
    )


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
    # Socket-level timeouts/retries for the boto3 bedrock-runtime client. Without
    # these, a stalled connection hangs the calling thread forever (boto3's
    # defaults are a 60s connect timeout but an unbounded read, plus retries).
    # read_timeout is supplied per-call from the role's timeout_s; these are the
    # connect ceiling and retry cap that apply to every call.
    bedrock_connect_timeout_s: float = 10.0
    bedrock_max_attempts: int = 2

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
    direct_video_max_length: timedelta = timedelta(hours=1)
    # Ordered fallback tiers for the YouTube results `sp` duration filter:
    # no filter -> short (<4 min) -> medium (4-20 min). Each fetch is appended
    # until the requested page is filled, so long-stream-heavy queries still
    # surface short videos buried below them.
    search_length_filter_sps: tuple = (None, "EgIYAQ==", "EgIYAw==")
    test_video_id: str = "3G4cwFIh_Ns"
    upload_allowed_extensions: tuple = ("mp3", "mp4", "m4a")
    upload_transcode_format: str = "mp4"
    upload_transcode_bitrate: str = "128k"
    download_progress_poll_interval_s: float = 0.3
    # TTL for the Redis download-progress record. Outlives a normal download so
    # the SSE client (possibly on another gunicorn worker) can read it; expires
    # on its own if a download dies without clearing the key.
    download_progress_ttl_s: int = 3600
    download_progress_redis_prefix: str = "nabicat:tubio:progress:"
    youtube_403_fallback_player_client: str = "web"
    youtube_watch_url_template: str = "https://www.youtube.com/watch?v={video_id}"
    youtube_mix_url_template: str = "https://www.youtube.com/watch?v={video_id}&list=RD{video_id}"
    youtube_thumbnail_url_template: str = "https://i.ytimg.com/vi/{video_id}/mqdefault.jpg"
    youtube_search_url: str = "https://www.youtube.com/results"
    youtube_url_patterns: tuple[str, ...] = (
        r'(?:https?://)?(?:www\.)?youtube\.com/watch\?v=([a-zA-Z0-9_-]{11})',
        r'(?:https?://)?(?:www\.)?youtube\.com/shorts/([a-zA-Z0-9_-]{11})',
        r'(?:https?://)?youtu\.be/([a-zA-Z0-9_-]{11})',
        r'(?:https?://)?(?:www\.)?youtube\.com/embed/([a-zA-Z0-9_-]{11})',
    )
    youtube_search_request_timeout_s: float = 10.0
    youtube_thumbnail_request_timeout_s: float = 10.0
    cookie_keepalive_url: str = "https://www.youtube.com/feed/subscriptions"
    cookie_keepalive_timeout_s: float = 30.0
    cookie_keepalive_user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
    youtube_download_format: str = "bestaudio[ext=m4a]/bestaudio/best"
    youtube_audio_preferred_codec: str = "m4a"
    youtube_audio_preferred_quality: str = "32"
    default_playlist_name: str = "Favourites"
    trackbar_volume_min_percent: int = 0
    trackbar_volume_max_percent: int = 100
    trackbar_volume_step_percent: int = 1
    trackbar_default_volume_percent: int = 80
    trackbar_volume_storage_key: str = "tubio.volume"
    trackbar_muted_storage_key: str = "tubio.muted"
    sidebar_collapsed_storage_key: str = "tubioSidebarCollapsed"
    sidebar_selected_storage_key: str = "tubioSelectedPlaylist"
    autocomplete_max_suggestions: int = 8
    autocomplete_min_query_len: int = 2
    autocomplete_debounce_ms: int = 200
    autocomplete_suggest_url: str = "https://suggestqueries.google.com/complete/search"
    autocomplete_request_timeout_s: float = 3.0
    surprise_mix_entries_per_seed: int = 15
    # Number of Surprise metadata entries kept ready ahead of playback.
    surprise_buffer_size: int = 5
    surprise_grow_batch_size: int = 1
    surprise_playlist_name: str = "Surprise Playlist"
    surprise_playlist_storage_key: str = "__surprise_playlist__"
    surprise_playlist_inactivity_ttl_s: int = 3600
    surprise_crc_collision_attempts: int = 100
    surprise_media_rate_limit: str = "30 per minute"
    playlist_create_rate_limit: str = "10 per minute"
    playlist_move_rate_limit: str = "20 per minute"
    playlist_delete_rate_limit: str = "10 per minute"
    upload_rate_limit: str = "20 per minute"
    audio_serve_rate_limit: str = "100 per second"
    resync_rate_limit: str = "5 per minute"
    client_log_rate_limit: str = "30 per minute"
    client_log_max_length: int = 2000
    client_log_scopes: tuple[str, ...] = (
        "discover-initialize",
        "media-element",
        "media-session-metadata",
        "playback-request",
        "surprise-payload",
        "surprise-refresh",
        "track-prefetch",
    )
    surprise_cache_claim_ttl_s: int = 3600
    surprise_cache_claim_token_bytes: int = 12
    surprise_cache_poll_interval_ms: int = 750
    surprise_cache_redis_prefix: str = "nabicat:tubio:cache:"

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
class GPTActionsConfig:
    authorization_code_ttl_s: int = 600
    access_token_ttl_s: int = 3600
    refresh_token_ttl_s: int = 30 * 24 * 60 * 60
    consent_ttl_s: int = 365 * 24 * 60 * 60
    read_scope: str = "todoist.goals.read"
    default_page_size: int = 50
    max_page_size: int = 100
    idempotency_ttl_s: int = 24 * 60 * 60
    idempotency_pending_ttl_s: int = 60
    idempotency_key_max_length: int = 200

    @property
    def client_id(self) -> str:
        return getenv("OAUTH_CLIENT_ID", "")

    @property
    def client_secret(self) -> str:
        return getenv("OAUTH_CLIENT_SECRET", "")

    @property
    def client_secret_hash(self) -> str:
        return getenv("OAUTH_CLIENT_SECRET_HASH", "")

    @property
    def redirect_uris(self) -> tuple[str, ...]:
        return tuple(
            uri.strip()
            for uri in getenv("OAUTH_REDIRECT_URIS", "").split(",")
            if uri.strip()
        )


@dataclass
class DevConfig:
    terminal_shell: str = "/bin/bash"
    terminal_max_sessions: int = 4
    terminal_idle_timeout_s: int = 1800
    terminal_buffer_bytes: int = 1_048_576
    terminal_read_chunk: int = 4096
    log_relative_path: Path = Path("logs/web_app.log")
    log_rotation_max_bytes: int = 5_000_000
    log_rotation_backup_count: int = 20
    log_viewer_file_count: int = 2
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
class LoftConfig:
    request_path_prefix: str = "/loft/"
    non_admin_quota_bytes: int = 50 * 1024 * 1024
    admin_quota_bytes: int = 10 * 1024 * 1024 * 1024
    gallery_max_files_per_upload: int = 20
    gallery_max_videos_per_upload: int = 5
    gallery_media_filename_max_chars: int = 100
    gallery_upload_stream_chunk_bytes: int = 1024 * 1024
    gallery_upload_max_total_bytes: int = 250 * 1024 * 1024
    gallery_request_max_bytes: int = 252 * 1024 * 1024
    gallery_quota_lock_timeout_s: int = 30
    gallery_quota_lock_blocking_timeout_s: float = 10.0
    gallery_staging_root: Path | None = None
    gallery_staging_dirname: str = "loft-gallery-upload-staging"
    gallery_staging_dir_mode: int = 0o700
    gallery_staging_max_age_s: int = 3600
    gallery_publish_journal_prefix: str = ".gallery-publish-"
    gallery_publish_journal_suffix: str = ".json"
    gallery_backup_excluded_names: tuple[str, ...] = (
        ".gallery-upload-staging",
    )
    gallery_image_max_upload_bytes: int = 25 * 1024 * 1024
    gallery_image_allowed_formats: tuple[str, ...] = (
        "AVIF",
        "BMP",
        "GIF",
        "HEIF",
        "JPEG",
        "MPO",
        "PNG",
        "TIFF",
        "WEBP",
    )
    gallery_thumb_max_px: int = 1400
    gallery_thumb_quality: int = 80
    max_image_pixels: int = 40_000_000
    gallery_image_max_batch_pixels: int = 80_000_000
    gallery_video_max_upload_bytes: int = 100 * 1024 * 1024
    gallery_video_max_duration_s: int = 60
    gallery_video_allowed_demuxers: tuple[str, ...] = (
        "avi",
        "matroska",
        "mov",
        "webm",
    )
    gallery_video_protocol_whitelist: str = "file"
    gallery_video_probe_size_bytes: int = 10 * 1024 * 1024
    gallery_video_analyze_duration_us: int = 10 * 1_000_000
    gallery_video_probe_timeout_s: int = 20
    gallery_video_max_input_width_px: int = 8192
    gallery_video_max_input_height_px: int = 8192
    gallery_video_max_input_pixels: int = 40_000_000
    gallery_video_max_input_fps: int = 240
    gallery_video_max_width_px: int = 1280
    gallery_video_max_height_px: int = 720
    gallery_video_max_output_fps: int = 60
    gallery_video_max_output_bytes: int = 100 * 1024 * 1024
    gallery_video_duration_tolerance_s: float = 0.5
    gallery_video_ffmpeg_threads: int = 2
    gallery_video_ffmpeg_max_alloc_bytes: int = 512 * 1024 * 1024
    gallery_video_max_muxing_queue_packets: int = 1024
    gallery_video_h264_preset: str = "veryfast"
    gallery_video_h264_crf: int = 28
    gallery_video_h264_profile: str = "high"
    gallery_video_h264_level: str = "3.2"
    gallery_video_output_encoder: str = "libx264"
    gallery_video_output_codec: str = "h264"
    gallery_video_output_codec_tag: str = "avc1"
    gallery_video_output_pixel_format: str = "yuv420p"
    gallery_video_output_audio_codec: str = "aac"
    gallery_video_output_audio_encoder: str = "aac"
    gallery_video_output_audio_codec_tag: str = "mp4a"
    gallery_video_output_audio_profile: str = "aac_low"
    gallery_video_output_color_space: str = "bt709"
    gallery_video_output_color_range: str = "tv"
    gallery_video_sd_input_color_space: str = "smpte170m"
    gallery_video_hd_input_color_space: str = "bt709"
    gallery_video_sd_max_height_px: int = 576
    gallery_video_output_format: str = "mp4"
    gallery_video_output_demuxer: str = "mov"
    gallery_video_audio_bitrate: str = "96k"
    gallery_video_audio_channels: int = 2
    gallery_video_audio_sample_rate_hz: int = 48_000
    gallery_video_hdr_peak_nits: int = 100
    gallery_video_hdr_desaturation: float = 0.5
    gallery_video_hdr_tonemap_algorithm: str = "mobius"
    gallery_video_hdr_transfers: tuple[str, ...] = (
        "arib-std-b67",
        "smpte2084",
    )
    gallery_video_private_metadata_fragments: tuple[str, ...] = (
        "artist",
        "comment",
        "copyright",
        "creation_time",
        "description",
        "device",
        "gps",
        "location",
        "make",
        "model",
        "title",
    )
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
    upload_stream_chunk_bytes: int = 1024 * 1024
    folder_upload_max_entries: int = 10_000
    archive_stream_queue_chunks: int = 8
    thumbnail_load_stagger_ms: int = 200
    thumbnail_load_max_retries: int = 3
    thumbnail_retry_delay_ms: int = 1_000
    gallery_columns_min: int = 2
    gallery_columns_max: int = 10
    gallery_columns_default: int = 5
    gallery_min_tile_px: int = 40


@dataclass
class ProxyConfig:
    request_timeout_s: int = 10
    max_redirects: int = 5
    response_max_bytes: int = 5 * 1024 * 1024
    response_read_chunk_bytes: int = 64 * 1024
    redirect_status_codes: tuple[int, ...] = (301, 302, 303, 307, 308)
    allowed_content_types: tuple[str, ...] = (
        "text/html",
        "text/plain",
        "image/avif",
        "image/gif",
        "image/jpeg",
        "image/png",
        "image/webp",
    )
    blocked_metadata_hostnames: tuple[str, ...] = (
        "metadata",
        "metadata.aws.internal",
        "metadata.azure.internal",
        "metadata.google.internal",
    )
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )


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
        self.deno_version = "v2.3.3"
        self.debug_mode = False
        self.production_data_root = Path.home() / ".nabicat" / "data"
        self.youtube_direct_timeout_s = 15
        self.youtube_direct_max_page_bytes = 5 * 1024 * 1024
        self.youtube_direct_max_media_bytes = 20 * 1024 * 1024
        self.youtube_direct_chunk_bytes = 64 * 1024
        self.debug_data_root = Path.home() / ".nabicat_debug" / "data"
        self.server_host = "0.0.0.0"
        self.server_default_port = 80
        self.session_cookie_name = "session"
        self.debug_session_cookie_name = "session_debug"
        self.site_url = getenv("SITE_URL") or "https://nabicat.site"
        self.redis_url = getenv("REDIS_URL") or "redis://127.0.0.1:6379/0"
        self.redis_readiness_timeout_s = 5.0
        self.redis_readiness_poll_s = 0.1
        self.password_hash_method = "scrypt"
        self.password_hash_prefix = "nabicat$"
        self.api_llm_timeout_s = 120.0
        self.api_llm_client_timeout_s = 130.0
        self.api_llm_approval_policy = "never"
        self.api_llm_sandbox = "read-only"
        self.gunicorn_workers = 4
        self.gunicorn_request_timeout_s = 720
        self.gunicorn_graceful_timeout_s = 720
        self.deployment_canary_port = 5001
        self.deployment_health_attempts = 30
        self.deployment_health_interval_s = 1
        self.deployment_lock_path = Path.home() / ".nabicat" / "update.lock"
        self.scheduled_job_service_unit_name = "nabicat-scheduled-job@.service"
        self.scheduled_job_timeout_s = 3600
        self.scheduled_backup_job_id = "backup"
        self.scheduled_cookie_keepalive_job_id = "cookie-keepalive"
        self.scheduled_download_health_check_job_id = "download-health-check"
        self.scheduled_job_timers = (
            (
                "nabicat-backup.timer",
                self.scheduled_backup_job_id,
                "Sun *-*-* 00:00:00",
            ),
            (
                "nabicat-cookie-keepalive.timer",
                self.scheduled_cookie_keepalive_job_id,
                "*-*-* 04:00:00",
            ),
            (
                "nabicat-download-health-check.timer",
                self.scheduled_download_health_check_job_id,
                "*-*-* 04:10:00",
            ),
        )
        self.log_format = (
            "%(asctime)s %(levelname)s worker=%(process)d "
            "thread=%(thread)d %(message)s"
        )
        self.request_id_header = "X-Request-ID"
        self.request_log_warning_status = 400
        self.request_log_error_status = 500
        # rmw_lock lease TTL, acquisition deadline, and renewal cadence. Active
        # holders renew; crashed holders expire after the TTL.
        self.rmw_lock_timeout_s = 10
        self.rmw_lock_blocking_timeout_s = 5.0
        self.rmw_lock_renewal_interval_s = 3.0
        self.app_data_file_mode = 0o600
        self.app_user_folder_pattern = r"[a-z0-9][a-z0-9._-]*"
        self.data_sync_bucket_name = "todoist"
        self.atomic_write_file_mode = 0o644
        self.atomic_write_chunk_size = 1024 * 1024
        self.random_string_length = 10
        self.random_generation_attempts = 100
        self.career_ops_repository = "https://github.com/santifer/career-ops.git"
        self.career_ops_revision = "fdda56138988873022bf0e1b42baa1fdef665494"
        self.backup_max_count = 8
        self.jswipe_request_path_prefix = "/jswipe/"
        self.jswipe_multipart_request_max_bytes = 6 * 1024 * 1024
        self.production_sync_excluded_paths = (
            "backups/",
            "data/logs/",
        )
        # Unmatched paths containing these segments are high-confidence
        # vulnerability probes. They receive a direct 404 without generic
        # request lifecycle logs; ordinary unknown URLs remain logged.
        self.scanner_path_segment_names = frozenset({
            ".aws",
            ".env",
            ".git",
            ".mist",
            ".ssh",
            "actuator",
            "dns-query",
            "eval-stdin.php",
            "phpunit",
            "xmlrpc",
            "xmlrpc.php",
        })
        self.scanner_path_segment_prefixes = (
            ".env.",
            "phpmyadmin",
            "wp-",
        )
        self.scanner_methods = frozenset({"PROPFIND", "TRACK", "TRACE"})
        self.request_log_suppressed_paths = {
            '/dev/terminal/input',
            '/dev/terminal/output',
        }
        self.cache_max_age = 606461 # Default cache max age (1 week) in seconds, can be overridden by environment variable
        self.cache_browser_max_size_bytes = 10 * 1024 * 1024 * 1024
        self.cache_service_worker_version = "v2"
        self.cache_service_worker_prefix = "nabicat-cache-"
        self.cache_versioned_static_path_prefixes = (
            "/static/",
            "/crosswords/static/",
            "/dev/static/",
            "/file_store/static/",
            "/loft/static/",
            "/jswipe/static/",
            "/sentinel/static/",
            "/metrics/static/",
            "/proxy/static/",
            "/simulations/static/",
            "/todoist/static/",
            "/tubio/static/",
        )
        self.cache_public_media_path_prefixes = (
            "/tubio/audio/",
            "/tubio/thumbnail/",
        )
        self.cache_service_worker_ready_timeout_ms = 5000
        self.cache_service_worker_message_timeout_ms = 5000
        self.cache_public_media_endpoints = frozenset({
            "tubio.serve_audio",
            "tubio.serve_thumbnail",
        })
        self.git_command_timeout_s = 2
        self.ytdlp_pypi_url = "https://pypi.org/pypi/yt-dlp/json"
        self.ytdlp_update_timeout_s = 10.0
        self.access_denied_redirect_endpoint = "home"
        self.elevated_access_denied_message = "You need elevated access to use this app."
        self.admin_access_denied_message = "You need admin access to use this app."
        self.dev_access_denied_api_prefixes = ("/dev/logs", "/dev/map-data", "/dev/terminal/")
        self.smtp_port = 587
        self.project_dir = Path.cwd()
        # TTL for the ephemeral RSA keypair minted during the encrypted-request
        # handshake. Surfaced to clients as `expires_in` in /api/handshake.
        self.ephemeral_key_ttl_s = 300
        self.jswipe = JswipeConfig()
        self.sentinel = SentinelConfig()
        self.llm = LLMConfig()
        self.tubio = TubioConfig(lambda: self.save_data_path)
        self.todoist = TodoistConfig()
        self.gpt_actions = GPTActionsConfig()
        self.dev = DevConfig()
        self.crosswords = CrosswordsConfig()
        self.loft = LoftConfig()
        self.file_store = FileStoreConfig()
        self.proxy = ProxyConfig()

    @property
    def project_name(self) -> str:
        return "nabicat" if not self.debug_mode else "nabicat_debug"

    @property
    def save_data_path(self) -> Path:
        return self.debug_data_root if self.debug_mode else self.production_data_root
    
    @property
    def temp_dir(self) -> Path:
        return self.save_data_path / "temp"

    @property
    def log_file_path(self) -> Path:
        return self.save_data_path / self.dev.log_relative_path

    @property
    def flask_secret_key(self) -> str:
        key = getenv('FLASK_SECRET_KEY')
        if key:
            return key

        if self.debug_mode:
            return "DEBUG_FLASK_SECRET_KEY"

        raise ValueError("Flask secret key is not set. Please set the 'FLASK_SECRET_KEY' environment variable.")

    @property
    def flask_session_cookie_name(self) -> str:
        return (
            self.debug_session_cookie_name
            if self.debug_mode
            else self.session_cookie_name
        )

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
