"""
backfill_lead.py — 특정 날짜의 S-Class 주도주 로그를 소급 기록합니다.

Usage:
    python backfill_lead.py 20260515
"""
import os
import sys
import socket

socket.setdefaulttimeout(15.0)

from dotenv import load_dotenv
load_dotenv()

from utils import write_log, GoogleSheetsManager
from brokers import KISBroker

APP_KEY    = os.getenv("KIS_APP_KEY")
APP_SECRET = os.getenv("KIS_APP_SECRET")
ACCOUNT_NO = os.getenv("KIS_ACCOUNT_NO")
MODE       = str(os.getenv("KIS_MODE", "0"))

if not all([APP_KEY, APP_SECRET, ACCOUNT_NO]):
    print("[ERROR] .env 파일에 KIS_APP_KEY / KIS_APP_SECRET / KIS_ACCOUNT_NO 확인 필요")
    sys.exit(1)

if len(sys.argv) < 2:
    print("Usage: python backfill_lead.py YYYYMMDD")
    print("Example: python backfill_lead.py 20260515")
    sys.exit(1)

target_date = sys.argv[1]
if len(target_date) != 8 or not target_date.isdigit():
    print(f"[ERROR] 날짜 형식 오류: {target_date} (YYYYMMDD 형식으로 입력)")
    sys.exit(1)

write_log(f"=== S-Class 주도주 백필 시작 (기준일: {target_date}) ===")

gs_manager = GoogleSheetsManager()
broker     = KISBroker(APP_KEY, APP_SECRET, ACCOUNT_NO, MODE, gs_manager=gs_manager)

results = broker.scan_s_class_targets(target_date=target_date)

write_log(f"=== 백필 완료: {len(results)}종목 → Google Sheets 기록 ===")
