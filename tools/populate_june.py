"""
tools/populate_june.py -- 6월 검증 데이터 3개 시트에 삽입

실행:
    python tools/populate_june.py --dry-run   # 출력만 (기록 안 함)
    python tools/populate_june.py             # 실제 기록
"""
import os, sys, socket
socket.setdefaulttimeout(15.0)
from dotenv import load_dotenv
load_dotenv()

DRY_RUN = "--dry-run" in sys.argv
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
from utils import GoogleSheetsManager

print("=" * 60)
print("AlgoRich -- 6월 데이터 정비 {}".format("(DRY-RUN)" if DRY_RUN else "(실제 기록)"))
print("=" * 60)

# 0_주도주_Log (9컬럼): 날짜, 코드, 종목명, 거래대금(억), 등락률, 거래량비, 52주신고가, Score, 비고
LEAD_DATA = [
    ("2026-06-02", "454910", "두산로보틱스", 14750, 20.4, 264, "Y", 55, "S-Class"),
    ("2026-06-02", "017670", "SK텔레콤",    10110, 12.4, 220, "Y", 49, "S-Class"),
]

# 1_Scan_Audit_Log (13컬럼)
# 날짜, 코드, 종목명, 거래대금(억), 거래대금30위, 등락률, 등락률10%통과,
# 거래량비, 거래량200%통과, 52주신고가, LeaderScore, 최종결과, 탈락사유
AUDIT_DATA = [
    ("2026-06-01", "011790", "SKC",       6661,  "Y", 12.3, "Y", 579.0, "Y", "N", "-", "FAIL", "52주 신고가 미충족"),
    ("2026-06-02", "454910", "두산로보틱스", 14750, "Y", 20.4, "Y", 264.0, "Y", "Y", 55,  "PASS", ""),
    ("2026-06-02", "017670", "SK텔레콤",   10110, "Y", 12.4, "Y", 220.0, "Y", "Y", 49,  "PASS", ""),
    ("2026-06-02", "043260", "성호전자",    6536,  "Y", 21.8, "Y", 231.0, "Y", "N", "-", "FAIL", "52주 신고가 미충족"),
]

# 2_Scan_Summary_Log (7컬럼)
SUMMARY_DATA = [
    ("2026-06-01", 30, 30, 4, 1, 0, 0),
    ("2026-06-02", 30, 30, 8, 3, 2, 2),
    ("2026-06-03",  0,  0, 0, 0, 0, 0),
    ("2026-06-04",  0,  0, 0, 0, 0, 0),
    ("2026-06-05", 30, 30, 0, 0, 0, 0),
]

print()
print("[0_주도주_Log] {} 행".format(len(LEAD_DATA)))
for r in LEAD_DATA:
    print("  {} {} {}억 등락:{}% 거래량:{}% 신고가:{} Score:{}".format(
        r[0], r[2], r[3], r[4], r[5], r[6], r[7]))

print()
print("[1_Scan_Audit_Log] {} 행".format(len(AUDIT_DATA)))
for r in AUDIT_DATA:
    print("  {} {} {}억 -> {} {}".format(
        r[0], r[2], r[3], r[11], r[12]))

print()
print("[2_Scan_Summary_Log] {} 행".format(len(SUMMARY_DATA)))
for r in SUMMARY_DATA:
    note = ""
    if r[1] == 0:
        note = " (공휴일/봇미실행)"
    print("  {} 30위:{} 10%:{} 200%:{} 52주:{} 선정:{}{}".format(
        r[0], r[1], r[3], r[4], r[5], r[6], note))

if DRY_RUN:
    print("\n[DRY-RUN] 기록 생략.")
    sys.exit(0)

print("\n기록 중...")
gs = GoogleSheetsManager()
if not gs.initialized:
    print("[ERROR] Google Sheets 연결 실패.")
    sys.exit(1)

lead_sheet    = gs.get_log_sheet("0_주도주_Log")
audit_sheet   = gs.get_audit_sheet()
summary_sheet = gs.get_summary_sheet()

for row in LEAD_DATA:
    lead_sheet.append_row(list(row), value_input_option="USER_ENTERED")
print("  0_주도주_Log: {}행 완료".format(len(LEAD_DATA)))

for row in AUDIT_DATA:
    audit_sheet.append_row(list(row), value_input_option="USER_ENTERED")
print("  1_Scan_Audit_Log: {}행 완료".format(len(AUDIT_DATA)))

for row in SUMMARY_DATA:
    summary_sheet.append_row(list(row), value_input_option="USER_ENTERED")
print("  2_Scan_Summary_Log: {}행 완료".format(len(SUMMARY_DATA)))

print("\n완료.")
