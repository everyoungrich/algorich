# -*- coding: utf-8 -*-
import os
import sys
import pandas as pd
from dotenv import load_dotenv
load_dotenv()

from utils import GoogleSheetsManager
from brokers import KISBroker

REAL_APP_KEY    = os.getenv("KIS_REAL_APP_KEY", "")
REAL_APP_SECRET = os.getenv("KIS_REAL_APP_SECRET", "")
MOCK_APP_KEY    = os.getenv("KIS_MOCK_APP_KEY", "")
MOCK_APP_SECRET = os.getenv("KIS_MOCK_APP_SECRET", "")
ACCOUNT_NO      = os.getenv("KIS_ACCOUNT_NO", "46601260")

def update():
    gs_manager = GoogleSheetsManager()
    if not gs_manager.initialized:
        print("Failed to init GS")
        return

    ws = gs_manager.log_wb.worksheet("0_주도주_Log")
    values = ws.get_all_values()

    rows_to_delete = []
    for i, row in enumerate(values):
        if len(row) > 0 and (row[0] == "2026-05-05" or row[0] == "2026-05-04"):
            rows_to_delete.append(i + 1)

    for row_idx in sorted(rows_to_delete, reverse=True):
        ws.delete_rows(row_idx)
        print(f"Deleted row {row_idx}")

    # Now fetch using broker, but we don't pass gs_manager so it doesn't log automatically.
    # We will log it manually here.
    broker = KISBroker(
        ACCOUNT_NO, gs_manager=None,
        real_app_key=REAL_APP_KEY, real_app_secret=REAL_APP_SECRET,
        mock_app_key=MOCK_APP_KEY, mock_app_secret=MOCK_APP_SECRET
    )
    
    # We will just run the scan logic manually to intercept the date
    targets = broker.scan_s_class_targets()
    
    date_str = "2026-05-04"
    print(f"Found {len(targets)} targets. Writing as {date_str}...")
    
    for rank, cand in enumerate(targets, start=1):
        code = cand["code"]
        name = cand["name"]
        
        # We need to get the change_rate and amt_100m. 
        # But wait, cand already has 'amt', but not change_rate.
        # Let's get daily prices
        daily_info = broker.get_daily_prices(code)
        if len(daily_info) >= 2:
            df = pd.DataFrame(daily_info)[::-1].reset_index(drop=True)
            df[['clpr', 'hgpr', 'amt', 'vol']] = df[['stck_clpr', 'stck_hgpr', 'acml_tr_pbmn', 'acml_vol']].apply(pd.to_numeric)
            curr = df.iloc[-1]
            prev = df.iloc[-2]
            change_rate = (curr['clpr'] - prev['clpr']) / prev['clpr'] * 100
            amt_100m = int(curr['amt'] / 100_000_000)
            
            gs_manager.log_lead_stock(date_str, code, name, int(curr['clpr']), round(change_rate, 2), amt_100m, rank, "S-Class 주도주 포착 (5/4 복구)")
            print(f"Logged {name} ({code})")

if __name__ == "__main__":
    update()
