"""
backfill_lead.py -- S-Class Leader Score backfill v2.5

Changes from v2.4:
  - 스캔일 거래대금 계산 방식 수정: acml_tr_pbmn(연간 누적값) → 종가×거래량(단일 거래일)
    (FHKST03010100 historical API의 acml_tr_pbmn은 YTD 누적이므로 단일 거래일 값이 아님)
  - 기존 잘못 기록된 데이터: --overwrite 옵션으로 해당 날짜 재실행 필요

Changes from v2.3:
  - --overwrite 옵션 추가: 대상 날짜의 기존 Google Sheets 행 삭제 후 재기록

Changes from v2.2:
  - 종목 목록: Naver sise_quant(거래량순위) -> pykrx KRX 공식 거래대금 순위로 교체
  - Naver는 fallback으로만 사용
  - 중복체크: (날짜,종목코드) 단위 (v2.2 유지)

Usage:
    pip install pykrx   # 최초 1회
    python backfill_lead.py YYYYMMDD [YYYYMMDD ...]

Options:
    --dry-run       Google Sheets 기록 없이 조회 결과만 출력
    --naver-only    pykrx 사용 안 하고 Naver fallback만 사용
    --overwrite     대상 날짜의 기존 Sheets 행 삭제 후 재기록 (데이터 정정용)
"""
import os
import sys
import socket
import time
import re
import requests
import pandas as pd
from datetime import datetime

socket.setdefaulttimeout(15.0)

from dotenv import load_dotenv
load_dotenv()

DRY_RUN    = "--dry-run"    in sys.argv
NAVER_ONLY = "--naver-only" in sys.argv
OVERWRITE  = "--overwrite"  in sys.argv
args = [a for a in sys.argv[1:] if not a.startswith("--")]

from utils import write_log, GoogleSheetsManager
from brokers import KISBroker, _is_etf_etn
from scores.leader_score import calculate_leader_score, format_score_log

REAL_APP_KEY    = os.getenv("KIS_REAL_APP_KEY")
REAL_APP_SECRET = os.getenv("KIS_REAL_APP_SECRET")
MOCK_APP_KEY    = os.getenv("KIS_MOCK_APP_KEY")
MOCK_APP_SECRET = os.getenv("KIS_MOCK_APP_SECRET")
ACCOUNT_NO      = os.getenv("KIS_ACCOUNT_NO")

print("=" * 60)
print("AlgoRich Backfill v2.5  |  DRY_RUN={} NAVER_ONLY={} OVERWRITE={}".format(DRY_RUN, NAVER_ONLY, OVERWRITE))
print("REAL_KEY: {}".format("OK" if REAL_APP_KEY else "MISSING"))
print("MOCK_KEY: {}".format("OK" if MOCK_APP_KEY else "MISSING"))
print("ACCOUNT:  {}".format("OK" if ACCOUNT_NO  else "MISSING"))
print("=" * 60)

if not all([REAL_APP_KEY, REAL_APP_SECRET, MOCK_APP_KEY, MOCK_APP_SECRET, ACCOUNT_NO]):
    print("[ERROR] .env 파일에 KIS API 키 및 계좌번호 확인 필요")
    sys.exit(1)

if not args:
    print("Usage: python backfill_lead.py YYYYMMDD [YYYYMMDD ...]")
    sys.exit(1)

target_dates = args
for d in target_dates:
    if len(d) != 8 or not d.isdigit():
        print("[ERROR] 날짜 형식 오류: {} (YYYYMMDD)".format(d))
        sys.exit(1)

print("대상 날짜: {}".format(", ".join(target_dates)))
print()

