from __future__ import annotations

import json
from types import SimpleNamespace

from scripts import weather_guarded_due_pipeline_runner as runner


def _args(tmp_path, *, now_utc="2026-06-28T03:06:00Z"):
    return runner.parse_args(
        [
            "--run-after-utc",
            "2026-06-28T03:05:00Z",
            "--paper-journal-dir",
            str(tmp_path / "paper"),
            "--strict-gate-queue-dir",
            str(tmp_path / "strict_queues"),
            "--orderbook-archive-dir",
            str(tmp_path / "archive"),
            "--backfill-dir",
            str(tmp_path / "backfill"),
            "--summary-output",
            str(tmp_path / "evidence" / "due_pipeline_metar_ltac_uuww_after_due.json"),
            "--lock-file",
            str(tmp_path / "evidence" / ".due_pipeline_metar_ltac_uuww.lock"),
            "--done-file",
            str(tmp_path / "evidence" / ".due_pipeline_metar_ltac_uuww.done"),
            "--include-station-code",
            "LTAC",
            "--include-station-code",
            "UUWW",
            "--include-settlement-source",
            "metar",
            "--include-market-slug",
            "highest-temperature-in-moscow-on-june-29-2026-23corhigher",
            "--confirm",
            "PAPER_ONLY_ARCHIVED_ORDERBOOK_REFRESH",
            "--allow-partial-official-truth",
            "--fetch-external-official-values",
            "--now-utc",
            now_utc,
        ]
    )


def test_guarded_due_runner_before_due_writes_pending_without_pipeline(tmp_path):
    calls = []

    rc = runner.run_guarded_due_pipeline(
        _args(tmp_path, now_utc="2026-06-28T03:04:00Z"),
        subprocess_run=lambda *args, **kwargs: calls.append(args),
    )

    pending = json.loads((tmp_path / "evidence" / "due_pipeline_metar_ltac_uuww_pending.json").read_text())
    assert rc == 0
    assert calls == []
    assert pending["status"] == "pending"
    assert pending["live_order_path"] is False
    assert pending["run_after_utc"] == "2026-06-28T03:05:00Z"
    assert pending["include_market_slug"] == ["highest-temperature-in-moscow-on-june-29-2026-23corhigher"]


def test_guarded_due_runner_after_due_calls_pipeline_with_subset_args(tmp_path):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        summary_path = command[command.index("--summary-output") + 1]
        with open(summary_path, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "live_order_path": False,
                    "strict_replay": {
                        "resolved_fill_count": 0,
                        "resolved_pnl_cents": None,
                    },
                },
                handle,
            )
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    rc = runner.run_guarded_due_pipeline(_args(tmp_path), subprocess_run=fake_run)

    assert rc == 0
    assert len(calls) == 1
    command = calls[0]
    assert "--execute-closed-backfill" in command
    assert command[command.index("--strict-gate-queue-dir") + 1] == str(tmp_path / "strict_queues")
    assert command[command.index("--confirm") + 1] == "PAPER_ONLY_ARCHIVED_ORDERBOOK_REFRESH"
    assert command.count("--include-station-code") == 2
    assert command[command.index("--include-station-code") + 1] == "LTAC"
    assert command[command.index("--include-station-code", command.index("--include-station-code") + 1) + 1] == "UUWW"
    assert command[command.index("--include-settlement-source") + 1] == "metar"
    assert "--allow-partial-official-truth" in command
    assert "--fetch-external-official-values" in command
    assert "--live" not in command
    done = json.loads((tmp_path / "evidence" / ".due_pipeline_metar_ltac_uuww.done").read_text())
    assert done["status"] == "completed"
    assert done["live_order_path"] is False
    assert not (tmp_path / "evidence" / ".due_pipeline_metar_ltac_uuww.lock").exists()


def test_guarded_due_runner_done_file_skips_pipeline(tmp_path):
    calls = []
    done_file = tmp_path / "evidence" / ".due_pipeline_metar_ltac_uuww.done"
    done_file.parent.mkdir(parents=True)
    done_file.write_text("{}", encoding="utf-8")

    rc = runner.run_guarded_due_pipeline(
        _args(tmp_path),
        subprocess_run=lambda *args, **kwargs: calls.append(args),
    )

    assert rc == 0
    assert calls == []


def test_guarded_due_runner_active_lock_skips_pipeline(tmp_path):
    calls = []
    lock_file = tmp_path / "evidence" / ".due_pipeline_metar_ltac_uuww.lock"
    lock_file.parent.mkdir(parents=True)
    lock_file.write_text(
        json.dumps({"created_at_utc": "2026-06-28T03:05:30Z"}),
        encoding="utf-8",
    )

    rc = runner.run_guarded_due_pipeline(
        _args(tmp_path),
        subprocess_run=lambda *args, **kwargs: calls.append(args),
    )

    assert rc == 0
    assert calls == []
    assert lock_file.exists()


def test_guarded_due_runner_check_only_after_due_does_not_execute_pipeline(tmp_path):
    calls = []
    args = _args(tmp_path)
    args.check_only = True

    rc = runner.run_guarded_due_pipeline(
        args,
        subprocess_run=lambda *args, **kwargs: calls.append(args),
    )

    pending = json.loads((tmp_path / "evidence" / "due_pipeline_metar_ltac_uuww_pending.json").read_text())
    assert rc == 0
    assert calls == []
    assert pending["status"] == "ready_to_run"
    assert "--execute-closed-backfill" in pending["command_preview"]
    assert "--live" not in pending["command_preview"]


def test_guarded_due_runner_rejects_unresolved_zero_pnl(tmp_path):
    def fake_run(command, **kwargs):
        summary_path = command[command.index("--summary-output") + 1]
        with open(summary_path, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "live_order_path": False,
                    "strict_replay": {
                        "resolved_fill_count": 0,
                        "resolved_pnl_cents": 0.0,
                    },
                },
                handle,
            )
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    rc = runner.run_guarded_due_pipeline(_args(tmp_path), subprocess_run=fake_run)

    failure = json.loads((tmp_path / "evidence" / "due_pipeline_metar_ltac_uuww_after_due_failure.json").read_text())
    assert rc == 1
    assert failure["failure_reason"] == "unresolved_pnl_cents_must_be_null_not_zero"
    assert not (tmp_path / "evidence" / ".due_pipeline_metar_ltac_uuww.done").exists()
