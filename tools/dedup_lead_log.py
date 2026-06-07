"""tools/dedup_lead_log.py -- 0_주도주_Log 중복 행 제거"""
import os, sys, socket
socket.setdefaulttimeout(15.0)
from dotenv import load_dotenv; load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils import GoogleSheetsManager

gs = GoogleSheetsManager()
if not gs.initialized:
    print("[ERROR] Sheets 연결 실패"); sys.exit(1)

sheet = gs.get_log_sheet("0_주도주_Log")
rows  = sheet.get_all_values()
if len(rows) <= 1:
    print("데이터 없음"); sys.exit(0)

header    = rows[0]
data_rows = rows[1:]
seen, keep, dupes = set(), [], []
for r in data_rows:
    key = (r[0], r[1])  # 날짜 + 종목코드
    if key in seen:
        dupes.append(r)
    else:
        seen.add(key)
        keep.append(r)

print("전체:{}행 유지:{}행 중복:{}행".format(len(data_rows), len(keep), len(dupes)))
if dupes:
    for d in dupes:
        print("  중복:", d[:3])

if not dupes:
    print("중복 없음"); sys.exit(0)

confirm = input("중복 제거하려면 yes 입력: ").strip().lower()
if confirm != "yes":
    sys.exit(0)

sheet.clear()
sheet.append_row(header)
if keep:
    sheet.append_rows(keep, value_input_option="USER_ENTERED")
print("완료: {}행 유지됨".format(len(keep)))
