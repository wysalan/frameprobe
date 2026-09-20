# frameprobe — 規格書

> **本文件為初版規格。** 後續新增的系統指標（CPU／GPU／記憶體／功耗／啟動時間）不在此文件內，以 [`metrics.md`](metrics.md) 與程式碼為準。
> 
> 後續調整與新增功能時的規則一律以根目錄的 [`CLAUDE.md`](../CLAUDE.md) 為準；兩者衝突時以 `CLAUDE.md` 為準。

Android 遊戲幀率採集工具。Python 後端 + Nuxt 前端。
目標平台：**Android 12 (API 31) ~ Android 17 (API 37)**，主要驗證對象是 Android 17。

這份文件是實作契約。任何與此處衝突的「常見做法」都以此處為準。

---

## 0. 名詞

| 詞 | 意義 |
|---|---|
| layer | SurfaceFlinger 的一個圖層。一個遊戲通常有一個 `SurfaceView[...]` layer 在跑 |
| present2present | 連續兩幀實際顯示到螢幕的時間間隔（ms）。這是唯一該拿來算 FPS 的量 |
| P90 FPS | 把所有 frame interval 由小到大排，累積到 90% 的那個 bucket 換算成的 FPS。代表「最慢的 10% 幀」的水準 |
| P99 FPS | 同上，累積到 99%。用來抓 micro-stutter |
| jank | 該幀顯示時間超過預期（`actualPresentTime > expectedPresentTime`） |

---

## 1. 採集策略（最重要的一節）

工具支援兩種模式，**共用同一份資料模型**，但底層來源不同。

### 1.1 即時模式（realtime）— 基於 timestats 差分

原理：`dumpsys SurfaceFlinger --timestats` 在啟用後會**累積**統計。我們啟用一次，然後每隔 N 秒 dump 一次，**把本次與上次的累積值相減**，得到這段區間的幀數與直方圖，再換算成即時 FPS。

```
adb shell dumpsys SurfaceFlinger --timestats -clear -enable   # 啟動採集
loop every interval:
    adb shell dumpsys SurfaceFlinger --timestats -dump         # 取累積快照
    delta = curr - prev                                        # 差分
    emit Sample(fps, p90, p99, dropped_ratio)
adb shell dumpsys SurfaceFlinger --timestats -disable          # 結束
```

**為什麼是這個而不是 `--latency`：** `dumpsys SurfaceFlinger --latency` 只回傳最近 127 幀，且需要先用 `--list` 取得完整 layer name，而 layer name 的輸出格式在 Android 15 起就已變動，各家 ROM 也不一致，遊戲類 App 經常抓不到。timestats 的 dump 本身就帶 `layerName`，不需要另外呼叫 `--list`。

**取樣間隔**：預設 1000ms，可設定，下限 500ms。低於 500ms 時 dumpsys 本身的開銷會開始影響被測 App。

### 1.2 離線模式（record）— 基於 Perfetto FrameTimeline

原理：錄一段 Perfetto trace，用 `trace_processor` 跑 SQL 分析。這條路資料最準、能做逐幀 jank 歸因，但無法即時。

```
adb shell perfetto -o /data/misc/perfetto-traces/<name>.pb -t <duration> -c - <<EOF
buffers: { size_kb: 131072 }
data_sources: { config { name: "android.surfaceflinger.frametimeline" } }
data_sources: { config { name: "android.gpu.memory" } }
data_sources: {
  config {
    name: "linux.process_stats"
    process_stats_config { scan_all_processes_on_start: true }
  }
}
EOF
adb pull /data/misc/perfetto-traces/<name>.pb ./sessions/<id>/trace.pb
```

然後用 Python 的 `perfetto` 套件（`pip install perfetto`）跑 `TraceProcessor` 查詢。

### 1.3 降級順序

啟動時執行能力探測（見 §3），依序嘗試：

1. timestats 可用 → 即時模式啟用
2. perfetto 二進位存在且 sdk >= 31 → 離線模式啟用
3. 兩者皆不可用 → **明確報錯並列出探測結果**，不要靜默降級到 `--latency`

