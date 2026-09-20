"""Typer CLI 進入點。所有指令支援 --json。"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, NoReturn

import typer
from pydantic import TypeAdapter
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .adb import Adb, FrameprobeError
from .analysis import analyze, build_record_summary, open_trace
from .launch import measure_launch
from .perfetto_runner import record_trace
from .probe import CheckStatus, DeviceInfo, ProbeReport, collect_device_info, probe
from .sampler import Sample, capture_timestats
from .session import RealtimeSession, RecordSession, attach_raw_battery, live_samples_to_polls
from .storage import (
    SessionStore,
    SessionSummary,
    export_session,
    new_session_id,
    summary_to_dict,
)
from .thermal import ThrottlingStatus
from .timestats import TimestatsDump, parse_timestats

app = typer.Typer(help="Android 遊戲幀率採集工具", no_args_is_help=True)
console = Console()
err_console = Console(stderr=True)

_STATUS_ICON = {
    CheckStatus.PASS: "✅",
    CheckStatus.WARN: "⚠️",
    CheckStatus.FAIL: "❌",
    CheckStatus.SKIP: "⏭️",
}

SerialOpt = typer.Option(None, "--serial", "-s", help="裝置序號（多台裝置時必填）")
PackageOpt = typer.Option(..., "--package", "-p", help="遊戲的 package name")
JsonOpt = typer.Option(False, "--json", help="以 JSON 輸出")


def _adb(serial: str | None) -> Adb:
    try:
        return Adb(serial=serial)
    except FrameprobeError as exc:
        _die(str(exc))


def _die(message: str, code: int = 1) -> NoReturn:
    err_console.print(f"[red]錯誤：[/red]{message}")
    raise typer.Exit(code)


def _emit_json(data: Any) -> None:
    json.dump(data, sys.stdout, ensure_ascii=False, indent=2, default=str)
    sys.stdout.write("\n")


@app.callback()
def _main() -> None:
    pass


# --------------------------------------------------------------------------- #


@app.command()
def devices(as_json: bool = JsonOpt) -> None:
    """列出連線裝置。"""
    try:
        found = Adb.list_devices()
    except FrameprobeError as exc:
        _die(str(exc))
    if as_json:
        _emit_json(found)
        return
    if not found:
        console.print("沒有偵測到任何裝置。請確認 USB 偵錯已開啟。")
        return
    table = Table("serial", "state", "model", "device")
    for d in found:
        table.add_row(d["serial"], d["state"], d.get("model", ""), d.get("device", ""))
    console.print(table)


@app.command()
def doctor(
    serial: str | None = SerialOpt,
    verbose: bool = typer.Option(False, "--verbose", "-v", help="展開失敗項目的原始輸出"),
    as_json: bool = JsonOpt,
) -> None:
    """能力探測報表。新機型上第一個要跑的指令。"""
    adb = _adb(serial)
    try:
        report = probe(adb)
    except FrameprobeError as exc:
        _die(str(exc))
    if as_json:
        _emit_json(asdict(report))
        return
    print_doctor(report, verbose=verbose)
    if report.blocking_reason():
        raise typer.Exit(2)


def print_doctor(report: ProbeReport, *, verbose: bool) -> None:
    console.print(
        f"[bold]{report.device.label}[/bold]  serial={report.device.serial}"
        f"{'  [yellow](rooted)[/yellow]' if report.device.is_rooted else ''}"
    )
    specs = report.device.specs
    if specs is not None:
        rr = (
            f"{_fmt(specs.refresh_rate_hz, ' Hz', 0)}{'（ARR）' if specs.arr else ''}"
            if specs.refresh_rate_hz
            else "unknown"
        )
        console.print(
            f"[dim]{specs.soc} · {specs.cpu_summary} · GPU {specs.gpu_summary or '-'} · "
            f"RAM {_fmt(specs.ram_mb / 1024 if specs.ram_mb else None, ' GB')} · "
            f"{specs.screen_px} @ {rr} · {specs.os_summary}[/dim]"
        )
    table = Table("", "檢查項目", "結果", show_lines=verbose)
    for check in report.checks:
        summary = check.summary
        if verbose and check.detail and check.status is not CheckStatus.PASS:
            summary += f"\n[dim]{check.detail.strip()}[/dim]"
        table.add_row(_STATUS_ICON[check.status], check.name, summary)
    console.print(table)

    if report.layer_candidates:
        console.print("[dim]活躍圖層：[/dim]")
        for name in report.layer_candidates[:8]:
            console.print(f"  [dim]- {name}[/dim]")

    def yn(flag: bool) -> str:
        return "[green]可用[/green]" if flag else "[red]不可用[/red]"

    console.print(
        f"\n即時模式（timestats）：{yn(report.realtime_available)}    "
        f"離線模式（Perfetto）：{yn(report.record_available)}    "
        f"本機分析：{yn(report.analyze_available)}"
    )
    reason = report.blocking_reason()
    if reason:
        console.print(f"\n[red]{reason}[/red]")
    elif not verbose and any(c.status is not CheckStatus.PASS and c.detail for c in report.checks):
        console.print("[dim]加 --verbose 可查看失敗項目的原始輸出。[/dim]")


def snapshot_timestats(adb: Adb, *, warmup_s: float = 1.5) -> TimestatsDump:
    try:
        return capture_timestats(adb, warmup_s=warmup_s)
    except FrameprobeError as exc:
        _die(str(exc))


@app.command()
def layers(
    package: str = PackageOpt,
    serial: str | None = SerialOpt,
    as_json: bool = JsonOpt,
) -> None:
    """列出該 package 的候選圖層，並標示會被自動選中的那一個。"""
    dump = snapshot_timestats(_adb(serial))
    candidates = dump.candidates_for(package)
    ambiguous = dump.is_ambiguous(candidates)
    selected = candidates[0].layer_name if candidates and not ambiguous else None

    if as_json:
        _emit_json(
            {
                "package": package,
                "ambiguous": ambiguous,
                "selected": selected,
                "candidates": [
                    {
                        "layer_name": c.layer_name,
                        "total_frames": c.total_frames,
                        "dropped_frames": c.dropped_frames,
                        "average_fps": c.effective_fps(),
                    }
                    for c in candidates
                ],
                "all_layers": [(ly.layer_name, ly.total_frames) for ly in dump.layers],
            }
        )
        return

    if not candidates:
        console.print(f"[yellow]找不到屬於 {package} 且有幀資料的圖層。[/yellow]")
        console.print("請確認遊戲在前景且畫面有在動。本次 dump 看到的所有圖層：")
        for layer in dump.layers:
            console.print(f"  [dim]- {layer.layer_name} (frames={layer.total_frames})[/dim]")
        raise typer.Exit(1)

    table = Table("", "layer", "frames", "dropped", "avg fps")
    for c in candidates:
        fps = c.effective_fps()
        table.add_row(
            "→" if c.layer_name == selected else "",
            c.layer_name,
            str(c.total_frames),
            str(c.dropped_frames),
            f"{fps:.1f}" if fps else "-",
        )
    console.print(table)
    if ambiguous:
        console.print(
            "[yellow]前兩名幀數相近，無法自動判定。請用 --layer <關鍵字> 手動指定。[/yellow]"
        )
    else:
        console.print(f"[green]→ 將自動選用：[/green]{selected}")


# --------------------------------------------------------------------------- #
# watch
# --------------------------------------------------------------------------- #


def mb(value: float | None) -> str:
    return _fmt(value, " MB", 0)


def _pct(ratio: float | None) -> float | None:
    return ratio * 100 if ratio is not None else None


def _fmt(value: float | None, suffix: str = "", digits: int = 1) -> str:
    return f"{value:.{digits}f}{suffix}" if value is not None else "-"


def render_watch(
    session: RealtimeSession, *, sensor_keyword: str | None = None, recent: int = 20
) -> Group:
    samples = session.samples
    last = samples[-1] if samples else None
    layer = session.sampler.layer_name or "(尚未鎖定圖層)"

    numbers = Table.grid(expand=True, padding=(0, 3))
    for _ in range(6):
        numbers.add_column(justify="center")
    labels = ("FPS", "P90", "P99", "掉幀率", "Jank(近似)", "BigJank")
    values = (
        _fmt(last.fps if last else None),
        _fmt(last.p90_fps if last else None),
        _fmt(last.p99_fps if last else None),
        _fmt(last.dropped_ratio * 100 if last else None, "%"),
        str(last.jank) if last else "-",
        str(last.big_jank) if last else "-",
    )
    numbers.add_row(*(Text(v, style="bold white", justify="center") for v in values))
    numbers.add_row(*(Text(lb, style="dim", justify="center") for lb in labels))

    thermal = Text(justify="center")
    if session.sampler.thermal is None:
        thermal.append("溫度：未啟用", style="dim")
    elif last is None or last.throttling_status is None and last.cpu_c is None:
        thermal.append("溫度：等待第一筆讀數…", style="dim")
    else:
        status = ThrottlingStatus.parse(last.throttling_status or 0)
        hot = status is not None and status.is_throttling
        style = "bold red" if hot else ("dim" if last.thermal_stale else "")
        thermal.append(
            f"CPU {_fmt(last.cpu_c, '°C')}   GPU {_fmt(last.gpu_c, '°C')}   "
            f"SKIN {_fmt(last.skin_c, '°C')}   NPU {_fmt(last.npu_c, '°C')}"
            f"   節流：{last.throttling_label or 'unknown'}",
            style=style,
        )
        if last.thermal_stale:
            thermal.append("  (沿用上次讀數)", style="dim")
        if sensor_keyword and session.sampler.thermal._last:
            extras = [
                f"{r.name}={r.celsius:.1f}°C"
                for r in session.sampler.thermal._last.readings
                if sensor_keyword.lower() in r.name.lower() and r.is_plausible
            ]
            if extras:
                thermal.append("\n" + "  ".join(extras), style="cyan")

    table = Table(
        "t(s)",
        "frames",
        "fps",
        "p90",
        "p99",
        "drop%",
        "cpu",
        "gpu",
        "skin",
        "節流",
        expand=True,
        pad_edge=False,
    )
    for s in samples[-recent:]:
        style = "red" if (s.throttling_status or 0) >= ThrottlingStatus.MODERATE else ""
        thermal_style = "dim" if s.thermal_stale else style
        table.add_row(
            f"{s.elapsed:.0f}",
            str(s.frames),
            _fmt(s.fps),
            _fmt(s.p90_fps),
            _fmt(s.p99_fps),
            _fmt(s.dropped_ratio * 100),
            Text(_fmt(s.cpu_c), style=thermal_style),
            Text(_fmt(s.gpu_c), style=thermal_style),
            Text(_fmt(s.skin_c), style=thermal_style),
            Text(s.throttling_label or "-", style=thermal_style),
            style=style,
        )

    sysline = Text(justify="center")
    if session.sampler.sysstats is None:
        sysline.append("CPU/記憶體：未啟用", style="dim")
    elif last is None or last.cpu_total_pct is None:
        sysline.append("CPU/記憶體：等待第一筆讀數…", style="dim")
    else:
        freq = sum(last.cpu_freq_mhz) / len(last.cpu_freq_mhz) if last.cpu_freq_mhz else None
        mem = (
            f"PSS {_fmt(last.mem_pss_mb, ' MB', 0)}"
            if last.mem_pss_mb is not None
            else f"RSS {_fmt(last.mem_rss_mb, ' MB', 0)}[dim](PSS 不可讀)[/dim]"
        )
        sysline.append(
            f"CPU 整機 {_fmt(last.cpu_total_pct, '%', 0)}   App {_fmt(last.cpu_app_pct, '%', 0)}"
            f"   App(norm) {_fmt(last.cpu_app_norm_pct, '%', 0)}   "
            f"頻率 {_fmt(freq, ' MHz', 0)}   GPU {_fmt(last.gpu_busy_pct, '%', 0)}"
            f"@{_fmt(last.gpu_freq_mhz, ' MHz', 0)} mem {mb(last.gpu_mem_mb)}   {mem}"
            f"   可用 {mb(last.mem_available_mb)}"
        )
        if last.cswitch is not None or last.net_rx_kbps is not None:
            sysline.append(
                f"\nWakeups {last.wakeups if last.wakeups is not None else '-'}"
                f"   CSwitch {last.cswitch if last.cswitch is not None else '-'}"
                f"   網路 ↓{_fmt(last.net_rx_kbps, ' KB/s', 0)}"
                f" ↑{_fmt(last.net_tx_kbps, ' KB/s', 0)}（整機）"
            )
        if last.battery_power_w is not None or last.battery_voltage_v is not None:
            plug = "（接電源，非整機耗電）" if last.battery_plugged else ""
            volts = _fmt(last.battery_voltage_v, " V", 2)
            amps = _fmt(last.battery_current_ma, " mA", 0)
            watts = _fmt(last.battery_power_w, " W", 2)
            sysline.append(
                f"\n電池 {volts} × {amps} = {watts}{plug}   "
                f"FPower {_fmt(last.fpower_mw, ' mW/幀')}",
                style="dim" if last.power_stale else ("yellow" if last.battery_plugged else ""),
            )
        if last.mem_detail_mb:
            d = last.mem_detail_mb
            sysline.append(
                f"\nJava {mb(d.get('java_heap'))}  Native {mb(d.get('native_heap'))}"
                f"  Graphics {mb(d.get('graphics'))}  GL {mb(d.get('gl_mtrack'))}"
                f"  Code {mb(d.get('code'))}  Swap {mb(d.get('total_swap_pss'))}",
                style="dim" if last.mem_detail_stale else "",
            )

    header = f"[bold]{session.package}[/bold]  [dim]{layer}[/dim]  session={session.session_id}"
    return Group(Panel(Group(numbers, Text(), thermal, sysline), title=header), table)


def print_summary(summary: SessionSummary) -> None:
    table = Table(title=f"Session {summary.session_id}", show_header=False)
    rows = [
        ("裝置", summary.device.label),
        ("package / layer", f"{summary.package}\n{summary.layer_name}"),
        ("時長", f"{summary.duration_s:.1f} s"),
        ("總幀數 / 掉幀", f"{summary.total_frames} / {summary.dropped_frames}"),
        (
            "Avg / P90 / P99 FPS",
            f"{_fmt(summary.average_fps)} / {_fmt(summary.p90_fps)} / {_fmt(summary.p99_fps)}",
        ),
        (
            "峰值 CPU / GPU / SKIN",
            f"{_fmt(summary.peak_cpu_c, '°C')} / {_fmt(summary.peak_gpu_c, '°C')} / "
            f"{_fmt(summary.peak_skin_c, '°C')}   NPU {_fmt(summary.peak_npu_c, '°C')}",
        ),
        (
            "開始節流（>= MODERATE）",
            f"{summary.time_to_throttle_s:.1f} s 後"
            if summary.time_to_throttle_s is not None
            else "全程未節流",
        ),
        ("節流累計", f"{summary.throttle_duration_s:.1f} s"),
        (
            "穩定度",
            f"1% Low {_fmt(summary.low_1_fps)}   "
            f"Std(FPS) {_fmt(summary.fps_std)}   Drop(FPS) {summary.drop_fps}   "
            f"Avg/Std(FTime) {_fmt(summary.ftime_avg_ms)} / {_fmt(summary.ftime_std_ms, ' ms')}"
            f"   >100ms 幀 {summary.delta_ftime}",
        ),
        (
            f"Jank / BigJank（{summary.jank_method or '-'}）",
            f"{summary.jank_total} / {summary.big_jank_total}"
            f"   每 10 分鐘 {_fmt(summary.jank_per_10min)} / {_fmt(summary.big_jank_per_10min)}"
            f"   Stutter {_fmt(_pct(summary.stutter_ratio), '%', 2)}",
        ),
        (
            "CPU App 平均 / 峰值",
            f"{_fmt(summary.avg_cpu_app_pct, '%', 0)} / {_fmt(summary.peak_cpu_app_pct, '%', 0)}"
            f"   整機平均 {_fmt(summary.avg_cpu_total_pct, '%', 0)}",
        ),
        (
            "GPU 平均 / 峰值 / 記憶體峰值",
            f"{_fmt(summary.avg_gpu_busy_pct, '%', 0)} / {_fmt(summary.peak_gpu_busy_pct, '%', 0)}"
            f" / {mb(summary.peak_gpu_mem_mb)}",
        ),
        (
            "Wakeups / CSwitch（每區間平均）",
            f"{_fmt(summary.avg_wakeups, '', 0)} / {_fmt(summary.avg_cswitch, '', 0)}"
            f"   整機網路平均 ↓{_fmt(summary.avg_net_rx_kbps, ' KB/s', 0)}"
            f" ↑{_fmt(summary.avg_net_tx_kbps, ' KB/s', 0)}",
        ),
        (
            "功耗（未接電源時）",
            f"平均 {_fmt(summary.avg_power_w, ' W', 2)}   "
            f"FPower {_fmt(summary.avg_fpower_mw, ' mW/幀')}"
            + (
                f"   接電源比例 {_fmt(_pct(summary.plugged_ratio), '%', 0)}"
                if summary.plugged_ratio is not None
                else ""
            ),
        ),
        (
            "記憶體峰值 PSS / RSS",
            f"{_fmt(summary.peak_mem_pss_mb, ' MB', 0)} / "
            f"{_fmt(summary.peak_mem_rss_mb, ' MB', 0)}",
        ),
    ]
    if summary.jank_breakdown:
        rows.append(("jank", ", ".join(f"{k}: {v}" for k, v in summary.jank_breakdown.items())))
    if summary.notes:
        rows.append(("備註", "\n".join(summary.notes)))
    for k, v in rows:
        table.add_row(k, v)
    console.print(table)


@app.command()
def watch(
    package: str = PackageOpt,
    serial: str | None = SerialOpt,
    interval: float = typer.Option(1.0, "--interval", min=0.5, help="取樣間隔（秒），下限 0.5"),
    layer: str | None = typer.Option(None, "--layer", help="圖層名稱關鍵字（多候選時必填）"),
    duration: float | None = typer.Option(
        None, "--duration", help="自動停止秒數；不給則 Ctrl-C 停"
    ),
    no_save: bool = typer.Option(False, "--no-save", help="不寫入 sessions/"),
    no_thermal: bool = typer.Option(False, "--no-thermal", help="關閉溫度採集"),
    thermal_interval: float = typer.Option(2.0, "--thermal-interval", help="溫度取樣間隔（秒）"),
    thermal_sensor: str | None = typer.Option(
        None, "--thermal-sensor", help="額外顯示名稱含此關鍵字的感測器"
    ),
    no_sysstats: bool = typer.Option(False, "--no-sysstats", help="關閉 CPU/記憶體採集"),
    screenshot_interval: float | None = typer.Option(
        None, "--screenshot-interval", min=1.0, help="每 N 秒截圖到 shots/（會影響效能）"
    ),
    as_json: bool = JsonOpt,
) -> None:
    """即時模式：終端機面板 + 寫入 session。"""
    adb = _adb(serial)
    try:
        device = collect_device_info(adb)
        session = RealtimeSession(
            adb,
            device,
            package,
            store=None if no_save else SessionStore(),
            interval=interval,
            layer_hint=layer,
            duration=duration,
            thermal=not no_thermal,
            thermal_interval=thermal_interval,
            sysstats=not no_sysstats,
            screenshot_interval=screenshot_interval,
        )
    except (FrameprobeError, ValueError) as exc:
        _die(str(exc))

    def emit_line(kind: str, data: Any) -> None:
        print(json.dumps({"type": kind, "data": data}, ensure_ascii=False), flush=True)

    try:
        if as_json:
            summary = session.run(lambda s: emit_line("sample", s.to_dict()))
        else:
            with Live(
                render_watch(session, sensor_keyword=thermal_sensor),
                console=console,
                refresh_per_second=4,
            ) as live:
                summary = session.run(
                    lambda _s: live.update(render_watch(session, sensor_keyword=thermal_sensor))
                )
    except FrameprobeError as exc:
        _die(str(exc))

    if as_json:
        emit_line("summary", summary_to_dict(summary))
    else:
        print_summary(summary)
        if session.store is not None:
            console.print(f"[dim]已寫入 {session.store.path(session.session_id)}[/dim]")


# --------------------------------------------------------------------------- #
# record / analyze
# --------------------------------------------------------------------------- #


_SAMPLE_ADAPTER = TypeAdapter(Sample)


def _load_poll(store: SessionStore, session_id: str, name: str) -> list[dict[str, Any]]:
    path = store.path(session_id) / "raw" / name
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _analyze_session(
    store: SessionStore,
    session_id: str,
    *,
    package: str,
    device: DeviceInfo,
    started_at: datetime,
    extra_notes: list[str],
    thermal_timeline_hint: str | None,
) -> SessionSummary:
    trace = store.path(session_id) / "raw" / "trace.pb"
    if not trace.exists():
        _die(
            f"找不到 {trace}。\n"
            "若錄製時 pull 失敗，trace 仍在手機上，請先執行：\n"
            f"  adb pull /data/misc/perfetto-traces/{session_id}.pb {trace}\n"
            "再重跑這個指令。"
        )
    # 隨錄隨停模式的溫度／CPU 來自即時取樣（live_samples.jsonl）；舊的輪詢檔案為備援
    thermal_poll = _load_poll(store, session_id, "thermal_poll.jsonl")
    sys_poll = _load_poll(store, session_id, "sys_poll.jsonl")
    live = _load_poll(store, session_id, "live_samples.jsonl")
    if live:
        live_samples = [_SAMPLE_ADAPTER.validate_python(item) for item in live]
        if attach_raw_battery(live_samples, store.path(session_id) / "raw"):
            extra_notes = [
                *extra_notes,
                "電量／Charge counter／電池狀態由 raw/battery_*.txt 補回（舊版錄製）",
            ]
        thermal_poll, sys_poll = live_samples_to_polls(live_samples)
        if thermal_poll and thermal_timeline_hint is None:
            thermal_timeline_hint = "approximate"
    with open_trace(trace) as query:
        result = analyze(
            query,
            package=package,
            thermal_poll=thermal_poll,
            thermal_timeline_hint=thermal_timeline_hint,
            sys_poll=sys_poll,
        )
    summary = build_record_summary(
        result,
        session_id=session_id,
        device=device,
        package=package,
        started_at=started_at,
        extra_notes=extra_notes,
    )
    store.write_summary(session_id, summary)
    (store.path(session_id) / "samples.jsonl").unlink(missing_ok=True)
    for sample in summary.samples:
        store.append_sample(session_id, sample)
    return summary


@app.command()
def record(
    package: str = PackageOpt,
    serial: str | None = SerialOpt,
    duration: float | None = typer.Option(
        None, "--duration", min=1.0, help="錄製秒數；不給則錄到 Ctrl-C"
    ),
    interval: float = typer.Option(1.0, "--interval", min=0.5, help="錄製期間即時顯示的取樣間隔"),
    layer: str | None = typer.Option(None, "--layer", help="圖層名稱關鍵字（多候選時必填）"),
    no_thermal: bool = typer.Option(False, "--no-thermal", help="不錄溫度"),
    thermal_interval: float = typer.Option(2.0, "--thermal-interval"),
    no_sysstats: bool = typer.Option(False, "--no-sysstats", help="關閉 CPU/記憶體採集"),
    screenshot_interval: float | None = typer.Option(
        None, "--screenshot-interval", min=1.0, help="每 N 秒截圖到 shots/（會影響效能）"
    ),
    as_json: bool = JsonOpt,
) -> None:
    """離線模式：Perfetto 背景錄製、隨錄隨停（Ctrl-C），期間用 timestats 顯示即時資料，
    停止後以 trace 分析出精確的 Avg/P90/P99/Jank。"""
    adb = _adb(serial)
    try:
        device = collect_device_info(adb)
        session = RecordSession(
            adb,
            device,
            package,
            store=SessionStore(),
            duration=duration,
            interval=interval,
            layer_hint=layer,
            thermal=not no_thermal,
            thermal_interval=thermal_interval,
            sysstats=not no_sysstats,
            screenshot_interval=screenshot_interval,
        )
    except (FrameprobeError, ValueError) as exc:
        _die(str(exc))

    def emit_line(kind: str, data: Any) -> None:
        print(json.dumps({"type": kind, "data": data}, ensure_ascii=False), flush=True)

    try:
        if as_json:
            summary = session.run(
                lambda s: emit_line("sample", s.to_dict()),
                lambda msg: emit_line("state", {"status": "running", "message": msg}),
            )
        else:
            assert session.live is not None
            live = session.live
            with Live(render_watch(live), console=console, refresh_per_second=4) as panel:
                summary = session.run(
                    lambda _s: panel.update(render_watch(live)),
                    lambda msg: console.print(f"[dim]{msg}[/dim]"),
                )
    except FrameprobeError as exc:
        _die(str(exc))

    if as_json:
        emit_line("summary", summary_to_dict(summary))
    else:
        print_summary(summary)
        console.print(f"[dim]已寫入 {session.store.path(session.session_id)}[/dim]")


@app.command("analyze")
def analyze_command(
    target: str = typer.Argument(..., metavar="SESSION_ID|TRACE.pb"),
    package: str | None = typer.Option(
        None, "--package", "-p", help="App 層級歸因用；不給就只做顯示層級"
    ),
    as_json: bool = JsonOpt,
) -> None:
    """分析 trace，輸出 Avg/P90/P99/jank。接受 session id 或任意 trace.pb 路徑。"""
    store = SessionStore()
    try:
        if Path(target).is_file():
            with open_trace(target) as query:
                result = analyze(query, package=package)
            summary = build_record_summary(
                result,
                session_id=Path(target).stem,
                device=DeviceInfo("unknown"),
                package=package or "",
                started_at=datetime.now(),
            )
        else:
            previous = store.load_summary(target)
            summary = _analyze_session(
                store,
                target,
                package=package or previous.package,
                device=previous.device,
                started_at=previous.started_at,
                extra_notes=[n for n in previous.notes if not n.startswith("尚未分析")],
                thermal_timeline_hint=previous.thermal_timeline,
            )
    except FrameprobeError as exc:
        _die(str(exc))
    if as_json:
        _emit_json(summary_to_dict(summary, with_samples=True))
    else:
        print_summary(summary)
        if summary.app_level_available is False:
            console.print("[yellow]注意：App 層級 FrameTimeline 無資料，數字為顯示層級。[/yellow]")


@app.command()
def diagnose(
    package: str = PackageOpt,
    serial: str | None = SerialOpt,
    duration: float = typer.Option(10.0, "--duration", min=2.0, help="兩種方法同時採集的秒數"),
    layer: str | None = typer.Option(None, "--layer", help="圖層名稱關鍵字"),
    as_json: bool = JsonOpt,
) -> None:
    """交叉驗證：同一時窗內同時跑 timestats 與 Perfetto，並排比較 Avg/P90/P99。

    這是唯一允許印出 `dumpsys SurfaceFlinger --latency` 的地方，且一律標註 (unreliable)。
    """
    adb = _adb(serial)
    store = SessionStore()
    try:
        device = collect_device_info(adb)
        console.print(f"[dim]{device.label}[/dim]") if not as_json else None
        session_id = new_session_id(f"diagnose-{package}")
        store.create(session_id)
        enable = adb.shell("dumpsys SurfaceFlinger --timestats -clear -enable")
        if not enable.ok:
            _die(f"無法啟用 timestats：{enable.stderr or enable.stdout}")
        try:
            rec = record_trace(adb, store, session_id, duration_s=duration, thermal=False)
        finally:
            dump = adb.shell("dumpsys SurfaceFlinger --timestats -dump", timeout=30.0)
            adb.shell("dumpsys SurfaceFlinger --timestats -disable")
        store.write_raw(session_id, "timestats_final.txt", dump.stdout)
        parsed = parse_timestats(dump.stdout)
    except FrameprobeError as exc:
        _die(str(exc))

    candidates = parsed.candidates_for(package)
    if layer:
        candidates = [c for c in candidates if layer.lower() in c.layer_name.lower()]
    ts_layer = candidates[0] if candidates else None
    ts_row: dict[str, Any] = {
        "source": "timestats",
        "layer": ts_layer.layer_name if ts_layer else None,
    }
    if ts_layer:
        hist = ts_layer.present_to_present
        ts_row |= {
            "frames": ts_layer.total_frames,
            "avg": ts_layer.effective_fps(),
            "p90": hist.percentile_fps(0.90),
            "p99": hist.percentile_fps(0.99),
        }

    try:
        with open_trace(rec.trace_path) as query:
            result = analyze(query, package=package)
    except FrameprobeError as exc:
        _die(f"{exc}\ntrace 已存於 {rec.trace_path}")
    rows = [ts_row]
    rows.append(
        {
            "source": "perfetto (display)",
            "frames": result.display.total_frames,
            "avg": result.display.average_fps,
            "p90": result.display.p90_fps,
            "p99": result.display.p99_fps,
        }
    )
    if result.app and result.app.total_frames > 0:
        rows.append(
            {
                "source": "perfetto (app)",
                "frames": result.app.total_frames,
                "avg": result.app.average_fps,
                "p90": result.app.p90_fps,
                "p99": result.app.p99_fps,
            }
        )

    # --latency：僅供人眼對照。沒有真實樣本前不做解析（CLAUDE.md 規則 3），只印原始輸出。
    latency_raw = ""
    if ts_layer:
        latency = adb.shell(
            f"dumpsys SurfaceFlinger --latency '{ts_layer.layer_name}'", timeout=15.0
        )
        latency_raw = latency.stdout
        store.write_raw(session_id, "latency_unreliable.txt", latency_raw)

    if as_json:
        _emit_json(
            {
                "session_id": session_id,
                "rows": rows,
                "notes": [*rec.notes, *result.notes],
                "latency_unreliable_raw": latency_raw[:2000],
            }
        )
        return

    table = Table(
        "來源", "frames", "avg", "p90", "p99", title=f"{package} — {duration:.0f}s 交叉驗證"
    )
    for r in rows:
        table.add_row(
            str(r["source"]),
            str(r.get("frames", "-")),
            _fmt(r.get("avg")),
            _fmt(r.get("p90")),
            _fmt(r.get("p99")),
        )
    console.print(table)
    if ts_layer is None:
        console.print("[yellow]timestats 找不到該 package 的圖層。[/yellow]")
    for note in [*rec.notes, *result.notes]:
        console.print(f"[dim]- {note}[/dim]")
    if latency_raw.strip():
        lines = latency_raw.strip().splitlines()
        console.print(
            f"\n[yellow]dumpsys SurfaceFlinger --latency (unreliable)[/yellow] "
            f"[dim]共 {len(lines)} 行，僅供對照，不得作為 FPS 依據。前 5 行：[/dim]"
        )
        for line in lines[:5]:
            console.print(f"  [dim]{line}[/dim]")
    console.print(f"[dim]原始輸出：{store.path(session_id) / 'raw'}[/dim]")


@app.command()
def export(
    session_id: str = typer.Argument(...),
    fmt: str = typer.Option("csv", "--format", help="csv 或 json"),
    output: Path | None = typer.Option(None, "--output", "-o", help="輸出檔；不給就印到 stdout"),
) -> None:
    """匯出 session（csv 為逐筆 sample，json 為 summary 含 samples）。"""
    try:
        text = export_session(SessionStore().load_session(session_id), fmt)
    except (FrameprobeError, ValueError) as exc:
        _die(str(exc))
    if output:
        output.write_text(text, encoding="utf-8")
        console.print(f"已寫入 {output}")
    else:
        sys.stdout.write(text)


@app.command()
def serve(
    port: int = typer.Option(8420, "--port"),
    host: str = typer.Option("127.0.0.1", "--host"),
    sessions_dir: Path | None = typer.Option(None, "--sessions-dir", help="預設 ./sessions"),
) -> None:
    """啟動 API + Web 儀表板（serve web/.output/public 的靜態檔）。"""
    import uvicorn

    from .server.app import WEB_DIST, create_app

    if not (WEB_DIST / "index.html").exists():
        console.print(
            f"[yellow]前端尚未 build（找不到 {WEB_DIST}）。[/yellow]\n"
            "API 仍會啟動；要用儀表板請先執行：cd web && npm install && npm run generate"
        )
    console.print(f"http://{host}:{port}  （API 文件：/docs）")
    uvicorn.run(create_app(SessionStore(sessions_dir)), host=host, port=port, log_level="warning")


@app.command()
def launch(
    package: str = PackageOpt,
    serial: str | None = SerialOpt,
    warm: bool = typer.Option(False, "--warm", help="不先 force-stop（量溫啟動）"),
    repeat: int = typer.Option(1, "--repeat", min=1, max=10, help="重複次數，取平均"),
    as_json: bool = JsonOpt,
) -> None:
    """App 啟動時間：am start -W 的 TotalTime（首幀）與 WaitTime。預設冷啟動。"""
    try:
        result = measure_launch(_adb(serial), package, cold=not warm, repeat=repeat)
    except FrameprobeError as exc:
        _die(str(exc))
    if as_json:
        _emit_json(result.to_dict())
        return
    table = Table(
        "項目", "值", title=f"{package} 啟動時間（{'冷' if not warm else '溫'}啟動 × {repeat}）"
    )
    table.add_row("Activity", result.component)
    table.add_row("LaunchState", result.launch_state or "-")
    table.add_row("TotalTime（首幀）", f"{result.total_ms} ms")
    table.add_row("WaitTime", f"{result.wait_ms} ms" if result.wait_ms is not None else "-")
    if len(result.runs) > 1:
        table.add_row("每次 TotalTime", ", ".join(str(r["total_ms"]) for r in result.runs))
    console.print(table)


if __name__ == "__main__":
    app()
