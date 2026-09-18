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
import json
import os
import sys
import urllib.request

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

    # 相容 API 直接回傳陣列，或包在 {"data": [...]} 裡兩種格式；
    # 如果是其他形狀（例如錯誤訊息物件 {"error": "..."}），明確報錯，
    # 不要誤把它當成資料列（之前的 bug：對字典 fallback 到自己，
    # 結果對字典做 for 迴圈會拿到 key 字串，導致 'str' object has no
    # attribute 'get'）。
    if isinstance(payload, list):
        records = payload
    elif isinstance(payload, dict) and isinstance(payload.get("data"), list):
        records = payload["data"]
    else:
        raise RuntimeError(
            f"回應格式不是預期的陣列或 {{'data': [...]}}（HTTP {status}）："
            f"{raw_text[:300]!r}"
        )

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
        created_at = r.get("created_at") or ""
        out.append(
            {
                "period": str(period),
                "date": r.get("draw_date"),
                "time": created_at[11:16] if len(created_at) >= 16 else "",
                "numbers": nums,
                # 這個公開資料源目前對賓果賓果不提供超級獎號（回傳 null），
                # analysis.py 的驗證邏輯本來就把 superNumber 當作可選欄位處理。
                "superNumber": r.get("special_number"),
            }
        )
    return out


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
        by_period[d["period"]] = d  # 同一期以最新抓到的內容為準

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
