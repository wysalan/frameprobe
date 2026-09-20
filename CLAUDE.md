# CLAUDE.md

## 專案

`frameprobe` — Android 遊戲效能採集工具（幀率為主，附帶溫度／CPU／GPU／記憶體／功耗）。Python CLI + FastAPI + Nuxt 儀表板。

初版規格在 `docs/SPEC.md`，初版任務拆解在 `docs/TASKS.md`（Phase 1～6 已全數完成，保留作為開發紀錄）。**動手前先讀 docs/SPEC.md。**

SPEC 只涵蓋初版範圍；後續新增的系統指標（CPU／GPU／記憶體／功耗／啟動時間）以 `docs/metrics.md` 與程式碼為準。後續調整與新增功能時的規則一律以本檔為準。

## 硬性規則

1. **禁止使用 `dumpsys SurfaceFlinger --latency` 作為 FPS 資料來源。** 唯一例外是 `diagnose` 指令的對照輸出，且必須標註 unreliable。詳見 SPEC §1.1、§1.3。
2. **禁止使用 `dumpsys gfxinfo` 判斷遊戲幀率。** 遊戲走 SurfaceView，不經過 HWUI，這條路對遊戲永遠是空的。
3. **禁止在沒有實際輸出樣本的情況下猜測 dumpsys 格式。** 若需要新的解析邏輯而手上沒有 fixture，停下來，在回應中明確說明「需要一份 XX 的真實輸出」，不要腦補一個格式然後寫測試去配合它。
4. **禁止預設 60Hz。** 取不到 refresh rate 就標 unknown。
5. **禁止靜默降級。** 任何 fallback 都必須在輸出中可見。

## 程式風格

- Python 3.11+，全面 type hints，`from __future__ import annotations`
- 純函式優先。解析器不得有 I/O，adb 呼叫不得有解析邏輯
- 例外一律用專案自訂型別（`FrameprobeError` 的子類），不要拋裸的 `RuntimeError`
- 每一個 adb 指令的原始輸出都要能被保留下來，這是新機型除錯的唯一線索
- 不要寫沒有被呼叫的抽象層。需要第二種實作時再抽介面

## 測試

- `pytest`。解析相關測試一律走 `tests/fixtures/` 的文字檔，不碰 adb
- 新增任何解析分支，同時新增對應 fixture
- 跑 `uv run pytest` 必須全綠才算完成一個任務
- 改動 `web/` 後跑 `npm run typecheck`（在 `web/` 目錄下）

## 提交

- 一個任務一個 commit，訊息用 conventional commits
- 不要在一個 commit 裡混入不相關的修改

## 遇到不確定時

優先順序：本檔 > docs/SPEC.md > 現有程式碼慣例 > 你的判斷。

如果本檔與 SPEC.md 都沒寫、而這個決定會影響資料正確性（例如某個 bucket 該不該算進百分位），**停下來問**，不要自己決定。

## 溫度相關的額外規則

6. **不要用 `mName` 猜感測器類型。** 一律看 `mType`。ROM 的命名沒有標準。
7. **同類型多顆感測器取 max，不要取平均。** 節流由最熱的那顆觸發，平均會稀釋訊號。
8. **`mType` 6/7/8 是 BCL 電壓／電流／百分比，不是溫度。** 漏濾會出現「3980°C」。
9. **溫度與其他輔助採集（CPU／記憶體／GPU／功耗）失敗不得中斷幀率 session。** 這些是脈絡資訊，幀率才是主體。
10. **sysfs 單位推斷的除數順序（1 → 10 → 1000）不可更動**，理由見 SPEC §12.4。
