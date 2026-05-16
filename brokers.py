from abc import ABC, abstractmethod
import os
import json
import requests
from datetime import datetime, timedelta
import pandas as pd
import time

from utils import write_log, safe_request

class BaseBroker(ABC):
    @abstractmethod
    def get_access_token(self):
        pass

    @abstractmethod
    def place_order(self, code, qty, price=0, is_buy=True):
        pass

    @abstractmethod
    def get_balance(self):
        pass

    @abstractmethod
    def get_daily_prices(self, code, count=250):
        pass

    @abstractmethod
    def check_market_crash(self):
        pass

    @abstractmethod
    def scan_s_class_targets(self):
        pass


class KISBroker(BaseBroker):
    def __init__(self, app_key, app_secret, account_no, mode="0", gs_manager=None):
        self.app_key = app_key
        self.app_secret = app_secret
        self.account_no = account_no
        self.mode = mode
        self.gs_manager = gs_manager
        
        default_base = "https://openapi.koreainvestment.com:9443" if mode == "1" else "https://openapivts.koreainvestment.com:29443"
        self.base_url = os.getenv("KIS_BASE_URL", default_base)
        
        if "-" in self.account_no:
            self.cano, self.acnt_prdt_cd = self.account_no.split("-")
        else:
            self.cano = self.account_no[:8]
            self.acnt_prdt_cd = self.account_no[8:] if len(self.account_no) > 8 else "01"
            
        self.access_token = ""
        self.token_expire_time = datetime.now()

    def get_access_token(self):
        if self.access_token and datetime.now() < self.token_expire_time: 
            return self.access_token
        
        url = f"{self.base_url}/oauth2/tokenP"
        body = {"grant_type": "client_credentials", "appkey": self.app_key, "appsecret": self.app_secret}
        res = safe_request('POST', url, gs_manager=self.gs_manager, headers={"content-type": "application/json"}, data=json.dumps(body), timeout=10)
        if res and res.status_code == 200:
            self.access_token = res.json().get("access_token")
            self.token_expire_time = datetime.now() + timedelta(hours=20)
            write_log("신규 Access Token 발급 성공")
            return self.access_token
        return None

    def place_order(self, code, qty, price=0, is_buy=True):
        token = self.get_access_token()
        if not token: return False
        
        url = f"{self.base_url}/uapi/domestic-stock/v1/trading/order-cash"
        tr_id = "TTTC0802U" if self.mode == "1" and is_buy else "TTTC0801U" if self.mode == "1" else "VTTC0802U" if is_buy else "VTTC0801U"
        headers = {
            "content-type": "application/json", "authorization": f"Bearer {token}",
            "appkey": self.app_key, "appsecret": self.app_secret, "tr_id": tr_id
        }
        body = {"CANO": self.cano, "ACNT_PRDT_CD": self.acnt_prdt_cd, "PDNO": code, "ORD_DVSN": "01", "ORD_QTY": str(qty), "ORD_UNPR": str(price)}
        res = safe_request('POST', url, gs_manager=self.gs_manager, headers=headers, data=json.dumps(body), timeout=10)
        if res and res.status_code == 200:
            data = res.json()
            if data.get("rt_cd") == "0":
                side = "매수" if is_buy else "매도"
                write_log(f"[{side} 주문성공(시장가)] 종목: {code}, 수량: {qty}주")
                return True
            else:
                write_log(f"[주문 실패] rt_cd={data.get('rt_cd')}, 메시지={data.get('msg1')}, 종목={code}")
                if self.gs_manager: self.gs_manager.log_error(f"주문실패 {code}: {data.get('msg1')}")
        elif res:
            write_log(f"[주문 HTTP 오류] status={res.status_code}, body={res.text[:200]}")
        return False

    def get_balance(self):
        token = self.get_access_token()
        if not token: return None, None, None
        
        url = f"{self.base_url}/uapi/domestic-stock/v1/trading/inquire-balance"
        tr_id = "TTTC8434R" if self.mode == "1" else "VTTC8434R"
        headers = {
            "content-type": "application/json", "authorization": f"Bearer {token}",
            "appkey": self.app_key, "appsecret": self.app_secret, "tr_id": tr_id
        }
        params = {
            "CANO": self.cano, "ACNT_PRDT_CD": self.acnt_prdt_cd, "AFHR_FLPR_YN": "N", "OFL_YN": "",
            "INQR_DVSN": "02", "UNPR_DVSN": "01", "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N", "PRCS_DVSN": "00", "CTX_AREA_FK100": "", "CTX_AREA_NK100": ""
        }
        res = safe_request('GET', url, gs_manager=self.gs_manager, headers=headers, params=params, timeout=10)
        if res is None or res.status_code != 200:
            return None, None, None
            
        cash, tot_eval = 0, 0
        holdings = []
        data = res.json()
        out2 = data.get("output2", [])
        if out2:
            cash = int(out2[0].get("dnca_tot_amt", 0))
            tot_eval = int(out2[0].get("tot_evlu_amt", 0))
        for item in data.get("output1", []):
            qty = int(item.get('hldg_qty'))
            if qty > 0:
                holdings.append({
                    "code": item.get('pdno'),
                    "name": item.get('prdt_name'),
                    "qty": qty,
                    "buy_price": float(item.get('pchs_avg_pric')),
                    "current_price": float(item.get('prpr')),
                    "profit_rt": float(item.get('evlu_erng_rt'))
                })
        return cash, tot_eval, holdings

    def get_daily_prices(self, code, count=250):
        token = self.get_access_token()
        if not token: return []
        
        url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
        headers = {
            "content-type": "application/json", "authorization": f"Bearer {token}",
            "appkey": self.app_key, "appsecret": self.app_secret, "tr_id": "FHKST03010100", "custtype": "P"
        }
        
        now = datetime.now()
        end_date = now.strftime("%Y%m%d")
        
        result = []
        while len(result) < count:
            rem = count - len(result)
            start_date = (datetime.strptime(end_date, "%Y%m%d") - timedelta(days=rem*1.5)).strftime("%Y%m%d")
            
            params = {
                "FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code,
                "FID_INPUT_DATE_1": start_date, "FID_INPUT_DATE_2": end_date,
                "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0"
            }
            res = safe_request('GET', url, gs_manager=self.gs_manager, headers=headers, params=params, timeout=10)
            if not res or res.status_code != 200:
                break
                
            data = res.json().get("output2", [])
            if not data:
                break
                
            valid_data = [d for d in data if d.get('stck_bsop_date')]
            if not valid_data:
                break
                
            result.extend(valid_data)
            
            oldest_date_str = valid_data[-1]['stck_bsop_date']
            end_date = (datetime.strptime(oldest_date_str, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
            
            time.sleep(0.2)
            
        return result[:count]

    def check_market_crash(self):
        token = self.get_access_token()
        if not token: return False
        
        url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-daily-indexchartprice"
        headers = {
            "content-type": "application/json", "authorization": f"Bearer {token}",
            "appkey": self.app_key, "appsecret": self.app_secret, "tr_id": "FHKUP03500100", "custtype": "P"
        }
        today = datetime.now().strftime("%Y%m%d")
        params = {
            "FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": "0001",
            "FID_INPUT_DATE_1": today, "FID_INPUT_DATE_2": today,
            "FID_PERIOD_DIV_CODE": "D"
        }
        res = safe_request('GET', url, gs_manager=self.gs_manager, headers=headers, params=params, timeout=10)
        if res and res.status_code == 200:
            data = res.json().get("output1", {})
            if isinstance(data, list) and len(data) > 0:
                data = data[0]
            if data:
                idx_prpr = float(data.get("bstp_nmix_prpr", 0))
                idx_lwpr = float(data.get("bstp_nmix_lwpr", 0))
                if idx_prpr > 0 and idx_prpr <= idx_lwpr * 1.002: 
                    return True
        return False

    def is_s_class_target(self, df):
        if len(df) < 250: return False

        try:
            curr = df.iloc[-1]
            curr_high = float(curr['hgpr'])
            if curr_high <= 0:
                return False

            # 오늘을 제외한 이전 거래일의 고가만으로 52주 최고가 산출
            prior_highs = df['hgpr'].iloc[:-1]
            prior_highs_clean = prior_highs[prior_highs > 0]
            if prior_highs_clean.empty:
                return False

            high_52w = float(prior_highs_clean.max())

            # 조건 4: 52주 신고가 (오늘 고가가 이전 52주 최고가 이상)
            return curr_high >= high_52w
        except (KeyError, ValueError, TypeError) as e:
            write_log(f"[에러] 52주 신고가 검증 중 차트 데이터 오류: {e}")
            return False

    def scan_s_class_targets(self, target_date: str = None):
        """
        target_date: "YYYYMMDD" 형식 (None이면 오늘). 과거 날짜 백필 가능.
        """
        token = self.get_access_token()
        if not token: return []

        date_label = target_date or datetime.now().strftime("%Y%m%d")
        log_date_str = datetime.strptime(date_label, "%Y%m%d").strftime("%Y-%m-%d")
        write_log(f"[@Scanner] V3.1 S-Class 주도주 스캐닝 시작 (기준일: {log_date_str})")

        url_amt = f"{self.base_url}/uapi/domestic-stock/v1/quotations/volume-rank"
        headers = {
            "content-type": "application/json", "authorization": f"Bearer {token}",
            "appkey": self.app_key, "appsecret": self.app_secret, "custtype": "P",
            "tr_id": "FHPST01710000"
        }
        params = {
            "FID_COND_MRKT_DIV_CODE": "J", "FID_COND_SCR_DIV_CODE": "20171",
            "FID_INPUT_ISCD": "0000", "FID_DIV_CLS_CODE": "0",
            "FID_BLNG_CLS_CODE": "3",  # [핵심] 3: 거래금액순 (거래대금 상위 정렬)
            "FID_TRGT_CLS_CODE": "111111111", "FID_TRGT_EXLS_CLS_CODE": "000000",
            "FID_INPUT_PRICE_1": "", "FID_INPUT_PRICE_2": "", "FID_VOL_CNT": "",
            "FID_INPUT_DATE_1": target_date or ""
        }

        res = safe_request('GET', url_amt, gs_manager=self.gs_manager, headers=headers, params=params, timeout=10)
        raw_list = res.json().get("output", []) if res and res.status_code == 200 else []

        candidates = []
        for item in raw_list:
            code = item.get("mksc_shrn_iscd", "")
            name = item.get("hts_kor_isnm", "")
            price = float(item.get("stck_prpr", 0))
            vol = float(item.get("acml_vol", 0))
            amt = float(item.get("acml_tr_pbmn", 0))
            if amt == 0: amt = price * vol

            if any(keyword in name for keyword in ["ETF", "ETN", "스팩", "스펙"]):
                continue

            vol_rate = float(item.get("vol_inrt", 0))
            price_rate = float(item.get("prdy_ctrt", 0))

            if price >= 1000 and vol_rate >= 300.0 and price_rate >= 10.0:
                candidates.append({"code": code, "name": name, "price": price, "amt": amt})

        candidates.sort(key=lambda x: x["amt"], reverse=True)
        top_30 = candidates[:30]

        s_class_list = []

        for rank, cand in enumerate(top_30, start=1):
            code = cand["code"]
            name = cand["name"]
            daily_info = self.get_daily_prices(code, count=260)
            if len(daily_info) < 250: continue

            df = pd.DataFrame(daily_info)[::-1].reset_index(drop=True)
            df[['clpr', 'hgpr', 'amt', 'vol']] = df[['stck_clpr', 'stck_hgpr', 'acml_tr_pbmn', 'acml_vol']].apply(pd.to_numeric)

            if self.is_s_class_target(df):
                curr = df.iloc[-1]
                prev = df.iloc[-2]
                change_rate = (curr['clpr'] - prev['clpr']) / prev['clpr'] * 100
                amt_100m = int(curr['amt'] / 100_000_000)

                if self.gs_manager:
                    is_logged = self.gs_manager.log_lead_stock(
                        log_date_str, code, name, curr['clpr'],
                        round(change_rate, 2), amt_100m, rank, "S-Class 주도주 포착"
                    )
                    if is_logged:
                        write_log(f"[S-Class 확정] {name}({code}) - 시트 기록 완료.")
                s_class_list.append(cand)

            time.sleep(0.2)

        write_log(f"S-Class 스캐닝 완료 ({log_date_str}): 총 {len(s_class_list)} 종목 발굴")
        return s_class_list


class KiwoomAdapter(BaseBroker):
    def __init__(self, gs_manager=None):
        self.gs_manager = gs_manager
        
    def get_access_token(self):
        # Kiwoom uses COM objects, not tokens usually
        return "KIWOOM_LOGGED_IN"
        
    def place_order(self, code, qty, price=0, is_buy=True):
        # KOA Studio 기반 SendOrder 실행 스켈레톤
        write_log(f"[Kiwoom] {'매수' if is_buy else '매도'} 주문 스켈레톤: {code} {qty}주")
        return True

    def get_balance(self):
        return 0, 0, []

    def get_daily_prices(self, code, count=250):
        return []

    def check_market_crash(self):
        return False

    def scan_s_class_targets(self):
        return []
