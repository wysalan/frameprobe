"""即時 session 的執行流程：sampler 迴圈 + 落盤 + 收尾彙總。

CLI 的 watch 與 API 的背景 task 都走這裡，差別只在 on_sample 回呼。
"""

from __future__ import annotations

import json
import math
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from .adb import AdbClient, FrameprobeError
from .analysis import analyze, build_record_summary, open_trace
from .perfetto_runner import PerfettoRecording
from .probe import DeviceInfo
from .sampler import RealtimeSampler, Sample, SamplerError
from .storage import SessionStore, SessionSummary, new_session_id
from .summary import summarize_realtime


class RealtimeSession:
    def __init__(
        self,
        adb: AdbClient,
        device: DeviceInfo,
        package: str,
        *,
        store: SessionStore | None,
        interval: float = 1.0,
        layer_hint: str | None = None,
        duration: float | None = None,
        thermal: bool = True,
        thermal_interval: float = 2.0,
        sysstats: bool = True,
        screenshot_interval: float | None = None,
        shots_dir: Path | None = None,
        raw_dir: Path | None = None,
    ) -> None:
        self.adb = adb
        self.device = device
        self.package = package
        self.store = store
        self.duration = duration
        # store=None 時（錄製模式的即時取樣）原始 dumpsys 仍要保留，寫到 raw_dir
        self._raw_dir_override = raw_dir
        # 截圖會影響效能，預設關。shots_dir 沒給就寫到 store 的 session 目錄。
        self.screenshot_interval = screenshot_interval
        self.shots_dir = shots_dir
        self._last_shot_at = -1e9
        self.screenshot_failures = 0
        self.interval = interval
        self.session_id = new_session_id(package)
        self.started_at = datetime.now()
        self.samples: list[Sample] = []
        self.notes: list[str] = []
        self.markers: list[tuple[float, str]] = []
        self.sampler = RealtimeSampler(
            adb,
            package,
            interval=interval,
            layer_hint=layer_hint,
            thermal=thermal,
            thermal_interval=thermal_interval,
            sysstats=sysstats,
        )
        self._sysstats_requested = sysstats
        self._stop = threading.Event()
        self._raw_seq = 0
        if store is not None:
            store.create(self.session_id)
            if self.shots_dir is None:
                self.shots_dir = store.path(self.session_id) / "shots"

    def _maybe_screenshot(self, sample: Sample) -> None:
        if not self.screenshot_interval or self.shots_dir is None or self.screenshot_failures >= 3:
            return
        if sample.elapsed - self._last_shot_at < self.screenshot_interval:
            return
        self._last_shot_at = sample.elapsed
        name = f"{int(sample.elapsed):06d}.png"
        remote = "/data/local/tmp/frameprobe_shot.png"
        cap = self.adb.shell(f"screencap -p {remote}", timeout=15.0)
        if not cap.ok:
            self.screenshot_failures += 1
            return
        self.shots_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.adb.pull(remote, str(self.shots_dir / name))
        except FrameprobeError:
            self.screenshot_failures += 1
            return
        self.adb.shell(f"rm -f {remote}")
        sample.screenshot = f"shots/{name}"

    @property
    def raw_dir(self) -> str | None:
        return str(self.store.path(self.session_id) / "raw") if self.store else None

    def stop(self) -> None:
        self._stop.set()

    def add_marker(self, label: str, elapsed: float | None = None) -> tuple[float, str]:
        marker = (
            round(elapsed if elapsed is not None else time.time() - self.started_at.timestamp(), 2),
            label,
        )
        self.markers.append(marker)
        return marker

    # ------------------------------------------------------------------ #

    def _write_raw(self, name: str, text: str) -> None:
        if self.store is not None:
            self.store.write_raw(self.session_id, name, text)
        elif self._raw_dir_override is not None:
            self._raw_dir_override.mkdir(parents=True, exist_ok=True)
            (self._raw_dir_override / name).write_text(text, encoding="utf-8")

    def _drain_raw(self) -> None:
        """把 sampler / thermal 累積的原始 dumpsys 文字寫進 raw/，一個檔一次 snapshot。"""
        if self.store is None and self._raw_dir_override is None:
            self.sampler.raw_snapshots.clear()
            if self.sampler.thermal:
                self.sampler.thermal.raw_snapshots.clear()
            if self.sampler.sysstats:
                self.sampler.sysstats.raw_snapshots.clear()
            if self.sampler.memdetail:
                self.sampler.memdetail.raw_snapshots.clear()
            if self.sampler.power:
                self.sampler.power.raw_snapshots.clear()
            return
        for text in self.sampler.raw_snapshots:
            self._raw_seq += 1
            self._write_raw(f"timestats_{self._raw_seq:05d}.txt", text)
        self.sampler.raw_snapshots.clear()
        if self.sampler.thermal:
            for text in self.sampler.thermal.raw_snapshots:
                self._raw_seq += 1
                self._write_raw(f"thermal_{self._raw_seq:05d}.txt", text)
            self.sampler.thermal.raw_snapshots.clear()
        if self.sampler.sysstats:
            for text in self.sampler.sysstats.raw_snapshots:
                self._raw_seq += 1
                self._write_raw(f"sysstats_{self._raw_seq:05d}.txt", text)
            self.sampler.sysstats.raw_snapshots.clear()
        if self.sampler.memdetail:
            for text in self.sampler.memdetail.raw_snapshots:
                self._raw_seq += 1
                self._write_raw(f"meminfo_{self._raw_seq:05d}.txt", text)
            self.sampler.memdetail.raw_snapshots.clear()
        if self.sampler.power:
            for text in self.sampler.power.raw_snapshots:
                self._raw_seq += 1
                self._write_raw(f"battery_{self._raw_seq:05d}.txt", text)
            self.sampler.power.raw_snapshots.clear()

    def run(self, on_sample: Callable[[Sample], None] | None = None) -> SessionSummary:
        """阻塞直到 duration 到期、stop() 被呼叫或 Ctrl-C。一定會回傳 summary。"""
        deadline = time.time() + self.duration if self.duration else None
        try:
            with self.sampler:
                if self.sampler.thermal is None and self.sampler.thermal_requested:
                    self.notes.append("溫度來源偵測失敗，本次 session 無溫度資料")
                elif self.sampler.thermal is not None:
                    self.notes.append(f"溫度來源：{self.sampler.thermal.source}")
                while not self._stop.is_set():
                    tick = time.time()
                    try:
                        sample = self.sampler.sample_once()
                    finally:
                        self._drain_raw()
                    if sample is not None:
                        self._maybe_screenshot(sample)
                        self.samples.append(sample)
                        if self.store is not None:
                            self.store.append_sample(self.session_id, sample)
                        if on_sample is not None:
                            on_sample(sample)
                    if deadline and time.time() >= deadline:
                        break
                    remaining = self.interval - (time.time() - tick)
                    if remaining > 0:
                        self._stop.wait(remaining)
        except KeyboardInterrupt:
            self.notes.append("使用者以 Ctrl-C 中斷")
        except FrameprobeError as exc:
            self._drain_raw()
            hint = f"\n原始輸出已存於 {self.raw_dir}" if self.raw_dir else ""
            raise SamplerError(f"{exc}{hint}") from exc
        return self.finalize()

    def finalize(self) -> SessionSummary:
        if self._sysstats_requested and self.sampler.sysstats is None:
            self.notes.append("CPU/記憶體採集連續失敗 5 次，已停用")
        if self.sampler.sysstats_pss_error:
            source = (
                "dumpsys meminfo 的 TOTAL PSS" if self.sampler.pss_from_meminfo else "statm 的 RSS"
            )
            self.notes.append(
                f"smaps_rollup 不可讀（{self.sampler.sysstats_pss_error.splitlines()[0]}），"
                f"記憶體改用 {source}；release App 對 shell 通常如此"
            )
        if self._sysstats_requested and self.sampler.memdetail is None:
            self.notes.append("dumpsys meminfo 連續失敗 5 次，Memory Detail 已停用")
        if self.screenshot_failures >= 3:
            self.notes.append("截圖連續失敗 3 次，已停用")
        if self.sampler.gpu_temp_from_kgsl:
            self.notes.append("thermalservice 無 GPU 型別，GPU 溫度取自 kgsl temp（單位為推斷值）")
        summary = summarize_realtime(
            self.samples,
            session_id=self.session_id,
            device=self.device,
            package=self.package,
            layer_name=self.sampler.layer_name or "",
            started_at=self.started_at,
            notes=self.notes,
        )
        summary.markers = list(self.markers)
        if self.store is not None:
            self.store.write_summary(self.session_id, summary)
        return summary


