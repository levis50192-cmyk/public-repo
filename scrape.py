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


def _assign_times(new_records, now_utc, prev_anchor=None):
    """
    幫這次新抓到的期別推算「日期／時間」顯示欄位。

    做法：賓果賓果公開規則是每 5 分鐘開一期，期別數字逐期加 1；用「現在
    （UTC 轉台灣時間）」當作這批資料裡最新一期的時間錨點，其餘每往前推
    一期就往回推 5 分鐘。這是在 API 沒有提供可信逐期時間戳記的情況下，
    唯一能重建出「每期時間確實間隔 5 分鐘」這個已知事實的方法，比直接
    採用 API 的批次同步時間（created_at）準確很多。

    prev_anchor（選填）是「目前已經記錄過、期別最大的那筆資料」的
    (period_int, 已存的 datetime)；如果有給，會確保新算出來的時間至少
    比它晚（用同樣的 5 分鐘間隔往後推），避免「現在時間」剛好比理論值
    還早（例如排程間隔比較密、或系統時鐘有一點誤差）時，新期別的時間
    反而比前一期還早，出現時間倒退的怪現象。

    已知的誤差來源（誠實寫在這裡，不隱藏）：如果賓果賓果在某些時段有暫停
    （例如跨日維護空檔），這個推算在暫停前後那幾期的時間可能會有一些偏差；
    另外「現在」跟「最新一期真正開獎的時間」中間也會有排程延遲的幾分鐘
    誤差。這個時間欄位純粹是給人看的顯示用途，不影響下面統計分析引擎的
    任何計算（分析引擎只用期別順序跟開獎號碼，不吃這個時間欄位）。
    """
    if not new_records:
        return
    anchor_period = max(d["_period_int"] for d in new_records)
    anchor_dt = now_utc + TAIPEI_OFFSET
    if prev_anchor is not None:
        prev_period, prev_dt = prev_anchor
        floor_dt = prev_dt + datetime.timedelta(minutes=5 * (anchor_period - prev_period))
        if floor_dt > anchor_dt:
            anchor_dt = floor_dt
    for d in new_records:
        offset_periods = anchor_period - d["_period_int"]
        dt = anchor_dt - datetime.timedelta(minutes=5 * offset_periods)
        d["date"] = dt.strftime("%Y-%m-%d")
        d["time"] = dt.strftime("%H:%M")
        d.pop("_period_int", None)


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

    # 只針對「這次才第一次看到」的期別推算時間；已經記錄過的期別維持原本算過
    # 的日期／時間不變——避免同一期在還沒過期、被重複抓到時，因為每次錨點
    # （現在時間）不同，導致時間欄位每次排程都跳來跳去。
    new_records = [d for d in fresh if d["period"] not in by_period]
    new_periods = {d["period"] for d in new_records}

    prev_anchor = None
    if by_period:
        last = max(by_period.values(), key=lambda d: int(d["period"]))
        if last.get("date") and last.get("time"):
            try:
                prev_dt = datetime.datetime.strptime(
                    f"{last['date']} {last['time']}", "%Y-%m-%d %H:%M"
                )
                prev_anchor = (int(last["period"]), prev_dt)
            except ValueError:
                pass

    now_utc_naive = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    _assign_times(new_records, now_utc_naive, prev_anchor=prev_anchor)
    added = len(new_records)

    for d in new_records:
        by_period[d["period"]] = d
    for d in fresh:
        # 期別已經存在過：只更新號碼／超級獎號（萬一上游資料有訂正），保留
        # 原本算過的 date/time，不要用新的批次重新覆蓋。
        if d["period"] not in new_periods and d["period"] in by_period:
            existing_rec = by_period[d["period"]]
            existing_rec["numbers"] = d["numbers"]
            existing_rec["superNumber"] = d["superNumber"]

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
