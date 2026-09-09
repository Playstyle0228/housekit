# -*- coding: utf-8 -*-
"""內政部不動產成交案件實際資訊資料供應系統 下載層（標準庫實作）。

三種來源，實測結果：
  季檔  DownloadHistory?type=season&fileName=115S2   全國 zip 約 113 MB，101S1 起永久保留
  旬檔  DownloadHistory?type=history&fileName=2026821 全國 zip 約 13.6 MB，僅保留最近 6 旬
  當期  Download?fileName=b_lvr_land_a.csv            單縣市 CSV 約 433 KB，僅當期一旬

季檔以「登記日期」而非交易日期切分，因此跨季合併時必須以「編號」去重。
"""
import gzip
import os
import re
import ssl
import time
import urllib.request
import zipfile

BASE = "https://plvr.land.moi.gov.tw"
UA = "Mozilla/5.0 (compatible; HouseKit/1.0; +https://github.com/Playstyle0228/housekit)"

SEASON_LIST = BASE + "/DownloadSeason_ajax_list"
PERIOD_LIST = BASE + "/DownloadHistory_ajax_list"
SEASON_ZIP = BASE + "/DownloadHistory?type=season&fileName={code}"
PERIOD_ZIP = BASE + "/DownloadHistory?type=history&fileName={code}"
CURRENT_CSV = BASE + "/Download?fileName={county}_lvr_land_a.csv"

COUNTY = {
    "a": "臺北市", "b": "臺中市", "c": "基隆市", "d": "臺南市", "e": "高雄市",
    "f": "新北市", "g": "宜蘭縣", "h": "桃園市", "i": "嘉義市", "j": "新竹縣",
    "k": "苗栗縣", "m": "南投縣", "n": "彰化縣", "o": "新竹市", "p": "雲林縣",
    "q": "嘉義縣", "t": "屏東縣", "u": "花蓮縣", "v": "臺東縣", "w": "金門縣",
    "x": "澎湖縣", "z": "連江縣",
}

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE   # 該站憑證鏈在部分 CI 環境不完整


def _get(url, retries=4, timeout=180):
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
                return r.read()
        except Exception as e:                                  # noqa: BLE001
            last = e
            time.sleep(2 ** i)
    raise RuntimeError("下載失敗 %s: %s" % (url, last))


def list_seasons():
    """可用季別，新到舊，如 ['115S2', '115S1', ...]。"""
    html = _get(SEASON_LIST).decode("utf-8", "replace")
    return re.findall(r'value="(\d{3}S[1-4])"', html)


def list_periods():
    """可用旬別（西元 YYYYMMDD 發布日），僅最近 6 旬。"""
    html = _get(PERIOD_LIST).decode("utf-8", "replace")
    return sorted(set(re.findall(r"downloadLast\('(\d{8})'\)", html)), reverse=True)


def season_reg_start(code):
    """季別 -> 登記期間起始 (民國年, 月)。

    依系統說明：S1 登記自前一年 12/11、S2 自 3/11、S3 自 6/11、S4 自 9/11。
    """
    y, s = int(code[:3]), int(code[4])
    return (y - 1, 12) if s == 1 else (y, {2: 3, 3: 6, 4: 9}[s])


def period_reg_start(code):
    """旬別發布日 -> 登記期間起始 (民國年, 月)。發布日涵蓋前一旬。"""
    y, m, d = int(code[:4]), int(code[4:6]), int(code[6:8])
    if d <= 10:                      # 1 日發布 -> 前月下旬
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return (y - 1911, m)


def fetch_zip(kind, code, cache_dir):
    """下載季/旬 zip，回傳本機路徑；已存在則沿用快取。"""
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, "%s_%s.zip" % (kind, code))
    if os.path.exists(path) and os.path.getsize(path) > 1_000_000:
        return path
    url = (SEASON_ZIP if kind == "season" else PERIOD_ZIP).format(code=code)
    data = _get(url)
    if not data.startswith(b"PK"):
        raise RuntimeError("%s %s 非 zip 內容（可能該期未發布）" % (kind, code))
    tmp = path + ".part"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)
    return path


def read_a_csv_from_zip(zip_path, counties=None):
    """從 zip 取出各縣市成屋買賣(A)檔，yield (縣市碼, csv 文字)。"""
    with zipfile.ZipFile(zip_path) as z:
        for name in z.namelist():
            m = re.fullmatch(r"([a-z])_lvr_land_a\.csv", name)
            if not m:
                continue
            cc = m.group(1)
            if counties and cc not in counties:
                continue
            yield cc, z.read(name).decode("utf-8-sig", "replace")


def fetch_current_csv(county):
    """當期（最新一旬）單縣市成屋買賣 CSV 文字。"""
    return _get(CURRENT_CSV.format(county=county)).decode("utf-8-sig", "replace")


def archive_csv(text, out_dir, tag, county):
    """把原始 CSV 壓存起來 —— 旬檔只保留 6 旬，過期就再也拿不到。"""
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "%s_%s_a.csv.gz" % (tag, county))
    with gzip.open(path, "wt", encoding="utf-8", newline="") as f:
        f.write(text)
    return path
