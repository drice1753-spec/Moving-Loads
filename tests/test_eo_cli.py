"""Tests for email_offerings.cli (the ``offerings`` command). No network access.

Every test drives ``main(argv)`` with settings supplied through ``OFFERINGS_*``
environment variables (``monkeypatch.setenv``), a database and outbox under
``tmp_path``, and reads the output with ``capsys``. The dry-run mailer is the
real one; the only mocks are ``time.sleep`` (for ``run --loop``), the Google
distance provider (for ``quote``) and a fake Gmail mailer for ``--live``.
"""

from __future__ import annotations

import email
import email.policy
import json
import sqlite3
from email.message import EmailMessage
from datetime import timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests

import email_offerings.cli as cli
import email_offerings.engine as engine_module
from email_offerings.cli import HANDLED_ERRORS, build_parser, load_settings, main, read_sheet_file
from email_offerings.engine import EngineError
from email_offerings.mailer import DryRunMailer, MailerError
from email_offerings.models import ActionKind, ActionRecord, BuyerStatus, CampaignStatus, OfferingStatus, utcnow
from email_offerings.store import Store

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = REPO_ROOT / "examples"
OFFERING_FILE = EXAMPLES / "offering.example.json"
OFFERING_ID = "silver-rustic-oak-spc-2026-10-01"
SHEET_FILE = EXAMPLES / "internal_sheet.example.txt"
BUYERS_CSV = EXAMPLES / "buyers.example.csv"
SENDER = "dan@example.com"
SALES = "sales-team@example.com"
MILES = 800.0  # 800 mi * $4.50 = $3,600 per truckload; / 20,000 sf = $0.18/sf


# --------------------------------------------------------------------------- #
# Fixtures and helpers
# --------------------------------------------------------------------------- #


@pytest.fixture
def paths(tmp_path, monkeypatch):
    """Isolated OFFERINGS_* environment: dry run, rules classifier, no quiet hours."""
    import os

    for key in [k for k in os.environ if k.startswith("OFFERINGS_")]:
        monkeypatch.delenv(key, raising=False)
    db = tmp_path / "data" / "offerings.db"
    outbox = tmp_path / "outbox"
    monkeypatch.setenv("OFFERINGS_SENDER_EMAIL", SENDER)
    monkeypatch.setenv("OFFERINGS_SENDER_NAME", "Alex Rivera")
    monkeypatch.setenv("OFFERINGS_COMPANY_NAME", "Alpinewest Resources")
    monkeypatch.setenv("OFFERINGS_SALES_TEAM_EMAIL", SALES)
    monkeypatch.setenv("OFFERINGS_CLASSIFIER", "rules")
    monkeypatch.setenv("OFFERINGS_DB_PATH", str(db))
    monkeypatch.setenv("OFFERINGS_OUTBOX_DIR", str(outbox))
    monkeypatch.setenv("OFFERINGS_POLICY_QUIET_HOURS_START", "none")
    monkeypatch.chdir(tmp_path)
    return {"tmp": tmp_path, "db": db, "outbox": outbox}


@pytest.fixture
def run(capsys):
    """``run(*argv) -> (exit_code, stdout, stderr)``."""

    def _run(*argv: str) -> tuple[int, str, str]:
        code = main(list(argv))
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    return _run


@pytest.fixture
def active_offering(paths, run):
    """Database initialised with the example offering stored as ACTIVE; returns its id."""
    assert run("init")[0] == 0
    code, out, err = run("offering", "add", "--file", str(OFFERING_FILE), "--activate")
    assert code == 0, err
    return OFFERING_ID


