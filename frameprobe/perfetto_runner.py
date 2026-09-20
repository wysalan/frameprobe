"""離線模式：在裝置上錄 Perfetto trace 並 pull 回本機（SPEC §1.2、§12.7）。"""

from __future__ import annotations

import json
import re
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .adb import AdbClient, FrameprobeError
from .meminfo import MemDetailCollector
from .power import PowerCollector
from .storage import SessionStore
from .sysstats import SysStatsCollector, kb_to_mb
from .thermal import ThermalCollector, ThermalSnapshot

REMOTE_TRACE_DIR = "/data/misc/perfetto-traces"
REMOTE_CONFIG = "/data/local/tmp/frameprobe.pbtxt"
THERMAL_TRACEFS = (
    "/sys/kernel/tracing/events/thermal/thermal_temperature",
    "/sys/kernel/debug/tracing/events/thermal/thermal_temperature",
)


class PerfettoError(FrameprobeError):
    pass


def build_config(duration_s: float, *, thermal: bool = True) -> str:
    """SPEC §1.2 的 config，加上 §12.7 的 ftrace 溫度／頻率事件。"""
    ftrace = """
data_sources: {
  config {
    name: "linux.ftrace"
    ftrace_config {
      ftrace_events: "thermal/thermal_temperature"
      ftrace_events: "power/cpu_frequency"
      ftrace_events: "power/gpu_frequency"
    }
  }
}
"""
    # write_into_file：traced 每 2 秒把 buffer 寫進輸出檔，長時間錄製不會被 ring buffer 覆寫
    # （真機驗證：Pixel 11 Pro 錄 19 分鐘只剩最後 5 分鐘）。
    return f"""buffers: {{ size_kb: 131072 }}
duration_ms: {int(duration_s * 1000)}
write_into_file: true
file_write_period_ms: 2000
flush_period_ms: 5000
data_sources: {{ config {{ name: "android.surfaceflinger.frametimeline" }} }}
data_sources: {{ config {{ name: "android.gpu.memory" }} }}
data_sources: {{
  config {{
    name: "linux.process_stats"
    process_stats_config {{ scan_all_processes_on_start: true }}
  }}
}}
{ftrace if thermal else ""}"""


@dataclass
class ThermalPoll:
    """錄製期間每 interval 秒 poll 一次。

    thermal=True 時讀 dumpsys thermalservice（SPEC §12.7 備援）；
    package 給了就順便讀 CPU/記憶體（procfs），兩者共用同一條 thread 與時間軸。
    """

    adb: AdbClient
    interval: float = 2.0
    thermal: bool = True
    package: str | None = None
    points: list[dict[str, float | int | None]] = field(default_factory=list)
    sys_points: list[dict[str, Any]] = field(default_factory=list)
    thermal_available: bool = False
    _thread: threading.Thread | None = None
    _stop: threading.Event = field(default_factory=threading.Event)
    _t0: float = 0.0

    def start(self) -> bool:
        """回傳有沒有任何來源可用；即使不可用，有 package 時仍會跑 sysstats。"""
        collector: ThermalCollector | None = None
        if self.thermal:
            collector = ThermalCollector(self.adb, min_interval=0.0)
            try:
                collector.detect_source()
            except FrameprobeError:
                collector = None
        sys_collector = SysStatsCollector(self.adb, self.package) if self.package else None
        mem_collector = (
            MemDetailCollector(self.adb, self.package, min_interval=0.0) if self.package else None
        )
        power_collector = PowerCollector(self.adb, min_interval=0.0) if self.package else None
        self.thermal_available = collector is not None
        if collector is None and sys_collector is None:
            return False
        self._t0 = time.time()

        def loop() -> None:
            while not self._stop.is_set():
                elapsed = time.time() - self._t0
                if collector is not None:
                    snap = collector.read(force=True)
                    if snap is not None:
                        self.points.append(_point(snap, elapsed))
                if sys_collector is not None:
                    read = sys_collector.read()
                    if read is not None and read[0] is not None:
                        usage, sysnap = read[0], read[1]
                        self.sys_points.append(
                            {
                                "elapsed": round(elapsed, 3),
                                "cpu_total_pct": usage.total_pct,
                                "cpu_app_pct": usage.app_pct,
                                "cpu_app_norm_pct": usage.app_norm_pct,
                                "cpu_freq_mhz": usage.freq_mhz,
                                "mem_pss_mb": kb_to_mb(sysnap.pss_kb),
                                "mem_rss_mb": kb_to_mb(sysnap.rss_kb),
                                "mem_swap_mb": kb_to_mb(sysnap.swap_kb),
                                "mem_available_mb": kb_to_mb(sysnap.mem_available_kb),
                                "gpu_busy_pct": sysnap.gpu_busy_pct,
                                "gpu_freq_mhz": sysnap.gpu_freq_mhz,
                                "gpu_mem_mb": kb_to_mb(sysnap.gpu_mem_kb),
                                "wakeups": usage.wakeups,
                                "cswitch": usage.cswitch,
                                "net_rx_kbps": usage.net_rx_kbps,
                                "net_tx_kbps": usage.net_tx_kbps,
                                "mem_detail_mb": (
                                    detail.to_dict()
                                    if mem_collector and (detail := mem_collector.read(force=True))
                                    else None
                                ),
                                "pss_error": sysnap.pss_error,
                                **_power_point(power_collector),
                            }
                        )
                self._stop.wait(self.interval)

        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=20.0)


