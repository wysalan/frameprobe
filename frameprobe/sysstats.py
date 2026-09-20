"""CPU 使用率／頻率與記憶體採集。

來源全部是 procfs / sysfs（kernel 文件化格式，非 dumpsys）：
  /proc/stat                     整機與各核心 tick
  /proc/<pid>/stat               程序 utime/stime
  /sys/devices/system/cpu/*/cpufreq/{scaling_cur_freq,cpuinfo_max_freq}
  /proc/<pid>/smaps_rollup       PSS / RSS / Swap（release App 對 shell 可能不可讀）
  /proc/<pid>/statm              RSS 備援（永遠可讀）
  /proc/meminfo                  整機可用記憶體
  /sys/class/kgsl/kgsl-3d0/gpubusy   Adreno GPU 忙碌／總時間（每次讀取即重置，直接相除）
  /sys/class/kgsl/kgsl-3d0/{clock_mhz,gpuclk,devfreq/cur_freq}  GPU 頻率候選（各 ROM 可讀性不同）

解析為純函式；一次 adb shell 把所有檔案一起帶回，用 `#section` 分段。
CPU 使用率是兩次快照的差分，第一次只建基準。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .adb import AdbClient, FrameprobeError


class SysStatsError(FrameprobeError):
    pass


def build_command(package: str) -> str:
    pkg = package.replace("'", "")
    return (
        f"pid=$(pidof -s '{pkg}' 2>/dev/null || pgrep -f '^{pkg}$' | head -1); "
        "echo '#stat'; cat /proc/stat; "
        'echo "#pid $pid"; '
        "echo '#pstat'; [ -n \"$pid\" ] && cat /proc/$pid/stat 2>&1; "
        "echo '#freq'; for c in /sys/devices/system/cpu/cpu[0-9]*; do "
        'echo "$(basename $c) $(cat $c/cpufreq/scaling_cur_freq 2>/dev/null || echo -) '
        '$(cat $c/cpufreq/cpuinfo_max_freq 2>/dev/null || echo -)"; done; '
        "echo '#smaps'; [ -n \"$pid\" ] && cat /proc/$pid/smaps_rollup 2>&1; "
        "echo '#statm'; [ -n \"$pid\" ] && cat /proc/$pid/statm 2>&1; "
        'echo "#pagesize $(getconf PAGESIZE 2>/dev/null || echo 4096)"; '
        "echo '#meminfo'; cat /proc/meminfo; "
        "echo '#gpubusy'; cat /sys/class/kgsl/kgsl-3d0/gpubusy 2>&1; "
        "echo '#gpuclk'; for f in " + " ".join(GPU_FREQ_FILES) + "; do "
        'echo "$f $(cat $f 2>&1)"; done; '
        'echo \'#gpumem\'; [ -n "$pid" ] && dumpsys gpu 2>/dev/null | grep -E "^Proc $pid total"'
    )


#: Adreno 頻率候選檔，依序取第一個讀得到整數的。clock_mhz 單位 MHz，其餘 Hz。
GPU_FREQ_FILES = (
    "/sys/class/kgsl/kgsl-3d0/clock_mhz",
    "/sys/class/kgsl/kgsl-3d0/gpuclk",
    "/sys/class/kgsl/kgsl-3d0/devfreq/cur_freq",
    "/sys/class/devfreq/*gpu*/cur_freq",  # Pixel（Tensor / PowerVR）：34f00000.gpu0
)


@dataclass
class SysSnapshot:
    ts: float
    pid: int | None = None
    cores: dict[str, tuple[int, int]] = field(default_factory=dict)
    """cpuN -> (busy_ticks, total_ticks)。key 'cpu' 為整機加總。"""
    app_ticks: int | None = None
    freq_khz: dict[str, tuple[int | None, int | None]] = field(default_factory=dict)
    """cpuN -> (cur, max)"""
    pss_kb: int | None = None
    rss_kb: int | None = None
    swap_kb: int | None = None
    pss_error: str | None = None
    """smaps_rollup 讀不到的原因（例如 Permission denied），要讓使用者看到。"""
    mem_available_kb: int | None = None
    mem_total_kb: int | None = None
    gpu_busy_pct: float | None = None
    """Adreno gpubusy：busy/total×100；檔案不存在或不可讀為 None。"""
    gpu_freq_mhz: int | None = None
    gpu_error: str | None = None
    gpu_mem_kb: int | None = None
    """dumpsys gpu 的 `Proc <pid> total:`（GPU 記憶體，含紋理／buffer）。"""
    gpu_temp_c: float | None = None
    """kgsl 的 temp（毫度），thermalservice 沒有 GPU 型別時當備援。"""
    voluntary_ctxt: int | None = None
    """程序所有 thread 的 voluntary_ctxt_switches 總和（累積值）。"""
    nonvoluntary_ctxt: int | None = None
    net_rx_bytes: int | None = None
    """整機（lo 以外所有介面）累積收包 bytes。Android 12+ 無 root 拿不到 per-app。"""
    net_tx_bytes: int | None = None
    raw: str = ""


@dataclass
class CpuUsage:
    total_pct: float | None
    """整機 busy / total，0~100。"""
    app_pct: float | None
    """程序 ticks × 核心數 / total，同 top 的演算法，可超過 100。"""
    app_norm_pct: float | None
    """app_pct × (Σ當前頻率 / Σ最高頻率)：以頻率加權的實際運算量。"""
    freq_mhz: list[int]
    """各核心當前頻率（MHz），取不到的核心為 0。"""
    wakeups: int | None = None
    """區間內 voluntary context switch 數（≈ thread 被喚醒次數）。"""
    cswitch: int | None = None
    """區間內 voluntary + nonvoluntary context switch 總數。"""
    net_rx_kbps: float | None = None
    net_tx_kbps: float | None = None


def kb_to_mb(value: int | None) -> float | None:
    return round(value / 1024, 1) if value is not None else None


def _sections(text: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    current = "_"
    for line in text.splitlines():
        if line.startswith("#"):
            head, _, rest = line[1:].partition(" ")
            current = head
            out[current] = [rest] if rest else []
            continue
        out.setdefault(current, []).append(line)
    return out


def _kb(lines: list[str], key: str) -> int | None:
    for line in lines:
        if line.startswith(key + ":"):
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                return int(parts[1])
    return None


def parse_sysstats(text: str, *, ts: float | None = None) -> SysSnapshot:
    snap = SysSnapshot(ts=ts or time.time(), raw=text)
    sec = _sections(text)
    if "stat" not in sec:
        raise SysStatsError("輸出中沒有 /proc/stat 區段。")

    for line in sec["stat"]:
        parts = line.split()
        if not parts or not parts[0].startswith("cpu"):
            continue
        ticks = [int(p) for p in parts[1:] if p.isdigit()]
        if len(ticks) < 4:
            continue
        total = sum(ticks)
        idle = ticks[3] + (ticks[4] if len(ticks) > 4 else 0)  # idle + iowait
        snap.cores[parts[0]] = (total - idle, total)
    if "cpu" not in snap.cores:
        raise SysStatsError("/proc/stat 沒有 cpu 總計行。")

    pid_raw = (sec.get("pid") or [""])[0].strip()
    snap.pid = int(pid_raw) if pid_raw.isdigit() else None

    pstat = "\n".join(sec.get("pstat", [])).strip()
    if snap.pid is not None and ")" in pstat:
        rest = pstat[pstat.rfind(")") + 1 :].split()
        # rest[0]=state，utime/stime 是 proc(5) 的第 14/15 欄 → rest[11]/rest[12]
        if len(rest) > 12 and rest[11].isdigit() and rest[12].isdigit():
            snap.app_ticks = int(rest[11]) + int(rest[12])

    for line in sec.get("freq", []):
        parts = line.split()
        if len(parts) == 3 and parts[0].startswith("cpu"):
            cur = int(parts[1]) if parts[1].isdigit() else None
            mx = int(parts[2]) if parts[2].isdigit() else None
            snap.freq_khz[parts[0]] = (cur, mx)

    smaps = sec.get("smaps", [])
    snap.pss_kb = _kb(smaps, "Pss")
    snap.rss_kb = _kb(smaps, "Rss")
    snap.swap_kb = _kb(smaps, "Swap")
    if snap.pid is not None and snap.pss_kb is None:
        snap.pss_error = ("\n".join(smaps).strip() or "smaps_rollup 無輸出")[:200]
        statm = "\n".join(sec.get("statm", [])).split()
        pagesize_raw = (sec.get("pagesize") or ["4096"])[0].strip()
        pagesize = int(pagesize_raw) if pagesize_raw.isdigit() else 4096
        if len(statm) >= 2 and statm[1].isdigit():
            snap.rss_kb = int(statm[1]) * pagesize // 1024

    meminfo = sec.get("meminfo", [])
    snap.mem_available_kb = _kb(meminfo, "MemAvailable")
    snap.mem_total_kb = _kb(meminfo, "MemTotal")

    gpubusy = " ".join(sec.get("gpubusy", [])).split()
    if len(gpubusy) >= 2 and gpubusy[0].isdigit() and gpubusy[1].isdigit():
        busy, total = int(gpubusy[0]), int(gpubusy[1])
        # total 為 0 代表這段期間 GPU 完全 power collapse，視為 0%
        snap.gpu_busy_pct = min(100.0, busy / total * 100) if total > 0 else 0.0
    elif sec.get("gpubusy"):
        snap.gpu_error = " ".join(sec["gpubusy"]).strip()[:200]

    for line in sec.get("gpuclk", []):
        path, _, value = line.partition(" ")
        value = value.strip()
        if value.isdigit() and int(value) > 0:
            hz_or_mhz = int(value)
            snap.gpu_freq_mhz = hz_or_mhz if path.endswith("clock_mhz") else hz_or_mhz // 1_000_000
            break

    for line in sec.get("gpumem", []):
        parts = line.split()
        if len(parts) == 4 and parts[0] == "Proc" and parts[3].isdigit():
            snap.gpu_mem_kb = int(parts[3]) // 1024

    vol = nonvol = 0
    seen_ctxt = False
    for line in sec.get("ctxt", []):
        key, _, value = line.partition(":")
        value = value.strip()
        if not value.isdigit():
            continue
        seen_ctxt = True
        if key.strip() == "voluntary_ctxt_switches":
            vol += int(value)
        elif key.strip() == "nonvoluntary_ctxt_switches":
            nonvol += int(value)
    if seen_ctxt:
        snap.voluntary_ctxt, snap.nonvoluntary_ctxt = vol, nonvol

    rx = tx = 0
    seen_net = False
    for line in sec.get("net", []):
        iface, sep, counters = line.partition(":")
        if not sep or iface.strip() == "lo":
            continue
        fields = counters.split()
        if len(fields) >= 9 and fields[0].isdigit() and fields[8].isdigit():
            rx += int(fields[0])
            tx += int(fields[8])
            seen_net = True
    if seen_net:
        snap.net_rx_bytes, snap.net_tx_bytes = rx, tx

    gputemp = " ".join(sec.get("gputemp", [])).strip()
    if gputemp.lstrip("-").isdigit():
        from .thermal import normalize_sysfs_temp

        snap.gpu_temp_c = normalize_sysfs_temp(int(gputemp))
    return snap


def cpu_usage(prev: SysSnapshot, curr: SysSnapshot) -> CpuUsage:
    busy_prev, total_prev = prev.cores["cpu"]
    busy_curr, total_curr = curr.cores["cpu"]
    d_total = total_curr - total_prev
    if d_total <= 0:
        return CpuUsage(None, None, None, _freqs_mhz(curr))

    total_pct = max(0.0, (busy_curr - busy_prev) / d_total * 100)
    ncores = max(1, len([k for k in curr.cores if k != "cpu"]))

    app_pct: float | None = None
    if (
        prev.app_ticks is not None
        and curr.app_ticks is not None
        and prev.pid == curr.pid
        and curr.app_ticks >= prev.app_ticks
    ):
        app_pct = (curr.app_ticks - prev.app_ticks) * ncores / d_total * 100

    app_norm: float | None = None
    cur_sum = sum(c for c, _ in curr.freq_khz.values() if c)
    max_sum = sum(m for _, m in curr.freq_khz.values() if m)
    if app_pct is not None and cur_sum and max_sum:
        app_norm = app_pct * cur_sum / max_sum
    usage = CpuUsage(total_pct, app_pct, app_norm, _freqs_mhz(curr))

    same_pid = prev.pid == curr.pid and curr.pid is not None
    if (
        same_pid
        and prev.voluntary_ctxt is not None
        and curr.voluntary_ctxt is not None
        and prev.nonvoluntary_ctxt is not None
        and curr.nonvoluntary_ctxt is not None
    ):
        vol = max(0, curr.voluntary_ctxt - prev.voluntary_ctxt)
        nonvol = max(0, curr.nonvoluntary_ctxt - prev.nonvoluntary_ctxt)
        usage.wakeups, usage.cswitch = vol, vol + nonvol

    dt = curr.ts - prev.ts
    if (
        dt > 0
        and prev.net_rx_bytes is not None
        and curr.net_rx_bytes is not None
        and prev.net_tx_bytes is not None
        and curr.net_tx_bytes is not None
    ):
        usage.net_rx_kbps = max(0, curr.net_rx_bytes - prev.net_rx_bytes) / 1024 / dt
        usage.net_tx_kbps = max(0, curr.net_tx_bytes - prev.net_tx_bytes) / 1024 / dt
    return usage


def _freqs_mhz(snap: SysSnapshot) -> list[int]:
    ordered = sorted(snap.freq_khz, key=lambda k: int(k[3:]) if k[3:].isdigit() else 0)
    return [(snap.freq_khz[k][0] or 0) // 1000 for k in ordered]


class SysStatsCollector:
    """每次 read() 打一次 adb，回傳 (CpuUsage|None, SysSnapshot)。第一次只建基準。

    採集失敗不拋例外，回傳 None；連續失敗次數由呼叫端決定是否停用。
    """

    def __init__(self, adb: AdbClient, package: str) -> None:
        self.adb = adb
        self.command = build_command(package)
        self._prev: SysSnapshot | None = None
        self.failures = 0
        self.raw_snapshots: list[str] = []

    def read(self) -> tuple[CpuUsage | None, SysSnapshot] | None:
        result = self.adb.shell(self.command, timeout=15.0)
        if "#stat" not in result.stdout:
            self.failures += 1
            return None
        self.raw_snapshots.append(result.stdout)
        try:
            snap = parse_sysstats(result.stdout)
        except SysStatsError:
            self.failures += 1
            return None
        self.failures = 0
        usage = cpu_usage(self._prev, snap) if self._prev is not None else None
        self._prev = snap
        return usage, snap
