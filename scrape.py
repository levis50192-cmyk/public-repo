#!/usr/bin/env python3
"""
抓取台灣彩券 BINGO BINGO（賓果賓果）最新開獎資料，合併進 data/draws.json。

重要：這支程式是普通的 Python 程式，跑在 GitHub Actions 的一般 CI 執行環境裡
（不是 Claude 工具呼叫），所以不會觸發任何「是否允許存取網站」的核准視窗，
可以真正無人值守、每次排程自動執行，不需要任何人手動點擊。

【誠實記錄：這是第二個資料來源，換過一次】原本用 lottery.timetable.tw 這個
公開 JSON API，結構乾淨、原本用起來沒問題，但陸續發現三種不同的可靠性問題：
(1) 它自己同步時偶爾會整批漏掉幾期資料；(2) 曾經卡住好幾小時才恢復；
(3) 這次直接整整停擺了快 14 小時、上百期沒有更新，而同時間其他獨立來源
（例如彩世界開獎網 988cp.net）跟官方開獎其實都正常。追查後判斷是這個
第三方 API 本身的服務不穩，不是我們抓取程式或 GitHub Actions 排程的問題，
但既然它已經連續出過三次狀況，就不適合再繼續依賴它。

改用 988cp.net 這個即時開獎網站的「歷史查詢」頁面：
    https://988cp.net/history?g=BingoBingo
這個頁面沒有公開文件記載的 JSON API（試過幾個常見的 /api/... 路徑都是
404），資料是網頁渲染出來的文字，所以改用 Playwright 開一個無頭瀏覽器
把頁面實際載入、讀取渲染後的純文字內容，再用固定格式（「HH:MM 期別期」
後面接著一行 40 位數字，也就是 20 個開獎號碼各兩位數字串接）解析出每一期
的期別跟號碼。這個頁面預設會顯示最近 100 期（約 8 小時份量），遠超過我們
15 分鐘排程一次所需要的量，就算某次排程漏跑、或這個網站本身短暫不穩，
下一次成功執行時通常還是能把中間漏掉的期數一次補齊，比之前那種「漏了就
永遠補不回來」的情況更耐用。

只抓期別（period）跟 20 個號碼；日期／時間刻意不採用這個網站顯示的文字
（避免又踩到前一個資料源「時間戳記不可信」的同類問題），而是沿用既有的
「期別 ↔ 現在時間」自我校正錨點機制（見下面 _get_anchor），完全不吃任何
外部來源的時間欄位。超級獎號（superNumber）這個資料源目前沒有穩定抓取，
一律留白（None）——之前的資料源也是回傳 null，前端本來就把它當可選欄位、
沒有資料時就不顯示那個區塊，行為不變。
"""
import datetime
import json
import os
import re
import sys

TAIPEI_OFFSET = datetime.timedelta(hours=8)  # 台灣全年不用日光節約時間，固定 UTC+8

SOURCE_URL = "https://988cp.net/history?g=BingoBingo"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(BASE_DIR, "data", "draws.json")
MAX_KEEP = 8000  # 約可涵蓋最近一個多月的每 5 分鐘開獎，避免檔案無限成長

# 「HH:MM  期別期」後面一行 40 位數字（20 個號碼各兩位）。988cp.net 目前的
# 版面就是這個順序：時間跟期別一行，緊接著號碼一行，再來是猜大小／猜單雙
# 那行（不理會）。用 re.finditer 逐一掃描整段渲染後的純文字。
_RECORD_RE = re.compile(r"(\d{2}:\d{2})\s+(\d{9})\s*期\s*\n\s*(\d{40})")


def _fetch_rendered_text(timeout_ms=30000):
    """用 headless Chromium 把歷史頁面實際載入一次，回傳渲染後的純文字。"""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(
                viewport={"width": 1280, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
                ),
                locale="zh-TW",
            )
            page.goto(SOURCE_URL, timeout=timeout_ms, wait_until="domcontentloaded")
            # 頁面資料是進站後才用前端邏輯畫出來的，給它一點時間渲染；
            # 用「等到至少出現一個『期』字」取代死等固定秒數，較不脆弱。
            page.wait_for_selector("text=期", timeout=timeout_ms)
            page.wait_for_timeout(1500)
            text = page.inner_text("body")
        finally:
            browser.close()
    return text


def fetch_latest():
    text = _fetch_rendered_text()

    out = []
    seen = set()
    for m in _RECORD_RE.finditer(text):
        _time_str, period, digits = m.groups()
        if period in seen:
            continue
        nums = [int(digits[i : i + 2]) for i in range(0, 40, 2)]
        # 防呆：BINGO BINGO 是從 1~80 選 20 個「不重複」的號碼，任何一項
        # 對不上就當成解析壞掉（例如網站改版、剛好卡到還沒渲染完的畫面），
        # 這期直接跳過，不要把髒資料寫進 draws.json。
        if len(nums) != 20 or len(set(nums)) != 20 or any(n < 1 or n > 80 for n in nums):
            continue
        seen.add(period)
        out.append(
            {
                "period": period,
                "numbers": sorted(nums),
                "superNumber": None,
            }
        )

    if not out:
        raise RuntimeError(
            "從 988cp.net 渲染出來的內容裡一筆有效資料都解析不到——"
            "可能是網站改版了格式，也可能是這次載入沒等到內容出現。"
        )
    return out


ANCHOR_PATH = os.path.join(BASE_DIR, "data", "time_anchor.json")


