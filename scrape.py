#!/usr/bin/env python3
"""
從 lottery.timetable.tw 的公開 JSON API 抓取台灣彩券 BINGO BINGO（賓果賓果，
gameTypeId=10）最新開獎資料，合併進 data/draws.json。

重要：這支程式是普通的 Python HTTP request，跑在 GitHub Actions 的一般 CI
執行環境裡（不是 Claude 工具呼叫），所以不會觸發任何「是否允許存取網站」的
核准視窗，可以真正無人值守、每次排程自動執行，不需要任何人手動點擊。

API 為公開唯讀端點，不需要金鑰或登入：
    GET https://lottery.timetable.tw/api/draws?gameTypeId=10&limit=500&sortOrder=DESC
"""
import datetime
import json
import os
import sys
import urllib.request

TAIPEI_OFFSET = datetime.timedelta(hours=8)  # 台灣全年不用日光節約時間，固定 UTC+8

API_URL = (
    "https://lottery.timetable.tw/api/draws"
    "?gameTypeId=10&limit=500&sortOrder=DESC"
)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(BASE_DIR, "data", "draws.json")
MAX_KEEP = 8000  # 約可涵蓋最近一個多月的每 5 分鐘開獎，避免檔案無限成長


def fetch_latest():
    req = urllib.request.Request(
        API_URL,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; bingo-analysis-bot/1.0)",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        status = resp.status
        raw_bytes = resp.read()

    raw_text = raw_bytes.decode("utf-8", errors="replace")
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"回應不是合法 JSON（HTTP {status}）：{raw_text[:300]!r}"
        ) from e

    # 相容 API 直接回傳陣列，或包在 {"records": [...]}／{"data": [...]}
    # 等常見包法；如果是其他形狀（例如錯誤訊息物件 {"error": "..."}），
    # 明確報錯，不要誤把它當成資料列（之前的 bug：對字典 fallback 到
    # 自己，結果對字典做 for 迴圈會拿到 key 字串，導致 'str' object
    # has no attribute 'get'）。目前已確認 lottery.timetable.tw 實際
    # 是用 "records" 這個欄位包資料。
    records = None
    if isinstance(payload, list):
        records = payload
    elif isinstance(payload, dict):
        for key in ("records", "data", "draws", "results", "items"):
            if isinstance(payload.get(key), list):
                records = payload[key]
                break
    if records is None:
        raise RuntimeError(
            f"回應格式不是預期的陣列或已知的包裝格式（HTTP {status}）："
            f"{raw_text[:300]!r}"
        )

    # 誠實更正（之前用 created_at 換算時間的做法有 bug）：原本以為 API 的
    # created_at 是「這一期開獎當下」的時間戳記，轉成台灣時間後拆成 date/time。
    # 但實際抓資料驗證後發現，created_at／updated_at 其實是「這批資料被寫進
    # 資料庫的批次同步時間」，同一批（往往連續 10 幾期）會共用完全相同的
    # created_at，導致畫面上一整排不同期別、不同號碼的開獎，顯示出一模一樣
    # 的時間（例如都顯示 23:00）。這裡不再採用 API 的 created_at／draw_date
    # 當作逐期時間來源，改成只留下 period／numbers／superNumber，時間欄位
    # 交給呼叫端用「期別間隔固定 5 分鐘」的方式另外推算（見 _assign_times）。
    out = []
    for r in records:
        if not isinstance(r, dict):
            continue
        nums = r.get("numbers")
        if not isinstance(nums, list) or len(nums) != 20:
            continue
        period = r.get("period")
        if period is None:
            continue
        try:
            period_int = int(period)
        except (TypeError, ValueError):
            continue
        out.append(
            {
                "period": str(period),
                "_period_int": period_int,
                "numbers": nums,
                # 這個公開資料源目前對賓果賓果不提供超級獎號（回傳 null），
                # analysis.py 的驗證邏輯本來就把 superNumber 當作可選欄位處理。
                "superNumber": r.get("special_number"),
            }
        )
    return out


ANCHOR_PATH = os.path.join(BASE_DIR, "data", "time_anchor.json")


def _load_or_create_anchor(all_period_ints, now_utc_naive):
    """
    建立（只建立一次）並讀取一個固定不變的「期別 ↔ 台灣時間」錨點。

    賓果賓果公開規則是每 5 分鐘開一期、期別數字逐期加 1，這是可以信賴的事實；
    但 API 沒有提供可信的逐期時間戳記（created_at 其實是批次同步時間，同一批
    會共用同一個值，詳見上面 fetch_latest 的說明），所以用這個固定錨點 + 5
    分鐘間隔反推每一期的時間，取代不可信的 API 時間欄位。

    錨點只在第一次執行（找不到 time_anchor.json）時建立一次，用當下抓到的
    最大期別 + 現在的台灣時間當基準，之後永久寫死、不再更動。之後每次都用
    同一個錨點對「全部」資料（不管新舊）重新套公式計算 date/time——這樣同一
    期別任何時候算出來的時間都一樣（穩定、不會每次排程亂跳），而且能一次
    修好舊版程式誤用 created_at 算出來的錯誤時間（同一批期別顯示同一個時間
    的問題），不用等資料自然汰換掉。
    """
    if os.path.exists(ANCHOR_PATH):
        with open(ANCHOR_PATH, "r", encoding="utf-8") as f:
            saved = json.load(f)
        return saved["anchor_period"], datetime.datetime.strptime(
            saved["anchor_time"], "%Y-%m-%d %H:%M"
        )
    anchor_period = max(all_period_ints)
    anchor_dt = now_utc_naive + TAIPEI_OFFSET
    os.makedirs(os.path.dirname(ANCHOR_PATH), exist_ok=True)
    with open(ANCHOR_PATH, "w", encoding="utf-8") as f:
        json.dump(
            {"anchor_period": anchor_period, "anchor_time": anchor_dt.strftime("%Y-%m-%d %H:%M")},
            f,
            ensure_ascii=False,
        )
    return anchor_period, anchor_dt


def _apply_time_formula(records, anchor_period, anchor_dt):
    """
    對每一筆資料套用：時間 = 錨點時間 + 5 分鐘 × (這期期別 - 錨點期別)。
    純粹由期別數字決定，不吃任何來自 API 的時間欄位，所以每次重算結果都一樣。

    已知的誤差來源（誠實寫在這裡，不隱藏）：如果賓果賓果在某些時段有暫停
    （例如跨日維護空檔），這個推算在暫停前後那幾期的時間可能會有一些偏差；
    錨點本身也只是「建立當下」抓到的最新一期 vs. 現在時間，跟真正開獎時間
    可能差個幾分鐘的排程延遲。這個時間欄位純粹是給人看的顯示用途，不影響
    下面統計分析引擎的任何計算（分析引擎只用期別順序跟開獎號碼，不吃這個
    時間欄位）。
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
        anchor_period, anchor_dt = _load_or_create_anchor(all_period_ints, now_utc_naive)
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
