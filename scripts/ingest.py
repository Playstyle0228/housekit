# -*- coding: utf-8 -*-
"""下載並清洗新來源，寫入持久層。

  python scripts/ingest.py --backfill        回填 111S4 起所有季檔（一次性，約 1.8 GB）
  python scripts/ingest.py                   只抓尚未收錄的旬檔（CI 每月 1/11/21 用，約 14 MB）
  python scripts/ingest.py --keep-zip        保留 .cache 內的 zip（預設下載後即刪，省磁碟）
"""
import argparse
import csv
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch                                   # noqa: E402
import store                                   # noqa: E402
from clean import clean_row                    # noqa: E402
from zones import zones_of                     # noqa: E402

CACHE = os.path.join(store.ROOT, ".cache")
BACKFILL_FROM = "111S4"     # 涵蓋交易月 112/01 起所有登記案件
TAICHUNG = "b"


def wanted_seasons():
    all_s = sorted(fetch.list_seasons())
    return [s for s in all_s if s >= BACKFILL_FROM]


def ingest_source(kind, code, rows_out, seen):
    reg = (fetch.season_reg_start(code) if kind == "season"
           else fetch.period_reg_start(code))
    path = fetch.fetch_zip(kind, code, CACHE)
    kept = dup = 0
    for cc, text in fetch.read_a_csv_from_zip(path):
        rows = list(csv.DictReader(io.StringIO(text)))
        for row in rows[1:]:                   # 第 2 列為英文欄名
            rid = row.get("編號", "")
            if not rid:
                continue
            if rid in seen:
                dup += 1
                continue
            rec = clean_row(row, reg)
            if rec is None:
                continue
            dist = row.get("鄉鎮市區", "")
            rec.update(cc=cc, dist=dist, src="%s:%s" % (kind, code))
            if cc != TAICHUNG:
                rec["addr"] = ""               # 非台中不需門牌，省空間
            seen.add(rid)
            rows_out.append(rec)
            kept += 1
    return kept, dup, path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", action="store_true", help="連季檔一起收錄")
    ap.add_argument("--keep-zip", action="store_true")
    args = ap.parse_args()

    state = store.load_state()
    done = set(state["ingested"])
    rows, seen = store.load_records()
    print("持久層現有 %d 筆，已收錄 %d 個來源" % (len(rows), len(done)), flush=True)

    todo = []
    if args.backfill:
        todo += [("season", s) for s in wanted_seasons()]
    todo += [("history", p) for p in fetch.list_periods()]
    todo = [(k, c) for k, c in todo if "%s:%s" % (k, c) not in done]
    if not todo:
        print("無新來源，持久層已是最新。")
        return

    for kind, code in todo:
        kept, dup, path = ingest_source(kind, code, rows, seen)
        done.add("%s:%s" % (kind, code))
        print("  %-8s %-8s 新增 %6d 筆（重複 %6d）" % (kind, code, kept, dup), flush=True)
        if not args.keep_zip:
            os.remove(path)

    n = store.save_records(rows)
    state["ingested"] = sorted(done)
    store.save_state(state)
    print("持久層寫出 %d 筆 -> %s (%.1f MB)"
          % (n, store.RECORDS, os.path.getsize(store.RECORDS) / 1e6), flush=True)


if __name__ == "__main__":
    main()