# ── pykrx 거래대금 순위 함수 ─────────────────────────────────────────────
def get_top_by_amount_pykrx(date_str, top_n=40):
    try:
        from pykrx import stock as pykrx_stock
    except ImportError:
        print("[WARN] pykrx 미설치.")
        return []

    results = []
    seen    = set()

    for market in ["KOSPI", "KOSDAQ"]:
        try:
            df = pykrx_stock.get_market_ohlcv_by_ticker(date_str, market=market)
            if df is None or df.empty:
                continue
            df = df.reset_index()

            col_map = {}
            for c in df.columns:
                cs = str(c).strip()
                if cs in ("티커", "Ticker", "종목코드"):
                    col_map[c] = "code"
                elif "거래대금" in cs or "TradingValue" in cs or cs == "Amount":
                    col_map[c] = "amount"
            # 첫 번째 컬럼이 티커인 경우 (index.name이 없거나 매핑 실패 시)
            if "code" not in col_map.values() and len(df.columns) > 0:
                col_map[df.columns[0]] = "code"
            df = df.rename(columns=col_map)

            if "code" not in df.columns or "amount" not in df.columns:
                print("[WARN] pykrx {} 컬럼 부재: {}".format(market, list(df.columns)))
                continue

            for _, row in df.iterrows():
                code = str(row["code"]).zfill(6)
                if code in seen:
                    continue
                amt  = int(row.get("amount", 0) or 0)
                if amt <= 0:
                    continue
                try:
                    name = pykrx_stock.get_market_ticker_name(code)
                except Exception:
                    name = code

                if not _is_etf_etn(code, name):
                    results.append({"code": code, "name": name, "amount": amt})
                    seen.add(code)

            time.sleep(0.2)
        except Exception as e:
            print("[WARN] pykrx {} 조회 오류: {}".format(market, e))

    results.sort(key=lambda x: x["amount"], reverse=True)
    print("[pykrx] 비ETF {}종목 거래대금 순위 확보".format(len(results)))
    return results[:top_n]


# ── Google Sheets 연결 ───────────────────────────────────────────────────
gs_manager          = None
existing_date_stock = set()
existing_dates      = set()

if not DRY_RUN:
    gs_manager = GoogleSheetsManager()
    if not gs_manager.initialized:
        print("[ERROR] Google Sheets 초기화 실패.")
        sys.exit(1)
    print("[OK] Google Sheets 연결 성공")
    try:
        lead_sheet    = gs_manager.get_log_sheet("0_주도주_Log")
        existing_rows = lead_sheet.get_all_values()
        for row in existing_rows[1:]:
            if len(row) >= 2 and row[0] and row[1]:
                existing_date_stock.add((row[0], str(row[1]).zfill(6)))
            if row and row[0]:
                existing_dates.add(row[0])
        print("[중복체크] 기존 날짜 {}개 / 종목 {}개".format(
            len(existing_dates), len(existing_date_stock)))
        if existing_dates:
            sd = sorted(existing_dates)
            print("           최초: {}  최근: {}".format(sd[0], sd[-1]))
    except Exception as e:
        print("[WARN] 기존 데이터 조회 실패: {}".format(e))

    if OVERWRITE:
        print()
        print("[OVERWRITE] 대상 날짜의 기존 Sheets 데이터 삭제 시작...")
        for target_date in target_dates:
            del_date_str = datetime.strptime(target_date, "%Y%m%d").strftime("%Y-%m-%d")
            try:
                lead_sheet_ow = gs_manager.get_log_sheet("0_주도주_Log")
                all_vals      = lead_sheet_ow.get_all_values()
                rows_to_del = []
                for i, row in enumerate(all_vals[1:], start=2):
                    if row and row[0] == del_date_str:
                        rows_to_del.append(i)
                if rows_to_del:
                    for row_idx in reversed(rows_to_del):
                        lead_sheet_ow.delete_rows(row_idx)
                        time.sleep(0.2)
                    existing_date_stock = {k for k in existing_date_stock if k[0] != del_date_str}
                    existing_dates.discard(del_date_str)
                    print("[OVERWRITE] {} → {}행 삭제 완료".format(del_date_str, len(rows_to_del)))
                else:
                    print("[OVERWRITE] {} → 기존 데이터 없음 (스킵)".format(del_date_str))
            except Exception as e:
                print("[WARN] OVERWRITE 삭제 실패 {}: {}".format(del_date_str, e))
        print()
else:
    print("[DRY-RUN] Sheets 연결 스킵")

print()

# ── KIS 브로커 초기화 ───────────────────────────────────────────────────
broker = KISBroker(
    ACCOUNT_NO, gs_manager=gs_manager,
    real_app_key=REAL_APP_KEY, real_app_secret=REAL_APP_SECRET,
    mock_app_key=MOCK_APP_KEY, mock_app_secret=MOCK_APP_SECRET
)
if not broker._get_real_token():
    print("[ERROR] KIS 실전 토큰 발급 실패.")
    sys.exit(1)
print("[OK] KIS 실전 토큰 발급 성공")
print()

# ── Naver 접근 테스트 (fallback용) ──────────────────────────────────────
naver_ok = False
try:
    r = requests.get(
        "https://finance.naver.com/sise/sise_quant.nhn?sosok=0&date=20260522",
        timeout=10, headers={"User-Agent": "Mozilla/5.0",
                              "Referer": "https://finance.naver.com/"})
    naver_ok = r.ok
    print("[OK] Naver 접근 {}".format("성공" if naver_ok else "응답코드:" + str(r.status_code)))