> **硬性禁令：`dumpsys SurfaceFlinger --latency` 不得作為 FPS 的資料來源。**
> 只允許在 `frameprobe diagnose` 指令中作為「參考對照值」輸出，且必須標註 `(unreliable)`。

---

## 2. timestats 輸出格式與解析規則

### 2.1 實際格式

dump 內容分為「全域區段」與若干「layer 區段」。layer 區段以 `layerName = ` 開頭。

```
layerName = SurfaceView[com.example.game/com.unity3d.player.UnityPlayerActivity]@0(BLAST)#132833
packageName =
totalFrames = 1000
droppedFrames = 12
averageFPS = 59.812
present2present histogram is as below:
0ms=0 1ms=0 2ms=0 3ms=0 4ms=0 5ms=0 6ms=0 7ms=0 8ms=0 9ms=0 10ms=0 11ms=0 12ms=0
13ms=0 14ms=0 15ms=0 16ms=850 17ms=0 18ms=0 ... 1000ms=0
post2present histogram is as below:
0ms=0 1ms=12 ...
```

### 2.2 解析必須處理的變異（已知風險）

這幾點是實作的重點，不要省略：

1. **直方圖鍵名不一致。** AOSP 原始碼用 `present2present`，Google 官方遊戲文件寫的是 `presentToPresent`。解析器必須把鍵名正規化後比對：轉小寫、移除非英數字元，接受 `present2present` / `presenttopresent` 兩種。同理 `post2present` / `posttopresent`。
2. **直方圖會換行。** bucket 不是固定一行，必須一路讀到下一個 `xxx is as below:` 或下一個 `layerName =` 或空行區塊結束為止，把所有 `<n>ms=<count>` token 收集起來。
3. **bucket 間距非線性。** 低段是 1ms 一格，34ms 之後變 2ms、50ms 之後變 4ms、150ms 之後變 50ms。**不要假設等距**，直接用解析出來的 key 排序。
4. **`averageFPS` 可能不存在或為 0**，此時用 `1000 / mean(present2present)` 自行計算。
5. **layer name 內含 `/` 與 `@`、`#`**，不要用它當檔名，要 slugify。
6. **同一個 package 可能有多個 layer**（啟動畫面、廣告 WebView 等）。選取規則見 §2.3。

### 2.3 Layer 選取規則

給定 package name，在所有 layer 中依序套用：

1. 篩出 `layerName` 含有該 package 字串的 layer
2. 排除 `totalFrames == 0` 的
3. 排除名稱含 `Splash`、`Starting`、`Dim`、`Wallpaper`、`NavigationBar`、`StatusBar` 的
4. 剩下的取 `totalFrames` 最大者
5. 若仍有多個且幀數相近（差距 < 10%），**回傳清單讓使用者選**，不要自動猜

CLI 提供 `--layer <substring>` 讓使用者手動指定。

### 2.4 百分位計算

依 Google 官方定義實作：

```python
p90_target = total_frames * 0.90
p99_target = total_frames * 0.99
cumulative = 0
for ms_bucket, count in sorted(histogram.items()):
    cumulative += count
    if p90_fps is None and cumulative >= p90_target:
        p90_fps = 1000 / ms_bucket      # ms_bucket == 0 時跳過，取下一個非零 bucket
    if p99_fps is None and cumulative >= p99_target:
        p99_fps = 1000 / ms_bucket
        break
```

`ms_bucket` 為 0 時會除零 —— 必須 guard，遇到 0 就往下一個 bucket 找。

---

## 3. 能力探測（capability probe）

每次連線裝置時執行一次，結果快取於 session。探測項目：

| 項目 | 方式 | 失敗影響 |
|---|---|---|
| `sdk_int` | `getprop ro.build.version.sdk` | 致命 |
| `android_release` | `getprop ro.build.version.release` | 無 |
| `model` / `manufacturer` | `getprop ro.product.model` / `ro.product.manufacturer` | 無 |
| `is_rooted` | `id` 的 uid 是否為 0 | 無，僅記錄 |
| `timestats_works` | `-clear -enable` → sleep 1.5s → `-dump`，檢查是否解析出至少一個 layer 且 `totalFrames > 0` | 即時模式不可用 |
| `perfetto_binary` | `perfetto --version` exit code | 離線模式不可用 |
| `traced_running` | `getprop init.svc.traced` == `running` | 嘗試 `setprop persist.traced.enable 1` 後重測 |
| `frametimeline_supported` | `sdk_int >= 31` | 離線模式不可用 |
| `trace_processor` | 本機 `shutil.which("trace_processor_shell")` 或 `import perfetto` 成功 | 離線分析不可用 |

