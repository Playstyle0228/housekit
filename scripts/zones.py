# -*- coding: utf-8 -*-
"""行政區與生活圈分區：以「路街名 → 里 → 生活圈」兩段式對照。

為什麼用里而不是憑印象列白名單
--------------------------------
實價登錄成屋檔(A)沒有社區名稱或里別欄位（只有預售屋 B 檔才有「建案名稱」），
只有門牌字串，所以分區只能靠路街名比對。但路街名要對到哪個生活圈不能憑印象。

里別由 scripts/geocode.py 逐一查詢資料中出現過的每個門牌號取得，結果存在
data/street_li.json。那是一次性離線作業，本檔只讀 JSON，執行期與 CI 都不呼叫
外部服務。

只用路名比對不夠：長路會橫跨數個里（烏日 6 條／282 筆、南屯 55 條／3,496 筆），
街道層級的多數決會在關鍵處判錯——新興路多數決落在湖日里而被歸入高鐵特區，
實際屬舊市區。因此 li_of() 以門牌號逐筆解析，街道多數決僅作未見過門牌的退路。

要新增行政區：

    python scripts/geocode.py 西屯區        # 查該區路街的里別
    # 在 DISTRICTS 加一列，需要生活圈再到 ZONES 加，然後
    python scripts/aggregate.py

持久層保留台中全市門牌，所以新增分區不必重跑回填。

分區依據
--------
沿用樂居的生活圈劃分，並以公開的都市計畫範圍對照到里別。各生活圈的依據寫在
ZONES 的 basis 欄位，會一併輸出到 data.json 供頁面顯示。
"""
import collections
import json
import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
_LI_PATH = os.path.join(_HERE, "..", "data", "street_li.json")

# 地理編碼查無里別、但歸屬明確可考的路街，在此人工補上並記錄依據。
# 只補空缺：street_li.json 有實測值時一律以實測值為準，人工表不覆蓋它。
FALLBACK = {
    "烏日區": {
        # 「三榮」即三和里與榮泉里之合稱，為高鐵特區重劃後系統命名的路網，
        # 兩里同屬高鐵特區，故此處記為三和里不影響任何輸出。
        # 這些新路早期在 OSM 查不到，改用門牌層級查詢後多數已能實測到里別，
        # 本表僅作為仍查無者的補位。
        "三榮路一段": "三和里", "三榮路二段": "三和里", "三榮路三段": "三和里",
        "三榮北路": "三和里", "三榮北一路": "三和里",
        "三榮一路": "三和里", "三榮二路": "三和里", "三榮三路": "三和里",
        "三榮五路": "三和里", "三榮六路": "三和里", "三榮七路": "三和里",
        "三榮十二路": "三和里", "三榮十五路": "三和里", "三榮十八路": "三和里",
        "五光路": "五光里",          # 不屬任何生活圈，僅計入烏日全區
    },
    # 南屯區不做補位。曾憑字面把「三和街」對到三和里，門牌層級查詢實測為
    # 南屯里——路名與里名相近並不代表同屬，此類推測一律不採用。
}

# dist -> street -> {"li": 街道層級多數決, "nums": {門牌號: 里}}
# 由 geocode.py 產生；缺檔時分區退化為只有行政區層級。
try:
    with open(_LI_PATH, encoding="utf-8") as _f:
        _RAW = json.load(_f)
except (OSError, ValueError):
    _RAW = {}

SMOOTH_WINDOW = 2      # 平滑時各方向納入的鄰近門牌數


def _smooth(nums):
    """以鄰近門牌的眾數修掉孤立錯值。

    Nominatim 對同一門牌的回應並非完全穩定：實測「中山路一段 2 號」先回前竹里、
    再查回九德里（實際距明道中學 974 公尺，前竹里在烏日南端，明顯是誤值）。
    這類錯誤的特徵是「孤立單點」，而真實的里界是沿門牌號的連續區段——例如
    長春街 373 號回烏日里，但其前後 136～541 號全是九德里；環河路四段 300 號
    回湖日里，但其前的 268～278 號是三和里。

    因此按號排序後取自身與前後各 SMOOTH_WINDOW 個門牌的眾數。連續區段會被保留
    （健行路 781/785 三和里 → 798~810 榮泉里 的轉折不受影響），孤立跳動會被抹平。
    平票時保留原值，原值不在眾數內則取號碼最接近的眾數。
    """
    ks = sorted(nums)
    out = {}
    for i, k in enumerate(ks):
        win = ks[max(0, i - SMOOTH_WINDOW):i + SMOOTH_WINDOW + 1]
        tally = collections.Counter(nums[w] for w in win)
        top = tally.most_common(1)[0][1]
        modes = [li for li, c in tally.items() if c == top]
        if nums[k] in modes:
            out[k] = nums[k]
        else:
            out[k] = min(((abs(w - k), nums[w]) for w in win if nums[w] in modes))[1]
    return out


# 逐號對照表：(行政區, 路街) -> {號: 里}，只留查得到里別的號
ADDR_LI = {}
# 街道層級多數決，作為未見過門牌的退路
STREET_LI = {}
for _d, _t in _RAW.items():
    STREET_LI[_d] = {}
    for _s, _v in _t.items():
        _n = {int(k): li for k, li in (_v.get("nums") or {}).items()
              if li and k.isdigit()}
        if _n:
            _n = _smooth(_n)
            ADDR_LI[(_d, _s)] = _n
            # 街道層級退路改用平滑後的眾數，與逐號結果一致
            STREET_LI[_d][_s] = collections.Counter(_n.values()).most_common(1)[0][0]
        elif _v.get("li"):
            STREET_LI[_d][_s] = _v["li"]

