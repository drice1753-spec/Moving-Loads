"""Tests for email_offerings.config (Policy and Settings; no network, tmp_path only)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from email_offerings.config import (
    AUTO_REPLY_MODES,
    CLASSIFIER_MODES,
    ENV_PREFIX,
    Policy,
    Settings,
    _coerce,
)

SENDER = "owner@example.com"


class TestPolicyValidation:
    """Policy defaults are the safe choice and bad values are rejected."""

    def test_defaults_are_safe(self):
        policy = Policy()
        assert policy.live is False
        assert policy.auto_reply_mode == "draft"
        assert policy.auto_send_follow_ups is False
        assert policy.acknowledge_offers is False
        assert policy.unsubscribe_footer is True
        assert "draft" in AUTO_REPLY_MODES and "auto" in CLASSIFIER_MODES

    @pytest.mark.parametrize(
        "kwargs, match",
        [
            ({"auto_reply_mode": "yolo"}, "auto_reply_mode"),
            ({"max_sends_per_run": -1}, "Send caps"),
            ({"max_sends_per_day": -5}, "Send caps"),
            ({"bcc_chunk_size": 0}, "bcc_chunk_size"),
            ({"follow_up_after_days": -1}, "Follow-up"),
            ({"max_follow_ups": -1}, "Follow-up"),
            ({"quiet_hours_start": 24}, "Quiet hours"),
            ({"quiet_hours_end": -1}, "Quiet hours"),
        ],
    )
    def test_rejects_bad_values(self, kwargs, match):
        with pytest.raises(ValueError, match=match):
            Policy(**kwargs)

    def test_domains_are_normalised(self):
        policy = Policy(allowed_recipient_domains=(" @Example.COM ", "", "   ", "b.org"))
        assert policy.allowed_recipient_domains == ("example.com", "b.org")


class TestPolicyRecipientAllowed:
    """Domain allow-list."""

    def test_any_domain_when_list_is_empty(self):
        assert Policy().recipient_allowed("anyone@anything.io")

    def test_filters_by_domain_case_insensitively(self):
        policy = Policy(allowed_recipient_domains=("example.com",))
        assert policy.recipient_allowed("Buyer@EXAMPLE.com")
        assert not policy.recipient_allowed("buyer@other.com")


class TestPolicyQuietHours:
    """in_quiet_hours covers disabled, same-day and midnight-wrapping windows."""

    def test_disabled_when_either_bound_is_none(self):
        assert not Policy(quiet_hours_start=None).in_quiet_hours(23)
        assert not Policy(quiet_hours_end=None).in_quiet_hours(23)

    def test_equal_bounds_disable_quiet_hours(self):
        assert not Policy(quiet_hours_start=7, quiet_hours_end=7).in_quiet_hours(7)

    def test_window_wrapping_midnight(self):
        policy = Policy(quiet_hours_start=20, quiet_hours_end=7)
        assert policy.in_quiet_hours(20) and policy.in_quiet_hours(0) and policy.in_quiet_hours(6)
        assert not policy.in_quiet_hours(7) and not policy.in_quiet_hours(12)

    def test_same_day_window(self):
        policy = Policy(quiet_hours_start=1, quiet_hours_end=5)
        assert policy.in_quiet_hours(1) and policy.in_quiet_hours(4)
        assert not policy.in_quiet_hours(5) and not policy.in_quiet_hours(0)


class TestSettingsValidation:
    """Settings.__post_init__ and the configured/validate helpers."""

    @pytest.mark.parametrize("email", ["", "   ", "nobody"])
    def test_sender_email_required(self, email):
        with pytest.raises(ValueError, match="sender_email"):
            Settings(sender_email=email)

    def test_sender_normalised_and_escalation_defaults_to_sender(self):
        settings = Settings(sender_email="  Owner@Example.COM ")
        assert settings.sender_email == SENDER
        assert settings.escalation_email == SENDER

    def test_explicit_escalation_email_is_kept(self):
        settings = Settings(sender_email=SENDER, escalation_email="boss@example.com")
        assert settings.escalation_email == "boss@example.com"

    def test_rejects_unknown_classifier(self):
        with pytest.raises(ValueError, match="classifier"):
            Settings(sender_email=SENDER, classifier="gpt")

    def test_rejects_negative_rate(self):
        with pytest.raises(ValueError, match="rate_per_mile"):
            Settings(sender_email=SENDER, rate_per_mile=-0.5)

    def test_policy_dict_is_coerced(self):
        settings = Settings(sender_email=SENDER, policy={"live": True, "bcc_chunk_size": 10})
        assert isinstance(settings.policy, Policy)
        assert settings.policy.live is True and settings.policy.bcc_chunk_size == 10

    def test_configured_flags(self):
        bare = Settings(sender_email=SENDER)
        assert not bare.gmail_configured and not bare.freight_configured and not bare.claude_configured
        full = Settings(
            sender_email=SENDER,
            gmail_client_id="id",
            gmail_client_secret="secret",
            gmail_refresh_token="token",
            google_maps_api_key="maps",
            anthropic_api_key="key",
        )
        assert full.gmail_configured and full.freight_configured and full.claude_configured

    def test_validate_for_live(self):
        Settings(sender_email=SENDER).validate_for_live()  # dry run never needs Gmail
        with pytest.raises(ValueError, match="Live mode requires"):
            Settings(sender_email=SENDER, policy=Policy(live=True)).validate_for_live()
        Settings(
            sender_email=SENDER,
            gmail_client_id="id",
            gmail_client_secret="secret",
            gmail_refresh_token="token",
            policy=Policy(live=True),
        ).validate_for_live()


class TestSettingsSerialisation:
    """to_dict / from_dict / from_file."""

    def test_to_dict_redacts_only_secrets_that_are_set(self):
        settings = Settings(
            sender_email=SENDER,
            gmail_client_id="id",
            gmail_client_secret="secret",
            gmail_refresh_token="token",
            google_maps_api_key="maps",
            anthropic_api_key="key",
            policy=Policy(allowed_recipient_domains=("example.com",)),
        )
        data = settings.to_dict()
        for key in ("google_maps_api_key", "anthropic_api_key", "gmail_client_secret", "gmail_refresh_token"):
            assert data[key] == "***"
        assert data["gmail_client_id"] == "id"
        assert data["policy"]["allowed_recipient_domains"] == ["example.com"]
        raw = settings.to_dict(redact=False)
        assert raw["google_maps_api_key"] == "maps" and raw["gmail_refresh_token"] == "token"
        assert Settings(sender_email=SENDER).to_dict()["anthropic_api_key"] == ""

    def test_from_dict_ignores_unknown_keys_and_coerces_domains(self):
        settings = Settings.from_dict(
            {
                "_note": "ignored",
                "sender_email": SENDER,
                "sender_name": "Alex Rivera",
                "policy": {"_note": "ignored", "allowed_recipient_domains": ["Example.com"], "bogus": 1},
            }
        )
        assert settings.sender_name == "Alex Rivera"
        assert settings.policy.allowed_recipient_domains == ("example.com",)

    def test_from_dict_tolerates_missing_or_null_policy(self):
        assert Settings.from_dict({"sender_email": SENDER}).policy == Policy()
        assert Settings.from_dict({"sender_email": SENDER, "policy": None}).policy == Policy()
        empty = Settings.from_dict({"sender_email": SENDER, "policy": {"allowed_recipient_domains": None}})
        assert empty.policy.allowed_recipient_domains == ()

    def test_round_trip(self):
        settings = Settings(
            sender_email=SENDER,
            sender_name="Alex",
            anthropic_api_key="key",
            rate_per_mile=4.5,
            policy=Policy(live=True, allowed_recipient_domains=("example.com",), quiet_hours_start=None),
        )
        assert Settings.from_dict(settings.to_dict(redact=False)) == settings

    def test_from_file_accepts_str_and_path(self, tmp_path: Path):
        path = tmp_path / "config.json"
        path.write_text(json.dumps({"sender_email": SENDER, "company_name": "Example Surplus"}), encoding="utf-8")
        assert Settings.from_file(path).company_name == "Example Surplus"
        assert Settings.from_file(str(path)).company_name == "Example Surplus"


class TestSettingsFromEnv:
    """Environment overlay on top of an optional JSON file."""

    def test_requires_sender(self):
        with pytest.raises(ValueError, match=f"{ENV_PREFIX}SENDER_EMAIL"):
            Settings.from_env({})

    def test_env_overrides_file_including_policy(self, tmp_path: Path):
        path = tmp_path / "config.json"
        path.write_text(
            json.dumps(
                {
                    "sender_email": SENDER,
                    "sender_name": "From File",
                    "rate_per_mile": 3.0,
                    "policy": {"live": False, "bcc_chunk_size": 50},
                }
            ),
            encoding="utf-8",
        )
        environ = {
            f"{ENV_PREFIX}SENDER_NAME": "From Env",
            f"{ENV_PREFIX}RATE_PER_MILE": "4.25",
            f"{ENV_PREFIX}POLICY_LIVE": "true",
            f"{ENV_PREFIX}POLICY_BCC_CHUNK_SIZE": "25",
            f"{ENV_PREFIX}POLICY_ALLOWED_RECIPIENT_DOMAINS": "a.com, b.org",
            f"{ENV_PREFIX}POLICY_QUIET_HOURS_START": "",
        }
        settings = Settings.from_env(environ, path=path)
        assert settings.sender_email == SENDER
        assert settings.sender_name == "From Env"
        assert settings.rate_per_mile == 4.25
        assert settings.policy.live is True
        assert settings.policy.bcc_chunk_size == 25
        assert settings.policy.allowed_recipient_domains == ("a.com", "b.org")
        assert settings.policy.quiet_hours_start is None

    def test_config_env_var_names_the_file(self, tmp_path: Path):
        path = tmp_path / "config.json"
        path.write_text(json.dumps({"sender_email": SENDER, "sender_name": "Config"}), encoding="utf-8")
        settings = Settings.from_env({f"{ENV_PREFIX}CONFIG": str(path)})
        assert settings.sender_name == "Config"

    def test_missing_config_file_is_ignored(self, tmp_path: Path):
        settings = Settings.from_env({f"{ENV_PREFIX}SENDER_EMAIL": SENDER}, path=tmp_path / "missing.json")
        assert settings.sender_email == SENDER and settings.policy == Policy()

    def test_reads_os_environ_by_default(self, monkeypatch):
        for key in list(os.environ):
            if key.startswith(ENV_PREFIX):
                monkeypatch.delenv(key)
        monkeypatch.setenv(f"{ENV_PREFIX}SENDER_EMAIL", "Env@Example.com")
        monkeypatch.setenv(f"{ENV_PREFIX}POLICY_AUTO_REPLY_MODE", "off")
        settings = Settings.from_env()
        assert settings.sender_email == "env@example.com"
        assert settings.policy.auto_reply_mode == "off"


class TestCoerce:
    """_coerce turns environment strings into the annotated types."""

    @pytest.mark.parametrize("raw", ["1", "true", "YES", " on "])
    def test_true_values(self, raw):
        assert _coerce(raw, "bool") is True

    @pytest.mark.parametrize("raw", ["0", "false", "No", "off", ""])
    def test_false_values(self, raw):
        assert _coerce(raw, "bool") is False

    def test_bad_boolean_raises(self):
        with pytest.raises(ValueError, match="boolean"):
            _coerce("maybe", "bool")

    def test_tuple_int_float_and_str(self):
        assert _coerce("a.com, ,b.org", "tuple[str, ...]") == ("a.com", "b.org")
        assert _coerce(" 12 ", "int | None") == 12
        assert _coerce("none", "int | None") is None
        assert _coerce("", "int") is None
        assert _coerce("4.5", "float") == 4.5
        assert _coerce(" text ", "str") == "text"
