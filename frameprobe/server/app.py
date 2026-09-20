"""FastAPI 後端（SPEC §7）。

Session 在背景 thread 執行（adb 是阻塞的 subprocess），
sample 透過 loop.call_soon_threadsafe 推進每個 WebSocket 訂閱者的 asyncio.Queue。
"""

from __future__ import annotations

import asyncio
import os
import re
import threading
from collections import deque
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..adb import Adb, AdbClient, FrameprobeError
from ..launch import measure_launch
from ..probe import collect_device_info, probe
from ..sampler import Sample, capture_timestats
from ..session import RealtimeSession, RecordSession
from ..storage import (
    SessionNotFoundError,
    SessionStore,
    SessionSummary,
    export_session,
    summary_to_dict,
)

WEB_DIST = Path(__file__).resolve().parent.parent.parent / "web" / ".output" / "public"
AdbFactory = Callable[[str | None], AdbClient]


class SessionCreate(BaseModel):
    serial: str | None = None
    package: str
    mode: Literal["realtime", "record"] = "realtime"
    interval: float = Field(1.0, ge=0.5)
    layer: str | None = None
    duration: float | None = Field(None, gt=0)
    thermal: bool = True
    thermal_interval: float = Field(2.0, ge=0.5)
    sysstats: bool = True
    screenshot_interval: float | None = Field(None, ge=1.0)


class TransferChoice(BaseModel):
    serial: str | None = None
    """要用哪條 adb 連線 pull trace；None 代表沿用錄製時的連線。"""


# 超過 15 分鐘的錄製，停止時先問要走哪條連線傳 trace。環境變數可調低，方便測試流程。
LONG_RECORDING_S = float(os.environ.get("FRAMEPROBE_LONG_RECORDING_S", "900"))


def transfer_options(serial: str | None) -> list[dict[str, str]]:
    """同一支手機的所有 adb 連線（USB 與無線會各列一筆，靠 model+device 配對）。"""
    try:
        devices = Adb.list_devices()
    except FrameprobeError:
        return []
    me = next((d for d in devices if d["serial"] == serial), None)
    if me is None:
        return [d for d in devices if d["state"] == "device"]
    key = (me.get("model"), me.get("device"))
    return [
        d for d in devices if d["state"] == "device" and (d.get("model"), d.get("device")) == key
    ]


class LaunchCreate(BaseModel):
    package: str
    cold: bool = True
    repeat: int = Field(1, ge=1, le=10)


class MarkerCreate(BaseModel):
    label: str = Field(..., min_length=1, max_length=80)
    elapsed: float | None = Field(None, ge=0)


@dataclass
class Running:
    session_id: str
    serial: str | None
    package: str
    mode: str
    status: str = "running"
    message: str = ""
    started_at: datetime = field(default_factory=datetime.now)
    recent: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=120))
    subscribers: set[asyncio.Queue[dict[str, Any]]] = field(default_factory=set)
    realtime: RealtimeSession | None = None
    record: RecordSession | None = None
    transfer: dict[str, Any] | None = None
    """等待傳輸時的資訊（trace 大小、可用連線、上次錯誤）。"""
    thread: threading.Thread | None = None
    summary: SessionSummary | None = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "serial": self.serial,
            "package": self.package,
            "mode": self.mode,
            "status": self.status,
            "message": self.message,
            "started_at": self.started_at.isoformat(),
            "samples": len(self.recent),
            "transfer": self.transfer,
        }


