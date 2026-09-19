#!/usr/bin/env python3
"""
讀取 data/draws.json，呼叫 analysis.py 的統計引擎重新計算，
把結果連同更新時間等中繼資料寫成 data/stats.json（給 index.html 讀取的靜態檔）。
"""
import datetime
import json
import os

from analysis import run_all, validate_draws, recent_suggestion_hits

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DRAWS_PATH = os.path.join(BASE_DIR, "data", "draws.json")
STATS_PATH = os.path.join(BASE_DIR, "data", "stats.json")
SOURCE_URL = "https://lottery.timetable.tw/api/draws?gameTypeId=10 (台灣彩券公開開獎資料)"
RECENT_DRAWS_LIMIT = 100  # 開獎歷史列表最多顯示幾期（避免頁面隨資料量無限變大）


def main():
    with open(DRAWS_PATH, "r", encoding="utf-8") as f:
        raw = json.load(f)

    out = run_all(raw)
    out["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    out["source"] = SOURCE_URL
    dates = sorted({d["date"] for d in raw if d.get("date")})
    out["data_since"] = dates[0] if dates else None

    # 給前端「開獎歷史紀錄」列表用：最近 N 期，新到舊排序。
    valid, _rejected = validate_draws(raw)
    recent = valid[-RECENT_DRAWS_LIMIT:]

    # 每一期「當時」的綜合建議號碼跟該期實際開獎比對，命中的號碼會在歷史列表上
    # 標示出來（只用「該期之前」的歷史資料算建議，不是用未來資料回推，誠實對照）。
    hits_by_period = recent_suggestion_hits(valid, recent_n=RECENT_DRAWS_LIMIT)
    for d in recent:
        d["suggested_hits"] = hits_by_period.get(d["period"], [])

    out["recent_draws"] = list(reversed(recent))

    with open(STATS_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)

    print(f"統計完成，寫入 {STATS_PATH}（歷史 {len(raw)} 期，updated_at={out['updated_at']}）")


if __name__ == "__main__":
    main()
