from __future__ import annotations

import plistlib
from types import SimpleNamespace

from scripts import weather_install_bucket_family_arbitrage_launchd as installer


def _args(tmp_path):
    return installer.parse_args(
        [
            "--repo-root",
            str(tmp_path),
            "--plist-path",
            str(tmp_path / "LaunchAgents" / "com.polyweather.bucket-family-arbitrage-sampler.plist"),
            "--python-path",
            str(tmp_path / ".venv" / "bin" / "python"),
            "--install-report-output",
            str(tmp_path / "install_report.json"),
        ]
    )


def test_bucket_family_launchd_plist_contains_sampler_args(tmp_path):
    args = _args(tmp_path)
    plist = installer.build_plist(args)

    assert plist["Label"] == "com.polyweather.bucket-family-arbitrage-sampler"
    assert plist["StartInterval"] == 60
    assert plist["WorkingDirectory"] == str(tmp_path.resolve())
    assert plist["StandardOutPath"].endswith("evidence/bucket_family/logs/arbitrage_sampler.out.log")
    assert plist["StandardErrorPath"].endswith("evidence/bucket_family/logs/arbitrage_sampler.err.log")
    argv = plist["ProgramArguments"]
    assert "scripts/weather_bucket_family_arbitrage_report.py" in argv[1]
    assert "--paper-only" in argv
    assert "--orderbook-archive-dir" in argv
    assert "evidence/bucket_family/orderbook_archive" in argv
    assert "--paper-fill-dir" in argv
    assert "evidence/bucket_family/basket_paper" in argv
    assert "--min-edge-cents" in argv
    assert "1.0" in argv
    assert "--live" not in argv


def test_bucket_family_launchd_install_writes_plist_and_report(tmp_path):
    args = _args(tmp_path)
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    report = installer.install_launchd(args, run=fake_run)

    plist_path = tmp_path / "LaunchAgents" / "com.polyweather.bucket-family-arbitrage-sampler.plist"
    assert report["installed"] is True
    assert report["live_order_path"] is False
    assert calls[0][:2] == ["launchctl", "bootstrap"]
    assert calls[1][:2] == ["launchctl", "enable"]
    assert calls[2][:3] == ["launchctl", "kickstart", "-k"]
    with plist_path.open("rb") as handle:
        plist = plistlib.load(handle)
    assert plist["StartInterval"] == 60
    assert plist["WorkingDirectory"] == str(tmp_path.resolve())
