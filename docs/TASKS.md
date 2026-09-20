# TASKS

> **Phase 1～6 已全數完成，本文件保留作為開發紀錄。**
> 
> 之後的功能（CPU／GPU／記憶體／功耗／啟動時間、匯出圖等）不在此清單內，請看 git 歷史。後續調整與新增功能時的規則一律以根目錄的 [`CLAUDE.md`](../CLAUDE.md) 為準。

依序執行。每個任務結束時 `uv run pytest` 必須全綠，然後 commit。
**不要跳著做**，後面的任務假設前面的已完成。

已經給你的東西（不要重寫，只在必要時擴充）：

- `frameprobe/adb.py` — adb 封裝
- `frameprobe/timestats.py` — timestats 解析器（已驗證，P90/P99 與官方範例一致）
- `frameprobe/probe.py` — 能力探測
- `frameprobe/sampler.py` — 即時差分取樣器（已整合溫度）
- `frameprobe/thermal.py` — 溫度與節流狀態採集（已驗證，四種來源皆有解析器）
- `tests/fixtures/` — timestats、thermalservice、sysfs 三份 fixture

---

## Phase 1 — 專案骨架與 doctor

### T1.1 專案初始化
- `uv init`，Python >= 3.11
- 依賴：`typer`, `rich`, `fastapi`, `uvicorn[standard]`, `pydantic>=2`, `perfetto`
- dev 依賴：`pytest`, `pytest-cov`, `ruff`, `mypy`
- 設定 `ruff`（line-length 100）與 `mypy`（strict）
- 把既有的四個模組放好，確認 `import frameprobe` 可用

### T1.2 為既有模組補測試
- `tests/test_timestats.py`：解析、兩種拼法、跨行直方圖、非等距 bucket、候選篩選、差分
- 邊界案例：`total_frames == 0`、全部落在 0ms bucket、只有一個 bucket、空輸入
- `tests/test_thermal.py`：四種來源的解析、mType 分類、無效讀數過濾（0.0 sentinel、BCL 型別）、
  同類型取最高值、sysfs 單位推斷（三種刻度各一例 + 猜不出來回 None）
- `tests/test_probe.py`：用 fake AdbClient（實作 `AdbClient` Protocol）餵各種 stdout，驗證每個 check 的 status
- 覆蓋率 `timestats.py`、`thermal.py`、`probe.py` >= 90%

### T1.3 `frameprobe devices` / `frameprobe doctor`
- Typer CLI 進入點 `frameprobe/cli.py`
- doctor 用 `rich.table` 輸出，每項標 ✅/⚠️/❌
- 失敗項目提供 `--verbose` 展開 `CheckResult.detail`
- 全部支援 `--json`
- doctor 最後印出結論：即時模式可用？離線模式可用？若都不可用，印 `blocking_reason()`
- 溫度那一列要印出實際讀到的 CPU/GPU/SKIN 數值與節流狀態，讓使用者一眼確認來源正確

### T1.4 `frameprobe layers -p <pkg>`
- 列出所有候選圖層與其 totalFrames
- 標示哪一個會被自動選中
- 若 `is_ambiguous()` 為真，明確提示需要 `--layer`

---

## Phase 2 — 即時模式

### T2.1 Session 儲存層
- `frameprobe/storage.py`
- 目錄結構 `sessions/<session_id>/`，內含 `summary.json`、`samples.jsonl`、`raw/`
- session_id 用 `<yyyymmdd-HHMMSS>-<pkg-slug>-<短 hash>`
- **每次 dump 的原始文字都要寫進 `raw/`**，一個檔一次 snapshot
- 提供 `list_sessions()` / `load_session()` / `append_sample()`

### T2.2 `frameprobe watch`
- 用 `RealtimeSampler` + `rich.live` 做終端機面板
- 上方大數字：當前 FPS / P90 / P99 / 掉幀率
- 第二行溫度：CPU / GPU / SKIN / 節流狀態；status >= MODERATE 時整行轉警告色
- 沿用舊值的溫度（`thermal_stale`）要用淡色顯示，不要讓使用者誤判
- 下方滾動的最近 20 筆表格
- Ctrl-C 要能乾淨收尾（呼叫 `sampler.stop()`、寫出 summary）
- 支援 `--interval`、`--layer`、`--duration`、`--no-save`
- 溫度旗標：`--no-thermal`、`--thermal-interval`、`--thermal-sensor`

### T2.3 Summary 彙總
- session 結束時把所有 sample 的直方圖累加，算出整段的 Avg/P90/P99
- 注意：**不能把每個 sample 的 FPS 平均起來當 average FPS**，要用總幀數 ÷ 總時間，或把直方圖合併後重算
- 溫度彙總：`peak_cpu_c` / `peak_gpu_c` / `peak_skin_c`、`time_to_throttle_s`、
  `throttle_duration_s`、`throttle_timeline`（見 SPEC §12.6）
