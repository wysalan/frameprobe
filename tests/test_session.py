from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from frameprobe import cli
from frameprobe.adb import CommandResult
from frameprobe.probe import DeviceInfo
from frameprobe.sampler import Sample, SamplerError
from frameprobe.session import RealtimeSession, attach_raw_battery
from frameprobe.storage import SessionStore

from .conftest import FakeAdb, load


class GrowingAdb(FakeAdb):
    """每次 timestats -dump 幀數 +60，模擬累積計數器。"""

    def __init__(self) -> None:
        super().__init__(
            responses={
                r"timestats -clear -enable": ("", 0),
                r"timestats -disable": ("", 0),
                r"dumpsys thermalservice": (load("thermalservice_pixel.txt"), 0),
                r"cat /proc/stat": (load("sysstats_b.txt"), 0),
                r"dumpsys meminfo": (load("meminfo_samsung_s23.txt"), 0),
                r"^echo '#sysfs'": (load("dumpsys_battery_samsung_s23.txt"), 0),
            }
        )
        self.dumps = 0

    def shell(self, command: str, *, timeout: float = 20.0) -> CommandResult:
        if "timestats -dump" in command:
            self.dumps += 1
            n = self.dumps * 60
            text = (
                "layerName = SurfaceView[com.example.game/x]#1\n"
                f"totalFrames = {n}\ndroppedFrames = {self.dumps}\naverageFPS = 60.0\n"
                f"present2present histogram is as below:\n16ms={n - 6} 33ms={6}\n"
            )
            return CommandResult(["adb"], 0, text, "")
        return super().shell(command, timeout=timeout)


DEVICE = DeviceInfo("FAKE", "Google", "Pixel 10", "17", 37)


def test_session_runs_persists_and_summarizes(tmp_path: Path) -> None:
    adb = GrowingAdb()
    store = SessionStore(tmp_path)
    session = RealtimeSession(
        adb, DEVICE, "com.example.game", store=store, interval=0.5, duration=1.2
    )
    seen = []
    summary = session.run(seen.append)

    assert len(seen) >= 2
    assert summary.total_frames == 60 * len(seen)
    assert summary.layer_name == "SurfaceView[com.example.game/x]#1"
    assert summary.p90_fps == pytest.approx(1000 / 16)
    assert summary.peak_cpu_c == 61.9
    assert summary.time_to_throttle_s == seen[0].elapsed  # fixture 的 status 是 MODERATE
    assert any("thermalservice" in n for n in summary.notes)
    # sysstats：靜態 fixture 差分為 0 → CPU 無法算，但記憶體有值、PSS 不可讀要寫進 notes
    assert seen[0].mem_rss_mb == pytest.approx(61000 * 4 / 1024, abs=0.1)
    # smaps 被擋 → PSS 改由 dumpsys meminfo 提供
    assert seen[0].mem_pss_mb == pytest.approx(3229008 / 1024, abs=0.1)
    assert seen[0].mem_detail_mb is not None and seen[0].mem_detail_mb["gl_mtrack"] > 400
    assert seen[0].cpu_freq_mhz == [1800, 1800, 3000, 3000]
    assert summary.avg_cpu_freq_mhz == [1800, 1800, 3000, 3000]
    assert summary.peak_gpu_freq_mhz == summary.avg_gpu_freq_mhz
    assert seen[0].gpu_busy_pct == 0.0
    assert seen[0].battery_plugged is True and seen[0].battery_power_w == pytest.approx(
        2.185, abs=0.01
    )
    assert seen[0].fpower_mw is None  # 接著 USB 不算 FPower
    assert summary.plugged_ratio == 1.0 and summary.avg_power_w is None
    assert any("全程接著電源" in n for n in summary.notes)
    assert any("dumpsys meminfo 的 TOTAL PSS" in n for n in summary.notes)
    assert summary.peak_mem_rss_mb is not None and summary.avg_cpu_app_pct is None
    assert any("smaps_rollup 不可讀" in n for n in summary.notes)
    assert any("-disable" in c for c in adb.calls)

    session_dir = tmp_path / session.session_id
    raw = sorted(p.name for p in (session_dir / "raw").iterdir())
    assert len([r for r in raw if r.startswith("timestats_")]) == adb.dumps
    assert any(r.startswith("thermal_") for r in raw)
    assert any(r.startswith("sysstats_") for r in raw)
    assert any(r.startswith("meminfo_") for r in raw)
    assert any(r.startswith("battery_") for r in raw)
    assert len((session_dir / "samples.jsonl").read_text().splitlines()) == len(seen)
    assert json.loads((session_dir / "summary.json").read_text())["mode"] == "realtime"
    assert store.load_session(session.session_id).samples[0].frames == 60


def test_session_stop_and_no_store() -> None:
    adb = GrowingAdb()
    session = RealtimeSession(
        adb, DEVICE, "com.example.game", store=None, interval=0.5, thermal=False
    )

    def stop_after_first(_s: object) -> None:
        session.stop()

    summary = session.run(stop_after_first)
    assert len(summary.samples) == 1
    assert summary.peak_cpu_c is None
    assert session.raw_dir is None
    assert adb.dumps == 2


