# -*- coding: utf-8 -*-
import sys
from utils import GoogleSheetsManager

gs_manager = GoogleSheetsManager()
if not gs_manager.initialized:
    print("Failed to initialize Google Sheets")
    sys.exit(1)

print("Listing all worksheets:")
ws = gs_manager.log_wb.worksheet("0_주도주_Log")
print(f"Reading from: {ws.title} (GID: {ws.id})")
values = ws.get_all_values()
print(f"Total rows in sheet (including empty): {len(values)}")
for i, row in enumerate(values):
    if any(row):
        print(f"Row {i+1}: {row}")
