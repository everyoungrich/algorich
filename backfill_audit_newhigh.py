# -*- coding: utf-8 -*-
"""
backfill_audit_newhigh.py — 1_Scan_Audit_Log의 미검증("-") 52주 신고가 소급 판정

대상: 등락률 10% 이상인데 52주신고가가 "-"인 행 (과거 1차 탈락 종목)
판정: pykrx KRX 공식 일봉 — 해당 날짜 고가 >= 직전 250거래일 고가 최대값
갱신: 시트 J열("-" 셀만) → Y/N. 기존 Y/N 값은 절대 덮어쓰지 않음.

실행: python backfill_audit_newhigh.py
"""
import time
from datetime import datetime, timedelta

import gspread
from oauth2client.service_account import ServiceAccountCredentials
from pykrx import stock as krx

LOG_DB_ID  = "1rf4ppPSqYlM9-dr1WcFpHl3g-mCeWzl4xlibZ1OnBbY"
SHEET_NAME = "1_Scan_Audit_Log"
NH_COL     = 10          # J열 = 52주신고가
DRY_RUN    = False       # True면 시트에 쓰지 않고 출력만


def judge_new_high(code: str, d8: str):
    """d8(YYYYMMDD) 기준 52주 신고가 여부. 데이터 부족 시 None."""
    start = (datetime.strptime(d8, "%Y%m%d") - timedelta(days=540)).strftime("%Y%m%d")
    try:
        df = krx.get_market_ohlcv(start, d8, code)
    except Exception as e:
        print(f"  [조회실패] {code} {d8}: {e}")
        return None
    if df is None or len(df) < 250:
        return None  # 상장 250일 미만 등 — "-" 유지
    df = df.tail(251)
    curr_high = float(df["고가"].iloc[-1])
    prior = df["고가"].iloc[:-1]
    prior = prior[prior > 0]
    if curr_high <= 0 or prior.empty:
        return None
    return "Y" if curr_high >= float(prior.max()) else "N"


def main():
    scope = ["https://spreadsheets.google.com/feeds",
             "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(
        "google_credentials.json", scope)
    ws = gspread.authorize(creds).open_by_key(LOG_DB_ID).worksheet(SHEET_NAME)

    vals = ws.get_all_values()
    print(f"audit 행 수: {len(vals)-1}")

    updates, cache = [], {}
    for i, r in enumerate(vals[1:], start=2):   # i = 실제 시트 행 번호
        if len(r) < NH_COL:
            continue
        try:
            chg = float(r[5])
        except (ValueError, TypeError):
            continue
        nh = (r[9] or "").strip()
        if chg < 10.0 or nh in ("Y", "N"):
            continue

        date_s = (r[0] or "").strip()
        code   = (r[1] or "").strip().zfill(6)
        if len(date_s) != 10 or not code.isdigit():
            continue
        d8  = date_s.replace("-", "")
        key = (code, d8)

        if key not in cache:
            cache[key] = judge_new_high(code, d8)
            time.sleep(0.6)   # KRX 서버 예의

        res = cache[key]
        label = res if res else "판정불가(- 유지)"
        print(f"행{i:>4} {date_s} {r[2]}({code}) 등락:{chg:+.1f}% → {label}")
        if res:
            updates.append({
                "range":  gspread.utils.rowcol_to_a1(i, NH_COL),
                "values": [[res]],
            })

    if not updates:
        print("\n갱신할 셀 없음.")
        return
    if DRY_RUN:
        print(f"\n[DRY_RUN] {len(updates)}개 셀 갱신 예정 (쓰기 생략)")
        return
    ws.batch_update(updates)
    print(f"\n완료: {len(updates)}개 셀 갱신 (J열 '-' → Y/N)")


if __name__ == "__main__":
    main()