def attach_raw_battery(samples: list[Sample], raw_dir: Path) -> int:
    """舊 Session 的 live_samples 沒存電量／Charge counter／狀態：從 raw/battery_*.txt 補回。

    每一份 raw 對應一筆 power_stale=False 的取樣（取樣器只在真正讀到新值時才存 raw），
    之後的 stale 取樣沿用同一份。回傳補到的取樣數；已有值的 Session 直接回 0。
    """
    if any(s.battery_level_pct is not None for s in samples):
        return 0
    files = sorted(raw_dir.glob("battery_*.txt"))
    if not files:
        return 0
    from .power import PowerError, parse_power

    fresh = iter(files)
    current = None
    filled = 0
    for s in samples:
        if not s.power_stale:
            path = next(fresh, None)
            if path is None:
                break
            try:
                current = parse_power(path.read_text(encoding="utf-8", errors="replace"))
            except PowerError:
                current = None
        if current is None:
            continue
        s.battery_level_pct = current.level
        s.battery_charge_mah = (
            round(current.charge_counter_uah / 1000, 1)
            if current.charge_counter_uah is not None
            else None
        )
        s.battery_status = current.status
        filled += 1
    return filled


def live_samples_to_polls(
    samples: list[Sample],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """把即時取樣的溫度／CPU 欄位轉成 analyze() 吃的 poll 點（只取非 stale 的溫度）。"""
    thermal = [
        {
            "elapsed": s.elapsed,
            "cpu_c": s.cpu_c,
            "gpu_c": s.gpu_c,
            "skin_c": s.skin_c,
            "npu_c": s.npu_c,
            "battery_c": s.battery_c,
            "status": s.throttling_status,
        }
        for s in samples
        if not s.thermal_stale and (s.cpu_c is not None or s.throttling_status is not None)
    ]
    sys_points = [
        {
            "elapsed": s.elapsed,
            "cpu_total_pct": s.cpu_total_pct,
            "cpu_app_pct": s.cpu_app_pct,
            "cpu_app_norm_pct": s.cpu_app_norm_pct,
            "cpu_freq_mhz": s.cpu_freq_mhz,
            "mem_pss_mb": s.mem_pss_mb,
            "mem_rss_mb": s.mem_rss_mb,
            "mem_swap_mb": s.mem_swap_mb,
            "mem_available_mb": s.mem_available_mb,
            "gpu_busy_pct": s.gpu_busy_pct,
            "gpu_freq_mhz": s.gpu_freq_mhz,
            "gpu_mem_mb": s.gpu_mem_mb,
            "wakeups": s.wakeups,
            "cswitch": s.cswitch,
            "net_rx_kbps": s.net_rx_kbps,
            "net_tx_kbps": s.net_tx_kbps,
            "mem_detail_mb": s.mem_detail_mb,
            "battery_voltage_v": s.battery_voltage_v,
            "battery_current_ma": s.battery_current_ma,
            "battery_power_w": s.battery_power_w,
            "battery_plugged": s.battery_plugged,
            "battery_level_pct": s.battery_level_pct,
            "battery_charge_mah": s.battery_charge_mah,
            "battery_status": s.battery_status,
            "pss_error": None,
        }
        for s in samples
        if s.cpu_total_pct is not None or s.mem_rss_mb is not None
    ]
    return thermal, sys_points


class RecordSession:
    """隨錄隨停的 Perfetto 錄製 + 錄製期間的 timestats 即時顯示。

    流程：
      1. perfetto --background-wait 開始錄
      2. 另起 thread 跑 RealtimeSession（store=None）供即時畫面；它的溫度／CPU 讀數
         同時作為 trace 分析時的 poll 點（時間軸近似對齊）
      3. stop()/duration 到期/Ctrl-C → 停即時取樣 → SIGTERM perfetto → pull → 分析
      4. summary 與 samples 以 trace 分析結果為準；即時取樣另存 raw/live_samples.jsonl
    timestats 不可用時改用原本的 poll thread，只是錄製期間沒有即時畫面。
    """

    def __init__(
        self,
        adb: AdbClient,
        device: DeviceInfo,
        package: str,
        *,
        store: SessionStore,
        duration: float | None = None,
        interval: float = 1.0,
        layer_hint: str | None = None,
        thermal: bool = True,
        thermal_interval: float = 2.0,
        sysstats: bool = True,
        screenshot_interval: float | None = None,
        transfer_threshold_s: float | None = None,
    ) -> None:
        self.adb = adb
        self.device = device
        self.package = package
        self.store = store
        self.duration = duration
        # 錄製超過 transfer_threshold_s 秒時，停止後不自動 pull，改由呼叫端（Web）選擇傳輸連線
        self.transfer_threshold_s = transfer_threshold_s
        self.transfer_pending = False
        self.transfer_error: str | None = None
        self._transfer_event = threading.Event()
        self._transfer_adb: AdbClient | None = None
        self.session_id = new_session_id(package)
        self.started_at = datetime.now()
        self.notes: list[str] = []
        self.markers: list[tuple[float, str]] = []
        self._stop = threading.Event()
        self.recording = PerfettoRecording(
            adb,
            store,
            self.session_id,
            thermal=thermal,
            thermal_interval=thermal_interval,
            package=package if sysstats else None,
            poll=False,
        )
        self.live: RealtimeSession | None = RealtimeSession(
            adb,
            device,
            package,
            store=None,
            interval=interval,
            layer_hint=layer_hint,
            thermal=thermal,
            thermal_interval=thermal_interval,
            sysstats=sysstats,
            screenshot_interval=screenshot_interval,
            shots_dir=store.path(self.session_id) / "shots",
            raw_dir=store.path(self.session_id) / "raw",  # store=None 仍要保留原始 dumpsys
        )
        assert self.live is not None
        self.live.session_id = self.session_id
        self.live.started_at = self.started_at
        store.create(self.session_id)

    @property
    def samples(self) -> list[Sample]:
        return self.live.samples if self.live else []

    def stop(self) -> None:
        self._stop.set()
        if self.live:
            self.live.stop()

    def add_marker(self, label: str, elapsed: float | None = None) -> tuple[float, str]:
        marker = (
            round(elapsed if elapsed is not None else time.time() - self.started_at.timestamp(), 2),
            label,
        )
        self.markers.append(marker)
        return marker

    def choose_transfer(self, adb: AdbClient | None) -> None:
        """Web 選好傳輸連線後呼叫；None 代表沿用錄製用的連線。"""
        if not self.transfer_pending:
            raise SamplerError("目前不在等待傳輸的狀態")
        self._transfer_adb = adb
        self._transfer_event.set()

    def remote_trace_size_mb(self) -> float | None:
        size = self.recording.remote_size_bytes()
        return round(size / 1e6, 1) if size is not None else None

    def run(
        self,
        on_sample: Callable[[Sample], None] | None = None,
        on_state: Callable[[str], None] | None = None,
        on_transfer: Callable[[dict[str, Any]], None] | None = None,
    ) -> SessionSummary:
        def state(msg: str) -> None:
            if on_state:
                on_state(msg)

        self.recording.start(self.duration)
        live_error: list[str] = []

        def live_loop() -> None:
            assert self.live is not None
            try:
                self.live.run(on_sample)
            except FrameprobeError as exc:
                live_error.append(str(exc))

        live_thread = threading.Thread(target=live_loop, name="record-live", daemon=True)
        live_thread.start()
        state("錄製中（即時資料來自 timestats，停止後以 trace 分析為準）")
        try:
            self._stop.wait(self.duration) if self.duration else self._stop.wait()
        except KeyboardInterrupt:
            self.notes.append("使用者以 Ctrl-C 停止錄製")
        finally:
            if self.live:
                self.live.stop()
            live_thread.join(timeout=30.0)

        if live_error:
            self.notes.append(f"錄製期間的即時取樣失敗：{live_error[0].splitlines()[0]}")
        live_samples = list(self.samples)
        if live_samples:
            self.store.write_raw(
                self.session_id,
                "live_samples.jsonl",
                "\n".join(json.dumps(s.to_dict(), ensure_ascii=False) for s in live_samples) + "\n",
            )

        state("停止 perfetto…")
        try:
            self.recording.stop_tracing()
            elapsed_s = time.time() - self.started_at.timestamp()
            gate = (
                on_transfer is not None
                and self.transfer_threshold_s is not None
                and elapsed_s > self.transfer_threshold_s
            )
            if not gate:
                state("pull trace…")
                rec = self.recording.pull()
            else:
                # 長時間錄製：trace 很大，讓使用者決定走哪條連線；失敗就再問一次
                while True:
                    self.transfer_pending = True
                    self._transfer_event.clear()
                    assert on_transfer is not None
                    on_transfer(
                        {
                            "elapsed_s": round(elapsed_s),
                            "trace_size_mb": self.remote_trace_size_mb(),
                            "error": self.transfer_error,
                        }
                    )
                    self._transfer_event.wait()
                    self.transfer_pending = False
                    state("pull trace…")
                    try:
                        rec = self.recording.pull(self._transfer_adb)
                        break
                    except FrameprobeError as exc:
                        self.transfer_error = str(exc).splitlines()[0]
                        continue
        except FrameprobeError as exc:
            # 先把 session 寫下來（含錯誤與救回步驟），讓 `frameprobe analyze <id>` 之後能接手
            stub = SessionSummary(
                session_id=self.session_id,
                device=self.device,
                package=self.package,
                layer_name="",
                mode="record",
                started_at=self.started_at,
                duration_s=time.time() - self.started_at.timestamp(),
                notes=[
                    *self.notes,
                    f"錄製停止時發生錯誤：{exc}",
                    "尚未分析；救回 trace 後執行 `frameprobe analyze <session_id>`",
                ],
                markers=list(self.markers),
            )
            self.store.write_summary(self.session_id, stub)
            raise
        thermal_poll, sys_poll = live_samples_to_polls(live_samples)
        if rec.thermal_timeline is None and thermal_poll:
            rec.thermal_timeline = "approximate"
            rec.notes.append("溫度取自錄製期間的 timestats 即時取樣，時間軸為近似對齊")
        if self.live and self.live.notes:
            rec.notes.extend(n for n in self.live.notes if "Ctrl-C" not in n)
        if self.live and self.live.screenshot_failures >= 3:
            rec.notes.append("截圖連續失敗 3 次，已停用")

        state("分析 trace 中…")
        with open_trace(rec.trace_path) as query:
            result = analyze(
                query,
                package=self.package,
                thermal_poll=thermal_poll or rec.thermal_poll,
                thermal_timeline_hint=rec.thermal_timeline,
                sys_poll=sys_poll or rec.sys_poll,
            )
        summary = build_record_summary(
            result,
            session_id=self.session_id,
            device=self.device,
            package=self.package,
            started_at=self.started_at,
            extra_notes=[*self.notes, *rec.notes],
        )
        summary.markers = list(self.markers)
        # 每秒 sample 的 elapsed=N 代表 (N-1, N]，即時取樣在 e 秒截的圖屬於第 ceil(e) 秒
        shots = {max(1, math.ceil(s.elapsed)): s.screenshot for s in live_samples if s.screenshot}
        for sample in summary.samples:
            sample.screenshot = shots.get(int(sample.elapsed))
        self.store.write_summary(self.session_id, summary)
        for sample in summary.samples:
            self.store.append_sample(self.session_id, sample)
        return summary