for _d, _tbl in FALLBACK.items():
    _cur = STREET_LI.setdefault(_d, {})
    for _s, _li in _tbl.items():
        _cur.setdefault(_s, _li)          # 只補空缺，不覆蓋實測值

_FW = "０１２３４５６７８９"


# ── 行政區層級序列 ──
DISTRICTS = {
    "烏日區": {"key": "d_wuri",   "label": "烏日全區"},
    "南屯區": {"key": "d_nantun", "label": "南屯全區"},
}

# ── 生活圈層級序列 ──
ZONES = {
    "z_wuri_hsr": {
        "label": "烏日高鐵特區", "dist": "烏日區",
        "li": ["三和里", "榮泉里", "學田里", "湖日里"],
        "basis": "都市計畫範圍：北以中山路三段延伸至高鐵路一至三段、東以高鐵東路、"
                 "南以環河路四段五段、西臨長壽路為界",
    },
    "z_wuri_mingdao": {
        "label": "明道花園", "dist": "烏日區",
        "li": ["九德里", "仁德里"],
        "basis": "中山路一段與興祥街交叉口為中心、半徑約 700 公尺（舊地名「楓樹腳」）",
    },
    "z_wuri_station": {
        "label": "烏日車站", "dist": "烏日區",
        "li": ["烏日里", "東園里"],
        "basis": "舊市區，以中山路二段、新興路、光日路為主",
    },
    "z_wuri_xinan": {
        "label": "烏日溪南", "dist": "烏日區",
        "li": ["溪埧里"],
        "basis": "烏日南側，鄰霧峰、大里",
    },
    "z_nantun_fengshu": {
        "label": "南屯楓樹", "dist": "南屯區",
        "li": ["楓樹里"],
        "basis": "樂居楓樹生活圈；位於烏日市區旁，以楓樹綠園道為核心的單純住宅區",
    },
}

# 為什麼沒有「南屯單元二」
# --------------------
# OpenStreetMap 在南屯部分門牌的 quarter 欄位放的是重劃區名（單元二自辦重劃
# 黎明重劃區）而非里名，一度看來可直接當作區界。但實測標籤極不一致：新富路
# 7 個門牌只有 3 個帶此標籤、其餘 4 個是新生里；逐號解析後全區僅 14 筆帶標籤
# （街道層級多數決時曾誤放大到 263 筆）。標籤覆蓋率過低且不成區段，不足以
# 界定範圍，故不建立此序列。

# 為什麼沒有「十三期」
# ------------------
# 十三期市地重劃區只涵蓋豐樂里、楓樹里、樹德里、崇倫里、鎮平里各里的「部分」
# 區域，且跨南屯區與南區，無法以里別界定。實測以其四條界線路（東起西川一路、
# 西至環中路及南屯溪、南至建國北路、北抵文心南七路）的外接範圍比對：
#
#     豐樂里  框內  25 筆 / 框外 293 筆   命中率  8%
#     楓樹里  框內  39 筆 / 框外  67 筆   命中率 37%
#     鎮平里  框內  57 筆 / 框外  35 筆   命中率 62%
#
# 若以豐樂里代表十三期，會納入 293 筆實際位於文心南路、豐樂重劃區一帶的成交。
# 外接矩形本身也過於粗略——十三期並非矩形，且其東南角落在南區崇倫里。
# 要正確切分需引入內政部市地重劃區範圍圖做點在多邊形判定，尚未實作。

_STREETS = {d: sorted(t, key=len, reverse=True) for d, t in STREET_LI.items()}
_LI_ZONE = {}
for _k, _z in ZONES.items():
    for _li in _z["li"]:
        _LI_ZONE[(_z["dist"], _li)] = _k


def _half(s):
    for i, c in enumerate(_FW):
        s = s.replace(c, str(i))
    return s


def li_of(dist, addr):
    """由門牌字串推出里別，查不到回 None。

    解析順序：
      1. 該門牌號有實測里別 → 直接用
      2. 同一條路上最接近的已知門牌號 → 用它的里別
         （台灣的里界沿門牌號連續區間劃分，就近取值是合理近似）
      3. 該路的街道層級多數決
    """
    table = STREET_LI.get(dist)
    if not table or dist not in addr:
        return None
    tail = addr.split(dist, 1)[1]
    for s in _STREETS[dist]:
        if not tail.startswith(s):
            continue
        nums = ADDR_LI.get((dist, s))
        if nums:
            m = re.match(r"[0-9０-９]+", tail[len(s):])
            if m:
                num = int(_half(m.group(0)))
                if num in nums:
                    return nums[num]
                return nums[min(nums, key=lambda k: abs(k - num))]
        return table.get(s)
    return None


def zones_of(dist, addr):
    """回傳該筆成交歸屬的序列代碼清單（行政區層級 + 生活圈層級）。

    路街名未收錄、或其里別不屬任何生活圈時，只計入行政區層級。
    """
    d = DISTRICTS.get(dist)
    if not d:
        return []
    hit = [d["key"]]
    li = li_of(dist, addr)
    if li:
        z = _LI_ZONE.get((dist, li))
        if z:
            hit.append(z)
    return hit


def streets_of_zone(code):
    """該生活圈涵蓋的路街名（用於頁面說明）。

    逐號解析後同一條路可能橫跨生活圈界線，僅部分門牌落在區內者標註「部分」。
    """
    z = ZONES[code]
    dist, want = z["dist"], set(z["li"])
    out = []
    for s, li in STREET_LI.get(dist, {}).items():
        nums = ADDR_LI.get((dist, s))
        if nums:
            inside = sum(1 for v in nums.values() if v in want)
            if not inside:
                continue
            out.append(s if inside == len(nums) else s + "（部分）")
        elif li in want:
            out.append(s)
    return sorted(out, key=len, reverse=True)
