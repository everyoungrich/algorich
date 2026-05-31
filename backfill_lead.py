"""
backfill_lead.py — 특정 날짜(들)의 S-Class 주도주 로그를 소급 기록합니다.

Usage:
    python backfill_lead.py YYYYMMDD [YYYYMMDD ...]
    python backfill_lead.py 20260518 20260519 20260520 20260521 20260522

옵션:
    --dry-run   Google Sheets에 기록하지 않고 조회 결과만 출력
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

DRY_RUN = '--dry-run' in sys.argv
args    = [a for a in sys.argv[1:] if not a.startswith('--')]

from utils import write_log, GoogleSheetsManager
from brokers import KISBroker

REAL_APP_KEY    = os.getenv("KIS_REAL_APP_KEY")
REAL_APP_SECRET = os.getenv("KIS_REAL_APP_SECRET")
MOCK_APP_KEY    = os.getenv("KIS_MOCK_APP_KEY")
MOCK_APP_SECRET = os.getenv("KIS_MOCK_APP_SECRET")
ACCOUNT_NO      = os.getenv("KIS_ACCOUNT_NO")

print("=" * 60)
print(f"AlgoRich Backfill v2.0  |  DRY_RUN={DRY_RUN}")
print(f"REAL_KEY: {'OK' if REAL_APP_KEY else '❌ MISSING'}")
print(f"MOCK_KEY: {'OK' if MOCK_APP_KEY else '❌ MISSING'}")
print(f"ACCOUNT:  {'OK' if ACCOUNT_NO  else '❌ MISSING'}")
print("=" * 60)

if not all([REAL_APP_KEY, REAL_APP_SECRET, MOCK_APP_KEY, MOCK_APP_SECRET, ACCOUNT_NO]):
    print("[ERROR] .env 파일에 KIS API 키 및 계좌번호 확인 필요")
    sys.exit(1)

if not args:
    print("Usage: python backfill_lead.py YYYYMMDD [YYYYMMDD ...]")
    print("Example: python backfill_lead.py 20260518 20260519 20260520 20260521 20260522")
    sys.exit(1)

target_dates = args
for d in target_dates:
    if len(d) != 8 or not d.isdigit():
        print(f"[ERROR] 날짜 형식 오류: {d} (YYYYMMDD 형식)")
        sys.exit(1)

print(f"대상 날짜: {', '.join(target_dates)}")
print()

# ── Google Sheets 연결 ──────────────────────────────────────────
gs_manager = None
existing_dates = set()

if not DRY_RUN:
    gs_manager = GoogleSheetsManager()
    if not gs_manager.initialized:
        print("[ERROR] Google Sheets 초기화 실패. --dry-run 옵션으로 테스트하거나")
        print("        google_credentials.json 파일 확인 필요.")
        sys.exit(1)
    print("[OK] Google Sheets 연결 성공")

    try:
        lead_sheet    = gs_manager.get_log_sheet("0_주도주_Log")
        existing_rows = lead_sheet.get_all_values()
        existing_dates = {row[0] for row in existing_rows[1:] if row and row[0]}
        print(f"[중복체크] 시트 기존 날짜 수: {len(existing_dates)}개")
        if existing_dates:
            sorted_ed = sorted(existing_dates)
            print(f"           최초: {sorted_ed[0]}  최근: {sorted_ed[-1]}")
    except Exception as e:
        print(f"[WARN] 기존 날짜 조회 실패, 중복체크 스킵: {e}")
else:
    print("[DRY-RUN] Sheets 연결 스킵")

print()

# ── KIS 브로커 초기화 ────────────────────────────────────────────
broker = KISBroker(
    ACCOUNT_NO, gs_manager=gs_manager,
    real_app_key=REAL_APP_KEY, real_app_secret=REAL_APP_SECRET,
    mock_app_key=MOCK_APP_KEY, mock_app_secret=MOCK_APP_SECRET
)

# 실전 토큰 발급 테스트
token = broker._get_real_token()
if not token:
    print("[ERROR] KIS 실전 Access Token 발급 실패. REAL_APP_KEY / REAL_APP_SECRET 확인 필요.")
    sys.exit(1)
print(f"[OK] KIS 실전 토큰 발급 성공")
print()

# ── Naver 데이터 접근 테스트 ─────────────────────────────────────
print("[테스트] Naver Finance 접근 확인 중...")
try:
    test_url = "https://finance.naver.com/sise/sise_quant.nhn?sosok=0&date=20260522"
    r = requests.get(test_url, timeout=10,
                     headers={"User-Agent": "Mozilla/5.0",
                              "Referer": "https://finance.naver.com/"})
    if r.ok:
        text = r.content.decode("euc-kr", errors="replace")
        code_count = len(re.findall(r'code=\d{6}', text))
        print(f"[OK] Naver 접근 성공 (종목코드 {code_count}개 파싱)")
    else:
        print(f"[WARN] Naver 응답 코드: {r.status_code}")
except Exception as e:
    print(f"[ERROR] Naver 접근 실패: {e}")
    print("        VPN/방화벽 또는 네트워크 문제일 수 있습니다.")
    sys.exit(1)

print()

# ── 날짜별 백필 실행 ─────────────────────────────────────────────
total_logged = 0

for target_date in target_dates:
    log_date_str = datetime.strptime(target_date, "%Y%m%d").strftime("%Y-%m-%d")
    print(f"{'='*60}")
    print(f"📅 [{log_date_str}] 백필 시작")

    if not DRY_RUN and log_date_str in existing_dates:
        print(f"   → [스킵] 이미 데이터 존재")
        continue

    # Step 1: Naver 거래대금 상위
    print(f"   Step 1: Naver 거래대금 상위 40종목 조회 중...")
    top_stocks = broker._naver_top_by_amount(target_date, top_n=40)
    print(f"          → 비ETF {len(top_stocks)}종목 확보")

    if not top_stocks:
        print(f"   → 거래대금 상위 종목 없음 (휴장일이거나 Naver 데이터 없음)")
        print()
        continue

    # 상위 5개 미리보기
    for i, s in enumerate(top_stocks[:5], 1):
        print(f"          {i}. {s['name']}({s['code']}) 거래대금={s['amount']//100}억")

    # Step 2: 각 종목별 S-Class 조건 확인
    print(f"   Step 2: S-Class 조건 필터링 (거래량300%·등락10%·52주신고가)")
    found = 0

    for rank, stock in enumerate(top_stocks, start=1):
        code   = stock["code"]
        name   = stock["name"]
        amount = stock["amount"]

        daily_info = broker.get_daily_prices(code, count=260, end_date=target_date)
        if len(daily_info) < 2:
            continue

        df = pd.DataFrame(daily_info)[::-1].reset_index(drop=True)
        df[["clpr", "hgpr", "acml_vol"]] = (
            df[["stck_clpr", "stck_hgpr", "acml_vol"]].apply(pd.to_numeric, errors="coerce")
        )

        curr = df.iloc[-1]
        if str(curr.get("stck_bsop_date", "")) != target_date:
            continue  # 비거래일

        prev = df.iloc[-2]
        curr_vol = float(curr["acml_vol"] or 0)
        prev_vol = float(prev["acml_vol"] or 1)
        vol_inrt = curr_vol / prev_vol * 100

        curr_clpr = float(curr["clpr"] or 0)
        prev_clpr = float(prev["clpr"] or 1)
        price_rate = (curr_clpr - prev_clpr) / prev_clpr * 100

        # 조건 체크 (미충족 시 skip, 충족 시 출력)
        if vol_inrt < 300.0:
            continue
        if price_rate < 10.0:
            continue
        if len(df) < 250:
            continue
        if not broker.is_s_class_target(df):
            continue

        amt_100m = amount // 100
        print(f"   ✅ S-Class [{rank}위] {name}({code}) "
              f"거래량={vol_inrt:.0f}% 등락={price_rate:.1f}% "
              f"거래대금={amt_100m}억")

        if not DRY_RUN and gs_manager:
            gs_manager.log_lead_stock(
                log_date_str, code, name, int(curr_clpr),
                round(price_rate, 2), amt_100m, rank,
                f"S-Class 주도주 포착 (Naver백필 {target_date})",
                vol_ratio=vol_inrt
            )

        found += 1
        total_logged += 1
        time.sleep(0.3)

    if found == 0:
        print(f"   → 조건 충족 종목 없음 (4대 S-Class 조건 동시 미충족)")
    else:
        print(f"   → {found}종목 {'기록 완료' if not DRY_RUN else '(DRY-RUN 기록 생략)'}")

    if not DRY_RUN:
        existing_dates.add(log_date_str)
    print()

print(f"{'='*60}")
print(f"전체 백필 완료: 총 {total_logged}종목 {'기록됨' if not DRY_RUN else '발견 (DRY-RUN)'}")