def test_session_error_points_to_raw_dir(tmp_path: Path) -> None:
    adb = GrowingAdb()
    session = RealtimeSession(adb, DEVICE, "com.other", store=SessionStore(tmp_path), interval=0.5)
    with pytest.raises(SamplerError) as exc:
        session.run()
    assert "原始輸出已存於" in str(exc.value)
    assert list((tmp_path / session.session_id / "raw").glob("timestats_*.txt"))


def test_watch_cli(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    adb = GrowingAdb()
    adb.props["ro.build.version.sdk"] = "37"
    adb.responses[r"^id$"] = ("uid=2000(shell)", 0)
    monkeypatch.setattr(cli, "_adb", lambda serial: adb)
    monkeypatch.setenv("FRAMEPROBE_SESSIONS", str(tmp_path))
    runner = CliRunner()

    result = runner.invoke(
        cli.app,
        [
            "watch",
            "-p",
            "com.example.game",
            "--duration",
            "0.7",
            "--interval",
            "0.5",
            "--thermal-sensor",
            "gold",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Avg / P90 / P99" in result.output
    assert "已寫入" in result.output
    assert len(list(tmp_path.iterdir())) == 1

    result = runner.invoke(
        cli.app,
        [
            "watch",
            "-p",
            "com.example.game",
            "--duration",
            "0.7",
            "--interval",
            "0.5",
            "--no-save",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    lines = [json.loads(line) for line in result.output.splitlines() if line.startswith("{")]
    assert lines[0]["type"] == "sample" and lines[0]["data"]["frames"] == 60
    assert lines[-1]["type"] == "summary" and lines[-1]["data"]["mode"] == "realtime"
    assert len(list(tmp_path.iterdir())) == 1  # --no-save 沒有新資料夾

    result = runner.invoke(
        cli.app, ["watch", "-p", "com.nothere", "--duration", "0.6", "--no-save"]
    )
    assert result.exit_code == 1 and "找不到" in result.output


def test_screenshots(tmp_path: Path) -> None:
    class ShotAdb(GrowingAdb):
        def __init__(self) -> None:
            super().__init__()
            self.responses[r"^screencap -p"] = ("", 0)
            self.responses[r"^rm -f"] = ("", 0)

        def pull(self, remote: str, local: str, *, timeout: float = 120.0) -> CommandResult:
            Path(local).write_bytes(b"\x89PNG")
            return self.run("pull", remote, local)

    adb = ShotAdb()
    store = SessionStore(tmp_path)
    session = RealtimeSession(
        adb,
        DEVICE,
        "com.example.game",
        store=store,
        interval=0.5,
        duration=1.7,
        thermal=False,
        sysstats=False,
        screenshot_interval=1.0,
    )
    summary = session.run()
    shots = [s.screenshot for s in summary.samples if s.screenshot]
    assert shots and all(s.startswith("shots/") and s.endswith(".png") for s in shots)
    assert (tmp_path / session.session_id / shots[0]).read_bytes() == b"\x89PNG"
    # 間隔 1s、取樣 0.5s → 不是每筆都截
    assert len(shots) < len(summary.samples)

    adb.responses[r"^screencap -p"] = ("", 1)
    session = RealtimeSession(
        adb,
        DEVICE,
        "com.example.game",
        store=store,
        interval=0.5,
        duration=2.2,
        thermal=False,
        sysstats=False,
        screenshot_interval=0.5,
    )
    summary = session.run()
    assert not any(s.screenshot for s in summary.samples)
    assert any("截圖連續失敗" in n for n in summary.notes)


def _bare_sample(t: float, *, stale: bool) -> Sample:
    return Sample(
        ts=t,
        elapsed=t,
        frames=60,
        fps=60.0,
        p90_fps=None,
        p99_fps=None,
        dropped=0,
        dropped_ratio=0.0,
        histogram={16: 60},
        cpu_c=None,
        skin_c=None,
        throttling_status=None,
        power_stale=stale,
    )


def test_attach_raw_battery_maps_fresh_samples_to_raw_files(tmp_path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    s23 = load("dumpsys_battery_samsung_s23.txt")  # level 44
    (raw / "battery_00002.txt").write_text(s23)
    (raw / "battery_00009.txt").write_text(s23.replace("level: 44", "level: 43"))
    samples = [_bare_sample(t, stale=t in (2.0, 4.0)) for t in (1.0, 2.0, 3.0, 4.0)]
    assert attach_raw_battery(samples, raw) == 4
    assert [s.battery_level_pct for s in samples] == [44, 44, 43, 43]
    assert samples[0].battery_charge_mah == pytest.approx(1633.9)
    assert samples[0].battery_status == 2
    assert attach_raw_battery(samples, raw) == 0  # 已有值不再補
