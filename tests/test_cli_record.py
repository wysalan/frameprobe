from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from frameprobe import cli
from frameprobe.storage import SessionStore

from .conftest import load
from .test_analysis import FakeQuery
from .test_perfetto_runner import RecordingAdb

runner = CliRunner()


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> RecordingAdb:
    adb = RecordingAdb()
    adb.props["ro.build.version.sdk"] = "37"
    adb.responses[r"^id$"] = ("uid=2000(shell)", 0)
    adb.responses[r"timestats -clear -enable"] = ("", 0)
    adb.responses[r"timestats -disable"] = ("", 0)
    adb.responses[r"timestats -dump"] = (load("timestats_android17.txt"), 0)
    adb.responses[r"--latency"] = ("16666666\n1 2 3\n", 0)
    adb.responses[r"^screencap -p"] = ("", 0)
    monkeypatch.setattr(cli, "_adb", lambda serial: adb)
    monkeypatch.setenv("FRAMEPROBE_SESSIONS", str(tmp_path))

    from contextlib import contextmanager

    @contextmanager
    def fake_open_trace(path):
        assert Path(path).exists()
        yield FakeQuery(app_frames=False, thermal_tracks=False)

    monkeypatch.setattr(cli, "open_trace", fake_open_trace)
    import frameprobe.session as session_mod

    monkeypatch.setattr(session_mod, "open_trace", fake_open_trace)
    return adb


def test_record_then_analyze(env: RecordingAdb, tmp_path: Path) -> None:
    result = runner.invoke(
        cli.app,
        [
            "record",
            "-p",
            "com.example.game",
            "--duration",
            "1.2",
            "--interval",
            "0.5",
            "--screenshot-interval",
            "1",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "App 層級" in result.output
    sid = next(p.name for p in tmp_path.iterdir())
    store = SessionStore(tmp_path)
    loaded = store.load_session(sid)
    assert loaded.mode == "record" and loaded.total_frames == 180
    # 溫度來自錄製期間的 timestats 即時取樣（thermalservice fixture：MODERATE）
    assert loaded.thermal_timeline == "approximate"
    assert len(loaded.samples) == 3 and loaded.samples[-1].throttling_status == 2
    assert any("即時取樣" in n for n in loaded.notes)
    assert (tmp_path / sid / "raw" / "live_samples.jsonl").exists()
    # 錄製期間即時取樣的原始 dumpsys 也要保留
    raw = {p.name for p in (tmp_path / sid / "raw").iterdir()}
    assert any(n.startswith("timestats_") for n in raw), raw
    assert any(n.startswith("thermal_") for n in raw), raw
    # 錄製模式的截圖也要有，且對到分析後的每秒 sample
    assert list((tmp_path / sid / "shots").glob("*.png")), "record 模式沒有截圖"
    assert any(s.screenshot for s in loaded.samples)

    result = runner.invoke(cli.app, ["analyze", sid])
    assert result.exit_code == 0, result.output

    result = runner.invoke(cli.app, ["analyze", str(tmp_path / sid / "raw" / "trace.pb"), "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["mode"] == "record" and data["package"] == "" and len(data["samples"]) == 3


def test_record_with_analysis_json(env: RecordingAdb) -> None:
    result = runner.invoke(
        cli.app, ["record", "-p", "com.example.game", "--duration", "1", "--json"]
    )
    assert result.exit_code == 0, result.output
    lines = [json.loads(line) for line in result.output.splitlines() if line.startswith("{")]
    assert lines[0]["type"] == "state" and lines[-1]["type"] == "summary"
    data = lines[-1]["data"]
    assert data["average_fps"] == 59.9 and data["app_level_available"] is False


def test_analyze_missing_session(env: RecordingAdb) -> None:
    result = runner.invoke(cli.app, ["analyze", "nope"])
    assert result.exit_code == 1 and "找不到" in result.output


def test_diagnose(env: RecordingAdb, tmp_path: Path) -> None:
    result = runner.invoke(cli.app, ["diagnose", "-p", "com.example.game", "--duration", "2"])
    assert result.exit_code == 0, result.output
    assert "timestats" in result.output and "perfetto (display)" in result.output
    assert "(unreliable)" in result.output
    assert any("-disable" in c for c in env.calls)
    sid = next(p.name for p in tmp_path.iterdir())
    raw = {p.name for p in (tmp_path / sid / "raw").iterdir()}
    assert {"timestats_final.txt", "latency_unreliable.txt", "trace.pb"} <= raw

    data = json.loads(
        runner.invoke(
            cli.app, ["diagnose", "-p", "com.example.game", "--duration", "2", "--json"]
        ).output
    )
    assert data["rows"][0]["source"] == "timestats" and data["rows"][0]["frames"] == 1000
    assert data["rows"][0]["p90"] == pytest.approx(1000 / 33)
    assert data["rows"][1]["avg"] == 59.9


def test_record_perfetto_failure(env: RecordingAdb) -> None:
    env.responses[r"perfetto --background-wait -c - --txt -o"] = ("", 1)
    result = runner.invoke(cli.app, ["record", "-p", "x", "--duration", "1"])
    assert result.exit_code == 1 and "啟動失敗" in result.output


def test_record_pull_failure_then_manual_recover(
    env: RecordingAdb, tmp_path: Path, monkeypatch
) -> None:
    from frameprobe.adb import AdbTimeoutError, CommandResult

    fail = {"on": True}
    original_pull = env.pull

    def flaky_pull(remote: str, local: str, *, timeout: float = 120.0) -> CommandResult:
        if fail["on"]:
            raise AdbTimeoutError("adb 指令逾時（900.0s）")
        return original_pull(remote, local, timeout=timeout)

    monkeypatch.setattr(env, "pull", flaky_pull)
    result = runner.invoke(
        cli.app, ["record", "-p", "com.example.game", "--duration", "1.2", "--interval", "0.5"]
    )
    assert result.exit_code == 1 and "手動救回" in result.output
    sid = next(p.name for p in tmp_path.iterdir())
    store = SessionStore(tmp_path)
    stub = store.load_summary(sid)
    assert stub.mode == "record" and any("尚未分析" in n for n in stub.notes)
    assert (tmp_path / sid / "raw" / "live_samples.jsonl").exists()

    # trace 還沒救回 → analyze 給出明確指示
    result = runner.invoke(cli.app, ["analyze", sid])
    assert result.exit_code == 1 and "adb pull /data/misc/perfetto-traces" in result.output

    # 手動 pull 後 analyze 成功，且溫度來自 live_samples
    (tmp_path / sid / "raw" / "trace.pb").write_bytes(b"PB")
    result = runner.invoke(cli.app, ["analyze", sid])
    assert result.exit_code == 0, result.output
    loaded = store.load_session(sid)
    assert loaded.total_frames == 180 and loaded.samples[-1].throttling_status == 2
    assert not any("尚未分析" in n for n in loaded.notes)
