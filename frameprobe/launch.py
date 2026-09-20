"""App 啟動時間。

流程：
  1. `cmd package resolve-activity --brief -a MAIN -c LAUNCHER <pkg>` 取得啟動 Activity
  2. 冷啟動先 `am force-stop`
  3. `am start -W -n <component>`，解析 TotalTime / WaitTime / LaunchState

`am start -W` 的輸出格式（ActivityManager shell，自 Android 4 起未變）：
    Starting: Intent { ... }
    Status: ok
    LaunchState: COLD
    Activity: com.example/.MainActivity
    TotalTime: 812
    WaitTime: 830
    Complete
尚未用本專案的真機 fixture 驗證，原始輸出一律保留在結果的 raw 裡。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from .adb import AdbClient, FrameprobeError


class LaunchError(FrameprobeError):
    pass


_KV_RE = re.compile(
    r"^\s*(Status|LaunchState|Activity|TotalTime|WaitTime):\s*(.+?)\s*$", re.MULTILINE
)


@dataclass
class LaunchResult:
    package: str
    component: str
    cold: bool
    status: str | None = None
    launch_state: str | None = None
    """COLD / WARM / HOT（Android 11+ 才有這行）"""
    total_ms: int | None = None
    """TotalTime：從 startActivity 到第一幀畫出來（Android 官方定義的 TTID）。"""
    wait_ms: int | None = None
    """WaitTime：含 am 本身等待的時間，一律 >= TotalTime。"""
    raw: str = ""
    runs: list[dict[str, Any]] = field(default_factory=list)
    """--repeat 時每一次的結果；total_ms 為平均。"""

    def to_dict(self) -> dict[str, Any]:
        return {
            "package": self.package,
            "component": self.component,
            "cold": self.cold,
            "status": self.status,
            "launch_state": self.launch_state,
            "total_ms": self.total_ms,
            "wait_ms": self.wait_ms,
            "runs": self.runs,
        }


def parse_am_start(text: str) -> dict[str, str]:
    return {key: value for key, value in _KV_RE.findall(text)}


def resolve_launcher_activity(adb: AdbClient, package: str) -> str:
    result = adb.shell(
        "cmd package resolve-activity --brief -a android.intent.action.MAIN "
        f"-c android.intent.category.LAUNCHER '{package}'"
    )
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    component = next((line for line in reversed(lines) if "/" in line and " " not in line), None)
    if not result.ok or component is None:
        raise LaunchError(
            f"找不到 {package} 的啟動 Activity。\n"
            f"resolve-activity 輸出：{result.stdout.strip() or result.stderr.strip() or '(空)'}"
        )
    return component


def measure_launch(
    adb: AdbClient,
    package: str,
    *,
    cold: bool = True,
    repeat: int = 1,
    settle_s: float = 1.0,
) -> LaunchResult:
    component = resolve_launcher_activity(adb, package)
    result = LaunchResult(package=package, component=component, cold=cold)
    raws: list[str] = []
    for _ in range(max(1, repeat)):
        if cold:
            adb.shell(f"am force-stop '{package}'")
            time.sleep(settle_s)
        start = adb.shell(f"am start -W -n '{component}'", timeout=60.0)
        raws.append(start.stdout + ("\n--- stderr ---\n" + start.stderr if start.stderr else ""))
        fields = parse_am_start(start.stdout)
        if not start.ok or fields.get("Status", "").lower() != "ok" or "TotalTime" not in fields:
            result.raw = "\n=====\n".join(raws)
            raise LaunchError(
                f"am start -W 失敗或輸出無法解析：{start.stderr.strip() or start.stdout.strip()}"
            )
        run = {
            "status": fields.get("Status"),
            "launch_state": fields.get("LaunchState"),
            "total_ms": int(fields["TotalTime"]),
            "wait_ms": int(fields["WaitTime"]) if fields.get("WaitTime", "").isdigit() else None,
        }
        result.runs.append(run)
    result.raw = "\n=====\n".join(raws)
    last = result.runs[-1]
    result.status = last["status"]
    result.launch_state = last["launch_state"]
    totals = [r["total_ms"] for r in result.runs]
    waits = [r["wait_ms"] for r in result.runs if r["wait_ms"] is not None]
    result.total_ms = round(sum(totals) / len(totals))
    result.wait_ms = round(sum(waits) / len(waits)) if waits else None
    return result
