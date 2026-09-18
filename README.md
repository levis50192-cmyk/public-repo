# 賓果分析台（GitHub 版）

這是「賓果分析台」的完全自動化版本，用 GitHub Actions 排程 + GitHub Pages 架設，
取代之前 Claude 排程任務會卡在「等待人工核准」的問題。設定好之後**完全不需要人再點任何東西**，
也不會再消耗 Claude 的用量額度，因為抓資料、算統計、寫回、部署網頁這幾步都是純程式碼跑在
GitHub 自己的伺服器上，不經過任何 AI 或需要核准的流程。

## 這個版本改善了什麼

1. **真正自動、免費、無上限**：GitHub Actions 對公開（public）repo 完全免費、分鐘數無上限，
   排程用純 Python 發 HTTP 請求，不會跳出「是否允許存取」的核准視窗。
2. **資料來源升級**：改用 [lottery.timetable.tw](https://lottery.timetable.tw/developers)
   提供的台灣彩券公開資料 API（結構化 JSON、免金鑰、唯讀），取代原本請 AI 讀網頁、
   用文字摘要方式轉錄表格的做法——後者偶爾會摘要不完整或抓不到最新列，前者是乾淨的
   結構化資料，準確且穩定很多。
3. **頻率拉高**：從每小時一次改成每 15 分鐘一次（GitHub 排程本身在系統忙碌時可能延遲
   幾分鐘是正常現象，但比原本的每小時已經好上很多）。

## 檔案說明

- `scrape.py` — 呼叫 lottery.timetable.tw 的公開 API，抓最新開獎資料，合併進 `data/draws.json`（去重、依期別排序，只保留最近約一個月份的量避免檔案無限成長）。
- `analysis.py` — 你原本的統計/回測引擎，完全沒改，直接複製過來重用。
- `build_stats.py` — 讀 `data/draws.json`，呼叫 `analysis.py` 重新計算，寫成 `data/stats.json` 給前端讀。
- `index.html` — 儀表板本體，跟原本 Claude Artifact 版本外觀、功能一致（熱力圖、多方法比較、綜合建議、命中回測、星數下拉選單），差別只是改成定期用 `fetch()` 讀取同目錄下的 `data/stats.json`，而不是連 Claude 的即時資料庫。
- `.github/workflows/update.yml` — 排程設定，每 15 分鐘自動跑 `scrape.py` → `build_stats.py` → 把有變動的檔案 commit 回 repo。
- `data/draws.json`、`data/stats.json` — 目前已經放了你原本的 10 期真實種子資料算出來的結果，一上線就有內容可看，不會是空的。

## 上線步驟（大約 5 分鐘）

1. 到 GitHub 網站，用你現有帳號（`levis50192-cmyk`）新增一個 **public**（公開）repository，
   例如取名 `bingo-analysis`（一定要是 public，private repo 排程要另外設定額度，比較麻烦）。
   建立時不要勾選「Add a README」（我們自己有帶）。
2. 把這個資料夾裡的所有檔案（包含 `.github` 這個隱藏資料夾）上傳到剛剛建立的 repo：
   最簡單的方式是在 repo 頁面點 **Add file → Upload files**，把整個資料夾內容拖進去。
   如果你比較熟悉 git，也可以直接 `git init && git add . && git commit -m "init" && git push`。
3. 進到 repo 的 **Settings → Pages**，「Source」選 **Deploy from a branch**，
   Branch 選 `main`（或你上傳時的分支）、資料夾選 `/ (root)`，存檔。
   等 1-2 分鐘，GitHub 會給你一個網址，長得像：
   `https://levis50192-cmyk.github.io/bingo-analysis/`
4. 進到 repo 的 **Actions** 分頁，應該會看到「更新賓果賓果開獎資料與統計」這個 workflow。
   第一次可以手動點右邊的 **Run workflow** 觸發一次，確認它能成功抓到資料、綠勾勾代表成功。
   之後就會照排程（每 15 分鐘）自動執行，完全不用管它。
5. 把步驟 3 拿到的網址加到手機主畫面（Safari／Chrome 分享選單裡的「加入主畫面」），
   之後想看的時候直接點主畫面圖示即可，不用開 Claude。

## 之後想調整排程頻率

打開 `.github/workflows/update.yml`，把 `cron: "*/15 * * * *"` 改成你要的間隔即可，
例如 `*/30 * * * *` 是每 30 分鐘。**不建議設太密**（例如低於 5 分鐘），GitHub 對高頻率排程
本來就會自動延後執行，設太密也沒有實際效果。

## 誠實聲明（沿用原本立場，沒有改變）

BINGO BINGO 是每期獨立、電腦亂數產生的抽獎，不存在任何數學方法能讓「下一期」的預測準確率
超越均勻亂猜。這個工具的「建議號碼」只是把各種統計/機率方法整理成可以互相比較、並誠實回測
命中率的儀表板，用來證明「長期下來所有方法都會收斂到跟亂猜差不多」這件事，不是中獎預測。
BINGO BINGO 的獎金結構是負期望值，請理性消費、量力而為。