探測結果必須能以 `frameprobe doctor` 指令印成人看得懂的報表，每一項標 ✅ / ❌ 並附上失敗時的原始輸出。**這是使用者在新機型上第一個會跑的指令。**

---

## 4. Perfetto SQL 查詢

### 4.1 顯示層級（使用者實際看到的畫面刷新）

```sql
WITH target_process AS (
    SELECT upid FROM process WHERE name = '/system/bin/surfaceflinger'
),
actual_present_times AS (
    SELECT (ts + dur) AS present_ts
    FROM actual_frame_timeline_slice
    WHERE upid IN (SELECT upid FROM target_process) AND dur > 0
),
present_intervals AS (
    SELECT (LEAD(present_ts) OVER (ORDER BY present_ts ASC) - present_ts) / 1000000.0 AS p2p_ms
    FROM actual_present_times
),
valid_intervals AS (
    SELECT p2p_ms FROM present_intervals WHERE p2p_ms IS NOT NULL AND p2p_ms > 0
),
ordered_frames AS (
    SELECT p2p_ms,
           ROW_NUMBER() OVER (ORDER BY p2p_ms ASC) AS row_num,
           COUNT(1) OVER () AS total_frames
    FROM valid_intervals
)
SELECT
    (SELECT COUNT(1) FROM valid_intervals) AS total_presented_frames,
    ROUND(1000.0 / NULLIF((SELECT AVG(p2p_ms) FROM valid_intervals), 0), 2) AS average_fps,
    ROUND(1000.0 / NULLIF((SELECT p2p_ms FROM ordered_frames WHERE row_num = CAST(total_frames * 0.90 AS INT)), 0), 2) AS low_10_fps,
    ROUND(1000.0 / NULLIF((SELECT p2p_ms FROM ordered_frames WHERE row_num = CAST(total_frames * 0.99 AS INT)), 0), 2) AS low_1_fps;
```

### 4.2 App 層級（歸因用）

把 `target_process` 換成 `WHERE process.name = :package_name`，並額外撈 jank 欄位：

```sql
SELECT jank_type, COUNT(*) AS cnt
FROM actual_frame_timeline_slice
WHERE upid IN (SELECT upid FROM process WHERE name = :package_name)
GROUP BY jank_type ORDER BY cnt DESC;
```

`jank_type` 的值包含 `None`、`App Deadline Missed`、`SurfaceFlinger CPU Deadline Missed`、`Prediction Error`、`Buffer Stuffing` 等。用它區分是遊戲的鍋還是系統合成的鍋。

### 4.3 已知限制（必須寫進 README）

FrameTimeline 的官方文件長期註記 **SurfaceView 尚未完整支援**。因此對純 SurfaceView 遊戲，§4.2 的 App 層級查詢可能查不到資料，而 §4.1 的顯示層級查詢仍然有效。實作時：若 App 層級查詢回傳 0 列，**自動回退到顯示層級並在報告中標註**，不要當成錯誤。

---

## 5. 資料模型

```python
@dataclass
class Sample:              # 即時模式的單次取樣
    ts: float              # unix timestamp
    elapsed: float         # 距離 session 開始的秒數
    frames: int            # 本區間幀數
    fps: float
    p90_fps: float | None
    p99_fps: float | None
    dropped: int
    dropped_ratio: float
    histogram: dict[int, int]

@dataclass
class SessionSummary:
    session_id: str
    device: DeviceInfo
    package: str
    layer_name: str
    mode: Literal["realtime", "record"]
    started_at: datetime
    duration_s: float
    total_frames: int
    average_fps: float
    p90_fps: float
    p99_fps: float
    dropped_frames: int
    jank_breakdown: dict[str, int] | None   # 僅 record 模式
    samples: list[Sample]                    # 僅 realtime 模式
```