@pytest.fixture
def buyers_csv(tmp_path):
    """The example buyer CSV when present, else a temporary one with the same shape."""
    if BUYERS_CSV.is_file():
        return BUYERS_CSV
    path = tmp_path / "buyers.csv"
    path.write_text(
        "email,name,company,postal_code,city_state,tags\n"
        "buyer-one@example.com,Marcus Thibodeaux,Thibodeaux Flooring,73127,\"Oklahoma City, OK\",flooring\n"
        "buyer-two@example.com,Priya Venkataraman,Lone Star Surplus,75247,\"Dallas, TX\",tile\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def distance(monkeypatch):
    """Configure a Maps key and replace the Google provider with a fixed-distance lambda."""
    monkeypatch.setenv("OFFERINGS_GOOGLE_MAPS_API_KEY", "fake-maps-key")
    calls: list[tuple[str, str]] = []

    def provider_factory(api_key: str):
        assert api_key == "fake-maps-key"

        def provider(origin: str, destination: str) -> float:
            calls.append((origin, destination))
            return MILES

        return provider

    monkeypatch.setattr(engine_module, "google_distance_provider", provider_factory)
    return calls


def _eml_files(outbox: Path) -> list[Path]:
    return sorted(outbox.glob("*.eml")) if outbox.is_dir() else []


def _parse_eml(path: Path) -> tuple[EmailMessage, str]:
    """``(message, plain-text body)`` of a dry-run ``.eml`` file."""
    with open(path, "rb") as fh:
        message = email.message_from_binary_file(fh, policy=email.policy.default)
    body = message.get_body(preferencelist=("plain",))
    return message, (body.get_content() if body is not None else "")


# --------------------------------------------------------------------------- #
# Parser and settings
# --------------------------------------------------------------------------- #


class TestParser:
    """Argument parsing: help, missing and unknown commands exit through argparse."""

    def test_help_exits_zero(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            main(["--help"])
        assert exc_info.value.code == 0
        assert "offerings" in capsys.readouterr().out

    def test_no_command_is_a_usage_error(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            main([])
        assert exc_info.value.code == 2

    def test_unknown_command_is_a_usage_error(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            main(["frobnicate"])
        assert exc_info.value.code == 2

    def test_offering_without_subcommand_is_a_usage_error(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            main(["offering"])
        assert exc_info.value.code == 2

    def test_invalid_status_choice_is_a_usage_error(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            main(["offering", "set-status", "x", "bogus"])
        assert exc_info.value.code == 2

    def test_quote_requires_exactly_one_destination(self, capsys):
        with pytest.raises(SystemExit):
            main(["quote", "x"])
        with pytest.raises(SystemExit):
            main(["quote", "x", "--zip", "73127", "--dest", "Dallas, TX"])

    def test_run_interval_rejects_negative_and_non_numeric(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            main(["run", "--interval", "-5"])
        assert exc_info.value.code == 2
        with pytest.raises(SystemExit):
            main(["run", "--interval", "soon"])

    def test_every_handler_is_reachable_from_the_parser(self):
        parser = build_parser()
        for (command, sub), handler in cli._HANDLERS.items():
            argv = [command] if sub is None else [command, sub]
            # Give each command the positional arguments it needs so parsing succeeds.
            extra = {
                ("offering", "add"): ["--file", "x.json"],
                ("offering", "set-status"): ["id", "active"],
                ("offering", "preview"): ["id"],
                ("buyers", "import"): ["f.csv"],
                ("buyers", "add"): ["a@example.com"],
                ("buyers", "unsubscribe"): ["a@example.com"],
                ("blast", None): ["id"],
                ("internal", None): ["id"],
                ("quote", None): ["id", "--zip", "73127"],
            }.get((command, sub), [])
            args = parser.parse_args(argv + extra)
            assert cli._command_key(args) == (command, sub)
            assert callable(handler)


class TestSettings:
    """Settings come from the environment / --config; --live and --db are applied on top."""

    def test_missing_sender_is_a_one_line_error(self, run, paths, monkeypatch):
        monkeypatch.delenv("OFFERINGS_SENDER_EMAIL")
        code, out, err = run("init")
        assert code == 1
        assert err.startswith("error: ")
        assert "OFFERINGS_SENDER_EMAIL" in err
        assert "Traceback" not in err
        assert out == ""

    def test_config_file_is_read_and_env_wins(self, run, paths, tmp_path, monkeypatch):
        config = tmp_path / "config.json"
        config.write_text(
            json.dumps({"sender_email": "file@example.com", "company_name": "From File Co", "db_path": "ignored.db"}),
            encoding="utf-8",
        )
        code, out, _ = run("--config", str(config), "--json", "status")
        assert code == 0
        data = json.loads(out)
        assert data["settings"]["company_name"] == "Alpinewest Resources"  # env overrides the file
        assert data["settings"]["sender_email"] == SENDER

    def test_missing_config_file_is_an_error(self, run, paths, tmp_path):
        code, _, err = run("--config", str(tmp_path / "nope.json"), "init")
        assert code == 1
        assert "Config file not found" in err

    def test_db_flag_overrides_db_path(self, run, paths, tmp_path):
        other = tmp_path / "elsewhere" / "other.db"
        code, out, _ = run("--db", str(other), "init")
        assert code == 0
        assert other.is_file()
        assert not paths["db"].exists()
        assert str(other) in out

    def test_live_without_gmail_credentials_is_refused(self, run, paths):
        code, out, err = run("--live", "status")
        assert code == 1
        assert "Live mode requires OFFERINGS_GMAIL_CLIENT_ID" in err
        assert "OFFERINGS_GMAIL_REFRESH_TOKEN" in err
        assert out == ""

    def test_live_flag_sets_policy_live(self, paths, monkeypatch):
        for key in ("CLIENT_ID", "CLIENT_SECRET", "REFRESH_TOKEN"):
            monkeypatch.setenv(f"OFFERINGS_GMAIL_{key}", "x")
        args = build_parser().parse_args(["--live", "--db", str(paths["db"]), "status"])
        settings = load_settings(args)
        assert settings.policy.live is True
        assert settings.db_path == str(paths["db"])

    def test_dry_run_is_the_default(self, paths):
        args = build_parser().parse_args(["status"])
        assert load_settings(args).policy.live is False


# --------------------------------------------------------------------------- #
# init
# --------------------------------------------------------------------------- #


class TestInit:
    def test_creates_database_with_tables(self, run, paths):
        code, out, err = run("init")
        assert code == 0
        assert "Database created" in out
        assert paths["db"].is_file()
        with Store(paths["db"]) as store:
            assert store.list_offerings() == []

    def test_second_init_is_idempotent(self, run, paths):
        assert run("init")[0] == 0
        code, out, _ = run("init")
        assert code == 0
        assert "already initialised" in out

    def test_json_output(self, run, paths):
        code, out, _ = run("--json", "init")
        assert code == 0
        data = json.loads(out)
        assert data == {"db_path": str(paths["db"]), "created": True}

    def test_unusable_db_path_is_a_handled_error(self, run, paths, tmp_path):
        code, _, err = run("--db", str(tmp_path), "init")  # a directory, not a file
        assert code == 1
        assert err.startswith("error: ")
        assert "Traceback" not in err


# --------------------------------------------------------------------------- #
# offering
# --------------------------------------------------------------------------- #


class TestOfferingAdd:
    def test_add_from_json_file_stays_draft(self, run, paths):
        run("init")
        code, out, _ = run("offering", "add", "--file", str(OFFERING_FILE))
        assert code == 0
        assert f"Added offering {OFFERING_ID} [draft]" in out
        assert "$0.99/sf FOB Calhoun, GA" in out
        with Store(paths["db"]) as store:
            offering = store.get_offering(OFFERING_ID)
        assert offering is not None and offering.status is OfferingStatus.DRAFT

    def test_activate_flag(self, run, paths):
        run("init")
        code, out, _ = run("offering", "add", "--file", str(OFFERING_FILE), "--activate")
        assert code == 0
        assert "[active]" in out
        with Store(paths["db"]) as store:
            assert store.get_offering(OFFERING_ID).status is OfferingStatus.ACTIVE

    def test_re_adding_updates(self, run, paths):
        run("init")
        run("offering", "add", "--file", str(OFFERING_FILE))
        code, out, _ = run("--json", "offering", "add", "--file", str(OFFERING_FILE), "--activate")
        assert code == 0
        data = json.loads(out)
        assert data["created"] is False
        assert data["offering"]["id"] == OFFERING_ID
        assert data["offering"]["status"] == "active"

    def test_add_from_sheet_with_subject_line(self, run, paths):
        run("init")
        code, out, _ = run("--json", "offering", "add", "--sheet", str(SHEET_FILE))
        assert code == 0, out
        data = json.loads(out)["offering"]
        assert data["title"] == "Silver Rustic Oak SPC Vinyl Click Flooring"
        assert data["fob_location"] == "Calhoun, GA"
        assert data["sell_price"] == 0.99
        assert data["cost_price"] == 0.84
        assert data["make_offers"] is True
        assert data["status"] == "draft"
        assert "DELETE RED" not in data["description"]
        assert "COST FOB" not in data["description"]
        assert data["id"].startswith("silver-rustic-oak-spc-vinyl-click-flooring-")

    def test_add_from_sheet_with_explicit_subject(self, run, paths, tmp_path):
        run("init")
        sheet = tmp_path / "sheet.txt"
        sheet.write_text(
            "DELETE RED...FOB: Dalton, GA...COST FOB: $8,000/TL...SELL: $8,500/TL...BRING BACK ALL FIRM OFFERS...DELETE RED.\n\n"
            "Porcelain tile, 12x24, full pallets.\n",
            encoding="utf-8",
        )
        code, out, _ = run(
            "offering", "add", "--sheet", str(sheet), "--subject", "$8,500/TL Glazed Porcelain Tile (AWR 10/1)", "--activate"
        )
        assert code == 0, out
        assert "[active]" in out
        assert "Glazed Porcelain Tile" in out
        assert "$8,500/TL FOB Dalton, GA" in out
        with Store(paths["db"]) as store:
            offering = store.get_offering("glazed-porcelain-tile-" + utcnow().date().isoformat())
        assert offering is not None
        assert offering.cost_price == 8000.0
        assert "DELETE RED" not in offering.description

    def test_sheet_without_subject_is_an_error(self, run, paths, tmp_path):
        run("init")
        sheet = tmp_path / "sheet.txt"
        sheet.write_text("FOB: Dalton, GA...COST FOB: $8,000/TL\n", encoding="utf-8")
        code, out, err = run("offering", "add", "--sheet", str(sheet))
        assert code == 1
        assert "--subject" in err
        assert out == ""

    def test_sheet_missing_fob_is_an_error(self, run, paths, tmp_path):
        run("init")
        sheet = tmp_path / "sheet.txt"
        sheet.write_text("Subject: $0.99/sf Oak Flooring (AWR 10/1)\n\nNice floor.\n", encoding="utf-8")
        code, _, err = run("offering", "add", "--sheet", str(sheet))
        assert code == 1
        assert "FOB" in err

    def test_missing_file_is_an_error(self, run, paths, tmp_path):
        run("init")
        code, _, err = run("offering", "add", "--file", str(tmp_path / "missing.json"))
        assert code == 1
        assert "missing.json" in err
        assert "Traceback" not in err

    def test_invalid_json_is_an_error(self, run, paths, tmp_path):
        run("init")
        bad = tmp_path / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        code, _, err = run("offering", "add", "--file", str(bad))
        assert code == 1
        assert "not valid JSON" in err

    def test_file_and_sheet_are_mutually_exclusive(self, paths, capsys):
        with pytest.raises(SystemExit) as exc_info:
            main(["offering", "add", "--file", "a.json", "--sheet", "b.txt"])
        assert exc_info.value.code == 2


class TestReadSheetFile:
    def test_subject_line_is_case_insensitive_and_removed(self, tmp_path):
        sheet = tmp_path / "s.txt"
        sheet.write_text("\n\nSUBJECT:  $1.00/sf Oak (AWR 10/1)  \n\nBody line.\n", encoding="utf-8")
        subject, body = read_sheet_file(str(sheet))
        assert subject == "$1.00/sf Oak (AWR 10/1)"
        assert body == "Body line."

    def test_explicit_subject_wins_over_the_line(self, tmp_path):
        sheet = tmp_path / "s.txt"
        sheet.write_text("Subject: from file\nBody.", encoding="utf-8")
        subject, body = read_sheet_file(str(sheet), "from flag")
        assert subject == "from flag"
        assert body == "Body."

    def test_no_subject_anywhere_raises(self, tmp_path):
        sheet = tmp_path / "s.txt"
        sheet.write_text("Body only.", encoding="utf-8")
        with pytest.raises(ValueError, match="--subject"):
            read_sheet_file(str(sheet))

    def test_empty_file_raises(self, tmp_path):
        sheet = tmp_path / "s.txt"
        sheet.write_text("", encoding="utf-8")
        with pytest.raises(ValueError):
            read_sheet_file(str(sheet))


class TestOfferingList:
    def test_empty_database(self, run, paths):
        run("init")
        code, out, _ = run("offering", "list")
        assert code == 0
        assert "(no offerings)" in out

    def test_lists_with_columns(self, run, active_offering):
        code, out, _ = run("offering", "list")
        assert code == 0
        assert "ID" in out and "STATUS" in out and "PRICE" in out
        assert OFFERING_ID in out
        assert "active" in out
        assert "$0.99/sf" in out

    def test_status_filter(self, run, active_offering):
        code, out, _ = run("offering", "list", "--status", "draft")
        assert code == 0
        assert "(no offerings)" in out
        code, out, _ = run("offering", "list", "--status", "active")
        assert OFFERING_ID in out

    def test_json_output_parses(self, run, active_offering):
        code, out, _ = run("--json", "offering", "list")
        assert code == 0
        data = json.loads(out)
        assert isinstance(data, list) and len(data) == 1
        assert data[0]["id"] == OFFERING_ID
        assert data[0]["unit"] == "sf"
        assert data[0]["status"] == "active"


class TestOfferingSetStatus:
    def test_pauses_and_resumes(self, run, active_offering, paths):
        code, out, _ = run("offering", "set-status", OFFERING_ID, "paused")
        assert code == 0
        assert "is now paused" in out
        with Store(paths["db"]) as store:
            assert store.get_offering(OFFERING_ID).status is OfferingStatus.PAUSED
        code, out, _ = run("--json", "offering", "set-status", OFFERING_ID, "active")
        assert code == 0
        assert json.loads(out) == {"id": OFFERING_ID, "status": "active"}

    def test_unknown_offering_is_an_error(self, run, paths):
        run("init")
        code, out, err = run("offering", "set-status", "nope", "sold")
        assert code == 1
        assert "nope" in err
        assert "KeyError" not in err  # the message, not the exception repr
        assert out == ""


class TestOfferingPreview:
    def test_blast_preview_never_leaks_internal_data(self, run, active_offering):
        code, out, _ = run("offering", "preview", OFFERING_ID)
        assert code == 0
        assert "Subject: MAKE OFFERS: 5mm/12mil Silver Rustic Oak SPC Vinyl Click Flooring (New" in out
        assert f"To: {SENDER} (Bcc: buyer list)" in out
        lowered = out.lower()
        for secret in ("delete red", "cost fob", "suggested sell", "0.84", "mill is closing out"):
            assert secret not in lowered
        assert "Reply with REMOVE" in out
        assert "-Alex Rivera" in out

    def test_internal_preview_contains_cost_sheet(self, run, active_offering):
        code, out, _ = run("offering", "preview", OFFERING_ID, "--internal")
        assert code == 0
        assert f"To: {SALES}" in out
        assert "DELETE RED" in out
        assert "COST FOB: $0.84/sf" in out
        assert "(AWR " in out

    def test_internal_preview_without_sales_team_email(self, run, active_offering, monkeypatch):
        monkeypatch.delenv("OFFERINGS_SALES_TEAM_EMAIL")
        code, out, _ = run("offering", "preview", OFFERING_ID, "--internal")
        assert code == 0
        assert "sales_team_email not set" in out

    def test_personal_preview_uses_buyer_name_and_note(self, run, active_offering):
        run("buyers", "add", "buyer-one@example.com", "--name", "Marcus Thibodeaux")
        code, out, _ = run(
            "offering", "preview", OFFERING_ID, "--buyer", "buyer-one@example.com", "--note", "Pretty great deal."
        )
        assert code == 0
        assert "Subject: MARCUS>>MAKE OFFERS: 5mm/12mil Silver Rustic Oak" in out
        assert "To: buyer-one@example.com" in out
        assert "Hi Marcus," in out
        assert "Pretty great deal." in out

    def test_personal_preview_for_unknown_buyer(self, run, active_offering):
        code, out, _ = run("--json", "offering", "preview", OFFERING_ID, "--buyer", "new-buyer@example.com")
        assert code == 0
        data = json.loads(out)
        assert data["to"] == "new-buyer@example.com"
        assert data["subject"].startswith("MAKE OFFERS:")  # no name → no NAME>> prefix
        assert data["html"].startswith("<p>")

    def test_unknown_offering_is_an_error(self, run, paths):
        run("init")
        code, _, err = run("offering", "preview", "ghost")
        assert code == 1
        assert "Unknown offering" in err

    def test_internal_and_buyer_are_mutually_exclusive(self, paths, capsys):
        with pytest.raises(SystemExit) as exc_info:
            main(["offering", "preview", "x", "--internal", "--buyer", "a@example.com"])
        assert exc_info.value.code == 2


# --------------------------------------------------------------------------- #
# buyers
# --------------------------------------------------------------------------- #


class TestBuyersImport:
    def test_import_example_csv(self, run, paths, buyers_csv):
        run("init")
        code, out, _ = run("buyers", "import", str(buyers_csv))
        assert code == 0
        assert "Imported" in out and "buyer(s)" in out
        with Store(paths["db"]) as store:
            buyers = store.list_buyers(None)
        assert len(buyers) >= 2
        assert all(b.email.endswith("@example.com") for b in buyers)

    def test_rejected_lines_are_reported(self, run, paths, tmp_path):
        run("init")
        csv_path = tmp_path / "mixed.csv"
        csv_path.write_text(
            "email,name\nbuyer-one@example.com,Marcus\nnot-an-email,Nobody\n", encoding="utf-8"
        )
        code, out, _ = run("--json", "buyers", "import", str(csv_path))
        assert code == 0
        data = json.loads(out)
        assert data["imported"] == 1
        assert data["rejected"] == ["not-an-email,Nobody"]
        code, out, _ = run("buyers", "import", str(csv_path))
        assert "Rejected 1 line(s)" in out
        assert "not-an-email,Nobody" in out

    def test_missing_file_is_an_error(self, run, paths, tmp_path):
        run("init")
        code, _, err = run("buyers", "import", str(tmp_path / "none.csv"))
        assert code == 1
        assert "none.csv" in err

    def test_header_without_email_is_an_error(self, run, paths, tmp_path):
        run("init")
        csv_path = tmp_path / "bad.csv"
        csv_path.write_text("name,company\nMarcus,Floors\n", encoding="utf-8")
        code, _, err = run("buyers", "import", str(csv_path))
        assert code == 1
        assert "header" in err.lower()


class TestBuyersAdd:
    def test_add_new_buyer(self, run, paths):
        run("init")
        code, out, _ = run(
            "buyers", "add", "Buyer-Six@Example.com", "--name", "Jordan Lee", "--company", "Lee Flooring", "--zip", "37203"
        )
        assert code == 0
        assert "Added buyer buyer-six@example.com [active]: Jordan Lee, Lee Flooring, 37203" in out
        with Store(paths["db"]) as store:
            buyer = store.get_buyer("buyer-six@example.com")
        assert buyer.postal_code == "37203"

    def test_add_existing_merges_and_keeps_status(self, run, paths):
        run("init")
        run("buyers", "add", "buyer-one@example.com", "--name", "Marcus Thibodeaux", "--zip", "73127")
        run("buyers", "unsubscribe", "buyer-one@example.com")
        code, out, _ = run("--json", "buyers", "add", "buyer-one@example.com", "--company", "Thibodeaux Flooring")
        assert code == 0
        data = json.loads(out)
        assert data["created"] is False
        assert data["buyer"]["name"] == "Marcus Thibodeaux"  # kept
        assert data["buyer"]["company"] == "Thibodeaux Flooring"  # added
        assert data["buyer"]["postal_code"] == "73127"
        assert data["buyer"]["status"] == "unsubscribed"  # never resurrected by an add

    def test_invalid_email_is_an_error(self, run, paths):
        run("init")
        code, _, err = run("buyers", "add", "not-an-email")
        assert code == 1
        assert "Invalid buyer email" in err


class TestBuyersList:
    def test_lists_all_statuses_by_default(self, run, paths):
        run("init")
        run("buyers", "add", "buyer-one@example.com", "--name", "Marcus Thibodeaux")
        run("buyers", "add", "buyer-two@example.com")
        run("buyers", "unsubscribe", "buyer-two@example.com")
        code, out, _ = run("buyers", "list")
        assert code == 0
        assert "buyer-one@example.com" in out and "buyer-two@example.com" in out
        assert "unsubscribed" in out
        assert "2 buyer(s)" in out

    def test_status_filter_and_json(self, run, paths):
        run("init")
        run("buyers", "add", "buyer-one@example.com")
        run("buyers", "add", "buyer-two@example.com")
        run("buyers", "unsubscribe", "buyer-two@example.com")
        code, out, _ = run("--json", "buyers", "list", "--status", "unsubscribed")
        assert code == 0
        data = json.loads(out)
        assert [b["email"] for b in data] == ["buyer-two@example.com"]

    def test_empty_list(self, run, paths):
        run("init")
        code, out, _ = run("buyers", "list")
        assert code == 0
        assert "(no buyers)" in out
        assert "0 buyer(s)" in out


class TestBuyersUnsubscribe:
    def test_unsubscribes(self, run, paths):
        run("init")
        run("buyers", "add", "buyer-one@example.com")
        code, out, _ = run("buyers", "unsubscribe", "Buyer-One@example.com")
        assert code == 0
        assert "Unsubscribed buyer-one@example.com" in out
        with Store(paths["db"]) as store:
            assert store.get_buyer("buyer-one@example.com").status is BuyerStatus.UNSUBSCRIBED

    def test_json_output(self, run, paths):
        run("init")
        run("buyers", "add", "buyer-one@example.com")
        code, out, _ = run("--json", "buyers", "unsubscribe", "buyer-one@example.com")
        assert json.loads(out) == {"email": "buyer-one@example.com", "status": "unsubscribed"}

    def test_unknown_buyer_is_an_error(self, run, paths):
        run("init")
        code, _, err = run("buyers", "unsubscribe", "ghost@example.com")
        assert code == 1
        assert "ghost@example.com" in err


# --------------------------------------------------------------------------- #
# blast / internal
# --------------------------------------------------------------------------- #


class TestBlast:
    def test_schedules_without_sending(self, run, active_offering, paths, buyers_csv):
        run("buyers", "import", str(buyers_csv))
        code, out, _ = run("blast", OFFERING_ID)
        assert code == 0
        assert f"Scheduled campaign {OFFERING_ID}-blast-1 (blast)" in out
        assert "recipient(s)" in out
        assert "Run 'offerings run' to send it." in out
        assert _eml_files(paths["outbox"]) == []
        with Store(paths["db"]) as store:
            campaign = store.get_campaign(f"{OFFERING_ID}-blast-1")
        assert campaign.status is CampaignStatus.SCHEDULED

    def test_now_dispatches_immediately(self, run, active_offering, paths, buyers_csv):
        run("buyers", "import", str(buyers_csv))
        code, out, _ = run("--json", "blast", OFFERING_ID, "--now")
        assert code == 0
        data = json.loads(out)
        assert data["campaign"]["id"] == f"{OFFERING_ID}-blast-1"
        assert data["campaign"]["status"] == "sent"
        assert data["recipients"] >= 2
        assert data["report"]["campaigns_sent"] == 1
        assert data["report"]["messages_sent"] == data["recipients"]
        assert data["report"]["live"] is False
        files = _eml_files(paths["outbox"])
        assert len(files) == 1 and files[0].name == "1-sent.eml"
        message, _ = _parse_eml(files[0])
        assert message["To"] == SENDER
        assert len(message["Bcc"].split(",")) == data["recipients"]

    def test_now_text_output_shows_summary_and_state(self, run, active_offering, buyers_csv):
        run("buyers", "import", str(buyers_csv))
        code, out, _ = run("blast", OFFERING_ID, "--now")
        assert code == 0
        assert "[DRY RUN] campaigns=1" in out
        assert f"Campaign {OFFERING_ID}-blast-1 is now sent" in out

    def test_now_reports_a_deferred_campaign(self, run, active_offering, buyers_csv, monkeypatch):
        monkeypatch.setenv("OFFERINGS_POLICY_MAX_SENDS_PER_RUN", "0")
        run("buyers", "import", str(buyers_csv))
        code, out, _ = run("blast", OFFERING_ID, "--now")
        assert code == 0
        assert "is now scheduled (deferred: per-run cap" in out

    def test_personal_blast_to_one_buyer(self, run, active_offering, paths):
        run("buyers", "add", "buyer-one@example.com", "--name", "Marcus Thibodeaux")
        code, out, _ = run(
            "blast", OFFERING_ID, "--to", "buyer-one@example.com", "--kind", "personal",
            "--note", "I can deliver this truckload at $0.99/sf.", "--now",
        )
        assert code == 0
        assert "(personal): 1 recipient(s)" in out
        assert "Subject: MARCUS>>MAKE OFFERS:" in out
        assert "To: buyer-one@example.com" in out
        message, body = _parse_eml(_eml_files(paths["outbox"])[0])
        assert message["To"] == "buyer-one@example.com"
        assert message["Subject"].strip().startswith("MARCUS>>MAKE OFFERS:")
        assert "I can deliver this truckload at $0.99/sf." in body

    def test_update_kind_uses_update_subject(self, run, active_offering):
        run("buyers", "add", "buyer-one@example.com")
        code, out, _ = run("--json", "blast", OFFERING_ID, "--kind", "update")
        assert code == 0
        data = json.loads(out)
        assert data["campaign"]["kind"] == "update"
        assert data["campaign"]["subject"].endswith("update")

    def test_explicit_recipients_can_be_repeated(self, run, active_offering):
        code, out, _ = run(
            "--json", "blast", OFFERING_ID, "--to", "buyer-one@example.com", "buyer-two@example.com", "--to", "buyer-three@example.com"
        )
        assert code == 0
        assert json.loads(out)["recipients"] == 3

    def test_personal_without_single_recipient_is_an_error(self, run, active_offering):
        run("buyers", "add", "buyer-one@example.com")
        run("buyers", "add", "buyer-two@example.com")
        code, _, err = run("blast", OFFERING_ID, "--kind", "personal")
        assert code == 1
        assert "exactly one recipient" in err

    def test_no_buyers_is_an_error(self, run, active_offering):
        code, _, err = run("blast", OFFERING_ID)
        assert code == 1
        assert "No recipients" in err

    def test_unknown_offering_is_an_error(self, run, paths):
        run("init")
        code, _, err = run("blast", "ghost", "--to", "buyer-one@example.com")
        assert code == 1
        assert "Unknown offering" in err

    def test_invalid_recipient_is_an_error(self, run, active_offering):
        code, _, err = run("blast", OFFERING_ID, "--to", "not-an-address")
        assert code == 1
        assert "Invalid recipient" in err

    def test_invalid_kind_is_a_usage_error(self, paths, capsys):
        with pytest.raises(SystemExit) as exc_info:
            main(["blast", "x", "--kind", "follow_up"])
        assert exc_info.value.code == 2


class TestInternal:
    def test_schedules_cost_sheet_to_sales_team(self, run, active_offering, paths):
        code, out, _ = run("internal", OFFERING_ID)
        assert code == 0
        assert f"Scheduled campaign {OFFERING_ID}-internal-1 (internal): 1 recipient(s)" in out
        assert f"To: {SALES}" in out
        assert "(AWR " in out
        code, out, _ = run("run")
        assert code == 0
        message, body = _parse_eml(_eml_files(paths["outbox"])[0])
        assert message["To"] == SALES
        assert "(AWR " in message["Subject"].strip()
        assert "COST FOB: $0.84/sf" in body

    def test_json_output(self, run, active_offering):
        code, out, _ = run("--json", "internal", OFFERING_ID)
        assert code == 0
        data = json.loads(out)
        assert data["campaign"]["kind"] == "internal"
        assert data["campaign"]["recipients"] == [SALES]

    def test_without_sales_team_email_is_an_error(self, run, active_offering, monkeypatch):
        monkeypatch.delenv("OFFERINGS_SALES_TEAM_EMAIL")
        code, _, err = run("internal", OFFERING_ID)
        assert code == 1
        assert "sales_team_email" in err


# --------------------------------------------------------------------------- #
# run
# --------------------------------------------------------------------------- #


class TestRun:
    def test_single_pass_prints_summary(self, run, active_offering, paths):
        run("buyers", "add", "buyer-one@example.com")
        run("blast", OFFERING_ID)
        code, out, _ = run("run")
        assert code == 0
        assert out.startswith("[DRY RUN] campaigns=1 sent=1")
        assert "errors=0" in out
        assert len(_eml_files(paths["outbox"])) == 1

    def test_json_report(self, run, active_offering):
        code, out, _ = run("--json", "run")
        assert code == 0
        data = json.loads(out)
        assert data["live"] is False
        assert data["campaigns_sent"] == 0
        assert data["errors"] == []
        assert data["started_at"] and data["finished_at"]

    def test_errors_are_listed_under_the_summary(self, run, paths, monkeypatch):
        run("init")

        def broken_fetch(self, since, *, max_results=200):
            raise MailerError("Gmail 503: unavailable")

        monkeypatch.setattr(DryRunMailer, "fetch_inbound", broken_fetch)
        code, out, _ = run("run")
        assert code == 0  # per-item failures never fail the pass
        assert "errors=1" in out
        assert "  error: fetch_inbound failed: MailerError: Gmail 503: unavailable" in out

    def test_loop_stops_cleanly_on_keyboard_interrupt(self, run, active_offering, monkeypatch):
        sleeps: list[float] = []

        def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)
            raise KeyboardInterrupt

        monkeypatch.setattr(cli.time, "sleep", fake_sleep)
        code, out, err = run("run", "--loop", "--interval", "5")
        assert code == 0
        assert out.count("[DRY RUN]") == 1
        assert sleeps == [5.0]
        assert "stopping" in err.lower()

    def test_loop_runs_several_passes(self, run, active_offering, monkeypatch):
        calls = {"n": 0}

        def fake_sleep(seconds: float) -> None:
            calls["n"] += 1
            if calls["n"] == 3:
                raise KeyboardInterrupt

        monkeypatch.setattr(cli.time, "sleep", fake_sleep)
        code, out, _ = run("run", "--loop")
        assert code == 0
        assert out.count("[DRY RUN]") == 3

    def test_loop_default_interval(self, run, active_offering, monkeypatch):
        seen: list[float] = []

        def fake_sleep(seconds: float) -> None:
            seen.append(seconds)
            raise KeyboardInterrupt

        monkeypatch.setattr(cli.time, "sleep", fake_sleep)
        assert run("run", "--loop")[0] == 0
        assert seen == [cli.DEFAULT_LOOP_INTERVAL]


# --------------------------------------------------------------------------- #
# quote
# --------------------------------------------------------------------------- #


class TestQuote:
    def test_zip_quote_text(self, run, active_offering, distance):
        code, out, _ = run("quote", OFFERING_ID, "--zip", "73127", "--truckloads", "2")
        assert code == 0, out
        assert distance == [("Calhoun, GA", "73127, USA")]
        assert f"Offering: {OFFERING_ID}" in out
        assert "Route: Calhoun, GA -> 73127, USA: 800.0 mi" in out
        assert "Freight: $3,600 per truckload ($4.50/mi) x 2 = $7,200" in out
        assert "FOB price: $0.99/sf" in out
        assert "Delivered price: $1.17/sf" in out
        assert "Total: 40,000 sf for $46,800.00 delivered" in out

    def test_dest_quote(self, run, active_offering, distance):
        code, out, _ = run("quote", OFFERING_ID, "--dest", "Oklahoma City, OK")
        assert code == 0
        assert distance == [("Calhoun, GA", "Oklahoma City, OK")]
        assert "x 1 = $3,600" in out

    def test_json_quote_is_serialisable(self, run, active_offering, distance):
        code, out, _ = run("--json", "quote", OFFERING_ID, "--zip", "73127", "--truckloads", "2")
        assert code == 0
        data = json.loads(out)
        assert data["offering_id"] == OFFERING_ID
        assert data["unit"] == "sf"
        assert data["distance_miles"] == 800.0
        assert data["freight_per_truckload"] == 3600.0
        assert data["delivered_price_per_unit"] == 1.17
        assert data["units_total"] == 40000.0
        assert data["delivered_total"] == 46800.0
        assert data["quoted_at"].endswith("+00:00")

    def test_truckload_priced_offering_total(self, run, paths, distance, tmp_path):
        run("init")
        offering = tmp_path / "tl.json"
        offering.write_text(
            json.dumps({"id": "tile-tl", "title": "Porcelain Tile", "description": "", "fob_location": "Dalton, GA",
                        "unit": "TL", "sell_price": 8500}),
            encoding="utf-8",
        )
        run("offering", "add", "--file", str(offering))
        code, out, _ = run("quote", "tile-tl", "--zip", "75247", "--truckloads", "2")
        assert code == 0, out
        assert "Delivered price: $12,100/TL" in out
        assert "Total: $24,200.00 delivered for 2 truckload(s)" in out

    def test_without_maps_key_is_an_error(self, run, active_offering):
        code, out, err = run("quote", OFFERING_ID, "--zip", "73127")
        assert code == 1
        assert "OFFERINGS_GOOGLE_MAPS_API_KEY" in err
        assert out == ""

    def test_unknown_offering_is_an_error(self, run, paths, distance):
        run("init")
        code, _, err = run("quote", "ghost", "--zip", "73127")
        assert code == 1
        assert "Unknown offering" in err

    def test_non_positive_truckloads_is_an_error(self, run, active_offering, distance):
        code, _, err = run("quote", OFFERING_ID, "--zip", "73127", "--truckloads", "0")
        assert code == 1
        assert "Truckloads must be positive" in err

    def test_provider_failure_is_a_handled_error(self, run, active_offering, monkeypatch):
        monkeypatch.setenv("OFFERINGS_GOOGLE_MAPS_API_KEY", "fake")

        def failing_factory(api_key):
            def provider(origin, destination):
                raise ValueError("No route found: ZERO_RESULTS")

            return provider

        monkeypatch.setattr(engine_module, "google_distance_provider", failing_factory)
        code, _, err = run("quote", OFFERING_ID, "--zip", "00000")
        assert code == 1
        assert "ZERO_RESULTS" in err


# --------------------------------------------------------------------------- #
# digest
# --------------------------------------------------------------------------- #


class TestDigest:
    def test_prints_digest_after_a_blast(self, run, active_offering, buyers_csv):
        run("buyers", "import", str(buyers_csv))
        run("blast", OFFERING_ID, "--now")
        code, out, _ = run("digest", "--since-hours", "24")
        assert code == 0
        assert out.startswith("Subject: Offerings digest")
        assert "[DRY RUN]" in out
        assert "blast_sent: 1" in out
        assert "Drafts still awaiting review: 0" in out

    def test_send_writes_to_outbox(self, run, active_offering, paths):
        code, out, _ = run("digest", "--send")
        assert code == 0
        assert f"Digest sent to {SENDER} (dry run, see the outbox)." in out
        files = _eml_files(paths["outbox"])
        assert len(files) == 1
        message, body = _parse_eml(files[0])
        assert message["Subject"].strip().startswith("Offerings digest")
        assert message["To"] == SENDER
        assert "Offerings digest [DRY RUN]" in body

    def test_send_goes_to_escalation_email(self, run, active_offering, paths, monkeypatch):
        monkeypatch.setenv("OFFERINGS_ESCALATION_EMAIL", "owner@example.com")
        code, out, _ = run("--json", "digest", "--send")
        assert code == 0
        data = json.loads(out)
        assert data["sent"] is True
        assert data["to"] == "owner@example.com"
        assert data["result"]["dry_run"] is True
        assert data["result"]["message_id"] == "dry-1"

    def test_json_without_send(self, run, active_offering):
        code, out, _ = run("--json", "digest", "--since-hours", "2")
        assert code == 0
        data = json.loads(out)
        assert data["sent"] is False
        assert data["subject"].startswith("Offerings digest")
        assert "since" in data and "text" in data and "html" in data

    def test_default_window_is_24_hours(self, run, active_offering, paths):
        with Store(paths["db"]) as store:
            store.add_action(ActionRecord(kind=ActionKind.QUOTE_SENT, at=utcnow() - timedelta(hours=30), details="old"))
            store.add_action(ActionRecord(kind=ActionKind.OFFER_ESCALATED, at=utcnow() - timedelta(hours=1), details="new"))
        code, out, _ = run("digest")
        assert code == 0
        assert "offer_escalated: 1" in out
        assert "quote_sent" not in out

    def test_non_positive_hours_is_an_error(self, run, active_offering):
        code, _, err = run("digest", "--since-hours", "0")
        assert code == 1
        assert "--since-hours must be positive" in err

    def test_send_blocked_by_cap_is_an_error(self, run, active_offering, monkeypatch):
        monkeypatch.setenv("OFFERINGS_POLICY_MAX_SENDS_PER_DAY", "0")
        code, _, err = run("digest", "--send")
        assert code == 1
        assert "digest not sent" in err


# --------------------------------------------------------------------------- #
# status
# --------------------------------------------------------------------------- #


class TestStatus:
    def test_empty_database(self, run, paths):
        run("init")
        code, out, _ = run("status")
        assert code == 0
        assert out.startswith(f"Mode: DRY RUN (outbox: {paths['outbox']})")
        assert "Offerings: 0 (none)" in out
        assert "Buyers: 0 (none)" in out
        assert "Campaigns: 0 (none)" in out
        assert "Sends today" in out and "of max 1500" in out
        assert "Recent actions (last 0):" in out
        assert "  (none)" in out

    def test_counts_by_status_and_recent_actions(self, run, active_offering, buyers_csv):
        run("buyers", "import", str(buyers_csv))
        run("buyers", "unsubscribe", "buyer-one@example.com")
        run("blast", OFFERING_ID, "--now")
        code, out, _ = run("status")
        assert code == 0
        assert "Offerings: 1 (active=1)" in out
        assert "unsubscribed=1" in out
        assert "Campaigns: 1 (sent=1)" in out
        assert "blast_sent" in out
        assert "Classifier: rules; auto-reply mode: draft" in out

    def test_json_redacts_secrets(self, run, active_offering, monkeypatch):
        monkeypatch.setenv("OFFERINGS_GMAIL_CLIENT_SECRET", "super-secret-value")
        monkeypatch.setenv("OFFERINGS_GOOGLE_MAPS_API_KEY", "maps-secret-value")
        code, out, _ = run("--json", "status")
        assert code == 0
        assert "super-secret-value" not in out
        assert "maps-secret-value" not in out
        data = json.loads(out)
        assert data["mode"] == "dry_run"
        assert data["live"] is False
        assert data["settings"]["gmail_client_secret"] == "***"
        assert data["settings"]["google_maps_api_key"] == "***"
        assert data["offerings"] == {"total": 1, "by_status": {"active": 1}}
        assert data["buyers"]["total"] == 0
        assert data["sends_today"]["count"] == 0
        assert isinstance(data["actions"], list)

    def test_json_actions_are_serialisable(self, run, active_offering):
        run("buyers", "add", "buyer-one@example.com")
        run("blast", OFFERING_ID, "--now")
        code, out, _ = run("--json", "status")
        data = json.loads(out)
        assert data["campaigns"] == {"total": 1, "by_status": {"sent": 1}}
        assert data["actions"][-1]["kind"] == "blast_sent"
        assert data["actions"][-1]["at"].endswith("+00:00")

    def test_shows_at_most_ten_actions(self, run, active_offering, paths):
        with Store(paths["db"]) as store:
            for index in range(12):
                store.add_action(ActionRecord(kind=ActionKind.IGNORED, at=utcnow(), details=f"item {index}"))
        code, out, _ = run("status")
        assert "Recent actions (last 10):" in out
        assert "item 11" in out and "item 0" not in out

    def test_timezone_fallback_and_known_zone(self, run, active_offering, monkeypatch):
        monkeypatch.setenv("OFFERINGS_TIMEZONE", "Nowhere/Invalid")
        code, out, _ = run("status")
        assert code == 0
        assert "Sends today (" in out
        monkeypatch.setattr(cli, "ZoneInfo", lambda name: timezone(timedelta(hours=-5)))
        code, out, _ = run("status")
        assert code == 0

    def test_live_status_checks_gmail_credentials(self, run, active_offering, monkeypatch):
        for key in ("CLIENT_ID", "CLIENT_SECRET", "REFRESH_TOKEN"):
            monkeypatch.setenv(f"OFFERINGS_GMAIL_{key}", "configured")
        fake = MagicMock()
        fake.access_token.return_value = "ya29.token"
        monkeypatch.setattr(cli, "build_mailer", lambda settings: fake)
        code, out, _ = run("--live", "status")
        assert code == 0
        assert f"Mode: LIVE (Gmail as {SENDER})" in out
        assert "Gmail credentials: OK" in out
        fake.access_token.assert_called_once_with()

    def test_live_status_reports_bad_credentials(self, run, active_offering, monkeypatch):
        for key in ("CLIENT_ID", "CLIENT_SECRET", "REFRESH_TOKEN"):
            monkeypatch.setenv(f"OFFERINGS_GMAIL_{key}", "configured")
        fake = MagicMock()
        fake.access_token.side_effect = MailerError("Gmail token 400: invalid_grant")
        monkeypatch.setattr(cli, "build_mailer", lambda settings: fake)
        code, out, err = run("--live", "status")
        assert code == 1
        assert "Gmail token 400: invalid_grant" in err
        assert out == ""

    def test_live_status_with_transport_lacking_a_check(self, run, active_offering, monkeypatch):
        for key in ("CLIENT_ID", "CLIENT_SECRET", "REFRESH_TOKEN"):
            monkeypatch.setenv(f"OFFERINGS_GMAIL_{key}", "configured")
        monkeypatch.setattr(cli, "build_mailer", lambda settings: DryRunMailer())
        code, out, _ = run("--json", "--live", "status")
        assert code == 0
        assert "does not expose a credential check" in json.loads(out)["gmail"]


# --------------------------------------------------------------------------- #
# Error handling and helpers
# --------------------------------------------------------------------------- #


class TestErrorHandling:
    def test_handled_errors_cover_the_documented_set(self):
        assert ValueError in HANDLED_ERRORS
        assert MailerError in HANDLED_ERRORS
        assert EngineError in HANDLED_ERRORS
        assert OSError in HANDLED_ERRORS  # FileNotFoundError
        assert sqlite3.Error in HANDLED_ERRORS

    def test_error_message_strips_keyerror_quotes(self):
        assert cli._error_message(KeyError("Unknown buyer: 'x'")) == "Unknown buyer: 'x'"
        assert cli._error_message(KeyError()) == "KeyError"
        assert cli._error_message(ValueError("")) == "ValueError"
        assert cli._error_message(ValueError("bad")) == "bad"

    def test_unexpected_exceptions_propagate(self, run, paths, monkeypatch):
        run("init")

        def explode(self, *args, **kwargs):
            raise RuntimeError("unexpected")

        monkeypatch.setattr(Store, "list_offerings", explode)
        with pytest.raises(RuntimeError, match="unexpected"):
            main(["offering", "list"])

    def test_store_is_closed_after_a_failure(self, run, paths, monkeypatch):
        run("init")
        closed: list[bool] = []
        original_close = Store.close

        def tracking_close(self):
            closed.append(True)
            original_close(self)

        monkeypatch.setattr(Store, "close", tracking_close)
        code, _, _ = run("offering", "set-status", "ghost", "sold")
        assert code == 1
        assert closed == [True]


class TestHelpers:
    def test_jsonable_handles_nested_types(self, tmp_path):
        value = {
            "status": OfferingStatus.ACTIVE,
            "when": utcnow(),
            "path": tmp_path,
            "items": (1, OfferingStatus.DRAFT),
            "nested": {"set": {"a"}},
            "plain": 3,
        }
        data = cli._jsonable(value)
        assert data["status"] == "active"
        assert data["when"].endswith("+00:00")
        assert data["path"] == str(tmp_path)
        assert data["items"] == [1, "draft"]
        assert data["nested"] == {"set": ["a"]}
        assert data["plain"] == 3
        json.dumps(data)

    def test_table_formatting(self):
        assert cli._table(("A", "B"), []) == []
        lines = cli._table(("A", "LONGER"), [("x", 1), ("yy", 22)])
        assert lines[0] == "A   LONGER"
        assert lines[1] == "x   1"
        assert lines[2] == "yy  22"

    def test_number_formatting(self):
        assert cli._number(2.0) == "2"
        assert cli._number(40000) == "40,000"
        assert cli._number(1.5) == "1.5"
        assert cli._number(0.25) == "0.25"

    def test_counts_phrase(self):
        assert cli._counts_phrase({}) == "none"
        assert cli._counts_phrase({"active": 2, "draft": 1}) == "active=2, draft=1"


# --------------------------------------------------------------------------- #
# The documented dry-run flow and safety invariants
# --------------------------------------------------------------------------- #


class TestFullDryRunFlow:
    def test_init_import_add_blast_status(self, run, paths, buyers_csv, monkeypatch):
        # Dry run must never touch the network: make any HTTP attempt explode.
        def no_network(*args, **kwargs):
            raise AssertionError("network call attempted in dry run")

        monkeypatch.setattr(requests.Session, "request", no_network)
        monkeypatch.setattr(requests, "get", no_network)
        monkeypatch.setattr(requests, "post", no_network)

        code, out, err = run("init")
        assert code == 0, err
        assert paths["db"].is_file()

        code, out, err = run("buyers", "import", str(buyers_csv))
        assert code == 0, err
        imported = int(out.split("Imported ")[1].split(" ")[0])
        assert imported >= 2

        code, out, err = run("offering", "add", "--file", str(OFFERING_FILE), "--activate")
        assert code == 0, err
        assert f"Added offering {OFFERING_ID} [active]" in out

        code, out, err = run("blast", OFFERING_ID, "--now")
        assert code == 0, err
        assert f"Scheduled campaign {OFFERING_ID}-blast-1 (blast): {imported} recipient(s)" in out
        assert f"[DRY RUN] campaigns=1 sent={imported}" in out
        assert f"Campaign {OFFERING_ID}-blast-1 is now sent" in out

        code, out, err = run("status")
        assert code == 0, err
        assert "Campaigns: 1 (sent=1)" in out
        assert f"Buyers: {imported} (active={imported})" in out
        assert "blast_sent" in out

        files = _eml_files(paths["outbox"])
        assert len(files) == 1
        message, body = _parse_eml(files[0])
        assert message["Subject"].strip().startswith("MAKE OFFERS: 5mm/12mil Silver Rustic Oak SPC Vinyl Click Flooring (New ")
        assert message["To"] == SENDER
        assert "buyer-one@example.com" in message["Bcc"]
        assert "$0.99/sf FOB Calhoun, GA" in body
        lowered = (str(message) + body).lower()
        for secret in ("delete red", "cost fob", "suggested sell", "mill is closing out", "0.84"):
            assert secret not in lowered

        code, out, _ = run("--json", "status")
        data = json.loads(out)
        assert data["campaigns"]["by_status"] == {"sent": 1}
        assert data["sends_today"]["count"] == imported

    def test_suppressed_buyer_gets_nothing(self, run, paths, buyers_csv):
        run("init")
        run("buyers", "import", str(buyers_csv))
        run("buyers", "unsubscribe", "buyer-one@example.com")
        run("offering", "add", "--file", str(OFFERING_FILE), "--activate")
        code, out, _ = run("blast", OFFERING_ID, "--now")
        assert code == 0
        message, _ = _parse_eml(_eml_files(paths["outbox"])[0])
        assert "buyer-one@example.com" not in message["Bcc"]
        assert "buyer-two@example.com" in message["Bcc"]

    def test_bcc_chunking_follows_policy(self, run, paths, buyers_csv, monkeypatch):
        monkeypatch.setenv("OFFERINGS_POLICY_BCC_CHUNK_SIZE", "2")
        run("init")
        run("buyers", "import", str(buyers_csv))
        run("offering", "add", "--file", str(OFFERING_FILE), "--activate")
        code, out, _ = run("--json", "blast", OFFERING_ID, "--now")
        recipients = json.loads(out)["recipients"]
        expected_files = -(-recipients // 2)  # ceil
        assert len(_eml_files(paths["outbox"])) == expected_files
