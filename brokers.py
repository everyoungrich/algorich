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
    REAL_BASE_URL = "https://openapi.koreainvestment.com:9443"   # 실전: 시세조회·차트
    MOCK_BASE_URL = "https://openapivts.koreainvestment.com:29443"  # 모의: 주문·잔고

    def __init__(self, account_no, gs_manager=None,
                 real_app_key=None, real_app_secret=None,
                 mock_app_key=None, mock_app_secret=None):
        self.real_app_key    = real_app_key    or os.getenv("KIS_REAL_APP_KEY", "")
        self.real_app_secret = real_app_secret or os.getenv("KIS_REAL_APP_SECRET", "")
        self.mock_app_key    = mock_app_key    or os.getenv("KIS_MOCK_APP_KEY", "")
        self.mock_app_secret = mock_app_secret or os.getenv("KIS_MOCK_APP_SECRET", "")
        self.account_no = account_no
        self.gs_manager = gs_manager

        if "-" in self.account_no:
            self.cano, self.acnt_prdt_cd = self.account_no.split("-")
        else:
            self.cano = self.account_no[:8]
            self.acnt_prdt_cd = self.account_no[8:] if len(self.account_no) > 8 else "01"

        self._real_token = ""
        self._real_token_expire = datetime.now()
        self._mock_token = ""
        self._mock_token_expire = datetime.now()

    # ── 토큰 발급 (실전/모의 분리) ──────────────────────────────────

    def _get_real_token(self):
        if self._real_token and datetime.now() < self._real_token_expire:
            return self._real_token
        url = f"{self.REAL_BASE_URL}/oauth2/tokenP"
        body = {"grant_type": "client_credentials",
                "appkey": self.real_app_key, "appsecret": self.real_app_secret}
        res = safe_request('POST', url, gs_manager=self.gs_manager,
                           headers={"content-type": "application/json"},
                           data=json.dumps(body), timeout=10)
        if res and res.status_code == 200:
            self._real_token = res.json().get("access_token")
            self._real_token_expire = datetime.now() + timedelta(hours=20)
            write_log("실전 Access Token 발급 성공 (시세조회용)")
            return self._real_token
        return None

    def _get_mock_token(self):
        if self._mock_token and datetime.now() < self._mock_token_expire:
            return self._mock_token
        url = f"{self.MOCK_BASE_URL}/oauth2/tokenP"
        body = {"grant_type": "client_credentials",
                "appkey": self.mock_app_key, "appsecret": self.mock_app_secret}
        res = safe_request('POST', url, gs_manager=self.gs_manager,
                           headers={"content-type": "application/json"},
                           data=json.dumps(body), timeout=10)
        if res and res.status_code == 200:
            self._mock_token = res.json().get("access_token")
            self._mock_token_expire = datetime.now() + timedelta(hours=20)
            write_log("모의 Access Token 발급 성공 (주문실행용)")
            return self._mock_token
        return None

    def get_access_token(self):
        return self._get_real_token()

    # ── 헤더 빌더 ───────────────────────────────────────────────────

    def _real_headers(self, tr_id):
        return {
            "content-type": "application/json",
            "authorization": f"Bearer {self._get_real_token()}",
            "appkey": self.real_app_key,
            "appsecret": self.real_app_secret,
            "tr_id": tr_id,
            "custtype": "P"
        }

    def _mock_headers(self, tr_id):
        return {
            "content-type": "application/json",
            "authorization": f"Bearer {self._get_mock_token()}",
            "appkey": self.mock_app_key,
            "appsecret": self.mock_app_secret,
            "tr_id": tr_id
        }

    # ── 주문·잔고 (모의 키 사용) ────────────────────────────────────

    def place_order(self, code, qty, price=0, is_buy=True):
        if not self._get_mock_token(): return False

        url = f"{self.MOCK_BASE_URL}/uapi/domestic-stock/v1/trading/order-cash"
        tr_id = "VTTC0802U" if is_buy else "VTTC0801U"
        body = {
            "CANO": self.cano, "ACNT_PRDT_CD": self.acnt_prdt_cd,
            "PDNO": code, "ORD_DVSN": "01",
            "ORD_QTY": str(qty), "ORD_UNPR": str(price)
        }
        res = safe_request('POST', url, gs_manager=self.gs_manager,
                           headers=self._mock_headers(tr_id),
                           data=json.dumps(body), timeout=10)
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
        if not self._get_mock_token(): return None, None, None

        url = f"{self.MOCK_BASE_URL}/uapi/domestic-stock/v1/trading/inquire-balance"
        params = {
            "CANO": self.cano, "ACNT_PRDT_CD": self.acnt_prdt_cd,
            "AFHR_FLPR_YN": "N", "OFL_YN": "", "INQR_DVSN": "02",
            "UNPR_DVSN": "01", "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N", "PRCS_DVSN": "00",
            "CTX_AREA_FK100": "", "CTX_AREA_NK100": ""
        }
        res = safe_request('GET', url, gs_manager=self.gs_manager,
                           headers=self._mock_headers("VTTC8434R"),
                           params=params, timeout=10)
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

    # ── 시세조회·차트 (실전 키 사용) ────────────────────────────────

    def get_daily_prices(self, code, count=250, end_date=None):
        if not self._get_real_token(): return []

        url = f"{self.REAL_BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
        end_date = end_date or datetime.now().strftime("%Y%m%d")
        result = []

        while len(result) < count:
            rem = count - len(result)
            start_date = (datetime.strptime(end_date, "%Y%m%d") - timedelta(days=rem * 1.5)).strftime("%Y%m%d")
            params = {
                "FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code,
                "FID_INPUT_DATE_1": start_date, "FID_INPUT_DATE_2": end_date,
                "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0"
            }
            res = safe_request('GET', url, gs_manager=self.gs_manager,
                               headers=self._real_headers("FHKST03010100"),
                               params=params, timeout=10)
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
        if not self._get_real_token(): return False

        url = f"{self.REAL_BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-daily-indexchartprice"
        today = datetime.now().strftime("%Y%m%d")
        params = {
            "FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": "0001",
            "FID_INPUT_DATE_1": today, "FID_INPUT_DATE_2": today,
            "FID_PERIOD_DIV_CODE": "D"
        }
        res = safe_request('GET', url, gs_manager=self.gs_manager,
                           headers=self._real_headers("FHKUP03500100"),
                           params=params, timeout=10)
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
        """target_date: 'YYYYMMDD' (None이면 오늘). 과거 날짜 백필 가능."""
        if not self._get_real_token(): return []

        date_label = target_date or datetime.now().strftime("%Y%m%d")
        log_date_str = datetime.strptime(date_label, "%Y%m%d").strftime("%Y-%m-%d")
        write_log(f"[@Scanner] V3.1 S-Class 주도주 스캐닝 시작 (기준일: {log_date_str})")

        url_amt = f"{self.REAL_BASE_URL}/uapi/domestic-stock/v1/quotations/volume-rank"
        params = {
            "FID_COND_MRKT_DIV_CODE": "J", "FID_COND_SCR_DIV_CODE": "20171",
            "FID_INPUT_ISCD": "0000", "FID_DIV_CLS_CODE": "0",
            "FID_BLNG_CLS_CODE": "3",  # [핵심] 3: 거래금액순 (거래대금 상위 정렬)
            "FID_TRGT_CLS_CODE": "111111111", "FID_TRGT_EXLS_CLS_CODE": "000000",
            "FID_INPUT_PRICE_1": "", "FID_INPUT_PRICE_2": "", "FID_VOL_CNT": "",
            "FID_INPUT_DATE_1": target_date or ""
        }
        res = safe_request('GET', url_amt, gs_manager=self.gs_manager,
                           headers=self._real_headers("FHPST01710000"),
                           params=params, timeout=10)
        # API는 거래대금순 정렬로 반환 — 즉시 상위 30개로 자름 (진짜 시장 거래대금 상위 30위)
        raw_list = (res.json().get("output", []) if res and res.status_code == 200 else [])[:30]
        write_log(f"[거래대금 상위 30위] API 수신: {len(raw_list)}종목")

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
        write_log(f"[필터 통과] vol>=300%+등락>=10%+1000원↑: {len(candidates)}종목 → 52주 신고가 검증 시작")

        s_class_list = []
        for rank, cand in enumerate(candidates, start=1):
            code = cand["code"]
            name = cand["name"]
            daily_info = self.get_daily_prices(code, count=260, end_date=target_date)
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


    # ── Naver Finance 거래대금 순위 파싱 (historical용) ──────────────────

    def _naver_top_by_amount(self, target_date: str, top_n: int = 40) -> list:
        """Naver Finance 거래대금 순위 페이지에서 비ETF 상위 종목 반환 (KOSPI+KOSDAQ)"""
        import re

        ETF_KEYWORDS = [
            "ETF", "ETN", "KODEX", "TIGER", "KBSTAR", "HANARO", "SOL",
            "ACE", "ARIRANG", "KOSEF", "인버스", "레버리지", "스팩", "SPAC",
        ]
        all_items = []

        for sosok in [0, 1]:
            url = (f"https://finance.naver.com/sise/sise_quant.nhn"
                   f"?sosok={sosok}&date={target_date}")
            try:
                r = requests.get(url, timeout=10, headers={
                    "User-Agent": "Mozilla/5.0",
                    "Referer": "https://finance.naver.com/",
                })
                if not r.ok:
                    write_log(f"[Naver] sosok={sosok} 로드 실패: {r.status_code}")
                    continue
                text = r.content.decode("euc-kr", errors="replace")
            except Exception as e:
                write_log(f"[Naver] sosok={sosok} 요청 오류: {e}")
                continue

            for row in text.split('<td class="no">')[1:]:
                code_m = re.search(r'code=(\d{6})', row)
                name_m = re.search(r'class="tltle">([^<]+)</a>', row)
                if not code_m or not name_m:
                    continue
                code = code_m.group(1)
                name = name_m.group(1).strip()

                if any(k in name for k in ETF_KEYWORDS):
                    continue

                # 순수 숫자 컬럼: 현재가(0) 거래량(1) 거래대금(2)
                nums = re.findall(r'<td class="number">([0-9,]+)</td>', row)
                rate_m = re.search(r'([-+]?[\d]+\.[\d]+)%', row)
                if len(nums) < 3:
                    continue
                try:
                    price  = int(nums[0].replace(",", ""))
                    amount = int(nums[2].replace(",", ""))  # 거래대금 (백만원)
                    rate   = float(rate_m.group(1)) if rate_m else 0.0
                except (ValueError, IndexError):
                    continue

                if price < 1000:
                    continue

                all_items.append({
                    "code": code, "name": name,
                    "price": price, "rate": rate, "amount": amount,
                })

            time.sleep(0.3)

        all_items.sort(key=lambda x: x["amount"], reverse=True)
        return all_items[:top_n]

    def scan_s_class_targets_historical(self, target_date: str) -> list:
        """Naver Finance 거래대금 순위 + KIS 일봉으로 과거 날짜 S-Class 스캐닝"""
        if not self._get_real_token():
            return []

        log_date_str = datetime.strptime(target_date, "%Y%m%d").strftime("%Y-%m-%d")
        write_log(f"[@HistScanner] S-Class 과거 스캐닝 시작 (기준일: {log_date_str})")

        # Step1: Naver 거래대금 상위 40개 비ETF 종목
        top_stocks = self._naver_top_by_amount(target_date, top_n=40)
        write_log(f"[Naver 거래대금 상위] 비ETF {len(top_stocks)}종목 확보")

        s_class_list = []
        for rank, stock in enumerate(top_stocks, start=1):
            code   = stock["code"]
            name   = stock["name"]
            amount = stock["amount"]  # 백만원

            # Step2: KIS 일봉 (기준일 포함 260일)
            daily_info = self.get_daily_prices(code, count=260, end_date=target_date)
            if len(daily_info) < 2:
                continue

            df = pd.DataFrame(daily_info)[::-1].reset_index(drop=True)
            df[["clpr", "hgpr", "acml_vol"]] = (
                df[["stck_clpr", "stck_hgpr", "acml_vol"]].apply(pd.to_numeric, errors="coerce")
            )

            curr = df.iloc[-1]
            # 기준일 일봉이 target_date인지 확인 (공휴일/비거래일 방어)
            if str(curr.get("stck_bsop_date", "")) != target_date:
                continue

            prev = df.iloc[-2]

            # 조건 ① 거래량 전일비 300% 이상
            curr_vol = float(curr["acml_vol"] or 0)
            prev_vol = float(prev["acml_vol"] or 1)
            vol_inrt = curr_vol / prev_vol * 100
            if vol_inrt < 300.0:
                continue

            # 조건 ② 등락률 10% 이상
            curr_clpr = float(curr["clpr"] or 0)
            prev_clpr = float(prev["clpr"] or 1)
            price_rate = (curr_clpr - prev_clpr) / prev_clpr * 100
            if price_rate < 10.0:
                continue

            # 조건 ③ 52주 신고가 (250일 이상 필요)
            if len(df) < 250:
                continue
            if not self.is_s_class_target(df):
                continue

            amt_100m = amount // 100  # 백만원 → 억원

            if self.gs_manager:
                is_logged = self.gs_manager.log_lead_stock(
                    log_date_str, code, name, int(curr_clpr),
                    round(price_rate, 2), amt_100m, rank,
                    f"S-Class 주도주 포착 (Naver백필 {target_date})"
                )
                if is_logged:
                    write_log(
                        f"[S-Class 확정] {name}({code}) "
                        f"vol={vol_inrt:.0f}%, 등락={price_rate:.1f}%, "
                        f"거래대금={amt_100m}억"
                    )

            s_class_list.append(stock)
            time.sleep(0.3)

        write_log(f"[HistScanner] 완료 ({log_date_str}): {len(s_class_list)}종목 발굴")
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