持久化：每個 session 一個資料夾 `sessions/<session_id>/`，內含 `summary.json`、`samples.jsonl`、`raw/`（原始 dumpsys 文字與 trace.pb）。**原始輸出一定要留**，因為新機型出問題時要靠它 debug。

---

## 6. CLI 介面

```
frameprobe devices                             列出連線裝置
frameprobe doctor [-s SERIAL]                  能力探測報表
frameprobe layers -p <pkg> [-s SERIAL]         列出該 package 的候選 layer
frameprobe watch -p <pkg> [--interval 1.0] [--layer SUBSTR] [--duration N]
                                               即時模式，終端機輸出 + 寫入 session
frameprobe record -p <pkg> --duration 30       離線模式，錄 Perfetto trace
frameprobe analyze <session_id|trace.pb>       分析 trace，輸出 Avg/P90/P99/jank
frameprobe export <session_id> --format csv|json
frameprobe serve [--port 8420]                 啟動 API + Web 儀表板
```

所有指令支援 `--json` 讓輸出可被其他程式消費。

---

## 7. 後端 API

FastAPI。`frameprobe serve` 同時 serve 靜態化的 Nuxt 產物。

| Method | Path | 說明 |
|---|---|---|
| GET | `/api/devices` | 裝置清單 |
| GET | `/api/devices/{serial}/doctor` | 能力探測 |
| GET | `/api/devices/{serial}/packages` | 前景 App 與已安裝清單 |
| GET | `/api/devices/{serial}/layers?package=` | 候選 layer |
| POST | `/api/sessions` | 建立並啟動 session（body: serial, package, mode, interval, layer） |
| DELETE | `/api/sessions/{id}` | 停止 |
| GET | `/api/sessions` | 歷史清單 |
| GET | `/api/sessions/{id}` | summary |
| GET | `/api/sessions/{id}/samples` | 全部取樣 |
| GET | `/api/sessions/{id}/export?format=` | 匯出 |
| WS | `/ws/sessions/{id}` | 即時推送 Sample |

WebSocket 訊息格式：`{"type": "sample", "data": {...}}` / `{"type": "state", "data": {"status": "running|stopped|error", "message": "..."}}`

---

## 8. 前端（Nuxt 4 + TypeScript）

頁面：

1. `/` — 裝置選擇 + doctor 報表（每一項 ✅/❌，失敗可展開看原始輸出）
2. `/watch` — 即時儀表板
   - 大數字：當前 FPS、P90、P99、掉幀率
   - 折線圖：FPS over time（滾動視窗 120 秒）
   - 直方圖：frame time 分布（即時更新）
   - 開始/停止按鈕、取樣間隔調整
3. `/sessions` — 歷史清單
4. `/sessions/[id]` — 單次 session 報告，可與另一個 session 疊圖比較

圖表用 **ECharts**（`vue-echarts`）。不要用需要付費授權的圖表庫。

即時圖表必須做節流：WebSocket 進來的資料先進 buffer，用 `requestAnimationFrame` 批次更新，不要每筆都觸發 reactive re-render。

---

## 9. 技術選型（已定案，不要更動）

| 項目 | 選擇 | 理由 |
|---|---|---|
| Python | >= 3.11 | `StrEnum`、更好的 `asyncio` |
| CLI | `typer` | 型別即介面 |
| 終端輸出 | `rich` | 表格、即時 live 面板 |
| API | `fastapi` + `uvicorn` | WebSocket 原生支援 |
| adb 呼叫 | `subprocess`（自行封裝） | 不引入 `adbutils`，減少相依與版本風險 |
| trace 分析 | `perfetto` (PyPI) | 官方套件，會自動處理 trace_processor 二進位 |
| 資料驗證 | `pydantic` v2 | 與 FastAPI 一致 |
| 前端 | Nuxt 4 + TypeScript + ECharts | — |
| 套件管理 | `uv` | — |

---

## 10. 測試要求

**不要求真機才能跑測試。** 把採集與解析徹底分離：