except Exception as e:
    print("[WARN] Naver 접근 불가: {}".format(e))
print()

# ── 날짜별 백필 실행 ────────────────────────────────────────────────────
total_logged = 0

for target_date in target_dates:
    log_date_str = datetime.strptime(target_date, "%Y%m%d").strftime("%Y-%m-%d")
    print("=" * 60)
    print("[{}] 백필 시작".format(log_date_str))

    top_stocks = []
    if not NAVER_ONLY:
        print("   Step 1: pykrx KRX 거래대금 순위 조회 중...")
        top_stocks = get_top_by_amount_pykrx(target_date, top_n=40)

    if not top_stocks:
        print("   Step 1 (Naver fallback): 거래대금 상위 40종목 조회 중...")
        top_stocks = broker._naver_top_by_amount(target_date, top_n=40)
        print("          -> 비ETF {}종목 확보".format(len(top_stocks)))

    if not top_stocks:
        print("   -> 종목 없음 (휴장일이거나 데이터 없음)")
        print()
        continue

    print("   거래대금 상위 5:")
    for i, s in enumerate(top_stocks[:5], 1):
        amt_100m = s["amount"] // 100_000_000 if s["amount"] > 10_000_000 else s["amount"]
        print("          {}. {}({}) {}억".format(i, s["name"], s["code"], amt_100m))

    print("   Step 2: S-Class 조건 필터링 (거래량200%+등락10%+52주신고가)")
    found = 0

    for rank, stock_item in enumerate(top_stocks, start=1):
        code = stock_item["code"]
        name = stock_item["name"]

        if not DRY_RUN and (log_date_str, code.zfill(6)) in existing_date_stock:
            continue

        daily_info = broker.get_daily_prices(code, count=260, end_date=target_date)
        if len(daily_info) < 2:
            continue

        df = pd.DataFrame(daily_info)[::-1].reset_index(drop=True)

        try:
            df[["clpr", "hgpr", "acml_vol"]] = (
                df[["stck_clpr", "stck_hgpr", "acml_vol"]]
                .apply(pd.to_numeric, errors="coerce")
            )
        except KeyError:
            continue

        curr = df.iloc[-1]
        if str(curr.get("stck_bsop_date", "")) != target_date:
            continue

        prev = df.iloc[-2]
        curr_vol  = float(curr["acml_vol"] or 0)
        prev_vol  = float(prev["acml_vol"] or 1)
        vol_inrt  = curr_vol / prev_vol * 100

        curr_clpr  = float(curr["clpr"] or 0)
        prev_clpr  = float(prev["clpr"] or 1)
        price_rate = (curr_clpr - prev_clpr) / prev_clpr * 100

        if curr_clpr < 1000:
            continue
        if vol_inrt < 200.0:
            continue
        if price_rate < 10.0:
            continue
        if len(df) < 250:
            continue
        if not broker.is_s_class_target(df):
            continue

        # 스캔일 거래대금: 종가 × 거래량 (단일 거래일 기준)
        # ※ FHKST03010100의 acml_tr_pbmn은 YTD 누적값이므로 사용 불가
        scan_amt_100m = int(curr_clpr * curr_vol / 100_000_000)

        # 거래대금 최소 기준: 500억 미만 제외
        # (Naver fallback이 거래량 순위를 쓰므로 소형주가 유입될 수 있음)
        if scan_amt_100m < 500:
            continue

        ls = calculate_leader_score(
            daily_df=df,
            vol_inrt=vol_inrt,
            prdy_ctrt=price_rate,
            amt_100m=scan_amt_100m,
            inst_net_pbmn=None,
        )
        score_str = "{}점({})".format(ls["score"], ls["grade"])

        print("   [S-Class] [{}위] {}({}) vol={:.0f}% 등락={:.1f}% scan_amt={}억 {}".format(
            rank, name, code, vol_inrt, price_rate, scan_amt_100m, score_str))
        print("      {}".format(format_score_log(code, name, ls)))

        if not DRY_RUN and gs_manager:
            gs_manager.log_lead_stock(
                log_date_str, code, name, int(curr_clpr),
                round(price_rate, 2), scan_amt_100m, rank,
                "S-Class (backfill {})".format(target_date),
                vol_ratio=vol_inrt,
                leader_score=ls["score"],
                leader_grade=ls["grade"],
            )
            existing_date_stock.add((log_date_str, code.zfi