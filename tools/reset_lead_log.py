"""
tools/reset_lead_log.py -- 0_주도주_Log 시트 백업 후 초기화

실행:
    python tools/reset_lead_log.py           # 백업 + 초기화
    python tools/reset_lead_log.py --dry-run # 백업만 (초기화 안 함)
"""
import os
import sys
import csv
import socket
from datetime import datetime

socket.setdefaulttimeout(15.0)

from dotenv import load_dotenv
load_dotenv()

DRY_RUN = "--dry-run" in sys.argv

print("=" * 55)
print("AlgoRich -- 주도주 로그 {}".format("백업 (DRY-RUN)" if DRY_RUN else "백업 + 초기화"))
print("=" * 55)

BASE_DIR   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKUP_DIR = os.path.join(BASE_DIR, "backups")
os.makedirs(BACKUP_DIR, exist_ok=True)

sys.path.insert(0, BASE_DIR)
from utils import GoogleSheetsManager

CORRECT_HEADER = [
    "날짜", "종목코드", "종목명", "거래대금(억)",
    "등락률(%)", "거래량비(%)", "52주신고가", "LeaderScore", "비고",
]

print("\n[1/3] Google Sheets 연결 중...")
gs = GoogleSheetsManager()
if not gs.initialized:
    print("[ERROR] Google Sheets 연결 실패.")
    sys.exit(1)
print("      연결 성공")

print("\n[2/3] 현재 시트 데이터 읽기...")
sheet      = gs.get_log_sheet("0_주도주_Log")
all_values = sheet.get_all_values()

if not all_values:
    print("      시트가 비어있음.")
    sys.exit(0)

data_rows = all_values[1:] if len(all_values) > 1 else []
print("      현재 헤더: {}".format(all_values[0]))
print("      데이터 행: {}행".format(len(data_rows)))

ts          = datetime.now().strftime("%Y%m%d_%H%M%S")
backup_file = os.path.join(BACKUP_DIR, "Lead_Log_backup_{}.csv".format(ts))

with open(backup_file, "w", newline="", encoding="utf-8-sig") as f:
    writer = csv.writer(f)
    writer.writerows(all_values)

print("\n[3/3] 백업 완료: {}".format(backup_file))
print("      {}행 저장됨".format(len(data_rows)))

if DRY_RUN:
    print("\n[DRY-RUN] 초기화 생략.")
    sys.exit(0)

print("\n" + "=" * 55)
print("WARNING: 0_주도주_Log 시트를 초기화합니다.")
print("  - {}행 삭제".format(len(data_rows)))
print("  - 9컬럼 헤더로 교체: {}".format(CORRECT_HEADER))
print("  - 백업: {}".format(backup_file))
print("=" * 55)
confirm = input("계속하려면 'yes' 입력: ").strip().lower()
if confirm != "yes":
    print("취소됨.")
    sys.exit(0)

print("\n초기화 중...")
sheet.clear()
sheet.append_row(CORRECT_HEADER)

print("\n결과:")
print("  OK 백업: {} ({}행)".format(backup_file, len(data_rows)))
print("  OK 시트: 9컬럼 헤더로 초기화 완료")
print("  OK 헤더: {}".format(CORRECT_HEADER))
print("  OK 월요일 08:00 실시간 스캔 시작 예정")