class SessionManager:
    def __init__(self, store: SessionStore, adb_factory: AdbFactory) -> None:
        self.store = store
        self.adb_factory = adb_factory
        self.running: dict[str, Running] = {}
        self.loop: asyncio.AbstractEventLoop | None = None

    # --- 廣播 ------------------------------------------------------------- #

    def _broadcast(self, run: Running, message: dict[str, Any]) -> None:
        if message["type"] == "sample":
            run.recent.append(message["data"])
        for queue in list(run.subscribers):
            queue.put_nowait(message)

    def _post(self, run: Running, message: dict[str, Any]) -> None:
        """從工作 thread 安全地送進事件迴圈。"""
        if self.loop is None or self.loop.is_closed():
            self._broadcast(run, message)
        else:
            self.loop.call_soon_threadsafe(self._broadcast, run, message)

    def _state(self, run: Running, status: str, message: str = "") -> None:
        run.status, run.message = status, message
        self._post(run, {"type": "state", "data": {"status": status, "message": message}})

    # --- 生命週期 --------------------------------------------------------- #

    def device_busy(self, serial: str | None) -> Running | None:
        for run in self.running.values():
            if run.serial == serial and run.status in ("running", "awaiting_transfer"):
                return run
        return None

    def start(self, req: SessionCreate) -> Running:
        busy = self.device_busy(req.serial)
        if busy:
            raise HTTPException(
                409, f"裝置 {req.serial or '(預設)'} 已有執行中的 session：{busy.session_id}"
            )
        adb = self.adb_factory(req.serial)
        device = collect_device_info(adb)

        if req.mode == "realtime":
            session = RealtimeSession(
                adb,
                device,
                req.package,
                store=self.store,
                interval=req.interval,
                layer_hint=req.layer,
                duration=req.duration,
                thermal=req.thermal,
                thermal_interval=req.thermal_interval,
            )
            run = Running(session.session_id, req.serial, req.package, "realtime", realtime=session)

            def work_realtime() -> None:
                try:
                    run.summary = session.run(
                        lambda s: self._post(run, {"type": "sample", "data": s.to_dict()})
                    )
                    self._state(run, "stopped", "session 已結束")
                except FrameprobeError as exc:
                    run.summary = session.finalize()
                    self._state(run, "error", str(exc))

            target = work_realtime
        else:
            record = RecordSession(
                adb,
                device,
                req.package,
                store=self.store,
                duration=req.duration,
                interval=req.interval,
                layer_hint=req.layer,
                thermal=req.thermal,
                thermal_interval=req.thermal_interval,
                sysstats=req.sysstats,
                screenshot_interval=req.screenshot_interval,
                transfer_threshold_s=LONG_RECORDING_S,
            )
            run = Running(record.session_id, req.serial, req.package, "record", record=record)

            def on_transfer(info: dict[str, Any]) -> None:
                payload = {
                    **info,
                    "current_serial": req.serial,
                    "options": transfer_options(req.serial),
                }
                run.transfer = payload
                run.status, run.message = "awaiting_transfer", "錄製已停止，等待選擇傳輸連線"
                self._post(run, {"type": "transfer", "data": payload})
                self._post(
                    run,
                    {
                        "type": "state",
                        "data": {"status": "awaiting_transfer", "message": run.message},
                    },
                )

            def work_record() -> None:
                try:
                    summary = record.run(
                        lambda s: self._post(run, {"type": "sample", "data": s.to_dict()}),
                        lambda msg: self._state(run, "running", msg),
                        on_transfer,
                    )
                    # 以 trace 分析結果取代畫面上的即時資料
                    self._post(run, {"type": "reset", "data": {}})
                    run.recent.clear()
                    for sample in summary.samples:
                        self._post(run, {"type": "sample", "data": sample.to_dict()})
                    run.summary = summary
                    self._state(run, "stopped", "分析完成")
                except FrameprobeError as exc:
                    self._state(run, "error", str(exc))

            target = work_record

        self.running[run.session_id] = run
        run.thread = threading.Thread(target=target, name=f"session-{run.session_id}", daemon=True)
        run.thread.start()
        return run

    def stop(self, session_id: str) -> Running:
        run = self.running.get(session_id)
        if run is None:
            raise HTTPException(404, "沒有這個執行中的 session")
        if run.status != "running":
            raise HTTPException(409, f"session 已經是 {run.status} 狀態")
        if run.realtime is not None:
            run.realtime.stop()
        elif run.record is not None:
            run.record.stop()
        return run

    def transfer(self, session_id: str, serial: str | None) -> Running:
        run = self.running.get(session_id)
        if run is None or run.record is None:
            raise HTTPException(404, "沒有這個錄製中的 session")
        if run.status != "awaiting_transfer":
            raise HTTPException(409, "session 不在等待傳輸的狀態")
        adb = None if serial in (None, run.serial) else self.adb_factory(serial)
        try:
            run.record.choose_transfer(adb)
        except FrameprobeError as exc:
            raise HTTPException(409, str(exc)) from exc
        run.transfer = None
        run.status, run.message = (
            "running",
            f"透過 {serial or run.serial or '預設連線'} 傳輸 trace 中",
        )
        self._post(run, {"type": "state", "data": {"status": "running", "message": run.message}})
        return run

    def shutdown(self) -> None:
        for run in self.running.values():
            if run.status == "running":
                if run.realtime:
                    run.realtime.stop()
                if run.record:
                    run.record.stop()
        for run in self.running.values():
            if run.thread:
                run.thread.join(timeout=10.0)


