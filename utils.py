import os
import time
import requests
import uuid
from datetime import datetime
import gspread
from oauth2client.service_account import ServiceAccountCredentials


class _TimeoutHTTPAdapter(requests.adapters.HTTPAdapter):
    """gspread 세션에 마운트해 명시적 timeout 없는 호출도 15초로 제한."""
    def send(self, request, **kwargs):
        kwargs.setdefault('timeout', 15)
        return super().send(request, **kwargs)

MAIN_DB_ID = "1DsinKhffeQBwP-_KjryGkfyAo8HpNbCWOtAuZx-iiK4"
LOG_DB_ID = "1rf4ppPSqYlM9-dr1WcFpHl3g-mCeWzl4xlibZ1OnBbY"

def write_log(message):
    now_str = datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")
    log_msg = f"{now_str} {message}"
    print(log_msg)
    with open("trade_log.txt", "a", encoding="utf-8") as f:
        f.write(log_msg + "\n")

class GoogleSheetsManager:
    def __init__(self, credentials_path="google_credentials.json"):
        self.client = None
        self.main_wb = None
        self.log_wb = None
        self.initialized = False
        self.logged_today = set()
        
        try:
            if os.path.exists(credentials_path):
                scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
                creds = ServiceAccountCredentials.from_json_keyfile_name(credentials_path, scope)
                self.client = gspread.authorize(creds)
                try:
                    _adapter = _TimeoutHTTPAdapter()
                    self.client.session.mount('https://', _adapter)
                    self.client.session.mount('http://', _adapter)
                except AttributeError:
                    pass

                self.main_wb = self.client.open_by_key(MAIN_DB_ID)
                self.log_wb = self.client.open_by_key(LOG_DB_ID)
                self.initialized = True
                write_log("Google Sheets 통합 DB 연동 성공 (MAIN & LOG).")
        except Exception as e:
            write_log(f"Google Sheets 초기화 에러: {repr(e)}")

    def get_main_sheet(self, strategy_type):
        sheet_name_map = {
            "NH_Sniper": "NH-Sniper",
            "1D_RB":     "1D-Rebound",
            "20D_SW":    "20D-Swing",
            "ENV_RB":    "Env-Rebound",
        }
        target_tab = sheet_name_map.get(strategy_type, "1D-Rebound")
        try:
            return self.main_wb.worksheet(target_tab)
        except gspread.exceptions.WorksheetNotFound:
            worksheet = self.main_wb.add_worksheet(title=target_tab, rows=1000, cols=15)
            worksheet.append_row([
                "Trade ID", "상태", "종목코드", "종목명", "매수일자", "매수단가", 
                "매수수량", "투자금액", "매도일자", "매도단가", "수익률(%)", "수익금(원)", "비고(로그)", "모드"
            ])
            return worksheet

    def get_log_sheet(self, tab_name="0_주도주_Log"):
        try:
            return self.log_wb.worksheet(tab_name)
        except gspread.exceptions.WorksheetNotFound:
            worksheet = self.log_wb.add_worksheet(title=tab_name, rows=1000, cols=10)
            worksheet.append_row(["날짜", "종목코드", "종목명", "종가", "상승률 (%)", "거래대금 (억)", "거래대금 순위", "비고", "거래량비(%)"])
            return worksheet

    def log_lead_stock(self, date_str, ticker, name, close_price, change_rate, amt_100m, rank, remark, vol_ratio=0):
        if not self.initialized: return False
        log_key = (date_str, ticker)
        if log_key in self.logged_today: return False

        row = [date_str, ticker, name, int(close_price), float(change_rate), int(amt_100m), int(rank), remark, round(float(vol_ratio), 1)]
        for attempt in range(3):
            try:
                lead_sheet = self.get_log_sheet("0_주도주_Log")
                lead_sheet.append_row(row, value_input_option='USER_ENTERED')
                self.logged_today.add(log_key)
                return True
            except Exception as e:
                write_log(f"[log_lead_stock 오류 attempt {attempt+1}/3] {repr(e)}")
                time.sleep(10)
        return False

    def get_past_lead_stocks(self, days_ago_list=[1, 2]):
        """
        1~2일 전 주도주로 찍혔던 종목의 코드를 반환합니다.
        """
        if not self.initialized: return []
        try:
            lead_sheet = self.get_log_sheet("0_주도주_Log")
            records = lead_sheet.get_all_records()
            
            target_dates = []
            now = datetime.now()
            import pandas as pd # To easily calculate business days if needed, but simple subtraction for now
            # To handle weekends, we can just grab all unique dates sorted and pick the last 2 before today
            
            dates = set([str(r.get("날짜", "")) for r in records if r.get("날짜", "")])
            sorted_dates = sorted(list(dates))
            
            # exclude today
            today_str = now.strftime("%Y-%m-%d")
            valid_dates = [d for d in sorted_dates if d < today_str]
            
            # get the last N valid dates
            selected_dates = []
            for d in days_ago_list:
                if len(valid_dates) >= d:
                    selected_dates.append(valid_dates[-d])
                    
            past_stocks = []
            for r in records:
                if str(r.get("날짜", "")) in selected_dates:
                    # 종목코드 컬럼이 있으면 우선 사용, 없으면 종목명만 반환 (불안정할 수 있으나 호환성)
                    code = str(r.get("종목코드", "")).zfill(6) if r.get("종목코드") else None
                    name = str(r.get("종목명", ""))
                    if code and code != "000000":
                        past_stocks.append({"code": code, "name": name, "date": str(r.get("날짜", ""))})
                        
            return past_stocks
        except Exception as e:
            write_log(f"과거 주도주 조회 실패: {e}")
            return []

    def log_buy(self, trade_id, code, name, price, qty, strategy_type="1D_RB", mode="모의"):
        if not self.initialized: return
        now_str = datetime.now().strftime("%Y-%m-%d")
        invest_amt = price * qty
        row = [trade_id, "보유", code, name, now_str, int(price), int(qty), int(invest_amt), "", "", "", "", "매수완료", mode]
        try:
            target_sheet = self.get_main_sheet(strategy_type)
            target_sheet.append_row(row, value_input_option='USER_ENTERED')
        except Exception as e:
            pass

    def log_error(self, message):
        if not self.initialized: return
        now_str = datetime.now().strftime("%Y-%m-%d")
        trade_id = str(uuid.uuid4())[:8]
        row = [trade_id, "시스템로그", "-", "-", now_str, 0, 0, 0, "", "", "", "", message]
        try:
            target_sheet = self.get_main_sheet("1D_RB")
            target_sheet.append_row(row, value_input_option='USER_ENTERED')
        except:
            pass

    def log_sell(self, code, sell_price, sell_qty, is_partial=False, strategy_type="1D_RB"):
        if not self.initialized: return
        try:
            target_sheet = self.get_main_sheet(strategy_type)
            records = target_sheet.get_all_records()
            for idx, row in enumerate(records):
                if str(row.get("종목코드", "")).zfill(6) == code and row.get("상태", "") == "보유":
                    trade_id = row.get("Trade ID")
                    buy_price = float(row.get("매수단가", 0))
                    buy_qty = int(row.get("매수수량", 0))
                    name = row.get("종목명", "")
                    buy_time = row.get("매수일자", "")
                    
                    if buy_qty == 0: continue
                    
                    sell_amt = sell_price * sell_qty
                    invest_amt = buy_price * sell_qty
                    
                    fee_tax_amt = sell_amt * 0.0022
                    profit = sell_amt - invest_amt - fee_tax_amt
                    profit_rt = round(profit / invest_amt * 100, 2)
                    
                    now_str = datetime.now().strftime("%Y-%m-%d")
                    row_index = idx + 2
                    
                    if is_partial and sell_qty < buy_qty:
                        target_sheet.update(f"G{row_index}:L{row_index}", [
                            [int(sell_qty), int(invest_amt), now_str, int(sell_price), float(profit_rt), int(profit)]
                        ], value_input_option='USER_ENTERED')
                        target_sheet.update(f"B{row_index}", [["완료"]], value_input_option='USER_ENTERED')
                        
                        rem_qty = buy_qty - sell_qty
                        rem_invest = buy_price * rem_qty
                        new_trade_id = str(uuid.uuid4())[:8]
                        new_row = [new_trade_id, "보유", code, name, buy_time, int(buy_price), int(rem_qty), int(rem_invest), "", "", "", "", "부분매도"]
                        target_sheet.append_row(new_row, value_input_option='USER_ENTERED')
                    else:
                        target_sheet.update(f"I{row_index}:L{row_index}", [
                            [now_str, int(sell_price), float(profit_rt), int(profit)]
                        ], value_input_option='USER_ENTERED')
                        target_sheet.update(f"B{row_index}", [["완료"]], value_input_option='USER_ENTERED')
                    break
        except Exception as e:
            pass

def safe_request(method, url, gs_manager=None, **kwargs):
    for attempt in range(3):
        try:
            if method == 'GET':
                res = requests.get(url, **kwargs)
            else:
                res = requests.post(url, **kwargs)
            return res
        except requests.exceptions.Timeout:
            err_msg = f"[API Timeout] {url} 타임아웃 발생. 10초 후 재시도... ({attempt+1}/3)"
            write_log(err_msg)
            if gs_manager: gs_manager.log_error(err_msg)
            time.sleep(10)
        except Exception as e:
            err_msg = f"[API Error] {url} - {e}"
            write_log(err_msg)
            if gs_manager: gs_manager.log_error(err_msg)
            time.sleep(10)
    return None
