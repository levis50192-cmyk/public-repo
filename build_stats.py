#!/usr/bin/env python3
"""
讀取 data/draws.json，呼叫 analysis.py 的統計引擎重新計算，
把結果連同更新時間等中繼資料寫成 data/stats.json（給 index.html 讀取的靜態檔）。
"""
import datetime
import json
import os

from analysis import run_all

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DRAWS_PATH = os.path.join(BASE_DIR, "data", "draws.json")
STATS_PATH = os.path.join(BASE_DIR, "data", "stats.json")
SOURCE_URL = "https://lottery.timetable.tw/api/draws?gameTypeId=10 (台灣彩券公開開獎資料)"


def main():
    with open(DRAWS_PATH, "r", encoding="utf-8") as f:
        raw = json.load(f)

    out = run_all(raw)
    out["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    out["source"] = SOURCE_URL
    dates = sorted({d["date"] for d in raw if d.get("date")})
    out["data_since"] = dates[0] if dates else None

    with open(STATS_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)

    print(f"統計完成，寫入 {STATS_PATH}（歷史 {len(raw)} 期，updated_at={out['updated_at']}）")


if __name__ == "__main__":
    main()
