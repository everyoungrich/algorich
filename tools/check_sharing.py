import requests

main_url = "https://docs.google.com/spreadsheets/d/1DsinKhffeQBwP-_KjryGkfyAo8HpNbCWOtAuZx-iiK4/export?format=csv"
log_url = "https://docs.google.com/spreadsheets/d/1rf4ppPSqYlM9-dr1WcFpHl3g-mCeWzl4xlibZ1OnBbY/export?format=csv"

print("Checking Main DB sharing status...")
res_main = requests.get(main_url)
if res_main.status_code == 200 and 'ServiceLogin' not in res_main.url:
    print("[OK] Main DB is public (Anyone with link can view).")
else:
    print(f"[FAIL] Main DB is NOT public. Status: {res_main.status_code}, URL redirected to: {res_main.url}")

print("Checking Log DB sharing status...")
res_log = requests.get(log_url)
if res_log.status_code == 200 and 'ServiceLogin' not in res_log.url:
    print("[OK] Log DB is public (Anyone with link can view).")
else:
    print(f"[FAIL] Log DB is NOT public. Status: {res_log.status_code}, URL redirected to: {res_log.url}")