def _load_saved_anchor():
    if not os.path.exists(ANCHOR_PATH):
        return None, None
    with open(ANCHOR_PATH, "r", encoding="utf-8") as f:
        saved = json.load(f)
    return saved["anchor_period"], datetime.datetime.strptime(
        saved["anchor_time"], "%Y-%m-%d %H:%M"
    )


def _save_anchor(anchor_period, anchor_dt):
    os.makedirs(os.path.dirname(ANCHOR_PATH), exist_ok=True)
    with open(ANCHOR_PATH, "w", encoding="utf-8") as f:
        json.dump(
            {"anchor_period": anchor_period, "anchor_time": anchor_dt.strftime("%Y-%m-%d %H:%M")},
            f,
            ensure_ascii=False,
        )


def _get_anchor(all_period_ints, now_utc_naive):
    """
    取得（並在需要時更新）一個「期別 ↔ 台灣時間」錨點，用來推算每一期的顯示時間。

    賓果賓果公開規則是每 5 分鐘開一期、期別數字逐期加 1；但 API 沒有提供可信
    的逐期時間戳記（created_at 其實是批次同步時間，同一批會共用同一個值，
    詳見上面 fetch_latest 的說明），所以用「錨點 + 5 分鐘間隔」反推每一期的
    時間，取代不可信的 API 時間欄位。

    【這裡是第二次修正，誠實記錄前一版錯在哪】：上一版把錨點「只建立一次、
    之後永久寫死」，原本用意是讓時間穩定、不會每次排程亂跳。但實測發現
    BINGO BINGO 晚上有一段真的會暫停開獎的維護時間（期別數字在暫停期間不會
    前進，可是現實時間照樣在走），一次永久寫死的錨點沒辦法感知這種暫停，
    每次暫停都會讓「用期別反推出來的時間」跟實際時間多差一截、而且不會自己
    修正，幾天累積下來就會差到十幾個小時（使用者回報「現在都早上了，網站卻
    還顯示昨天下午」正是這個累積誤差）。

    修正邏輯：每次執行都比較「這次抓到的最大期別」跟「上次存的錨點期別」。
      - 如果有抓到更新的期別（代表確實有新資料進來），就把錨點重新校正到
        「這個最新期別＝現在的台灣時間」──等於每次有新資料時都用真實時間
        重新歸零一次，不會讓誤差一路累積下去，也能撐過每天的暫停空檔。
      - 如果這次沒有抓到更新的期別（上游卡住、或剛好遇到批次同步的空檔），
        就沿用舊錨點，不要讓時間憑空往前跳──否則會把「其實還沒開出來」的
        期別顯示成「現在」，變成另一種造假。
    """
    saved_period, saved_dt = _load_saved_anchor()
    current_max = max(all_period_ints)
    if saved_period is None or current_max > saved_period:
        anchor_period = current_max
        anchor_dt = now_utc_naive + TAIPEI_OFFSET
        _save_anchor(anchor_period, anchor_dt)
        return anchor_period, anchor_dt
    return saved_period, saved_dt


def _apply_time_formula(records, anchor_period, anchor_dt):
    """
    對每一筆資料套用：時間 = 錨點時間 + 5 分鐘 × (這期期別 - 錨點期別)。
    純粹由期別數字決定，不吃任何來自 API 的時間欄位，所以每次重算結果都一樣。

    已知的誤差來源（誠實寫在這裡，不隱藏）：因為錨點會在每次抓到新期別時
    重新校正到現在的真實時間（見 _get_anchor），所以「最新一期」的顯示時間
    誤差通常只有幾分鐘（排程間隔 + 上游同步延遲）；但如果賓果賓果在某段
    期間暫停開獎（跨日維護空檔），暫停「之前」那些舊期別，是用暫停「之後」
    重新校正的錨點往回推算，會被拉近成看起來間隔還是 5 分鐘一期，跟暫停前
    那幾期實際開出的時間比，可能有偏差。這個時間欄位純粹是給人看的顯示
    用途，不影響下面統計分析引擎的任何計算（分析引擎只用期別順序跟開獎
    號碼，不吃這個時間欄位）。
    """
    for d in records:
        period_int = int(d["period"])
        dt = anchor_dt + datetime.timedelta(minutes=5 * (period_int - anchor_period))
        d["date"] = dt.strftime("%Y-%m-%d")
        d["time"] = dt.strftime("%H:%M")


def load_existing():
    if not os.path.exists(DATA_PATH):
        return []
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    existing = load_existing()

    try:
        fresh = fetch_latest()
    except Exception as e:
        print(f"抓取失敗，本次保留現有資料不變：{e}", file=sys.stderr)
        return 0

    by_period = {d["period"]: d for d in existing}
    added = 0
    for d in fresh:
        if d["period"] not in by_period:
            added += 1
        # 號碼／超級獎號一律用最新抓到的內容為準（萬一上游資料有訂正）；
        # date/time 不在這裡處理，下面會用固定錨點對全部資料統一重算。
        by_period[d["period"]] = {
            "period": d["period"],
            "numbers": d["numbers"],
            "superNumber": d["superNumber"],
        }

    all_period_ints = [int(p) for p in by_period.keys()]
    if all_period_ints:
        now_utc_naive = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        anchor_period, anchor_dt = _get_anchor(all_period_ints, now_utc_naive)
        _apply_time_formula(by_period.values(), anchor_period, anchor_dt)

    merged = sorted(by_period.values(), key=lambda d: d["period"])
    if len(merged) > MAX_KEEP:
        merged = merged[-MAX_KEEP:]

    os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=0)

    print(f"新增 {added} 期，目前共 {len(merged)} 期資料。")
    return added


if __name__ == "__main__":
    main()
