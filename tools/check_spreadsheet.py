# -*- coding: utf-8 -*-
from utils import GoogleSheetsManager

print("Checking Lead Stock Log in Google Sheets...")
gs = GoogleSheetsManager()
try:
    log_sheet = gs.get_log_sheet("0_주도주_Log")
    records = log_sheet.get_all_records()
    print(f"Total records in log sheet: {len(records)}")
    
    # Check for today's date or specific code
    found = []
    for r in records[-5:]:  # check last 5 records
        print(r)
        if str(r.get("종목코드", "")).zfill(6) == "018880" or "한온시스템" in str(r.get("종목명", "")):
            found.append(r)
            
    if found:
        print("\n[SUCCESS] Found Hanon Systems (018880) in the spreadsheet!")
        for f in found:
            print(f"Row Data: {f}")
    else:
        print("\n[FAIL] Hanon Systems (018880) NOT FOUND in the last 5 records.")
except Exception as e:
    print(f"Error accessing spreadsheet: {e}")
