# -*- coding: utf-8 -*-
"""清洗後紀錄的持久層：data/records.csv.gz + data/state.json。

為什麼要有這一層：
  - 旬檔只保留最近 6 旬，過期就再也拿不到，必須自己存下來。
  - 季檔每份 ~113 MB，CI 每次重下 1.8 GB 不現實；持久層讓 CI 只需下載新增的旬檔。
  - 保留 addr（僅台中市）而非只存分區代碼，使日後調整商圈範圍不需重跑回填。
"""
import csv
import gzip
import io
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
RECORDS = os.path.join(DATA, "records.csv.gz")
STATE = os.path.join(DATA, "state.json")

FIELDS = ["id", "cc", "dist", "tx_ym", "age", "unit", "ping", "btype", "lag", "addr", "src"]


def load_state():
    if os.path.exists(STATE):
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    return {"ingested": []}


def save_state(state):
    os.makedirs(DATA, exist_ok=True)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)


def load_records():
    """回傳 (list[dict], set[id])。"""
    if not os.path.exists(RECORDS):
        return [], set()
    with gzip.open(RECORDS, "rt", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["age"] = float(r["age"])
        r["unit"] = float(r["unit"])
        r["ping"] = float(r["ping"])
        r["lag"] = int(r["lag"])
    return rows, {r["id"] for r in rows}


def save_records(rows):
    """依 (交易月, 編號) 排序後寫出，讓 git diff 穩定、可讀。"""
    os.makedirs(DATA, exist_ok=True)
    rows = sorted(rows, key=lambda r: (r["tx_ym"], r["id"]))
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=FIELDS, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    tmp = RECORDS + ".part"
    with gzip.open(tmp, "wt", encoding="utf-8", newline="", compresslevel=9) as f:
        f.write(buf.getvalue())
    os.replace(tmp, RECORDS)
    return len(rows)
