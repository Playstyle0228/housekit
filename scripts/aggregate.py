# -*- coding: utf-8 -*-
"""把持久層聚合成前端用的 docs/data.json。

指標定義
  實質單價 = (總價元 - 車位總價元) / ((建物移轉總面積 - 車位移轉總面積) x 0.3025) / 10000  [萬元/坪]
  新成屋   = 交易年月 - 建築完成年月 <= 5 年
  代表值   = 中位數（樣本少時比平均數穩健，不受少數高單價個案拉動），另附 P25/P75 與 n

分區在此階段才由門牌推導，所以調整 zones.py 只要重跑本檔，不必重跑回填。
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch                                                       # noqa: E402
import store                                                       # noqa: E402
from clean import BUILDING_TYPES, SPECIAL_REMARKS, ym_index        # noqa: E402
from zones import DISTRICTS, ZONES, streets_of_zone, zones_of    # noqa: E402

OUT = os.path.join(store.ROOT, "docs", "data.json")

START_YM = (112, 1)      # 統計起點
MIN_N = 3                # 樣本數低於此值標記為不足（仍輸出中位數，由前端加註）
TAICHUNG = "b"

# 尾端期別的資料完整度控制。
# 實價登錄採申報後分旬揭露，最近數期的樣本仍在累積：實測 115/07 全台僅 227 筆、
# 115/08 為 0 筆（正常月份約 1,700 筆）。若照原樣輸出，圖表尾端會出現純屬
# 資料缺漏的假性崩跌。因此以「全台全屋齡樣本數 ÷ 前 N 期樣本數中位數」為覆蓋率，
# 只對尾端連續不足的期別處理（避免誤判農曆年等真實淡季，如 115/02）。
DROP_COVERAGE = 0.35         # 覆蓋率低於此值的尾端期別完全不輸出
PROVISIONAL_COVERAGE = 0.85  # 低於此值者輸出但標記為未定案
COVERAGE_WINDOW = {"month": 12, "quarter": 4}

# 合併樣本移動中位數的視窗期數（月頻為 3 個月、季頻為 3 季）
ROLL_WINDOW = 3
BASES = ("new", "all")

# 序列清單：全國 → 縣市 → 行政區 → 生活圈。順序即前端晶片與表格的呈現順序。
SERIES_META = (
    [{"key": "tw", "label": "全台灣", "kind": "national"},
     {"key": "taichung", "label": "台中市", "kind": "city"}]
    + [{"key": d["key"], "label": d["label"], "kind": "district"} for d in DISTRICTS.values()]
    + [{"key": k, "label": z["label"], "kind": "zone"} for k, z in ZONES.items()]
)
SERIES = [s["key"] for s in SERIES_META]

# 預設勾選：全國、縣市與各行政區全區，生活圈由使用者自行開啟
DEFAULT_SERIES = ["tw", "taichung"] + [d["key"] for d in DISTRICTS.values()]


def _median(v):
    v = sorted(v)
    n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2.0


def _pct(v, q):
    v = sorted(v)
    i = min(len(v) - 1, max(0, int(round(q * (len(v) - 1)))))
    return v[i]


def series_keys(r):
    ks = ["tw"]
    if r["cc"] == TAICHUNG:
        ks.append("taichung")
        ks.extend(zones_of(r["dist"], r["addr"]))
    return ks


def bucketize(rows):
    months, quarters = {}, {}
    for r in rows:
        y, m = int(r["tx_ym"][:3]), int(r["tx_ym"][4:])
        if ym_index((y, m)) < ym_index(START_YM):
            continue
        qk = "%03d/Q%d" % (y, (m - 1) // 3 + 1)
        keys = series_keys(r)
        for bucket, label in ((months, r["tx_ym"]), (quarters, qk)):
            for sk in keys:
                for base in BASES:
                    if base == "new" and r["age"] > 5:
                        continue
                    (bucket.setdefault(label, {}).setdefault(sk, {})
                           .setdefault(base, []).append(r["unit"]))
    return months, quarters


def trailing_completeness(bucket, labels, window):
    """依全台樣本數覆蓋率判定尾端期別的完整度。

    回傳 (保留的期別數, 未定案期別清單)。只檢查尾端連續不足的期別。
    """
    ns = [len(bucket.get(lb, {}).get("tw", {}).get("all", [])) for lb in labels]
    cov = []
    for i, n in enumerate(ns):
        prior = ns[max(0, i - window):i]
        ref = _median(prior) if prior else n
        cov.append(n / ref if ref else 0.0)

    i = len(ns) - 1
    keep = len(ns)
    while i >= 0 and cov[i] < DROP_COVERAGE:
        keep = i
        i -= 1
    prov = []
    while i >= 0 and cov[i] < PROVISIONAL_COVERAGE:
        prov.append(labels[i])
        i -= 1
    return keep, sorted(prov)


def emit(bucket, labels):
    """輸出各序列的單期中位數，以及 ROLL_WINDOW 期合併樣本中位數。

    合併樣本中位數是把視窗內各期的「交易明細」倒在一起後取中位數，
    而非對各期中位數再取平均。低樣本分區（如明道商圈每季僅 5～16 筆）
    的單期中位數鋸齒劇烈，合併後才有可讀的趨勢，且不會憑空造出資料點。

    樣本數不足（0 < n < MIN_N）時仍輸出中位數，另以 low / roll_low 標記，
    由前端加註；只有 n = 0 才輸出 null。單筆成交的「中位數」就是那一筆本身、
    統計意義有限，但它仍是真實成交價，不予隱藏。
    """
    out = {}
    for sk in SERIES:
        out[sk] = {}
        for base in BASES:
            med, cnt, low, p25, p75 = [], [], [], [], []
            roll, roll_n, roll_low = [], [], []
            for i, lb in enumerate(labels):
                v = bucket.get(lb, {}).get(sk, {}).get(base, [])
                cnt.append(len(v))
                low.append(0 < len(v) < MIN_N)
                if v:
                    med.append(round(_median(v), 2))
                    p25.append(round(_pct(v, 0.25), 2))
                    p75.append(round(_pct(v, 0.75), 2))
                else:
                    med.append(None); p25.append(None); p75.append(None)

                pool = []
                for lb2 in labels[max(0, i - ROLL_WINDOW + 1):i + 1]:
                    pool.extend(bucket.get(lb2, {}).get(sk, {}).get(base, []))
                roll_n.append(len(pool))
                roll_low.append(0 < len(pool) < MIN_N)
                roll.append(round(_median(pool), 2) if pool else None)

            out[sk][base] = {"median": med, "n": cnt, "low": low,
                             "p25": p25, "p75": p75,
                             "roll": roll, "roll_n": roll_n, "roll_low": roll_low}
    return out


def main():
    rows, _ = store.load_records()
    if not rows:
        raise SystemExit("持久層為空，請先執行：python scripts/ingest.py --backfill")
    state = store.load_state()
    seasons = sorted({s.split(":")[1] for s in state["ingested"] if s.startswith("season")}, reverse=True)
    periods = sorted({s.split(":")[1] for s in state["ingested"] if s.startswith("history")}, reverse=True)
    newest_reg = max(
        [fetch.season_reg_start(c) for c in seasons] +
        [fetch.period_reg_start(c) for c in periods],
        key=ym_index)

    months, quarters = bucketize(rows)
    mlabels = sorted(months, key=lambda s: (int(s[:3]), int(s[4:])))
    qlabels = sorted(quarters, key=lambda s: (int(s[:3]), int(s[5:])))

    mkeep, mprov = trailing_completeness(months, mlabels, COVERAGE_WINDOW["month"])
    qkeep, qprov = trailing_completeness(quarters, qlabels, COVERAGE_WINDOW["quarter"])
    dropped = mlabels[mkeep:] + qlabels[qkeep:]
    mlabels, qlabels = mlabels[:mkeep], qlabels[:qkeep]
    prov = mprov + qprov

    tz = timezone(timedelta(hours=8))
    data = {
        "meta": {
            "generated_at": datetime.now(tz).strftime("%Y-%m-%d %H:%M %z"),
            "source": "內政部不動產成交案件實際資訊資料供應系統 (plvr.land.moi.gov.tw)",
            "source_kind": "不動產買賣（成屋）A 檔",
            "seasons": seasons,
            "periods": periods,
            "newest_reg_start": "%03d/%02d" % newest_reg,
            "total_records": len(rows),
            "min_n": MIN_N,
            "roll_window": ROLL_WINDOW,
            "provisional_months": prov,
            "dropped_periods": dropped,
            "definition": {
                "unit_price": "(總價元−車位總價元) ÷ ((建物移轉總面積−車位移轉總面積) × 0.3025) ÷ 10000　萬元/坪",
                "new_build": "屋齡 = 交易年月 − 建築完成年月 ≤ 5 年",
                "statistic": ("中位數（另附 P25／P75、樣本數 n，以及 %d 期合併樣本移動中位數）"
                              % ROLL_WINDOW),
                "building_types": BUILDING_TYPES,
                "excluded_remarks": SPECIAL_REMARKS,
                "max_registration_lag_months": 6,
            },
            "series": SERIES_META,
            "default_series": DEFAULT_SERIES,
            "zones": {
                k: {"label": v["label"], "dist": v["dist"], "li": v["li"],
                    "basis": v["basis"], "streets": streets_of_zone(k)}
                for k, v in ZONES.items()
            },
        },
        "month": {"labels": mlabels, "data": emit(months, mlabels)},
        "quarter": {"labels": qlabels, "data": emit(quarters, qlabels)},
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)

    # generated_at 每次都會變，若照寫則 CI 每次執行都產生一個只有時間戳不同的
    # commit。內容（排除 generated_at）與現有檔一致時就不動檔案，讓 CI 的
    # git diff 判斷得以正確略過。
    if os.path.exists(OUT):
        try:
            with open(OUT, encoding="utf-8") as f:
                old = json.load(f)
            a, b = json.loads(json.dumps(data)), old
            a["meta"].pop("generated_at", None)
            b["meta"].pop("generated_at", None)
            if a == b:
                print("聚合結果與現有 %s 一致，未變更檔案（原始紀錄 %d 筆）"
                      % (OUT, len(rows)), flush=True)
                return
        except (ValueError, KeyError):
            pass        # 現有檔損壞或格式不符，直接覆寫

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print("寫出 %s (%.1f KB)｜原始紀錄 %d 筆｜月 %d 期｜季 %d 期｜未定案 %s"
          % (OUT, os.path.getsize(OUT) / 1024, len(rows), len(mlabels), len(qlabels), prov), flush=True)


if __name__ == "__main__":
    main()
