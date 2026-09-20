from __future__ import annotations

from pathlib import Path

import pytest

from frameprobe.adb import CommandResult
from frameprobe.perfetto_runner import (
    PerfettoError,
    build_config,
    record_trace,
    tracefs_has_thermal,
)
from frameprobe.storage import SessionStore

from .conftest import FakeAdb, load


def test_build_config() -> None:
    cfg = build_config(30, thermal=True)
    assert "android.surfaceflinger.frametimeline" in cfg
    assert "duration_ms: 30000" in cfg
    assert "thermal/thermal_temperature" in cfg and "power/gpu_frequency" in cfg
    assert "write_into_file: true" in cfg and "file_write_period_ms" in cfg
    assert "linux.ftrace" not in build_config(5, thermal=False)


def test_tracefs_detection() -> None:
    assert tracefs_has_thermal(FakeAdb(responses={r"test -d": ("yes", 0)})) is True
    assert tracefs_has_thermal(FakeAdb(responses={r"test -d": ("no", 0)})) is False
    assert tracefs_has_thermal(FakeAdb()) is None  # 指令失敗 → 不確定


class RecordingAdb(FakeAdb):
    """模擬 perfetto --background-wait：啟動印出 PID，kill -TERM 後 kill -0 變成不存活。"""

    def __init__(self, tracefs: str = "no", **kw):
        super().__init__(
            responses={
                r"test -d /sys": (tracefs, 0),
                r"perfetto --background-wait -c - --txt -o": ("Warning: No PTY.\n4242\n", 0),
                r"^rm -f": ("", 0),
                r"dumpsys thermalservice": (load("thermalservice_pixel.txt"), 0),
            },
            **kw,
        )
        self.alive = False

    def shell(self, command: str, *, timeout: float = 20.0) -> CommandResult:
        if (
            "perfetto --background-wait" in command
            and self.responses.get(r"perfetto --background-wait -c - --txt -o", ("", 1))[1] == 0
        ):
            self.alive = True
        if command.startswith("kill -TERM 4242"):
            self.calls.append(command)
            self.alive = False
            return CommandResult(["adb"], 0, "", "")
        if command.startswith("kill -0 4242"):
            return CommandResult(["adb"], 0, "alive" if self.alive else "", "")
        return super().shell(command, timeout=timeout)

    def pull(self, remote: str, local: str, *, timeout: float = 120.0) -> CommandResult:
        Path(local).write_bytes(b"PB")
        return self.run("pull", remote, local)


def test_record_success_with_poll_fallback(tmp_path: Path) -> None:
    adb = RecordingAdb()
    store = SessionStore(tmp_path)
    store.create("sid")
    result = record_trace(adb, store, "sid", duration_s=0.3, thermal_interval=0.1)
    assert result.trace_path.read_bytes() == b"PB"
    assert result.thermal_timeline == "approximate"
    assert result.thermal_poll and result.thermal_poll[0]["cpu_c"] == 61.9
    raw = {p.name for p in (tmp_path / "sid" / "raw").iterdir()}
    assert {
        "trace.pb",
        "perfetto_config.pbtxt",
        "perfetto_start.txt",
        "perfetto_stop.txt",
        "thermal_poll.jsonl",
    } <= raw
    assert any(c.startswith("kill -TERM 4242") for c in adb.calls)
    assert any(c.startswith("push ") for c in adb.calls)
    assert any(c.startswith("rm -f /data/misc/perfetto-traces/sid.pb") for c in adb.calls)


def test_record_exact_when_ftrace_present(tmp_path: Path) -> None:
    adb = RecordingAdb(tracefs="yes")
    store = SessionStore(tmp_path)
    store.create("sid")
    result = record_trace(adb, store, "sid", duration_s=0.1)
    assert result.thermal_timeline == "exact" and result.thermal_poll == []
    assert not (tmp_path / "sid" / "raw" / "thermal_poll.jsonl").exists()


