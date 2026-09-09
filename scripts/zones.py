# -*- coding: utf-8 -*-
"""烏日區地理分區定義：以實價登錄「土地位置建物門牌」的路街名+段比對。

實價登錄成屋檔(A)沒有社區名/里別欄位，只有門牌字串，因此商圈只能用路街白名單界定。
明道商圈範圍依「中山路一段 × 興祥街」路口為中心、半徑約 700m 的明道中學生活圈認定。
高鐵特區依高鐵台中站周邊重劃區主要幹道認定。
兩者刻意不重疊；未落入任一白名單者僅計入 wuri_all。
"""

MINGDAO = [
    "中山路一段", "興祥街", "信義街", "僑仁街", "光德路",
    "學田路", "三民街", "光日路", "仁德街", "文明街",
]

HSR = [
    "高鐵東路", "高鐵路一段", "高鐵路二段", "高鐵路三段",
    "高鐵一路", "高鐵二路", "高鐵三路", "高鐵五路", "高鐵六路", "高鐵七路",
    "三榮路一段", "三榮路二段", "三榮路三段",
    "三榮北路", "三榮三路", "三榮五路", "三榮十六路", "三榮十八路",
    "榮泰街", "榮泉街", "站區路",
]

ZONES = {
    "wuri_all":     {"label": "烏日全區",   "streets": None},   # None = 全區，不做門牌篩選
    "wuri_hsr":     {"label": "高鐵特區",   "streets": HSR},
    "wuri_mingdao": {"label": "明道商圈",   "streets": MINGDAO},
}


def zones_of(addr: str):
    """回傳該門牌命中的分區代碼清單（一定含 wuri_all）。"""
    hit = ["wuri_all"]
    for code, cfg in ZONES.items():
        if cfg["streets"] and any(s in addr for s in cfg["streets"]):
            hit.append(code)
    return hit
