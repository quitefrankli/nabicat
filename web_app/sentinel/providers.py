"""LLM provider plumbing for Sentinel.

Sentinel asks an LLM to do four distinct things during a run:

  - ``agent``        choose the next browser action (multimodal, dynamic system prompt)
  - ``final_report`` write the markdown report at the end of a run
  - ``title``        generate a short run title
  - ``verdict``      decide whether the run actually fulfilled the user's prompt

Each of those is a *role*. A *transport* is the wire-level mechanism for
talking to a particular backend (Codex CLI, Meridian HTTP, AWS Bedrock).
``_Provider`` glues the two together: you ask it for ``role X`` and it
dispatches to the active transport with the role's system prompt,
max_tokens, and timeout.
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from web_app.config import ConfigManager
from web_app.helpers import bedrock_text, codex_cli_text, meridian_text

from .runtime import capped_timeout_seconds

# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

_TITLE_SYSTEM = (
    "You are titling a Sentinel QA run. Given the target URL and the user's prompt, return a single "
    "short, professional title (4 to 8 words) describing the run. Plain text only. No quotes, no "
    "trailing punctuation, no emojis, no em or en dashes. Do not start with words like 'Sentinel' "
    "or 'QA'."
)

_VERDICT_SYSTEM = (
    "You are evaluating whether a completed Sentinel QA run actually fulfilled the user's original "
    "prompt. Given the prompt and a JSON dump of the run (steps, findings, final report), decide if "
    "the run succeeded. Respond with ONLY a single JSON object of the shape "
    '{"verdict":"pass"|"fail","reason":"..."}. '
    'Use "fail" if the agent self-aborted, was blocked, ran out of steps without exercising the '
    "requested flow, or otherwise did not actually verify what the prompt asked for. "
    'Use "pass" only if the run plausibly exercised the requested behavior end-to-end. '
    "Be strict: a clean technical exit does not imply success. The reason must be one short sentence."
)

_SYSTEM_BASE = (
    "You are controlling a browser as a practical human QA tester. "
    "Given the current observation and prior steps, choose exactly one next action. "
    "Your entire response MUST be a single JSON object and nothing else. Do not think out loud. "
    "Do not write commentary, reasoning, alternative options, or text before or after the JSON. "
    "Do not emit multiple JSON candidates. Put any deliberation into the 'reason' field of the "
    "single JSON object you return. Return ONLY JSON with one of these shapes: "
    '{"action":"click","element_id":"e1","reason":"..."}, '
    '{"action":"fill","element_id":"e2","value":"...","reason":"..."}, '
    '{"action":"select","element_id":"e3","value":"Option label","reason":"..."}, '
    '{"action":"goto","url":"/path","reason":"..."}, '
    '{"action":"scroll","value":"down","reason":"..."}, '
    '{"action":"wait","reason":"..."}, '
    '{"action":"peek","reason":"..."}, '
    '{"action":"finish","reason":"..."}. '
    "Use peek when the annotation boxes (e1, e2, ...) are obscuring ui content "
    "you need to read, or when you suspect a clickable element is on the page "
    "but isn't in the elements list. peek does not change the page; on the "
    "next step you will receive both the annotated screenshot and a clean "
    "un-annotated copy. Don't peek consecutively — one peek per uncertainty. "
    'Use scroll with value "down" or "up" when the requested flow likely continues outside '
    "the current viewport. For page audits, app launchers, navigation menus, lists, grids, or "
    "requests to check all items in a category, inspect the whole page: scroll down until no new "
    "relevant content appears, then scroll back up if needed to continue navigation. Do not emit "
    "finish just because the currently visible viewport looks complete; finish only after you have "
    "covered the full page or the requested scope is explicitly limited to visible content. "
    "Use select, not fill, for native dropdowns/select elements when choosing a visible option. "
    "Prefer exploring core navigation, links, and obvious broken states. "
    "Avoid using search bars, search boxes, or generic query inputs unless the user's prompt "
    "explicitly asks you to test search. Most real users navigate by clicking visible links and "
    "menu items, not by typing into a search field. "
    "Do not navigate by typing URLs or guessing paths. The 'goto' action is reserved for "
    "following links you can actually see in the screenshot or element list. Do not invent or "
    "guess URLs, slugs, or path fragments. If a destination is not reachable through visible "
    "links or menu items, treat it as out of scope rather than guessing the URL. "
    "Do not attempt payment or destructive account actions."
)

_SYSTEM_ACCOUNTS_FORBIDDEN = (
    " Do not attempt login, account registration, account deletion, password reset, or any "
    "credential entry. Skip authentication flows entirely."
)

_SYSTEM_ACCOUNTS_ALLOWED = (
    " You may attempt login, account registration, account deletion, and credential entry as part "
    "of testing the requested flows. Always use synthetic, throwaway test values; never use real "
    "personal data."
)

# TODO: consider removing _SYSTEM_EXTERNAL_FORBIDDEN from the system prompt in the future. The
# network-layer guard already enforces this, and a real user does sometimes click off-site links
# (footer "powered by", partner logos, etc.). Letting the agent attempt those gives us a useful
# signal about how often a human would have left the target site.
_SYSTEM_EXTERNAL_FORBIDDEN = (
    " Stay on the target site. Do not click links, banners, or buttons that lead to a different "
    "hostname. External navigation is blocked at the network layer and will fail; do not waste "
    "steps trying."
)

_SYSTEM_EXTERNAL_ALLOWED = (
    " You may follow links to external sites if doing so is part of testing the requested user "
    "flow (for example, OAuth providers or partner checkout)."
)

_SYSTEM_FINANCIAL_FORBIDDEN = (
    " Do not enter payment, credit card, or banking details. Skip checkout and payment flows."
)


def _system_prompt(
    allow_accounts: bool,
    demographic: str = "",
    allow_external: bool = False,
    card_fields: tuple[tuple[str, str], ...] = (),
    account_fields: tuple[tuple[str, str], ...] = (),
) -> str:
    persona = ConfigManager().sentinel.demographic_personas.get(demographic, "")
    base = _SYSTEM_BASE + (
        _SYSTEM_ACCOUNTS_ALLOWED if allow_accounts else _SYSTEM_ACCOUNTS_FORBIDDEN
    )
    base += _SYSTEM_EXTERNAL_ALLOWED if allow_external else _SYSTEM_EXTERNAL_FORBIDDEN
    if card_fields:
        placeholders = ", ".join(f"{label}: {placeholder}" for label, placeholder in card_fields)
        base += (
            " You may complete payment and checkout flows. Use these opaque placeholders as fill "
            f"values for the matching fields: {placeholders}. Never alter, explain, or echo a "
            "placeholder in a reason."
        )
    else:
        base += _SYSTEM_FINANCIAL_FORBIDDEN
    if account_fields:
        placeholders = ", ".join(f"{label}: {placeholder}" for label, placeholder in account_fields)
        base += (
            " When the target asks for credentials, use exactly these opaque placeholders as fill "
            f"values for the matching fields: {placeholders}. Do not invent alternatives. "
            "If these credentials are rejected (e.g. 'invalid password', 'user not found') after a "
            "genuine attempt to log in, do NOT silently retry with different values; instead emit "
            '{"action":"finish","reason":"login failed: <site error>"} so the run is '
            "recorded as a failure. Never echo the password back in your reason field."
        )
    if persona:
        return f"{persona} {base}"
    return base


_REPORT_SYSTEM = (
    "You are writing the final QA report for Sentinel. Directly answer the user's original prompt "
    "using only the run data provided. Be concise, factual, and practical. Include what was tested "
    "and what worked or failed. Do not invent findings. "
    "Write in the voice of a human QA engineer producing a professional internal report. Use plain, "
    "neutral prose and standard punctuation. Do not use emojis. Do not use em dashes or en dashes; "
    "use commas, periods, parentheses, or colons instead. Avoid marketing language, exclamation "
    "marks, and filler superlatives. Prefer short paragraphs and short bullet lists over long prose. "
    "The report MUST begin with a level-2 markdown heading exactly equal to '## Summary' followed "
    "by a brief two to four sentence overview answering the user's prompt and stating the overall "
    "outcome. The Summary section MUST also embed the final screenshot of the run (the highest "
    "step-NN.png available in attached_screenshots) directly under the overview text. Additional "
    "sections (for example '## Findings', '## What Was Tested') may follow the Summary as needed. "
    "Do NOT include a Caveats section or any equivalent disclaimers/limitations section. "
    "Output GitHub-flavored markdown. Embed relevant screenshots inline as evidence using exactly "
    "this syntax: ![short caption](step-NN.png). Only reference filenames listed in the run "
    "data's 'attached_screenshots' array (those are the frames you can actually see); if that "
    "array is empty, do not embed any screenshots. The naming convention is: step-00.png is the "
    "page BEFORE any action; step-NN.png (N >= 1) is the page AFTER step N's action. So if step "
    "17 was 'click View Chart', the chart appears in step-17.png, NOT step-16.png or step-18.png. "
    "Reference at most one screenshot per finding, and only when it visually supports the claim. "
    "Do not invent filenames."
)


_SCREENSHOT_PICKER_SYSTEM = (
    "You are picking which screenshots from a Sentinel QA run are most useful as visual evidence "
    "for the final report. You will receive the run's prompt, steps, and findings as JSON. "
    "Reply with ONLY a single JSON object of the shape "
    '{"screenshots":["step-NN.png", ...], "reason":"..."}. '
    "Pick at most the requested number of filenames, ordered by importance. "
    "Naming convention: step-00.png is the page before any action; step-NN.png (N >= 1) is the "
    "state AFTER step N's action. Prefer screenshots that visually confirm the user's prompt was "
    "fulfilled (or visually demonstrate why it failed). Pick distinct moments — don't pick "
    "consecutive screenshots showing the same state. The reason must be one short sentence."
)


# ---------------------------------------------------------------------------
# Capability-backed provider
# ---------------------------------------------------------------------------

# Per-role config: (default system prompt, SentinelConfig attr for max_tokens,
# SentinelConfig attr for timeout). The agent role's system prompt is dynamic
# (it depends on per-run flags) so its slot is None — _Provider passes the
# resolved string in directly.
_ROLE_CONFIG = {
    "final_report": (_REPORT_SYSTEM, "llm_final_report_max_tokens", "final_report_timeout_s"),
    "title": (_TITLE_SYSTEM, "llm_title_max_tokens", "llm_title_timeout_s"),
    "verdict": (_VERDICT_SYSTEM, "llm_verdict_max_tokens", "llm_verdict_timeout_s"),
    "screenshot_picker": (
        _SCREENSHOT_PICKER_SYSTEM,
        "llm_picker_max_tokens",
        "llm_picker_timeout_s",
    ),
    "agent": (None, "llm_step_max_tokens", "llm_step_timeout_s"),
}


class _Provider:
    """Maps Sentinel's roles onto the host's text-generation interface."""

    name = "host"

    def _call(
        self,
        role: str,
        user_message: str,
        system: str,
        image_bytes: list[bytes] | None = None,
    ) -> str:
        _, max_tokens_attr, timeout_attr = _ROLE_CONFIG[role]
        host_config = ConfigManager()
        cfg = host_config.sentinel
        timeout_s = capped_timeout_seconds(float(getattr(cfg, timeout_attr)))
        image_paths: list[Path] = []
        with TemporaryDirectory(prefix=cfg.image_temp_prefix) as temp_dir:
            for index, data in enumerate(image_bytes or []):
                image_path = Path(temp_dir) / f"image-{index}.png"
                image_path.write_bytes(data)
                image_paths.append(image_path)
            if host_config.llm.api_source == "codex":
                return codex_cli_text(
                    user_message,
                    instructions=system,
                    model=cfg.codex_model,
                    timeout_s=timeout_s,
                    image_paths=image_paths,
                    reasoning_effort=cfg.codex_reasoning_effort,
                    permissions_profile=cfg.codex_permissions_profile,
                )
            model = host_config.llm.model_for(cfg.llm_tier)
            transport = bedrock_text if host_config.llm.api_source == "bedrock" else meridian_text
            return transport(
                user_message,
                system=system,
                model=model,
                max_tokens=getattr(cfg, max_tokens_attr),
                timeout_s=timeout_s,
                image_paths=image_paths,
            )

    def agent_text(
        self,
        user_message: str,
        image_bytes: list[bytes] | None = None,
        allow_accounts: bool = False,
        demographic: str = "",
        allow_external: bool = False,
        card_fields: tuple[tuple[str, str], ...] = (),
        account_fields: tuple[tuple[str, str], ...] = (),
    ) -> str:
        return self._call(
            "agent",
            user_message,
            system=_system_prompt(
                allow_accounts,
                demographic,
                allow_external,
                card_fields,
                account_fields,
            ),
            image_bytes=image_bytes,
        )

    def final_report_text(self, user_message: str, image_bytes: list[bytes] | None = None) -> str:
        return self._call(
            "final_report", user_message, system=_REPORT_SYSTEM, image_bytes=image_bytes
        )

    def title_text(self, user_message: str) -> str:
        return self._call("title", user_message, system=_TITLE_SYSTEM)

    def verdict_text(self, user_message: str) -> str:
        return self._call("verdict", user_message, system=_VERDICT_SYSTEM)

    def screenshot_picker_text(self, user_message: str) -> str:
        return self._call("screenshot_picker", user_message, system=_SCREENSHOT_PICKER_SYSTEM)


def _get_provider() -> _Provider:
    return _Provider()
