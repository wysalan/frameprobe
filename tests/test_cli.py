from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from frameprobe import cli

from .test_probe import healthy_adb

runner = CliRunner()


@pytest.fixture
def fake_adb(monkeypatch: pytest.MonkeyPatch):
    adb = healthy_adb()
    monkeypatch.setattr(cli, "_adb", lambda serial: adb)
    import frameprobe.probe as probe_mod

    monkeypatch.setattr(probe_mod.time, "sleep", lambda _s: None)
    return adb


def test_doctor_table(fake_adb) -> None:
    result = runner.invoke(cli.app, ["doctor"])
    assert result.exit_code == 0, result.output
    assert "Pixel 10" in result.output
    assert "✅" in result.output
    assert "CPU=61.9°C" in result.output
    assert "MODERATE" in result.output
    assert "即時模式" in result.output


def test_doctor_json(fake_adb) -> None:
    result = runner.invoke(cli.app, ["doctor", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["device"]["sdk_int"] == 37
    assert {c["name"] for c in data["checks"]} >= {"timestats", "thermal", "perfetto_binary"}


def test_doctor_blocking_exit_code(fake_adb) -> None:
    fake_adb.props["ro.build.version.sdk"] = "30"
    fake_adb.responses[r"timestats -clear -enable"] = ("", 1)
    fake_adb.responses[r"perfetto --version"] = ("", 127)
    result = runner.invoke(cli.app, ["doctor", "--verbose"])
    assert result.exit_code == 2
    assert "皆不可用" in result.output


def test_devices_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cli.Adb, "list_devices", staticmethod(lambda: [{"serial": "X", "state": "device"}])
    )
    result = runner.invoke(cli.app, ["devices", "--json"])
    assert json.loads(result.output) == [{"serial": "X", "state": "device"}]


def test_layers_table_and_json(fake_adb) -> None:
    result = runner.invoke(cli.app, ["layers", "-p", "com.example.game"])
    assert result.exit_code == 0, result.output
    assert "→" in result.output and "將自動選用" in result.output
    assert "SplashActivity" not in result.output

    data = json.loads(runner.invoke(cli.app, ["layers", "-p", "com.example.game", "--json"]).output)
    assert data["ambiguous"] is False
    assert data["selected"].startswith("SurfaceView[com.example.game")
    assert data["candidates"][0]["total_frames"] == 1000


def test_layers_ambiguous_and_missing(fake_adb) -> None:
    fake_adb.responses[r"timestats -dump"] = (
        "layerName = SurfaceView[com.x/A]#1\ntotalFrames = 100\n\n"
        "layerName = SurfaceView[com.x/B]#2\ntotalFrames = 95\n",
        0,
    )
    result = runner.invoke(cli.app, ["layers", "-p", "com.x"])
    assert result.exit_code == 0 and "--layer" in result.output
    result = runner.invoke(cli.app, ["layers", "-p", "com.nothere"])
    assert result.exit_code == 1 and "找不到" in result.output


def test_export_cli(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from datetime import datetime

    from frameprobe.probe import DeviceInfo
    from frameprobe.storage import SessionStore, SessionSummary

    from .test_storage import make_sample

    monkeypatch.setenv("FRAMEPROBE_SESSIONS", str(tmp_path))
    store = SessionStore(tmp_path)
    store.create("sid")
    store.append_sample("sid", make_sample(1.0))
    store.write_summary(
        "sid",
        SessionSummary("sid", DeviceInfo("S"), "p", "l", "realtime", datetime(2026, 1, 1)),
    )
    result = runner.invoke(cli.app, ["export", "sid"])
    assert result.exit_code == 0 and result.output.startswith("ts,elapsed")
    out = tmp_path / "x.json"
    result = runner.invoke(cli.app, ["export", "sid", "--format", "json", "-o", str(out)])
    assert result.exit_code == 0 and json.loads(out.read_text())["session_id"] == "sid"
    assert runner.invoke(cli.app, ["export", "sid", "--format", "xml"]).exit_code == 1
    assert runner.invoke(cli.app, ["export", "nope"]).exit_code == 1


def test_serve_without_frontend(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    import uvicorn

    captured = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: captured.update(kw, app=app))
    import frameprobe.server.app as app_mod

    monkeypatch.setattr(app_mod, "WEB_DIST", tmp_path / "missing")
    result = runner.invoke(cli.app, ["serve", "--port", "9999", "--sessions-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "前端尚未 build" in result.output and captured["port"] == 9999
    assert captured["app"].state.manager.store.root == tmp_path


def test_launch_cli(fake_adb) -> None:
    from .test_launch import AM_OUT

    fake_adb.responses[r"cmd package resolve-activity"] = ("com.example.game/.Main\n", 0)
    fake_adb.responses[r"^am force-stop"] = ("", 0)
    fake_adb.responses[r"^am start -W"] = (AM_OUT, 0)
    import frameprobe.launch as launch_mod

    launch_mod.time.sleep = lambda _s: None  # type: ignore[assignment]
    result = runner.invoke(cli.app, ["launch", "-p", "com.example.game"])
    assert result.exit_code == 0, result.output
    assert "812 ms" in result.output
    data = json.loads(
        runner.invoke(cli.app, ["launch", "-p", "com.example.game", "--warm", "--json"]).output
    )
    assert data["cold"] is False and data["total_ms"] == 812
