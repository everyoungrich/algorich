"""
backfill_lead.py — 특정 날짜(들)의 S-Class 주도주 로그를 소급 기록합니다.

Usage:
    python backfill_lead.py YYYYMMDD [YYYYMMDD ...]
    python backfill_lead.py 20260518 20260519 20260520 20260521 20260522
"""
import os
import sys
import socket

socket.setdefaulttimeout(15.0)

from dotenv import load_dotenv
load_dotenv()

from utils import write_log, GoogleSheetsManager
from brokers import KISBroker
from datetime import datetime

REAL_APP_KEY    = os.getenv("KIS_REAL_APP_KEY")
REAL_APP_SECRET = os.getenv("KIS_REAL_APP_SECRET")
MOCK_APP_KEY    = os.getenv("KIS_MOCK_APP_KEY")
MOCK_APP_SECRET = os.getenv("KIS_MOCK_APP_SECRET")
ACCOUNT_NO      = os.getenv("KIS_ACCOUNT_NO")

if not all([REAL_APP_KEY, REAL_APP_SECRET, MOCK_APP_KEY, MOCK_APP_SECRET, ACCOUNT_NO]):
    print("[ERROR] .env 파일에 KIS API 키 및 계좌번호 확인 필요")
    sys.exit(1)

if len(sys.argv) < 2:
    print("Usage: python backfill_lead.py YYYYMMDD [YYYYMMDD ...]")
    print("Example: python backfill_lead.py 20260518 20260519 20260520 20260521 20260522")
    sys.exit(1)

target_dates = sys.argv[1:]
for d in target_dates:
    if len(d) != 8 or not d.isdigit():
        print(f"[ERROR] 날짜 형식 오류: {d} (YYYYMMDD 형식)")
        sys.exit(1)

gs_manager = GoogleSheetsManager()
if not gs_manager.initialized:
    print("[ERROR] Google Sheets 초기화 실패")
    sys.exit(1)

# 시트에 이미 있는 날짜 목록을 미리 가져와 중복 방지
try:
    lead_sheet    = gs_manager.get_log_sheet("0_주도주_Log")
    existing_rows = lead_sheet.get_all_values()
    existing_dates = {row[0] for row in existing_rows[1:] if row and row[0]}
    write_log(f"[중복체크] 시트 기존 날짜 수: {len(existing_dates)}개")
except Exception as e:
    write_log(f"[WARN] 기존 날짜 조회 실패, 중복체크 스킵: {e}")
    existing_dates = set()

broker = KISBroker(
    ACCOUNT_NO, gs_manager=gs_manager,
    real_app_key=REAL_APP_KEY, real_app_secret=REAL_APP_SECRET,
    mock_app_key=MOCK_APP_KEY, mock_app_secret=MOCK_APP_SECRET
)

total_logged = 0

for target_date in target_dates:
    log_date_str = datetime.strptime(target_date, "%Y%m%d").strftime("%Y-%m-%d")

    if log_date_str in existing_dates:
        write_log(f"[스킵] {log_date_str} 날짜 데이터 이미 존재. 건너뜀.")
        continue

    write_log(f"=== S-Class 백필 시작 (기준일: {log_date_str}) ===")
    # Naver Finance 거래대금 순위 기반 historical 스캐너 사용
    results = broker.scan_s_class_targets_historical(target_date=target_date)
    write_log(f"=== 백필 완료: {len(results)}종목 → Google Sheets 기록 ===")
    total_logged += len(results)
    existing_dates.add(log_date_str)

write_log(f"=== 전체 백필 완료: 총 {total_logged}종목 기록 ===")
