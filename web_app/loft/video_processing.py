from __future__ import annotations

import json
import logging
import math
import os
import subprocess
from fractions import Fraction
from pathlib import Path

from pydantic import BaseModel, Field

from web_app.config import ConfigManager
from web_app.errors import APIError
from web_app.logging_utils import log_event


class VideoInfo(BaseModel):
    duration: float | None = None
    video_index: int
    audio_index: int | None = None
    format_name: str = ""
    video_codec: str = ""
    video_codec_tag: str | None = None
    audio_codec: str | None = None
    audio_codec_tag: str | None = None
    audio_sample_rate: int | None = None
    audio_channels: int | None = None
    width: int = 0
    height: int = 0
    fps: float | None = None
    pixel_format: str | None = None
    sample_aspect_ratio: str | None = None
    rotation: float | None = None
    color_transfer: str | None = None
    color_primaries: str | None = None
    color_space: str | None = None
    is_hdr: bool = False
    video_stream_count: int = 1
    audio_stream_count: int = 0
    other_stream_count: int = 0
    chapter_count: int = 0
    private_metadata_keys: list[str] = Field(default_factory=list)



class GalleryVideoProcessor:
    """Validate and normalize uploaded gallery video using configured media limits."""

    @staticmethod
    def _run_media_command(cmd: list[str], timeout_s: int, error_message: str) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s, check=True)
        except FileNotFoundError as e:
            raise APIError("Video processing requires ffmpeg", reason="video_processing_requires_ffmpeg") from e
        except subprocess.TimeoutExpired as e:
            raise APIError(error_message, reason="media_processing_failed") from e
        except subprocess.CalledProcessError as e:
            log_event(
                "loft", "loft.media_command_failed",
                level=logging.WARNING, executable=Path(cmd[0]).name,
                returncode=e.returncode,
            )
            raise APIError(error_message, reason="media_processing_failed") from e

    def _probe_video_info(self, src: Path, display_name: str) -> VideoInfo:
        cfg = ConfigManager()
        result = self._run_media_command(
            [
                "ffprobe",
                "-hide_banner",
                "-v", "error",
                "-max_alloc", str(cfg.loft.gallery_video_ffmpeg_max_alloc_bytes),
                "-protocol_whitelist", cfg.loft.gallery_video_protocol_whitelist,
                "-format_whitelist", ",".join(cfg.loft.gallery_video_allowed_demuxers),
                "-probesize", str(cfg.loft.gallery_video_probe_size_bytes),
                "-analyzeduration", str(cfg.loft.gallery_video_analyze_duration_us),
                "-max_pixels", str(cfg.loft.gallery_video_max_input_pixels),
                "-threads", str(cfg.loft.gallery_video_ffmpeg_threads),
                "-show_entries",
                (
                    "format=format_name,duration:"
                    "stream=index,codec_type,codec_name,codec_tag_string,duration,"
                    "duration_ts,time_base,width,height,avg_frame_rate,r_frame_rate,"
                    "pix_fmt,sample_aspect_ratio,sample_rate,channels,color_space,"
                    "color_transfer,color_primaries:"
                    "stream_disposition=default,attached_pic,timed_thumbnails,"
                    "metadata,dependent,still_image:"
                    "stream_tags:"
                    "stream_side_data=side_data_type,rotation:"
                    "format_tags:"
                    "chapter=id"
                ),
                "-of", "json",
                str(src),
            ],
            cfg.loft.gallery_video_probe_timeout_s,
            f"Could not process {display_name} as a video",
        )
        try:
            payload = json.loads(result.stdout or "{}")
        except json.JSONDecodeError as e:
            raise APIError(f"Could not process {display_name} as a video", reason="could_not_process_as_a_video") from e
        streams = payload.get("streams", [])
        format_name = str(payload.get("format", {}).get("format_name") or "")
        detected_demuxers = set(format_name.split(","))
        if not detected_demuxers.intersection(
            cfg.loft.gallery_video_allowed_demuxers
        ):
            raise APIError(
                f"Unsupported video container for {display_name}",
                reason="unsupported_video_container_for",
            )

        invalid_codecs = {None, "", "none", "unknown", "bin_data"}

        def disposition_enabled(stream: dict, name: str) -> bool:
            try:
                return bool(int(stream.get("disposition", {}).get(name, 0)))
            except (TypeError, ValueError):
                return False

        video_candidates = [
            stream
            for stream in streams
            if stream.get("codec_type") == "video"
            and stream.get("codec_name") not in invalid_codecs
            and self._positive_int(stream.get("width")) is not None
            and self._positive_int(stream.get("height")) is not None
            and not any(
                disposition_enabled(stream, flag)
                for flag in (
                    "attached_pic",
                    "timed_thumbnails",
                    "metadata",
                    "dependent",
                    "still_image",
                )
            )
        ]
        video = max(
            video_candidates,
            key=lambda stream: (
                disposition_enabled(stream, "default"),
                int(stream["width"]) * int(stream["height"]),
                -int(stream["index"]),
            ),
            default=None,
        )
        if video is None:
            raise APIError(f"Video {display_name} has no valid video stream", reason="video_has_no_valid_video_stream")

        audio_candidates = [
            stream
            for stream in streams
            if stream.get("codec_type") == "audio"
            and stream.get("codec_name") not in invalid_codecs
            and str(stream.get("codec_tag_string") or "").lower() != "mebx"
            and self._positive_int(stream.get("sample_rate")) is not None
            and self._positive_int(stream.get("channels")) is not None
            and not any(
                disposition_enabled(stream, flag)
                for flag in ("metadata", "dependent")
            )
        ]
        audio = max(
            audio_candidates,
            key=lambda stream: (
                disposition_enabled(stream, "default"),
                -int(stream["index"]),
            ),
            default=None,
        )

        duration_candidates = [payload.get("format", {}).get("duration")]
        for selected_stream in (video, audio):
            if selected_stream is None:
                continue
            duration_candidates.extend(
                [
                    selected_stream.get("duration"),
                    selected_stream.get("tags", {}).get("DURATION"),
                    self._duration_from_time_base(selected_stream),
                ]
            )
        parsed_durations = [
            duration
            for duration in (
                self._parse_duration(candidate)
                for candidate in duration_candidates
            )
            if duration is not None
        ]
        duration = max(parsed_durations, default=None)

        frame_rates = [
            rate
            for rate in (
                self._frame_rate(video.get("avg_frame_rate")),
                self._frame_rate(video.get("r_frame_rate")),
            )
            if rate is not None
        ]
        fps = max(frame_rates, default=None)
        width = int(video["width"])
        height = int(video["height"])
        transfer = str(video.get("color_transfer") or "") or None
        side_data = video.get("side_data_list", [])
        rotation = None
        for rotation_candidate in [
            *(item.get("rotation") for item in side_data),
            video.get("tags", {}).get("rotate"),
        ]:
            try:
                parsed_rotation = float(rotation_candidate)
            except (TypeError, ValueError):
                continue
            if math.isfinite(parsed_rotation):
                rotation = parsed_rotation
                break
        has_dolby_vision = any(
            "dovi" in str(item.get("side_data_type") or "").lower()
            or "dolby vision" in str(item.get("side_data_type") or "").lower()
            for item in side_data
        )
        is_hdr = (
            transfer in cfg.loft.gallery_video_hdr_transfers
            or has_dolby_vision
        )

        video_stream_count = sum(
            stream.get("codec_type") == "video" for stream in streams
        )
        audio_stream_count = sum(
            stream.get("codec_type") == "audio" for stream in streams
        )
        metadata_keys = {
            str(key).lower()
            for tags in [
                payload.get("format", {}).get("tags", {}),
                video.get("tags", {}),
                audio.get("tags", {}) if audio is not None else {},
            ]
            for key in tags
        }
        private_metadata_keys = sorted(
            key
            for key in metadata_keys
            if any(
                fragment in key
                for fragment in cfg.loft.gallery_video_private_metadata_fragments
            )
        )
        return VideoInfo(
            duration=duration,
            video_index=int(video["index"]),
            audio_index=int(audio["index"]) if audio is not None else None,
            format_name=format_name,
            video_codec=str(video.get("codec_name") or ""),
            video_codec_tag=(
                str(video.get("codec_tag_string") or "") or None
            ),
            audio_codec=(
                str(audio.get("codec_name") or "") if audio is not None else None
            ),
            audio_codec_tag=(
                str(audio.get("codec_tag_string") or "")
                if audio is not None
                else None
            ),
            audio_sample_rate=(
                int(audio["sample_rate"]) if audio is not None else None
            ),
            audio_channels=(
                int(audio["channels"]) if audio is not None else None
            ),
            width=width,
            height=height,
            fps=fps,
            pixel_format=str(video.get("pix_fmt") or "") or None,
            sample_aspect_ratio=(
                str(video.get("sample_aspect_ratio") or "") or None
            ),
            rotation=rotation,
            color_transfer=transfer,
            color_primaries=(
                str(video.get("color_primaries") or "") or None
            ),
            color_space=str(video.get("color_space") or "") or None,
            is_hdr=is_hdr,
            video_stream_count=video_stream_count,
            audio_stream_count=audio_stream_count,
            other_stream_count=(
                len(streams) - video_stream_count - audio_stream_count
            ),
            chapter_count=len(payload.get("chapters", [])),
            private_metadata_keys=private_metadata_keys,
        )

    @staticmethod
    def _positive_int(value) -> int | None:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    @staticmethod
    def _positive_float(value) -> float | None:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        return parsed if math.isfinite(parsed) and parsed > 0 else None

    @classmethod
    def _parse_duration(cls, value) -> float | None:
        parsed = cls._positive_float(value)
        if parsed is not None or not isinstance(value, str):
            return parsed
        parts = value.split(":")
        if len(parts) != 3:
            return None
        hours = cls._positive_float(parts[0]) or 0
        minutes = cls._positive_float(parts[1]) or 0
        seconds = cls._positive_float(parts[2]) or 0
        total = hours * 3600 + minutes * 60 + seconds
        return total if total > 0 else None

    @classmethod
    def _duration_from_time_base(cls, stream: dict) -> float | None:
        duration_ts = cls._positive_float(stream.get("duration_ts"))
        time_base = cls._frame_rate(stream.get("time_base"))
        if duration_ts is None or time_base is None:
            return None
        return duration_ts * time_base

    @staticmethod
    def _frame_rate(value) -> float | None:
        if value in (None, "", "0/0", "N/A"):
            return None
        try:
            parsed = float(Fraction(str(value)))
        except (ValueError, ZeroDivisionError):
            return None
        return parsed if math.isfinite(parsed) and parsed > 0 else None

    def _probe_video_duration(self, src: Path, display_name: str) -> float | None:
        return self._probe_video_info(src, display_name).duration

    def _validate_video(self, src: Path, display_name: str) -> VideoInfo:
        cfg = ConfigManager()
        info = self._probe_video_info(src, display_name)
        if info.duration is None:
            raise APIError(
                f"Video {display_name} has no reliable duration metadata",
                reason="video_has_no_reliable_duration_metadata",
            )
        if info.duration > cfg.loft.gallery_video_max_duration_s:
            raise APIError(
                f"Video {display_name} is too long "
                f"(max {cfg.loft.gallery_video_max_duration_s} seconds)",
                reason="video_duration_exceeded",
            )
        if (
            info.width > cfg.loft.gallery_video_max_input_width_px
            or info.height > cfg.loft.gallery_video_max_input_height_px
            or info.width * info.height
            > cfg.loft.gallery_video_max_input_pixels
        ):
            raise APIError(f"Video {display_name} dimensions are too large", reason="video_dimensions_are_too_large")
        if (
            info.fps is not None
            and info.fps > cfg.loft.gallery_video_max_input_fps
        ):
            raise APIError(f"Video {display_name} frame rate is too high", reason="video_frame_rate_is_too_high")
        return info

    def _validate_normalized_video(
        self,
        src: Path,
        display_name: str,
    ) -> VideoInfo:
        cfg = ConfigManager().loft
        try:
            output_bytes = src.stat().st_size
        except OSError as error:
            raise APIError(
                f"Could not inspect normalized video {display_name}",
                reason="could_not_inspect_normalized_video",
            ) from error
        if output_bytes > cfg.gallery_video_max_output_bytes:
            raise APIError(
                f"Normalized video {display_name} exceeds the output size limit",
                reason="normalized_video_exceeds_the_output_size_limit",
            )
        info = self._probe_video_info(src, display_name)
        if info.duration is None:
            raise APIError(
                f"Normalized video {display_name} has no reliable duration",
                reason="normalized_video_has_no_reliable_duration",
            )
        if (
            info.duration
            > cfg.gallery_video_max_duration_s
            + cfg.gallery_video_duration_tolerance_s
        ):
            raise APIError(
                f"Normalized video {display_name} is too long",
                reason="video_duration_exceeded",
            )
        if cfg.gallery_video_output_demuxer not in set(
            info.format_name.split(",")
        ):
            raise APIError(f"Normalized video {display_name} is not MP4", reason="normalized_video_is_not_mp4")
        if (
            info.video_codec != cfg.gallery_video_output_codec
            or info.video_codec_tag != cfg.gallery_video_output_codec_tag
            or info.pixel_format != cfg.gallery_video_output_pixel_format
        ):
            raise APIError(
                f"Normalized video {display_name} is not browser-safe H.264",
                reason="normalized_video_is_not_browser_safe_h_264",
            )
        if info.width % 2 or info.height % 2:
            raise APIError(
                f"Normalized video {display_name} has invalid dimensions",
                reason="normalized_video_has_invalid_dimensions",
            )
        if info.sample_aspect_ratio not in (None, "0:1", "1:1"):
            raise APIError(
                f"Normalized video {display_name} has invalid aspect ratio",
                reason="normalized_video_has_invalid_aspect_ratio",
            )
        if info.rotation not in (None, 0):
            raise APIError(
                f"Normalized video {display_name} retains rotation metadata",
                reason="normalized_video_retains_rotation_metadata",
            )
        if (
            info.width > cfg.gallery_video_max_width_px
            or info.height > cfg.gallery_video_max_height_px
            or info.width <= 0
            or info.height <= 0
        ):
            raise APIError(
                f"Normalized video {display_name} exceeds the output size limit",
                reason="normalized_video_exceeds_the_output_size_limit",
            )
        if (
            info.fps is not None
            and info.fps > cfg.gallery_video_max_output_fps
        ):
            raise APIError(
                f"Normalized video {display_name} exceeds the frame rate limit",
                reason="normalized_video_exceeds_the_frame_rate_limit",
            )
        if any(
            value != cfg.gallery_video_output_color_space
            for value in (
                info.color_space,
                info.color_transfer,
                info.color_primaries,
            )
        ):
            raise APIError(
                f"Normalized video {display_name} is not tagged as BT.709",
                reason="normalized_video_is_not_tagged_as_bt_709",
            )
        if (
            info.audio_index is not None
            and (
                info.audio_codec != cfg.gallery_video_output_audio_codec
                or info.audio_codec_tag
                != cfg.gallery_video_output_audio_codec_tag
            )
        ):
            raise APIError(
                f"Normalized video {display_name} does not use AAC audio",
                reason="normalized_video_does_not_use_aac_audio",
            )
        if info.audio_index is not None and (
            info.audio_sample_rate
            != cfg.gallery_video_audio_sample_rate_hz
            or info.audio_channels > cfg.gallery_video_audio_channels
        ):
            raise APIError(
                f"Normalized video {display_name} has incompatible audio",
                reason="normalized_video_has_incompatible_audio",
            )
        if (
            info.video_stream_count != 1
            or info.audio_stream_count > 1
            or info.other_stream_count
            or info.chapter_count
        ):
            raise APIError(
                f"Normalized video {display_name} contains unexpected streams",
                reason="normalized_video_contains_unexpected_streams",
            )
        if info.private_metadata_keys:
            raise APIError(
                f"Normalized video {display_name} retains private metadata",
                reason="normalized_video_retains_private_metadata",
            )
        if not self._mp4_has_faststart(src):
            raise APIError(
                f"Normalized video {display_name} is not optimized for streaming",
                reason="normalized_video_is_not_optimized_for_streaming",
            )
        return info

    @staticmethod
    def _mp4_has_faststart(path: Path) -> bool:
        moov_offset = None
        mdat_offset = None
        try:
            with path.open("rb") as media:
                offset = 0
                while True:
                    header = media.read(8)
                    if len(header) != 8:
                        break
                    size = int.from_bytes(header[:4], "big")
                    atom_type = header[4:8]
                    header_size = 8
                    if size == 1:
                        extended_size = media.read(8)
                        if len(extended_size) != 8:
                            return False
                        size = int.from_bytes(extended_size, "big")
                        header_size = 16
                    elif size == 0:
                        media.seek(0, os.SEEK_END)
                        size = media.tell() - offset
                    if size < header_size:
                        return False
                    if atom_type == b"moov":
                        moov_offset = offset
                    elif atom_type == b"mdat":
                        mdat_offset = offset
                    if moov_offset is not None and mdat_offset is not None:
                        return moov_offset < mdat_offset
                    media.seek(offset + size)
                    offset += size
        except OSError:
            return False
        return False

    def _transcode_video(
        self,
        src: Path,
        dst: Path,
        display_name: str,
        info: VideoInfo,
    ) -> None:
        cfg = ConfigManager()
        max_width = cfg.loft.gallery_video_max_width_px
        max_height = cfg.loft.gallery_video_max_height_px
        filters = [
            (
                f"scale='max(2,trunc(iw*min(1,min({max_width}/iw,"
                f"{max_height}/ih))/2)*2)':"
                f"'max(2,trunc(ih*min(1,min({max_width}/iw,"
                f"{max_height}/ih))/2)*2)'"
            )
        ]
        if info.is_hdr:
            filters = [
                (
                    f"zscale=t=linear:npl="
                    f"{cfg.loft.gallery_video_hdr_peak_nits}"
                ),
                "format=gbrpf32le",
                (
                    f"tonemap={cfg.loft.gallery_video_hdr_tonemap_algorithm}:"
                    f"desat="
                    f"{cfg.loft.gallery_video_hdr_desaturation}"
                ),
                (
                    f"zscale=p={cfg.loft.gallery_video_output_color_space}:"
                    f"t={cfg.loft.gallery_video_output_color_space}:"
                    f"m={cfg.loft.gallery_video_output_color_space}:"
                    f"r={cfg.loft.gallery_video_output_color_range}"
                ),
            ] + filters
        elif any(
            value != cfg.loft.gallery_video_output_color_space
            for value in (
                info.color_space,
                info.color_transfer,
                info.color_primaries,
            )
        ):
            color_options = [
                f"all={cfg.loft.gallery_video_output_color_space}",
                f"format={cfg.loft.gallery_video_output_pixel_format}",
            ]
            if not all(
                (
                    info.color_space,
                    info.color_transfer,
                    info.color_primaries,
                )
            ):
                fallback = (
                    cfg.loft.gallery_video_sd_input_color_space
                    if info.height
                    <= cfg.loft.gallery_video_sd_max_height_px
                    else cfg.loft.gallery_video_hd_input_color_space
                )
                color_options.append(f"iall={fallback}")
            filters = [
                f"colorspace={':'.join(color_options)}"
            ] + filters
        filters.extend(
            [
                "setsar=1",
                f"format={cfg.loft.gallery_video_output_pixel_format}",
            ]
        )
        vf = ",".join(filters)
        cmd = [
            "ffmpeg",
            "-y",
            "-nostdin",
            "-hide_banner",
            "-loglevel", "error",
            "-max_alloc", str(cfg.loft.gallery_video_ffmpeg_max_alloc_bytes),
            "-protocol_whitelist", cfg.loft.gallery_video_protocol_whitelist,
            "-format_whitelist", ",".join(cfg.loft.gallery_video_allowed_demuxers),
            "-probesize", str(cfg.loft.gallery_video_probe_size_bytes),
            "-analyzeduration", str(cfg.loft.gallery_video_analyze_duration_us),
            "-max_pixels", str(cfg.loft.gallery_video_max_input_pixels),
            "-threads", str(cfg.loft.gallery_video_ffmpeg_threads),
            "-autorotate", "1",
            "-i", str(src),
            "-map", f"0:{info.video_index}",
        ]
        if info.audio_index is None:
            cmd.append("-an")
        else:
            cmd.extend(["-map", f"0:{info.audio_index}"])
        cmd.extend(
            [
                "-dn", "-sn",
                "-map_metadata", "-1",
                "-map_chapters", "-1",
                "-t", str(cfg.loft.gallery_video_max_duration_s),
                "-filter_threads", str(cfg.loft.gallery_video_ffmpeg_threads),
                "-vf", vf,
                "-c:v", cfg.loft.gallery_video_output_encoder,
                "-preset", cfg.loft.gallery_video_h264_preset,
                "-crf", str(cfg.loft.gallery_video_h264_crf),
                "-profile:v", cfg.loft.gallery_video_h264_profile,
                "-level:v", cfg.loft.gallery_video_h264_level,
                "-tag:v", cfg.loft.gallery_video_output_codec_tag,
                "-pix_fmt", cfg.loft.gallery_video_output_pixel_format,
                "-fpsmax", str(cfg.loft.gallery_video_max_output_fps),
                "-metadata:s:v:0", "rotate=0",
                "-color_primaries", cfg.loft.gallery_video_output_color_space,
                "-color_trc", cfg.loft.gallery_video_output_color_space,
                "-colorspace", cfg.loft.gallery_video_output_color_space,
                "-color_range", cfg.loft.gallery_video_output_color_range,
                "-c:a", cfg.loft.gallery_video_output_audio_encoder,
                "-profile:a", cfg.loft.gallery_video_output_audio_profile,
                "-ac", str(cfg.loft.gallery_video_audio_channels),
                "-ar", str(cfg.loft.gallery_video_audio_sample_rate_hz),
                "-b:a", cfg.loft.gallery_video_audio_bitrate,
                "-threads", str(cfg.loft.gallery_video_ffmpeg_threads),
                "-max_muxing_queue_size",
                str(cfg.loft.gallery_video_max_muxing_queue_packets),
                "-abort_on", "empty_output+empty_output_stream",
                "-movflags", "+faststart",
                "-fs", str(cfg.loft.gallery_video_max_output_bytes),
                "-f", cfg.loft.gallery_video_output_format,
                str(dst),
            ]
        )
        self._run_media_command(
            cmd,
            cfg.loft.gallery_video_transcode_timeout_s,
            f"Could not process {display_name} as a video",
        )

