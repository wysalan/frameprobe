# frameprobe

Android 遊戲幀率採集工具。Python CLI + FastAPI + Nuxt 儀表板。

目標平台 Android 12 (API 31) ～ Android 17 (API 37)。完整規格見 [`docs/SPEC.md`](docs/SPEC.md)。

兩種採集模式共用同一份資料模型：

| 模式 | 來源 | 特性 |
|---|---|---|
| `watch`（即時） | `dumpsys SurfaceFlinger --timestats` 累積值差分 | 每秒一筆 FPS / P90 / P99 / 掉幀率 + 溫度與節流狀態 |
| `record`（離線） | Perfetto FrameTimeline + `trace_processor` | 最準、有逐幀 jank 歸因；錄製期間以 timestats 顯示即時資料，停止後以 trace 分析為準 |

![錄製報告](screenshots/report-light.webp)

## 功能

- **幀率**：FPS、P90、P99、1% Low、掉幀率、frame time 直方圖
- **卡頓**：Jank、BigJank、Stutter，以及 Std(FPS)、Drop(FPS) 等穩定度指標
- **溫度**：CPU / GPU / 電池 / 機身、Thermal Status、time_to_throttle
- **系統**：CPU 使用率與各核心頻率、記憶體 PSS / RSS 與 Memory Detail、GPU 使用率（僅 Adreno）／頻率／記憶體、整機網路
- **功耗**：電壓、電流、功耗、每幀能耗
- **其他**：冷／溫啟動時間、定時截圖、場景標籤與分段統計、門檻告警、多 session 疊圖、CSV 匯出

不需要 root。每個指標的資料來源與定義見 [`docs/metrics.md`](docs/metrics.md)。

## 安裝

需要 Python 3.11+，以及 `adb` 在 PATH、裝置已開啟 USB 偵錯。

```bash
pipx install git+https://github.com/wysalan/frameprobe
# 或在原始碼目錄：uv sync && uv run frameprobe ...
```

有線／無線連接裝置的步驟見 [`docs/connecting.md`](docs/connecting.md)。

## 快速開始

```bash
frameprobe devices                 # 列出裝置
frameprobe doctor                  # 能力探測。新機型上第一個要跑的指令
frameprobe layers -p com.example.game
frameprobe watch  -p com.example.game --interval 1 --duration 60
frameprobe record -p com.example.game              # 隨錄隨停：Ctrl-C 結束並分析；--duration 30 可設上限
frameprobe analyze <session_id>    # 或 frameprobe analyze path/to/trace.pb -p <pkg>
frameprobe diagnose -p com.example.game   # 兩條路並排比較，判斷新機型上哪條可信
frameprobe export <session_id> --format csv
frameprobe launch -p com.example.game --repeat 3     # 冷啟動時間
frameprobe serve --port 8420       # API + 儀表板（前端需先 build，見下）
```

所有指令支援 `--json`。每個 session 存在 `sessions/<session_id>/`：

```
summary.json     彙總（Avg/P90/P99、峰值溫度、time_to_throttle_s …）
samples.jsonl    逐筆取樣
raw/             每次 dumpsys 的原始文字、perfetto config、trace.pb
```

歷史頁的「移除」只會把整個資料夾搬到 `sessions/removed/<session_id>/`，不刪任何檔案；要釋放空間請手動刪除該資料夾。

## Web 儀表板

```bash
cd web && npm install && npm run generate    # 產出 web/.output/public
frameprobe serve                              # http://127.0.0.1:8420
```

開發時可 `npm run dev`（預設打 `http://127.0.0.1:8420` 的 API，可用 `NUXT_PUBLIC_API_BASE` 覆寫）。

## 相容性

目標平台為 Android 12 (API 31) ～ 17 (API 37)。下表是實際用真機測過的機型；沒列到的機型不代表不能用，請先跑 `frameprobe doctor` 確認各項能力。

