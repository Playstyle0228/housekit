# -*- coding: utf-8 -*-
"""實價登錄成屋買賣(A檔)清洗與實質單價計算。"""
import re

PING = 0.3025  # 1 平方公尺 = 0.3025 坪

# 特殊交易備註黑名單（依 115S2 台中實測備註分布歸納）
SPECIAL_REMARKS = [
    "預售屋",           # 預售屋/土地及建物分件登記 → 交屋遞延，交易日與市況脫鉤
    "分件登記",
    "親友", "員工", "共有人", "特殊關係", "關係人",
    "政府機關標讓售", "協議價購", "標售", "標讓售",
    "僅車位交易", "市場攤位", "毛胚屋",
    "公共設施保留地", "畸零地",
    "地清", "未辦繼承",
    "讓渡", "換約", "轉讓",
    "債務", "抵債", "解約",
    "瑕疵", "凶宅", "地上權",
]

# 納入統計的建物型態（排除透天厝/店面/廠辦，其單價含大量土地價值不可比）
BUILDING_TYPES = ["住宅大樓", "華廈"]


def _f(v):
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return 0.0


def roc_ym(s):
    """民國 YYYMMDD -> (年, 月)；失敗回 None。"""
    s = re.sub(r"\D", "", str(s or ""))
    if len(s) < 5:
        return None
    y, m = int(s[:3]), int(s[3:5])
    if not (80 <= y <= 200 and 1 <= m <= 12):
        return None
    return y, m


def ym_index(ym):
    """(年,月) -> 連續月序，用於算月差。"""
    return ym[0] * 12 + (ym[1] - 1)


def is_special(remark):
    r = (remark or "").strip()
    return any(k in r for k in SPECIAL_REMARKS) if r else False


def clean_row(row, reg_start_ym, max_lag_months=6):
    """把一筆原始 CSV 轉成統計用紀錄，不合格回 None。

    reg_start_ym    來源檔登記期間的起始 (年,月)，用來推估申報遞延
    max_lag_months  登記期間起始 - 交易月 超過此值視為遞延案件剔除
    """
    if not str(row.get("交易標的", "")).startswith("房地"):
        return None
    btype = str(row.get("建物型態", ""))
    if not any(t in btype for t in BUILDING_TYPES):
        return None
    if is_special(row.get("備註")):
        return None

    tx = roc_ym(row.get("交易年月日"))
    if tx is None:
        return None

    # 遞延剔除：交易月早於本檔登記期間起始超過 max_lag_months
    # 另擋來源端錯誤的未來交易日：登記期間為 3 個月窗口，合理 lag 下界為 -3，
    # 放寬至 -4；實測 111S4~115S2 共 3 筆 lag <= -7 均屬鍵入錯誤（如民國 122 年）。
    lag = ym_index(reg_start_ym) - ym_index(tx)
    if lag > max_lag_months or lag < -4:
        return None

    built = roc_ym(row.get("建築完成年月"))
    if built is None:
        return None
    age = (ym_index(tx) - ym_index(built)) / 12.0

    area = _f(row.get("建物移轉總面積平方公尺")) - _f(row.get("車位移轉總面積平方公尺"))
    price = _f(row.get("總價元")) - _f(row.get("車位總價元"))
    if area < 15 or price <= 0:
        return None

    unit = price / (area * PING) / 10000.0   # 萬元/坪
    if not (5.0 <= unit <= 150.0):           # 明顯異常值
        return None

    return {
        "id": row.get("編號", ""),
        "tx_ym": "%03d/%02d" % tx,
        "age": round(age, 2),
        "unit": round(unit, 4),
        "ping": round(area * PING, 2),
        "addr": row.get("土地位置建物門牌", ""),
        "btype": btype,
        "lag": lag,
    }
