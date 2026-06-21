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

_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trade_log.txt")

def write_log(message):
    now_str = datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")
    log_msg = f"{now_str} {message}"
    print(log_msg)
    with open(_LOG_PATH, "a", encoding="utf-8") as f:
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

    # ── 시트 헤더 ───────────────────────────────────────────────────────
    LOG_SHEET_HEADER = [
        "날짜", "종목코드", "종목명", "거래대금(억)",
        "등락률(%)", "거래량비(%)", "52주신고가", "LeaderScore", "비고",
    ]
    AUDIT_SHEET_HEADER = [
        "날짜", "종목코드", "종목명", "거래대금(억)",
        "거래대금30위", "등락률(%)", "등락률10%통과",
        "거래량비(%)", "거래량200%통과", "52주신고가",
        "LeaderScore", "최종결과", "탈락사유",
    ]
    SUMMARY_SHEET_HEADER = [
        "날짜", "거래대금30위", "ETF제외후",
        "10%상승통과", "거래량200%통과", "52주신고가통과", "최종선정",
    ]

    NH_HUNTER_SHEET_HEADER = [
        "날짜", "종목코드", "종목명",
        "거래대금순위", "거래대금(억)",
        "상승률(%)", "거래량비(%)",
        "52주최고가", "52주고가이격률(%)",
        "최근7일고가", "7일고가이격률(%)",
        "윗꼬리비율(%)",
        "TOP30", "상승10%", "신고가90%", "7일박스", "윗꼬리3%", "거래량150%",
        # V1.1 추가: 섹터 분석 6컬럼
        "섹터명", "주도섹터", "섹터내순위", "섹터종목수", "상한가여부", "후발주여부",
        "통과여부", "탈락사유",
    ]

    def get_nh_hunter_sheet(self):
        try:
            return self.log_wb.worksheet("NH_Hunter_Audit")
        except gspread.exceptions.WorksheetNotFound:
            ws = self.log_wb.add_worksheet(title="NH_Hunter_Audit", rows=5000, cols=27)
            ws.append_row(self.NH_HUNTER_SHEET_HEADER)
            return ws

    def log_nh_hunter_batch(self, date_str, entries):
        """NH_Hunter_Audit 시트에 NH-Hunter 평가 결과 배치 기록."""
        if not self.initialized or not entries:
            return
        try:
            sheet = self.get_nh_hunter_sheet()
            rows = []
            for e in entries:
                nh = e.get("nh_hunter", {})
                rows.append([
                    date_str,
                    e.get("code", ""),
                    e.get("name", ""),
                    e.get("mkt_rank", ""),
                    e.get("amt_100m", ""),
                    round(float(e.get("change_rate", 0)), 2),
                    round(float(e.get("vol_ratio",   0)), 1),
                    nh.get("h52", ""),
                    nh.get("h52_gap_pct", ""),
                    nh.get("h7d", ""),
                    nh.get("h7d_gap_pct", ""),
                    nh.get("wick_pct", ""),
                    "Y" if nh.get("cond_top30")   else "N",
                    "Y" if nh.get("cond_rate10")  else "N",
                    "Y" if nh.get("cond_nh90")    else "N",
                    "Y" if nh.get("cond_box7")    else "N",
                    "Y" if nh.get("cond_wick3")   else "N",
                    "Y" if nh.get("cond_vol150")  else "N",
                    # V1.1: 섹터 분석 6컬럼
                    nh.get("sector", "기타"),
                    "Y" if nh.get("cond_sector")       else "N",
                    nh.get("sector_rank", 0),
                    nh.get("leading_sector_cnt", 0),
                    "Y" if nh.get("is_upper_limit")    else "N",
                    "Y" if nh.get("is_sector_fallback") else "N",
                    "PASS" if nh.get("pass_all") else "FAIL",
                    ", ".join(nh.get("fail_reasons", [])),
                ])
            sheet.append_rows(rows, value_input_option="USER_ENTERED")
        except Exception as ex:
            write_log(f"[log_nh_hunter_batch 오류] {repr(ex)}")

    def get_log_sheet(self, tab_name="0_주도주_Log"):
        try:
            return self.log_wb.worksheet(tab_name)
        except gspread.exceptions.WorksheetNotFound:
            ws = self.log_wb.add_worksheet(title=tab_name, rows=2000, cols=10)
            ws.append_row(self.LOG_SHEET_HEADER)
            return ws

    def get_audit_sheet(self):
        try:
            return self.log_wb.worksheet("1_Scan_Audit_Log")
        except gspread.exceptions.WorksheetNotFound:
            ws = self.log_wb.add_worksheet(title="1_Scan_Audit_Log", rows=5000, cols=14)
            ws.append_row(self.AUDIT_SHEET_HEADER)
            return ws

    def get_summary_sheet(self):
        try:
            return self.log_wb.worksheet("2_Scan_Summary_Log")
        except gspread.exceptions.WorksheetNotFound:
            ws = self.log_wb.add_worksheet(title="2_Scan_Summary_Log", rows=1000, cols=8)
            ws.append_row(self.SUMMARY_SHEET_HEADER)
            return ws
    def log_lead_stock(self, date_str, ticker, name, amt_100m, change_rate, vol_ratio,
                       is_new_high, leader_score=None, remark="S-Class"):
        """0_주도주_Log: 최종 S-Class 선정 종목 기록."""
        if not self.initialized:
            return False
        log_key = (date_str, ticker)
        if log_key in self.logged_today:
            return False
        row = [
            date_str, ticker, name,
            int(amt_100m), round(float(change_rate), 2), round(float(vol_ratio), 1),
            "Y" if is_new_high else "N",
            int(leader_score) if leader_score is not None else "",
            remark,
        ]
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

    def log_audit_batch(self, date_str, audit_entries):
        """1_Scan_Audit_Log: 스캔 30종목 전체 배치 기록."""
        if not self.initialized or not audit_entries:
            return
        try:
            sheet = self.get_audit_sheet()
            rows = []
            for e in audit_entries:
                chg = float(e.get("change_rate", 0))
                vol = float(e.get("vol_ratio", 0))
                rows.append([
                    date_str, e.get("code",""), e.get("name",""),
                    e.get("amt_100m",""), "Y",
                    round(chg,2), "Y" if chg>=10.0 else "N",
                    round(vol,1), "Y" if vol>=200.0 else "N",
                    e.get("new_high","-"), e.get("leader_score","-"),
                    e.get("result",""), e.get("fail_reason",""),
                ])
            sheet.append_rows(rows, value_input_option='USER_ENTERED')
        except Exception as ex:
            write_log(f"[log_audit_batch 오류] {repr(ex)}")

    def log_scan_summary(self, date_str, cnt_top30, cnt_etf_excl,
                         cnt_10pct, cnt_200vol, cnt_52wk, cnt_selected):
        """2_Scan_Summary_Log: 일별 단계 카운트 기록."""
        if not self.initialized:
            return
        try:
            sheet = self.get_summary_sheet()
            sheet.append_row(
                [date_str, cnt_top30, cnt_etf_excl,
                 cnt_10pct, cnt_200vol, cnt_52wk, cnt_selected],
                value_input_option='USER_ENTERED'
            )
        except Exception as ex:
            write_log(f"[log_scan_summary 오류] {repr(ex)}")
    def get_past_lead_stocks(self, days_ago_list=[1, 2]):
        """과거 주도주 종목 코드 반환. get_all_values() 사용 → 헤더 중복 무관."""
        if not self.initialized:
            return []
        try:
            lead_sheet = self.get_log_sheet("0_주도주_Log")
            all_values = lead_sheet.get_all_values()
            if len(all_values) <= 1:
                return []
            data_rows = all_values[1:]
            today_str = datetime.now().strftime("%Y-%m-%d")
            dates = sorted({
                row[0] for row in data_rows
                if len(row) > 0 and row[0] and row[0] < today_str
            })
            selected_dates = set()
            for d in days_ago_list:
                if d > 0 and len(dates) >= d:
                    selected_dates.add(dates[-d])
            past_stocks = []
            seen = set()
            for row in data_rows:
                if len(row) < 3:
                    continue
                date_val = row[0]
                code_val = str(row[1]).zfill(6) if row[1] else ""
                name_val = row[2]
                if date_val in selected_dates and code_val and code_val != "000000":
                    key = (code_val, date_val)
                    if key not in seen:
                        past_stocks.append({"code": code_val, "name": name_val, "date": date_val})
                        seen.add(key)
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
            write_log(f"[log_buy 실패] {code} {strategy_type}: {repr(e)}")

    def log_error(self, message):
        if not self.initialized: return
        now_str = datetime.now().strftime("%Y-%m-%d")
        trade_id = str(uuid.uuid4())[:8]
        row = [trade_id, "시스템로그", "-", "-", now_str, 0, 0, 0, "", "", "", "", message]
        try:
            target_sheet = self.get_main_sheet("1D_RB")
            target_sheet.append_row(row, value_input_option='USER_ENTERED')
        except Exception as e:
            write_log(f"[log_error 시트기록 실패] {repr(e)}")

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
            write_log(f"[log_sell 실패] {code} {strategy_type}: {repr(e)}")

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