def _power_point(collector: PowerCollector | None) -> dict[str, Any]:
    power = collector.read(force=True) if collector else None
    if power is None:
        return {}
    return {
        "battery_voltage_v": power.voltage_v,
        "battery_current_ma": power.current_ma,
        "battery_power_w": power.power_w,
        "battery_plugged": power.plugged,
    }


def _thermal_ok(poll: ThermalPoll) -> bool:
    return poll.thermal_available


def _point(snap: ThermalSnapshot, elapsed: float) -> dict[str, float | int | None]:
    return {
        "elapsed": round(elapsed, 3),
        "cpu_c": snap.cpu_c,
        "gpu_c": snap.gpu_c,
        "skin_c": snap.skin_c,
        "npu_c": snap.npu_c,
        "battery_c": snap.battery_c,
        "status": int(snap.status) if snap.status is not None else None,
    }


def tracefs_has_thermal(adb: AdbClient) -> bool | None:
    """True/False 為確定，None 為看不到（shell 通常無權讀 tracefs）。"""
    denied = False
    for path in THERMAL_TRACEFS:
        result = adb.shell(f"test -d {path} && echo yes || echo no")
        if result.stdout.strip() == "yes":
            return True
        if "Permission denied" in result.stderr or result.stdout.strip() != "no":
            denied = True
    return None if denied else False


@dataclass
class RecordResult:
    trace_path: Path
    notes: list[str]
    thermal_timeline: str | None  # "exact" | "approximate" | None
    thermal_poll: list[dict[str, float | int | None]]
    sys_poll: list[dict[str, Any]] = field(default_factory=list)


PULL_TIMEOUT_S = 900.0
MAX_UNBOUNDED_S = 3600.0  # 隨錄隨停時 config 仍給一個上限，避免忘記停把裝置塞爆
_PID_RE = re.compile(r"(\d+)\s*$")