| 機型 | SoC／GPU | Android | `watch` | `record` | 溫度／節流 | GPU 使用率 | 功耗 | 備註 |
|---|---|---|---|---|---|---|---|---|
| Samsung Galaxy S23 (SM-S9110) | Snapdragon 8 Gen 2／Adreno | 16 (API 36) | ✅ | ✅ | ✅（無 GPU 溫度） | ✅ kgsl `gpubusy` | ✅ `dumpsys battery` | sysfs 電流不可讀，改用 Samsung 的 `current now`（mA） |
| Google Pixel 11 Pro | Tensor G6／PowerVR | 17 (API 37) | ✅ | ✅ | ✅ | ❌ 無可讀節點 | ✅ sysfs `current_now` | GPU 頻率與 GPU 記憶體可用 |

兩台的溫度來源皆為 `dumpsys thermalservice`。`record` 模式下 App 層級資料是否可用取決於遊戲而非機型（見下方已知限制）。

想回報新機型的測試結果，請依「回報問題」一節附上 `doctor` 輸出；確認可用後會補進這張表，對應的真機輸出則收進 `tests/fixtures/`。

## 已知限制

- **不使用 `dumpsys SurfaceFlinger --latency`。** 它只回傳最近 127 幀，layer name 格式自 Android 15 起不穩定；只有 `frameprobe diagnose` 會印出它供對照，且標註 `(unreliable)`。
- **純 SurfaceView 遊戲在 `record` 模式可能沒有 App 層級資料。** 此時自動改用顯示層級，summary 會標 `app_level_available: false`，jank 歸因不可用。這是預期行為，不是錯誤。
- **即時模式的 Jank 是近似值。** timestats 只有直方圖，只能以 83ms / 125ms 絕對門檻計數，summary 的 `jank_method` 會標 `histogram`；要精確值用 `record`。
- **不預設 60Hz。** 取不到 refresh rate 一律顯示 unknown。
- **timestats 格式在未知 ROM 上可能不同。** 新機型請先跑 `frameprobe doctor`；解析失敗時原始文字會留在 `sessions/<id>/raw/`。
- **sysfs 溫度單位是推斷值。** 只有 `dumpsys thermalservice` 等來源都不可用時才會走 `/sys/class/thermal`，讀數落在 10～120°C 之外或感測器本身 <10°C 時會判錯或被丟棄；來源會寫在 summary 的 `notes`。
- **部分 ROM 沒有 Thermal HAL 2.0**，拿不到節流狀態，`time_to_throttle_s` 會是 null；溫度本身仍可能有。

任何 fallback 都會在輸出中標明。各項的完整說明（含 ftrace 對齊、record 模式輪詢間隔）與 `--latency` 的設計理由見 [`docs/limitations.md`](docs/limitations.md)。

## 回報問題

新機型上數字不對或解析失敗時，請附上 `sessions/<id>/raw/` 整個資料夾（含 `timestats_*.txt`、`thermal_*.txt`、`perfetto_config.pbtxt`、`perfetto_stdout.txt`），以及 `frameprobe doctor --verbose --json` 的輸出。

若是 `--latency` 對照值有疑問，`raw/latency_unreliable.txt` 也一併附上。

**上傳前請先檢查並遮蔽裝置序號**：`doctor` 的輸出與 `raw/` 內的檔案可能含有 adb 序號、無線連線的 IP 等資訊。

## 開發

```bash
uv sync
uv run pytest --cov=frameprobe     # 全部走 tests/fixtures/，不需要真機
uv run ruff check . && uv run mypy
uv build                            # dist/*.whl
```

新增任何解析分支時，同時新增對應的 fixture。**沒有真實輸出樣本不要猜格式。**

目前 `tests/fixtures/README.md` 列出哪些 fixture 是真機輸出、哪些仍待補。任務拆解見 [`docs/TASKS.md`](docs/TASKS.md)。

## 致謝與聲明

Jank / BigJank / Stutter 等指標採用 PerfDog（騰訊 WeTest）公開文件的定義，方便與該工具的數字對照，對照表見 [`docs/metrics.md`](docs/metrics.md#指標定義的參考來源)。

本專案為獨立實作，未使用 PerfDog 的任何程式碼，與騰訊／WeTest／PerfDog 無任何關聯，亦未獲其背書。

PerfDog 為其所有者之商標，此處僅用於說明指標定義的出處。

## 授權

[MIT](LICENSE)
