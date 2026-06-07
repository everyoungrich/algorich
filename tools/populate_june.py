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

# ── 검증된 데이터 ────────────────────────────────────────────────────
# 0_주도주_Log: 최종 S-Class 선정 (9컬럼)
# 날짜, 코드, 종목명, 거래대금(억), 등락률(%), 거래량비(%), 52주신고가, LeaderScore, 비고
LEAD_DATA = [
    ("2026-06-02", "454910", "두산로보틱스", 14750, 20.4, 264, "Y", 55, "S-Class"),
    ("2026-06-02", "017670", "SK텔레콤",    10110, 12.4, 220, "Y", 49, "S-Class"),
]

# 1_Scan_Audit_Log: 1차 필터 통과 + 탈락 종목 (13컬럼)
# 날짜, 코드, 종목명, 거래대금(억), 거래대금30위, 등락률, 등락률10%통과,
# 거래량비, 거래량200%통과, 52주신고가, LeaderScore, 최종결과, 탈락사유
AUDIT_DATA = [
    # 6/1: SKC 1종목만 vol+price 통과, 52주 신고가 실패
    ("2026-06-01", "011790", "SKC",      6661, "Y", 12.3, "Y", 579.0, "Y", "N", "-",  "FAIL", "52주 신고가 미충족"),
    # 6/2: 3종목 통과, 성호전자 52주 신고가 실패
    ("2026-06-02", "454910", "두산로보틱스", 14750, "Y", 20.4, "Y", 264.0, "Y", "Y", 55,   "PASS", ""),
    ("2026-06-02", "017670", "SK텔레콤",    10110, "Y", 12.4, "Y", 220.0, "Y", "Y", 49,   "PASS", ""),
    ("2026-06-02", "043260", "성호전자",     6536, "Y", 21.8, "Y", 231.0, "Y", "N", "-",  "FAIL", "52주 신고가 미충족"),
    # 6/5: 0종목 통과
]

# 2_Scan_Summary_Log: 일별 카운트 (7컬럼)
# 날짜, 거래대금30위, ETF제외후, 10%상승통과, 거래량200%통과, 52주신고가통과, 최종선정
SUMMARY_DATA = [
    ("2026-06-01", 30, 30, 4,  1, 0, 0),   # SKC만 통과, 신고가 실패
    ("2026-06-02", 30, 30, 8,  3, 2, 2),   # 3종목 통과, 2종목 신고가+선정
    ("2026-06-03", 0,  0,  0,  0, 0, 0),   # 지방선거일 공휴일 — 스캔 없음
    ("2026-06-04", 0,  0,  0,  0, 0, 0),   # 봇 미실행 — 누락
    ("2026-06-05", 30, 30, 0,  0, 0, 0),   # 전체 하락장, 0종목
]

print()
print("[0_주도주_Log] 삽입 데이터:")
for r in LEAD_DATA:
    print("  {} {} {} {}억 {}% {}%  신고가:{} {}점".format(*r[:8]))

print()
print("[1_Scan_Audit_Log] 삽입 데이터:")
for r in AUDIT_DATA:
    print("  {} {} {} {}억  {}:{} {}".format(r[0],r[2],r[3],r[4],r[11],r[12][:20] if r[12] else ""))

print()
print("[2_Scan_Summary_Log] 삽입 데이터:")
for r in SUMMARY_DATA:
    print("  {} 30위:{} ETF후:{} 10%:{} 200%:{} 52주:{} 선정:{}".format(*r))

if DRY_RUN:
    print("\n[DRY-RUN] 기록 생략.")
    sys.exit(0)

print("\n기록 중...")
gs = GoogleSheetsManager()
if not gs.initialized:
    print("[ERROR] Google Sheets 연결 실패.")
    sys.exit(1)

# ── 0_주도주_Log ─────────────────────────────────────────────────────
lead_sheet = gs.get_log_sheet("0_주도주_Log")
for row in LEAD_DATA:
    lead_sheet.append_row(list(row), value_input_option="USER_ENTERED")
print("  0_주도주_Log: {}행 기록 완료".format(len(LEAD_DATA)))

# ── 1_Scan_Audit_Log ─────────────────────────────────────────────────
audit_sheet = gs.get_audit_sheet()
for row in AUDIT_DATA:
    audit_sheet.append_row(list(row), value_input_option="USER_ENTERED")
print("  1_Scan_Audit_Log: {}행 기록 완료".format(len(AUDIT_DATA)))

# ── 2_Scan_Summary_Log ───────────────────────────────────────────────
summary_sheet = gs.get_summary_sheet()
for row in SUMMARY_DATA:
    summary_sheet.append_row(list(row), value_input_option="USER_ENTERED")
print("  2_Scan_Summary_Log: {}행 기록 완료".format(len(SUMMARY_DATA)))

print("\n완료.")
print("  0_주도주_Log:       최종 S-Class 2종목 (6/2)")
print("  1_Scan_Audit_Log:   6/1~6/5 필터 통과 종목 4행")
print("  2_Scan_Summary_Log: 6/1~6/5 일별 요약 5행")