class PerfettoRecording:
    """可隨時停止的背景錄製。

    start() 用 `perfetto --background-wait` 啟動並取得背景 PID（stdout 最後一個整數），
    stop() 送 SIGTERM 讓 perfetto 自己 flush 並寫檔，等它結束後 pull 回本機。
    這與 Perfetto 官方 tools/record_android_trace 的做法相同。

    poll=True 時另起 thread 每 thermal_interval 秒 poll 溫度與 CPU/記憶體；
    若呼叫端自己有即時取樣（timestats）就傳 poll=False，改由那邊供應。
    """

    def __init__(
        self,
        adb: AdbClient,
        store: SessionStore,
        session_id: str,
        *,
        thermal: bool = True,
        thermal_interval: float = 2.0,
        package: str | None = None,
        poll: bool = True,
    ) -> None:
        self.adb = adb
        self.store = store
        self.session_id = session_id
        self.thermal = thermal
        self.thermal_interval = thermal_interval
        self.package = package
        self.want_poll = poll
        self.notes: list[str] = []
        self.thermal_timeline: str | None = None
        self.remote_trace = f"{REMOTE_TRACE_DIR}/{session_id}.pb"
        self.pid: int | None = None
        self._poll: ThermalPoll | None = None
        self._started = False
        self._stopped = False

    # ------------------------------------------------------------------ #

    def start(self, max_duration_s: float | None = None) -> None:
        duration = max_duration_s or MAX_UNBOUNDED_S
        with tempfile.NamedTemporaryFile("w", suffix=".pbtxt", delete=False) as fh:
            fh.write(build_config(duration, thermal=self.thermal))
            local_cfg = fh.name
        self.store.write_raw(self.session_id, "perfetto_config.pbtxt", Path(local_cfg).read_text())
        push = self.adb.run("push", local_cfg, REMOTE_CONFIG)
        if not push.ok:
            raise PerfettoError(f"無法 push perfetto config：{push.stderr or push.stdout}")

        if self.thermal and tracefs_has_thermal(self.adb) is True:
            self.thermal_timeline = "exact"
            self.notes.append(
                "錄製設定已開 ftrace thermal_temperature；trace 裡是否真有溫度以分析結果的備註為準"
            )

        # config 用 cat 經 stdin 餵給 perfetto：perfetto domain 在 user build 上讀不到
        # /data/local/tmp（shell_data_file）。--background-wait 會等 tracing 真的開始才回來。
        result = self.adb.shell(
            f"cat {REMOTE_CONFIG} | perfetto --background-wait -c - --txt -o {self.remote_trace}",
            timeout=60.0,
        )
        self.store.write_raw(
            self.session_id,
            "perfetto_start.txt",
            result.stdout + "\n--- stderr ---\n" + result.stderr,
        )
        match = _PID_RE.search(result.stdout.strip())
        if not result.ok or not match:
            raise PerfettoError(
                f"perfetto 啟動失敗（exit {result.returncode}）：{result.stderr or result.stdout}\n"
                "常見原因：traced 未執行、ROM 移除 perfetto、或 SELinux 拒絕寫入 "
                f"{REMOTE_TRACE_DIR}。請跑 `frameprobe doctor --verbose`。\n"
                f"原始輸出已存於 {self.store.path(self.session_id) / 'raw'}"
            )
        self.pid = int(match.group(1))
        self._started = True
        self.adb.shell(f"rm -f {REMOTE_CONFIG}")

        if self.want_poll:
            poll = ThermalPoll(
                self.adb,
                interval=self.thermal_interval,
                thermal=self.thermal and self.thermal_timeline is None,
                package=self.package,
            )
            if poll.start():
                self._poll = poll
                if poll.thermal_available:
                    self.thermal_timeline = "approximate"
                    self.notes.append(
                        "kernel 未提供（或無法確認）thermal_temperature ftrace 事件，"
                        "改以每 2 秒 poll dumpsys thermalservice，時間軸為近似對齊"
                    )
            if self.thermal and self.thermal_timeline is None:
                self.notes.append("溫度來源偵測失敗，本次 trace 無溫度資料")
            if self.package and self._poll is None:
                self.notes.append("CPU/記憶體採集無法啟動")

    def is_running(self) -> bool:
        if self.pid is None:
            return False
        return (
            self.adb.shell(f"kill -0 {self.pid} 2>/dev/null && echo alive").stdout.strip()
            == "alive"
        )

    def stop_tracing(self, *, wait_s: float = 30.0) -> None:
        """送 SIGTERM 讓 perfetto flush 並寫檔，等它結束。之後 trace 留在手機上，等 pull()。"""
        if not self._started:
            raise PerfettoError("錄製尚未開始。")
        self._started = False
        if self._poll is not None:
            self._poll.stop()
            if self._poll.points:
                self.store.write_raw(
                    self.session_id,
                    "thermal_poll.jsonl",
                    "\n".join(json.dumps(p) for p in self._poll.points) + "\n",
                )
            if self._poll.sys_points:
                self.store.write_raw(
                    self.session_id,
                    "sys_poll.jsonl",
                    "\n".join(json.dumps(p) for p in self._poll.sys_points) + "\n",
                )

        # SIGTERM → perfetto 自己 flush 並寫檔；若它已因 duration 到期自行結束，kill 會失敗，無妨。
        term = self.adb.shell(f"kill -TERM {self.pid}")
        deadline = time.time() + wait_s
        while self.is_running() and time.time() < deadline:
            time.sleep(0.5)
        still_running = self.is_running()
        self.store.write_raw(
            self.session_id,
            "perfetto_stop.txt",
            f"kill exit={term.returncode}\n{term.stdout}\n--- stderr ---\n{term.stderr}\n"
            f"still_running_after_{wait_s}s={still_running}\n",
        )
        if still_running:
            raise PerfettoError(
                f"perfetto（pid {self.pid}）在 {wait_s}s 內沒有結束，trace 可能不完整。\n"
                f"原始輸出已存於 {self.store.path(self.session_id) / 'raw'}"
            )
        self._stopped = True

    def remote_size_bytes(self, adb: AdbClient | None = None) -> int | None:
        result = (adb or self.adb).shell(f"stat -c %s {self.remote_trace} 2>/dev/null")
        value = result.stdout.strip()
        return int(value) if value.isdigit() else None

    def pull(self, adb: AdbClient | None = None) -> RecordResult:
        """把手機上的 trace 拉回本機。adb 可指定另一條到同一支手機的連線（例如改走 USB）。"""
        conn = adb or self.adb
        local_trace = self.store.path(self.session_id) / "raw" / "trace.pb"
        recover = (
            f"trace 仍在手機上（{self.remote_trace}），可手動救回：\n"
            f"  adb pull {self.remote_trace} {local_trace}\n"
            f"  frameprobe analyze {self.session_id}"
        )
        try:
            # 長時間錄製的 trace 可達數百 MB，走無線 adb 只有幾 MB/s，給 15 分鐘
            pull = conn.pull(self.remote_trace, str(local_trace), timeout=PULL_TIMEOUT_S)
        except FrameprobeError as exc:
            raise PerfettoError(f"無法 pull trace：{exc}\n{recover}") from exc
        if not pull.ok or not local_trace.exists():
            raise PerfettoError(f"無法 pull trace：{pull.stderr or pull.stdout}\n{recover}")
        expected = self.remote_size_bytes(conn)
        actual = local_trace.stat().st_size
        if expected is not None and actual != expected:
            raise PerfettoError(
                f"pull 回來的 trace 不完整（{actual} / {expected} bytes）。\n{recover}"
            )
        conn.shell(f"rm -f {self.remote_trace}")
        return RecordResult(
            local_trace,
            list(self.notes),
            self.thermal_timeline,
            self._poll.points if self._poll else [],
            self._poll.sys_points if self._poll else [],
        )

    def stop(self, *, wait_s: float = 30.0) -> RecordResult:
        """stop_tracing() + pull()，給不需要中途決定傳輸方式的呼叫端（CLI）。"""
        self.stop_tracing(wait_s=wait_s)
        return self.pull()


def record_trace(
    adb: AdbClient,
    store: SessionStore,
    session_id: str,
    *,
    duration_s: float,
    thermal: bool = True,
    thermal_interval: float = 2.0,
    package: str | None = None,
    stop_event: threading.Event | None = None,
) -> RecordResult:
    """固定時長的阻塞式錄製（CLI 用）。stop_event 被 set 或 Ctrl-C 會提早停止。"""
    rec = PerfettoRecording(
        adb, store, session_id, thermal=thermal, thermal_interval=thermal_interval, package=package
    )
    rec.start(duration_s)
    try:
        if stop_event is not None:
            stop_event.wait(duration_s)
        else:
            time.sleep(duration_s)
    except KeyboardInterrupt:
        rec.notes.append("使用者以 Ctrl-C 提早停止錄製")
    return rec.stop()