- `tests/fixtures/` 放真實 dumpsys 輸出的文字檔（至少涵蓋 Android 12 / 14 / 15 / 17 各一份，以及一份 `present2present` 與一份 `presentToPresent` 拼法）
- 解析器測試全部走 fixture，不呼叫 adb
- adb 層用 protocol / 依賴注入，測試時塞 fake
- 百分位計算要有針對邊界的測試：total_frames = 0、histogram 全在 0ms bucket、只有一個 bucket

覆蓋率目標：`timestats.py` 與 `probe.py` >= 90%。

---

## 11. 給 Android 17 的特別注意

1. Android 17 = API level 37，2026-06-16 釋出。`sdk_int` 判斷式不要寫死上限。
2. 尚無任何社群工具公開驗證過 Android 17。**因此 `doctor` 指令是第一優先實作項目**，讓使用者能在任何新機型上先確認哪條路通。
3. 若 timestats 在 Android 17 上格式再度變更，解析器的正規化策略（§2.2）應該能吸收大部分變化；解析失敗時必須把原始文字存到 `sessions/<id>/raw/` 並在錯誤訊息中指出檔案路徑。
4. 不要在程式中假設 refresh rate 為 60Hz。VRR / 120Hz 裝置上 target FPS 應從 `dumpsys display` 或 timestats 的 `displayConfigStats` 推導，取不到就顯示為 unknown，不要預設 60。

---

## 12. 溫度與熱節流

### 12.1 為什麼這節重要

遊戲 FPS 下滑最常見的成因是熱節流，而節流是漸進的：看 FPS 曲線本身只看得到「掉了」，看不到「為什麼掉」。把 `Thermal Status` 與溫度疊在 FPS 曲線上，就能區分：

- FPS 在特定場景掉、溫度平穩 → 場景負載問題，去優化 draw call / shader
- FPS 隨時間緩慢下滑、溫度持續爬升、Status 由 NONE → LIGHT → MODERATE → 這是熱節流，優化 shader 沒用，要降功耗
- FPS 掉但溫度與 Status 都沒動 → 看 jank_type，多半是 asset streaming 或 GC

**因此溫度不是附加裝飾，而是幀率資料的必要脈絡。** 預設開啟，用 `--no-thermal` 關閉。

### 12.2 資料來源優先序

| 順序 | 指令 | 提供 | 備註 |
|---|---|---|---|
| 1 | `dumpsys thermalservice` | 分類溫度 + **節流狀態** | 唯一能拿到 Thermal Status 的來源。需 Thermal HAL 2.0（Android 10+） |
| 2 | `dumpsys hardware_properties` | CPU/GPU/battery/skin 溫度 | 舊機型備援，無節流狀態 |
| 3 | `/sys/class/thermal/thermal_zone*/` | 原始 zone 溫度 | 單位需推斷、類型只能靠名稱猜。部分 ROM 對 shell 不可讀 |
| 4 | `dumpsys battery` | 只有電池溫度 | 一定拿得到，但對遊戲效能參考價值低 |

來源在 session 開始時決定一次，之後不再重試。

### 12.3 `dumpsys thermalservice` 的解析規則

輸出中的溫度項格式固定為：

```
Temperature{mValue=58.4, mType=0, mName=cpu0-silver-usr, mStatus=1}
```

必須遵守的規則：

1. **優先取「Current temperatures from HAL:」區段。** 另有一個「Cached temperatures:」區段是上次回呼的快取，可能是舊值；兩者的感測器名稱會重複，以 HAL 區段為準，找不到才改用 cached。
2. **用 `mType` 判斷類型，不要用 `mName` 猜。** 各家 ROM 的命名天差地遠（`AP`、`cpu0-silver-usr`、`tsens_tz_sensor0` 都是 CPU），而且看過 `mType=5` 卻叫 `*-battery` 的板子。
3. **同類型多顆感測器取最高值，不要取平均。** 節流是由最熱的那顆觸發的，平均會把訊號稀釋掉。
4. **過濾無效讀數。** `mValue=0.0` 是常見的未接感測器 sentinel（Samsung 的 `SUBBAT`／`SUBBATRAW`）。
5. **`mType` 6/7/8 是 BCL_VOLTAGE / BCL_CURRENT / BCL_PERCENTAGE，不是溫度。** 它們共用 `Temperature{}` 結構但單位不是攝氏度，必須排除，否則會出現「3980°C」。
6. **未知的 mType 不要丟棄**，回退成 `TYPE_<n>` 顯示。新版 Android 會加新型別。

