"""Settings and sending policy.

Settings come from a JSON file, environment variables (prefix ``OFFERINGS_``),
or both; environment variables win. Secrets (Gmail OAuth, Google Maps,
Anthropic) are expected in the environment, never in the repository.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Mapping

from moving_loads.freight_calculator import DEFAULT_RATE_PER_MILE

ENV_PREFIX = "OFFERINGS_"

AUTO_REPLY_MODES = ("draft", "send", "off")
CLASSIFIER_MODES = ("rules", "claude", "auto")


@dataclass
class Policy:
    """Guard rails for autonomous behaviour. Defaults are the safe choice."""

    live: bool = False  # False → nothing reaches Gmail, mail goes to the outbox dir
    auto_reply_mode: str = "draft"  # buyer-facing auto replies: draft | send | off
    auto_send_follow_ups: bool = False  # follow-ups go out without review
    acknowledge_offers: bool = False  # send a short "got it" to firm offers
    max_sends_per_run: int = 200
    max_sends_per_day: int = 1500
    bcc_chunk_size: int = 50
    follow_up_after_days: int = 3
    max_follow_ups: int = 1
    quiet_hours_start: int | None = 20  # local hour, inclusive; None disables
    quiet_hours_end: int | None = 7  # local hour, exclusive
    allowed_recipient_domains: tuple[str, ...] = ()  # empty → any domain
    unsubscribe_footer: bool = True
    inbox_lookback_days: int = 7
    offering_ttl_days: int = 30  # default expiry for offerings without expires_at

    def __post_init__(self) -> None:
        if self.auto_reply_mode not in AUTO_REPLY_MODES:
            raise ValueError(f"auto_reply_mode must be one of {AUTO_REPLY_MODES}.")
        if self.max_sends_per_run < 0 or self.max_sends_per_day < 0:
            raise ValueError("Send caps must be non-negative.")
        if self.bcc_chunk_size <= 0:
            raise ValueError("bcc_chunk_size must be positive.")
        if self.follow_up_after_days < 0 or self.max_follow_ups < 0:
            raise ValueError("Follow-up settings must be non-negative.")
        for hour in (self.quiet_hours_start, self.quiet_hours_end):
            if hour is not None and not 0 <= hour <= 23:
                raise ValueError("Quiet hours must be between 0 and 23.")
        self.allowed_recipient_domains = tuple(
            d.strip().lower().lstrip("@") for d in self.allowed_recipient_domains if d and d.strip()
        )

    def recipient_allowed(self, email: str) -> bool:
        if not self.allowed_recipient_domains:
            return True
        domain = email.rsplit("@", 1)[-1].lower()
        return domain in self.allowed_recipient_domains

    def in_quiet_hours(self, local_hour: int) -> bool:
        start, end = self.quiet_hours_start, self.quiet_hours_end
        if start is None or end is None:
            return False
        if start == end:
            return False
        if start < end:
            return start <= local_hour < end
        return local_hour >= start or local_hour < end  # wraps midnight


@dataclass
class Settings:
    """Everything the system needs to run. See ``Settings.from_env``."""

    sender_email: str
    sender_name: str = ""
    company_name: str = ""
    reply_to: str = ""
    escalation_email: str = ""  # firm offers / digests go here (defaults to sender)
    sales_team_email: str = ""  # internal cost sheets go here
    signature: str = ""
    google_maps_api_key: str = ""
    rate_per_mile: float = DEFAULT_RATE_PER_MILE
    freight_margin_per_truckload: float = 0.0
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-5-5"
    classifier: str = "auto"  # rules | claude | auto
    gmail_client_id: str = ""
    gmail_client_secret: str = ""
    gmail_refresh_token: str = ""
    gmail_label_prefix: str = "Offerings"
    db_path: str = "offerings.db"
    outbox_dir: str = "outbox"
    timezone: str = "US/Eastern"
    policy: Policy = field(default_factory=Policy)

    def __post_init__(self) -> None:
        self.sender_email = self.sender_email.strip().lower()
        if not self.sender_email or "@" not in self.sender_email:
            raise ValueError("sender_email is required.")
        if not self.escalation_email:
            self.escalation_email = self.sender_email
        if self.classifier not in CLASSIFIER_MODES:
            raise ValueError(f"classifier must be one of {CLASSIFIER_MODES}.")
        if self.rate_per_mile < 0:
            raise ValueError("rate_per_mile must be non-negative.")
        if isinstance(self.policy, dict):
            self.policy = Policy(**self.policy)

    # ------------------------------------------------------------------ #
    @property
    def gmail_configured(self) -> bool:
        return bool(self.gmail_client_id and self.gmail_client_secret and self.gmail_refresh_token)

    @property
    def freight_configured(self) -> bool:
        return bool(self.google_maps_api_key)

    @property
    def claude_configured(self) -> bool:
        return bool(self.anthropic_api_key)

    def validate_for_live(self) -> None:
        """Raise ``ValueError`` if ``policy.live`` is set but Gmail is not configured."""
        if self.policy.live and not self.gmail_configured:
            raise ValueError(
                "Live mode requires OFFERINGS_GMAIL_CLIENT_ID, OFFERINGS_GMAIL_CLIENT_SECRET "
                "and OFFERINGS_GMAIL_REFRESH_TOKEN."
            )

    # ------------------------------------------------------------------ #
    def to_dict(self, *, redact: bool = True) -> dict[str, Any]:
        data = asdict(self)
        data["policy"]["allowed_recipient_domains"] = list(self.policy.allowed_recipient_domains)
        if redact:
            for key in ("google_maps_api_key", "anthropic_api_key", "gmail_client_secret", "gmail_refresh_token"):
                if data.get(key):
                    data[key] = "***"
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Settings":
        known = {f.name for f in fields(cls)}
        kwargs: dict[str, Any] = {k: v for k, v in data.items() if k in known}
        policy_data = dict(kwargs.pop("policy", {}) or {})
        policy_known = {f.name for f in fields(Policy)}
        policy_kwargs = {k: v for k, v in policy_data.items() if k in policy_known}
        if "allowed_recipient_domains" in policy_kwargs:
            policy_kwargs["allowed_recipient_domains"] = tuple(policy_kwargs["allowed_recipient_domains"] or ())
        kwargs["policy"] = Policy(**policy_kwargs)
        return cls(**kwargs)

    @classmethod
    def from_file(cls, path: str | os.PathLike[str]) -> "Settings":
        with open(path, "r", encoding="utf-8") as fh:
            return cls.from_dict(json.load(fh))

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        path: str | os.PathLike[str] | None = None,
    ) -> "Settings":
        """Load settings from an optional JSON file, then overlay ``OFFERINGS_*`` vars.

        Nested policy fields use ``OFFERINGS_POLICY_<FIELD>`` (e.g.
        ``OFFERINGS_POLICY_LIVE=true``). Booleans accept true/false/1/0/yes/no.
        ``OFFERINGS_POLICY_ALLOWED_RECIPIENT_DOMAINS`` is comma separated.
        """
        env = dict(os.environ if environ is None else environ)
        data: dict[str, Any] = {}
        file_path = path or env.get(f"{ENV_PREFIX}CONFIG")
        if file_path and Path(file_path).exists():
            with open(file_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        data.setdefault("policy", {})

        for f in fields(cls):
            if f.name == "policy":
                continue
            raw = env.get(f"{ENV_PREFIX}{f.name.upper()}")
            if raw is not None:
                data[f.name] = _coerce(raw, f.type)
        for f in fields(Policy):
            raw = env.get(f"{ENV_PREFIX}POLICY_{f.name.upper()}")
            if raw is not None:
                data["policy"][f.name] = _coerce(raw, f.type)
        if "sender_email" not in data:
            raise ValueError("OFFERINGS_SENDER_EMAIL (or sender_email in the config file) is required.")
        return cls.from_dict(data)


def _coerce(raw: str, annotation: Any) -> Any:
    """Convert an environment string to the annotated type (best effort)."""
    text = str(annotation)
    value = raw.strip()
    if "bool" in text:
        if value.lower() in ("1", "true", "yes", "on"):
            return True
        if value.lower() in ("0", "false", "no", "off", ""):
            return False
        raise ValueError(f"Expected a boolean, got {raw!r}.")
    if "tuple" in text:
        return tuple(v.strip() for v in value.split(",") if v.strip())
    if "int" in text and "float" not in text:
        if value.lower() in ("", "none", "null"):
            return None
        return int(value)
    if "float" in text:
        return float(value)
    return value