def test_record_errors(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    store.create("sid")
    # 沒有任何預檢：perfetto 本身失敗才報錯，且訊息要指向 doctor 與 raw/
    with pytest.raises(PerfettoError, match="啟動失敗") as exc:
        record_trace(FakeAdb(), store, "sid", duration_s=0.1, thermal=False)
    assert "frameprobe doctor" in str(exc.value)
    assert (tmp_path / "sid" / "raw" / "perfetto_start.txt").exists()

    # 有輸出但抓不到 PID 也算啟動失敗
    adb = RecordingAdb()
    adb.responses[r"perfetto --background-wait -c - --txt -o"] = ("no pid here", 0)
    with pytest.raises(PerfettoError, match="啟動失敗"):
        record_trace(adb, store, "sid", duration_s=0.1, thermal=False)


def test_stop_early_and_hang(tmp_path: Path) -> None:
    import threading

    from frameprobe.perfetto_runner import PerfettoRecording

    store = SessionStore(tmp_path)
    store.create("sid")
    adb = RecordingAdb()
    stop = threading.Event()
    stop.set()
    result = record_trace(adb, store, "sid", duration_s=30, thermal=False, stop_event=stop)
    assert result.trace_path.exists()

    rec = PerfettoRecording(adb, store, "sid", thermal=False, poll=False)
    with pytest.raises(PerfettoError, match="尚未開始"):
        rec.stop()
    rec.start(None)
    assert (
        "duration_ms: 3600000" in (tmp_path / "sid" / "raw" / "perfetto_config.pbtxt").read_text()
    )
    adb.shell = lambda cmd, **kw: (  # type: ignore[method-assign]
        CommandResult(["adb"], 0, "alive", "")
        if cmd.startswith("kill -0")
        else CommandResult(["adb"], 0, "", "")
    )
    with pytest.raises(PerfettoError, match="沒有結束"):
        rec.stop(wait_s=0.6)


def test_pull_failure_message_has_recovery(tmp_path: Path) -> None:
    from frameprobe.adb import AdbTimeoutError

    class SlowPull(RecordingAdb):
        def pull(self, remote: str, local: str, *, timeout: float = 120.0) -> CommandResult:
            assert timeout >= 600  # 大 trace 走無線要給足時間
            raise AdbTimeoutError("adb 指令逾時（900.0s）")

    store = SessionStore(tmp_path)
    store.create("sid")
    adb = SlowPull()
    with pytest.raises(PerfettoError) as exc:
        record_trace(adb, store, "sid", duration_s=0.1, thermal=False)
    msg = str(exc.value)
    assert "adb pull /data/misc/perfetto-traces/sid.pb" in msg and "frameprobe analyze sid" in msg
    # 沒有刪掉手機上的 trace
    assert not any(c.startswith("rm -f /data/misc/perfetto-traces/sid.pb") for c in adb.calls)


def test_stop_tracing_then_pull_with_other_connection(tmp_path: Path) -> None:
    from frameprobe.perfetto_runner import PerfettoRecording

    store = SessionStore(tmp_path)
    store.create("sid")
    adb = RecordingAdb()
    adb.responses[r"^stat -c %s"] = ("2", 0)  # 與 pull 寫出的 b"PB" 一致
    rec = PerfettoRecording(adb, store, "sid", thermal=False, poll=False)
    rec.start(None)
    rec.stop_tracing()
    assert rec.remote_size_bytes() == 2
    # 用另一條連線（例如 USB）pull：檔案要從那條連線拉、rm 也走那條
    usb = RecordingAdb()
    usb.responses[r"^stat -c %s"] = ("2", 0)
    result = rec.pull(usb)
    assert result.trace_path.read_bytes() == b"PB"
    assert any(c.startswith("rm -f /data/misc/perfetto-traces/sid.pb") for c in usb.calls)
    assert not any(c.startswith("rm -f /data/misc/perfetto-traces/sid.pb") for c in adb.calls)


def test_pull_size_mismatch_is_an_error(tmp_path: Path) -> None:
    from frameprobe.perfetto_runner import PerfettoRecording

    store = SessionStore(tmp_path)
    store.create("sid")
    adb = RecordingAdb()
    adb.responses[r"^stat -c %s"] = ("134919952", 0)  # 手機上比拉回來的大很多
    rec = PerfettoRecording(adb, store, "sid", thermal=False, poll=False)
    rec.start(None)
    rec.stop_tracing()
    with pytest.raises(PerfettoError, match="不完整"):
        rec.pull()
    assert not any(c.startswith("rm -f /data/misc/perfetto-traces/sid.pb") for c in adb.calls)