`Thermal Status:` 對應 `PowerManager.THERMAL_STATUS_*`：0 NONE / 1 LIGHT / 2 MODERATE / 3 SEVERE / 4 CRITICAL / 5 EMERGENCY / 6 SHUTDOWN。**>= 2 (MODERATE) 視為進入實質節流。**

### 12.4 sysfs 的單位推斷

不同 SoC 的 `thermal_zone*/temp` 單位分別是度、十分之一度、毫度，沒有統一標準，只能猜。

**除數必須由小到大嘗試（1 → 10 → 1000），取第一個落在 10~120°C 的結果。** 順序寫反會出錯：`44` 若先試 ÷10 會得到 4.4°C，看起來也「合理」但是錯的。由小到大時 `44 → 44°C`、`368 → 36.8°C`、`58400 → 58.4°C` 三種刻度都正確。

推斷用的帶寬（10~120°C）刻意比讀數有效性檢查的帶寬（1~150°C）窄，因為帶寬越寬越容易讓多種刻度同時看起來合理。

### 12.5 取樣頻率

溫度變化遠比幀率慢，而每次 dumpsys 都有成本。因此：

- 溫度預設每 **2 秒**才真正打一次指令（`--thermal-interval`）
- 兩次採集之間的 `Sample` 沿用上一次的讀數，並標記 `thermal_stale: true`
- 前端必須把 stale 的點畫成虛線或淡色，不要讓使用者誤以為那是新採的值
- 溫度採集失敗**不得中斷幀率 session**。失敗就沿用舊值，連續失敗就停用溫度並在 UI 提示

### 12.6 `Sample` 新增欄位

```python
cpu_c: float | None
gpu_c: float | None
battery_c: float | None
skin_c: float | None            # 機身表面，多數 OEM 節流的實際依據
throttling_status: int | None   # 0-6
throttling_label: str | None    # NONE / LIGHT / MODERATE / ...
thermal_stale: bool
```

`SessionSummary` 新增：

```python
peak_cpu_c: float | None
peak_gpu_c: float | None
peak_skin_c: float | None
time_to_throttle_s: float | None   # 從 session 開始到 status 首次 >= MODERATE 的秒數
throttle_duration_s: float          # status >= MODERATE 的累計秒數
throttle_timeline: list[tuple[float, int]]   # (elapsed, status) 變化點
```

`time_to_throttle_s` 是壓力測試最重要的單一數字：同一台裝置上比較兩個版本的遊戲，撐多久才開始降頻。

### 12.7 離線模式的溫度

Perfetto config 增加 ftrace 的 thermal 事件，讓溫度與逐幀資料共用同一條時間軸：

```
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
```

分析時從 `counter` 表撈對應的 counter track，與 frame timeline 對齊。若 ftrace 沒有 thermal 事件（部分 kernel 未啟用），回退成「錄製期間每 2 秒平行 poll 一次 `dumpsys thermalservice`」，並在報告標註時間軸為近似對齊。

### 12.8 CLI 與 UI

CLI 新增旗標：

```
--no-thermal                 關閉溫度採集
--thermal-interval <秒>      溫度取樣間隔，預設 2.0
--thermal-sensor <關鍵字>    額外顯示指定名稱的感測器
```

`frameprobe watch` 的面板在 FPS 數字旁增加一行溫度，**節流狀態 >= MODERATE 時整行轉為警告色**。

Web 儀表板：

- FPS 折線圖增加右側 Y 軸畫溫度（CPU/GPU/SKIN 三條）
- 圖表背景用色帶標示 Thermal Status 區間（NONE 透明 / LIGHT 黃 / MODERATE 橘 / SEVERE 以上紅）
- session 報告頁把 `time_to_throttle_s` 當成頭條數字之一