# --------------------------------------------------------------------------- #


def create_app(store: SessionStore | None = None, adb_factory: AdbFactory | None = None) -> FastAPI:
    store = store or SessionStore()
    manager = SessionManager(store, adb_factory or (lambda serial: Adb(serial=serial)))

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        manager.loop = asyncio.get_running_loop()
        yield
        manager.shutdown()

    app = FastAPI(title="frameprobe", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
    )
    app.state.manager = manager

    def adb_or_503(serial: str | None) -> AdbClient:
        try:
            return manager.adb_factory(serial)
        except FrameprobeError as exc:
            raise HTTPException(503, str(exc)) from exc

    # --- devices ---------------------------------------------------------- #

    @app.get("/api/devices")
    def api_devices() -> list[dict[str, str]]:
        try:
            return Adb.list_devices()
        except FrameprobeError as exc:
            raise HTTPException(503, str(exc)) from exc

    @app.get("/api/devices/{serial}/doctor")
    def api_doctor(serial: str) -> dict[str, Any]:
        from dataclasses import asdict

        try:
            report = probe(adb_or_503(serial))
        except FrameprobeError as exc:
            raise HTTPException(502, str(exc)) from exc
        data = asdict(report)
        data["device"]["specs"] = report.device.specs.to_dict() if report.device.specs else None
        return data

    @app.get("/api/devices/{serial}/info")
    def api_device_info(serial: str) -> dict[str, Any]:
        """裝置規格（頁面頂端的裝置列用）。"""
        from dataclasses import asdict

        try:
            info = collect_device_info(adb_or_503(serial))
        except FrameprobeError as exc:
            raise HTTPException(502, str(exc)) from exc
        data = asdict(info)
        data["specs"] = info.specs.to_dict() if info.specs else None
        data["label"] = info.label
        return data

    @app.get("/api/devices/{serial}/packages")
    def api_packages(serial: str) -> dict[str, Any]:
        """前景 App（盡力而為）與第三方已安裝套件。"""
        adb = adb_or_503(serial)
        installed = adb.shell("pm list packages -3")
        packages = sorted(
            line.partition(":")[2].strip()
            for line in installed.stdout.splitlines()
            if line.startswith("package:")
        )
        focus = adb.shell("dumpsys activity activities | grep -iE 'resumedactivity'")
        # 只抓 Android 通用的 component 寫法 `pkg/.Activity`，不解析 dumpsys 的行結構
        match = re.search(r"([A-Za-z][A-Za-z0-9_.]+)/[A-Za-z0-9_.$]+", focus.stdout)
        return {
            "foreground": match.group(1) if match else None,
            "foreground_raw": focus.stdout.strip()[:500],
            "packages": packages,
        }

    @app.get("/api/devices/{serial}/layers")
    def api_layers(serial: str, package: str = Query(...)) -> dict[str, Any]:
        try:
            dump = capture_timestats(adb_or_503(serial))
        except FrameprobeError as exc:
            raise HTTPException(502, str(exc)) from exc
        candidates = dump.candidates_for(package)
        ambiguous = dump.is_ambiguous(candidates)
        return {
            "package": package,
            "ambiguous": ambiguous,
            "selected": candidates[0].layer_name if candidates and not ambiguous else None,
            "candidates": [
                {
                    "layer_name": c.layer_name,
                    "total_frames": c.total_frames,
                    "dropped_frames": c.dropped_frames,
                    "average_fps": c.effective_fps(),
                }
                for c in candidates
            ],
        }

    @app.post("/api/devices/{serial}/launch")
    def api_launch(serial: str, req: LaunchCreate) -> dict[str, Any]:
        """App 啟動時間（am start -W）。cold=True 會先 force-stop。"""
        try:
            result = measure_launch(
                adb_or_503(serial), req.package, cold=req.cold, repeat=req.repeat
            )
        except FrameprobeError as exc:
            raise HTTPException(502, str(exc)) from exc
        return result.to_dict()

    # --- sessions --------------------------------------------------------- #

    @app.post("/api/sessions", status_code=201)
    def api_create_session(req: SessionCreate) -> dict[str, Any]:
        try:
            return manager.start(req).snapshot()
        except FrameprobeError as exc:
            raise HTTPException(502, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.delete("/api/sessions/{session_id}")
    def api_stop_session(session_id: str) -> dict[str, Any]:
        return manager.stop(session_id).snapshot()

    @app.post("/api/sessions/{session_id}/transfer")
    def api_transfer(session_id: str, req: TransferChoice) -> dict[str, Any]:
        """長時間錄製停止後選擇用哪條 adb 連線 pull trace。"""
        try:
            return manager.transfer(session_id, req.serial).snapshot()
        except FrameprobeError as exc:
            raise HTTPException(503, str(exc)) from exc

    @app.post("/api/sessions/{session_id}/remove")
    def api_remove_session(session_id: str) -> dict[str, Any]:
        """把 session 移到 sessions/removed/。執行中的不能移。"""
        run = manager.running.get(session_id)
        if run is not None and run.status in ("running", "awaiting_transfer"):
            raise HTTPException(409, "session 還在執行中，先停止再移除")
        try:
            dest = manager.store.remove_session(session_id)
        except SessionNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        manager.running.pop(session_id, None)
        return {"session_id": session_id, "moved_to": str(dest)}

    @app.get("/api/sessions")
    def api_list_sessions() -> dict[str, Any]:
        return {
            "running": [
                r.snapshot()
                for r in manager.running.values()
                if r.status in ("running", "awaiting_transfer")
            ],
            "sessions": [summary_to_dict(s) for s in store.list_sessions()],
        }

    def load_or_404(session_id: str) -> SessionSummary:
        run = manager.running.get(session_id)
        if run and run.summary is not None:
            return run.summary
        try:
            return store.load_session(session_id)
        except SessionNotFoundError as exc:
            if run is not None:  # 執行中、尚未有 summary
                raise HTTPException(202, "session 執行中，尚無 summary") from exc
            raise HTTPException(404, str(exc)) from exc

    @app.get("/api/sessions/{session_id}")
    def api_session(session_id: str) -> dict[str, Any]:
        run = manager.running.get(session_id)
        if run and run.summary is None:
            return {"status": run.status, "message": run.message, "summary": None}
        summary = load_or_404(session_id)
        return {
            "status": run.status if run else "stopped",
            "message": run.message if run else "",
            "summary": summary_to_dict(summary),
        }

    @app.post("/api/sessions/{session_id}/markers", status_code=201)
    def api_add_marker(session_id: str, req: MarkerCreate) -> dict[str, Any]:
        """場景標籤。執行中的即時 session 直接加並廣播；歷史 session 寫回 summary.json。"""
        run = manager.running.get(session_id)
        live = run.realtime or run.record if run else None
        if run and live is not None and run.status == "running":
            elapsed, label = live.add_marker(req.label, req.elapsed)
            manager._broadcast(
                run, {"type": "marker", "data": {"elapsed": elapsed, "label": label}}
            )
            return {"elapsed": elapsed, "label": label}
        summary = load_or_404(session_id)
        if req.elapsed is None:
            raise HTTPException(422, "非執行中的 session 必須指定 elapsed")
        summary.markers.append((round(req.elapsed, 2), req.label))
        summary.markers.sort()
        store.write_summary(session_id, summary)
        return {"elapsed": round(req.elapsed, 2), "label": req.label}

    @app.get("/api/sessions/{session_id}/samples")
    def api_samples(session_id: str) -> list[dict[str, Any]]:
        run = manager.running.get(session_id)
        if run and run.status == "running" and (run.realtime or run.record):
            live = run.realtime or run.record
            assert live is not None
            return [s.to_dict() for s in live.samples]
        return [s.to_dict() for s in load_or_404(session_id).samples]

    @app.get("/api/sessions/{session_id}/shots/{name}")
    def api_shot(session_id: str, name: str) -> FileResponse:
        if "/" in name or not name.endswith(".png"):
            raise HTTPException(404, "not found")
        path = store.path(session_id) / "shots" / name
        if not path.is_file():
            raise HTTPException(404, "not found")
        return FileResponse(path, media_type="image/png")

    @app.get("/api/sessions/{session_id}/export")
    def api_export(session_id: str, format: str = Query("csv")) -> PlainTextResponse:
        try:
            text = export_session(load_or_404(session_id), format)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        media = "application/json" if format == "json" else "text/csv"
        return PlainTextResponse(
            text,
            media_type=media,
            headers={"Content-Disposition": f'attachment; filename="{session_id}.{format}"'},
        )

    # --- websocket -------------------------------------------------------- #

    @app.websocket("/ws/sessions/{session_id}")
    async def ws_session(websocket: WebSocket, session_id: str, replay: int = 120) -> None:
        await websocket.accept()
        run = manager.running.get(session_id)
        if run is None:
            # 歷史 session：一次送完所有 sample 後關閉
            try:
                summary = store.load_session(session_id)
            except SessionNotFoundError:
                await websocket.send_json(
                    {"type": "state", "data": {"status": "error", "message": "找不到 session"}}
                )
                await websocket.close()
                return
            for sample in summary.samples[-replay:] if replay else summary.samples:
                await websocket.send_json({"type": "sample", "data": sample.to_dict()})
            await websocket.send_json(
                {"type": "state", "data": {"status": "stopped", "message": "歷史資料"}}
            )
            await websocket.close()
            return

        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        run.subscribers.add(queue)
        try:
            for data in list(run.recent)[-replay:] if replay else []:
                await websocket.send_json({"type": "sample", "data": data})
            await websocket.send_json(
                {"type": "state", "data": {"status": run.status, "message": run.message}}
            )
            while True:
                message = await queue.get()
                await websocket.send_json(message)
                if message["type"] == "state" and message["data"]["status"] in ("stopped", "error"):
                    break
        except WebSocketDisconnect:
            pass
        finally:
            run.subscribers.discard(queue)

    # --- 前端靜態檔 ------------------------------------------------------- #

    if (WEB_DIST / "index.html").exists():
        app.mount("/_nuxt", StaticFiles(directory=str(WEB_DIST / "_nuxt")), name="nuxt-assets")

        @app.get("/{path:path}", include_in_schema=False)
        def _spa(path: str) -> FileResponse:
            """靜態檔存在就給檔案，否則回 index.html 讓 Nuxt 的 client router 處理深層路由。"""
            candidate = (WEB_DIST / path).resolve()
            if path and candidate.is_file() and WEB_DIST.resolve() in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(WEB_DIST / "index.html")
    else:

        @app.get("/", response_class=HTMLResponse)
        def _no_frontend() -> str:
            return (
                "<h1>frameprobe API 已啟動</h1>"
                f"<p>前端尚未 build：找不到 <code>{WEB_DIST}</code>。</p>"
                "<p>請執行 <code>cd web && npm install && npm run generate</code> 後重新啟動。</p>"
                '<p>API 文件：<a href="/docs">/docs</a></p>'
            )

    return app


def sample_to_dict(sample: Sample) -> dict[str, Any]:
    return sample.to_dict()
