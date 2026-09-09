# -*- coding: utf-8 -*-
"""把持久層裡某個行政區出現過的路街名逐條地理編碼，取得里別。

  python scripts/geocode.py 烏日區 南屯區

結果累積寫入 data/street_li.json，供 zones.py 建立「路街名 → 里」對照。
這是一次性離線作業：zones.py 只讀 JSON，執行期與 CI 都不呼叫外部服務。

為什麼要逐號定位
----------------
只查路名時 Nominatim 回傳它匹配到的某一段路，長路會落在不同里而結果不穩定：
實測「烏日區公園路」兩次查詢分別回湖日里與烏日里。

改取三個門牌樣本後多數決仍不夠：烏日 6 條／282 筆、南屯 55 條／3,496 筆
橫跨多里，而多數決會在關鍵處判錯——新興路三點回烏日里／湖日里／湖日里，
多數決把它放進高鐵特區，但它其實屬舊市區；新富路也因此被踢出單元二重劃區。

因此本檔逐一查詢資料中出現過的**每個**門牌號，把 號 → 里 存進 nums，
由 zones.py 逐筆解析。街道層級的多數決仍保留在 li 欄位，作為未見過門牌的退路。

一次性離線作業，且可增量執行（已查過的門牌會跳過）。
用 OpenStreetMap Nominatim，依其使用政策限制每秒 1 次請求。
"""
import argparse
import collections
import json
import os
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import store                                                # noqa: E402

OUT = os.path.join(store.DATA, "street_li.json")
UA = "HouseKit/1.0 (https://github.com/Playstyle0228/housekit)"
_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE

# 門牌字串中「<行政區>」之後、第一個號碼之前的字樣即路街名（含「N段」）
_ST = r"(.{2,10}?(?:[一二三四五六七八九十]+段)?)(?=[0-9０-９])"


_FW = "０１２３４５６７８９"


def _half(s):
    for i, c in enumerate(_FW):
        s = s.replace(c, str(i))
    return s


def streets_in(rows, dist):
    """回傳 {路街名: [門牌號, ...]}，號碼已轉半角並去重排序。"""
    out = collections.defaultdict(list)
    for r in rows:
        if r["dist"] != dist or not r["addr"]:
            continue
        m = re.search(re.escape(dist) + _ST, r["addr"])
        if not m:
            continue
        st = m.group(1)
        tail = r["addr"].split(dist, 1)[1][len(st):]
        num = re.match(r"[0-9０-９]+", tail)
        out[st].append(int(_half(num.group(0))) if num else None)
    return {s: sorted(set(n for n in v if n)) for s, v in out.items()}, \
           {s: len(v) for s, v in out.items()}


def majority(votes):
    tally = collections.Counter(v for v in votes if v)
    return tally.most_common(1)[0][0] if tally else ""


def geocode(dist, street, num=None):
    """查台中市某行政區某路街（可帶門牌號）的里別。

    必須用 Nominatim 的結構化查詢，且縣市要寫「臺中市」：實測把號碼塞進自由
    文字（q=台中市烏日區公園路73號）一律回 0 筆，只有
    street="公園路 73" + city="烏日區" + county="臺中市" 才查得到門牌層級。
    """
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode({
        "street": ("%s %d" % (street, num)) if num else street,
        "city": dist, "county": "臺中市",
        "format": "json", "limit": 1,
        "countrycodes": "tw", "addressdetails": 1,
    })
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        d = json.loads(urllib.request.urlopen(req, timeout=25, context=_CTX).read())
    except Exception:                                        # noqa: BLE001
        return None
    if not d:
        return None
    a = d[0].get("address", {})
    li = a.get("quarter") or a.get("neighbourhood") or ""
    return {
        "li": li,
        "area": a.get("suburb") or a.get("city_district") or "",
        "lat": float(d[0]["lat"]),
        "lon": float(d[0]["lon"]),
    } if li else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dists", nargs="+", help="行政區名，如 烏日區 南屯區")
    ap.add_argument("--redo", action="store_true", help="連已有結果一併重查")
    args = ap.parse_args()

    table = {}
    if os.path.exists(OUT):
        with open(OUT, encoding="utf-8") as f:
            table = json.load(f)

    rows, _ = store.load_records()
    for dist in args.dists:
        nums, counts = streets_in(rows, dist)
        table.setdefault(dist, {})
        todo = [s for s in nums
                if args.redo or any(str(n) not in (table[dist].get(s, {}).get("nums") or {})
                                    for n in nums[s])]
        total = sum(counts.values())
        print("%s：資料中 %d 條路街 / %d 筆，待查 %d 條"
              % (dist, len(nums), total, len(todo)), flush=True)

        for i, st in enumerate(todo, 1):
            rec = table[dist].get(st) or {}
            known = dict(rec.get("nums") or {})
            pend = [n for n in nums[st] if str(n) not in known]
            for num in pend:
                g = geocode(dist, st, num)
                known[str(num)] = (g or {}).get("li", "")
                time.sleep(1.15)
            if not any(known.values()):        # 全部門牌查不到就退回只查路名
                g = geocode(dist, st)
                time.sleep(1.15)
                if g:
                    known.setdefault("_street", g["li"])
            lis = [v for k, v in known.items() if v]
            table[dist][st] = {
                "li": majority(lis), "nums": known,
                "split": len(set(lis)) > 1, "n": counts[st],
            }
            print("  [%3d/%3d] %-14s n=%-4d %d/%d 號有里別  %-10s %s"
                  % (i, len(todo), st, counts[st], len(lis), len(known),
                     table[dist][st]["li"] or "查無",
                     ("← 橫跨 " + "/".join(sorted(set(lis)))) if len(set(lis)) > 1 else ""),
                  flush=True)

        got = [v for v in table[dist].values() if v.get("li")]
        split = [v for v in table[dist].values() if v.get("split")]
        print("%s：%d/%d 條有里別，涵蓋 %d/%d 筆 (%.1f%%)；橫跨多里 %d 條 / %d 筆"
              % (dist, len(got), len(table[dist]), sum(v["n"] for v in got), total,
                 100 * sum(v["n"] for v in got) / max(1, total),
                 len(split), sum(v["n"] for v in split)), flush=True)

    os.makedirs(store.DATA, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(table, f, ensure_ascii=False, indent=1, sort_keys=True)
    print("寫出 %s" % OUT, flush=True)


if __name__ == "__main__":
    main()