- `time_to_throttle_s` 的定義是「status 首次 >= MODERATE 的 elapsed 秒數」；全程未節流則為 None

---

## Phase 3 — 離線模式

### T3.1 Perfetto 錄製
- `frameprobe/perfetto_runner.py`
- 依 SPEC §1.2 組 config、下 `adb shell perfetto`、pull 回本機
- trace 檔存進 `sessions/<id>/raw/trace.pb`
- 處理 `/data/misc/perfetto-traces/` 不存在或無權限的情況，錯誤訊息要明確

### T3.1b Trace 的溫度軌
- Perfetto config 加入 SPEC §12.7 的 ftrace thermal / cpu_frequency / gpu_frequency 事件
- 若目標 kernel 沒有 `thermal/thermal_temperature` 事件，啟動一條背景 thread 每 2 秒
  poll 一次 `dumpsys thermalservice`，並在 summary 標註 `thermal_timeline: "approximate"`
- 兩種路徑都要把溫度換算到與 trace 相同的時間軸

### T3.2 Trace 分析
- `frameprobe/analysis.py`
- 用 `perfetto.trace_processor.TraceProcessor` 跑 SPEC §4.1 的 SQL
- 再跑 §4.2 的 App 層級查詢取 jank breakdown
- **App 層級查詢回 0 列時自動回退到顯示層級，並在 summary 標註 `app_level_available: false`**（SPEC §4.3），這不是錯誤
- `frameprobe record` 與 `frameprobe analyze` 兩個指令

### T3.3 交叉驗證
- 寫一個 `frameprobe diagnose -p <pkg>` 指令：同時跑 timestats 與 perfetto，並排印出兩者的 Avg/P90/P99
- 這裡**才**允許順便印 `--latency` 的值，且必須標 `(unreliable)`
- 用途：在新機型上判斷哪條路可信

---

## Phase 4 — API

### T4.1 FastAPI 應用
- `frameprobe/server/app.py`，路由依 SPEC §7
- Session 以背景 task 執行，用 `asyncio.Queue` 把 sample 推給 WebSocket
- 一個裝置同時只能有一個 running session，重複建立要回 409

### T4.2 WebSocket
- `/ws/sessions/{id}`，訊息格式依 SPEC §7
- 斷線重連時要能補送最近 N 筆（預設 120）

### T4.3 `frameprobe serve`
- 啟動 uvicorn，並 serve `web/.output/public` 的靜態檔
- 前端尚未 build 時給明確提示，不要 404

---

## Phase 5 — 前端

### T5.1 Nuxt 專案
- `web/`，Nuxt 4 + TypeScript + Tailwind（原始碼在 `web/app/`）
- `nuxt.config.ts` 設 `ssr: false`（這是本機工具，不需要 SSR），`nitro.preset: 'static'`
- API base URL 可由環境變數覆寫，預設 `http://127.0.0.1:8420`

### T5.2 裝置與 doctor 頁
- 裝置下拉選單
- doctor 報表，每項可展開看 detail

### T5.3 即時儀表板
- `vue-echarts`
- FPS 折線圖（滾動 120s）、frame time 直方圖
- 溫度用**右側 Y 軸**疊在同一張 FPS 圖上（CPU/GPU/SKIN 三條），這是整個工具的核心畫面
- 圖表背景以色帶標示 Thermal Status 區間（NONE 透明 / LIGHT 黃 / MODERATE 橘 / SEVERE+ 紅）
- stale 的溫度點畫成虛線
- **WebSocket 資料先進 buffer，用 `requestAnimationFrame` 批次 flush**，不要每筆觸發 reactive 更新
- 開始/停止、間隔調整、layer 選擇

### T5.4 歷史與比較
- session 列表
- 單一 session 報告頁，頭條數字包含 `time_to_throttle_s`
- 兩個 session 疊圖比較（同一張圖兩條線）

---

## Phase 6 — 收尾

### T6.1 README
必須包含：
- 快速開始
- **已知限制**：FrameTimeline 對 SurfaceView 的支援限制、timestats 在未知 ROM 上可能的格式差異、
  sysfs 溫度單位為推斷值（說明何時會不準）、部分 ROM 無 Thermal HAL 因而拿不到節流狀態
- 「為什麼不用 `--latency`」的說明段落
- 回報新機型問題時該附上哪些檔案（`sessions/<id>/raw/`）

### T6.2 打包
- `uv build`
- 提供 `pipx install` 說明

### T6.3 CI
- GitHub Actions：ruff + mypy + pytest
- 不需要真機，所有測試走 fixture
