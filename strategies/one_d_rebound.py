import math
import time
import uuid
import pandas as pd

from utils import write_log
from .base import BaseStrategy


class OneDReboundStrategy(BaseStrategy):
    """
    1D-Rebound: 종가베팅 단기 반등 (신정재 특강 기반)

    핵심 원리 (PDF 정리):
    - 오후 3:00~3:20 매매 검토 -> 다음날 갭 상승 또는 장 초반 시세에 매도
    - 유형1: 오늘 시세가 내일까지 이어진다고 판단 -> 종가매수
    - 유형2: 지난 시세가 조정 후 상승 가능 -> 종가매수

    종목 선정 조건 (AND 조건):
    [1] 거래대금 급증 - 오늘 거래대금 >= 최근 5일 평균 * 1.5배
    [2] 양봉 + 위꼬리 없음 - 종가 > 시가, 위꼬리 <= 15%
    [3] 정배열 (MA5 > MA20) - 역배열 차단
    [4] 현재가 > MA5 (지지선 위)
    [5] 과열 아님 - MA5 <= MA20 * 1.20 (MA 갭 20% 미만)
    [6] (소프트) 기관/외인 양매수 우선순위

    매도 규칙 (PDF 핵심):
    A. 갭 상승 익절: 다음날 09:00 현재가 > 매수가 +1% -> 즉시 전량 매도
    B. 갭 하락 손절: 다음날 09:00 현재가 < 매수가 -2% -> 즉시 손절
    C. 장 중 손절: 현재가 < 매수가 * 0.97 (-3%) -> 즉시 전량 매도
    D. 강제 청산: 09:10까지 미처분 -> 전량 강제 매도 (오버나잇 금지)

    손절 철칙 (PDF):
    ★ 시간외(대체거래)에서 하락하면 매도
    ★ 매수 평단 아래로 내려가면 매도
    ★ 빠리 손절한다!
    """
    name = "1D-Rebound"

    def __init__(self, broker, gs_manager=None, mode="모의"):
        super().__init__(broker, gs_manager)
        self.mode = mode
        self.bought_today = set()
        self.buy_candidates = []

        self.has_executed_morning_reset  = False
        self.has_executed_morning_report = False
        self.has_executed_morning_sell   = False
        self.has_executed_force_close    = False
        self.has_executed_entry_check    = False
        self.has_executed_entry_buy      = False

    def reset_daily_state(self):
        self.has_executed_morning_reset  = False
        self.has_executed_morning_report = False
        self.has_executed_morning_sell   = False
        self.has_executed_force_close    = False
        self.has_executed_entry_check    = False
        self.has_executed_entry_buy      = False
        self.bought_today.clear()
        self.buy_candidates.clear()
        if self.gs_manager:
            self.gs_manager.logged_today.clear()

    def _to_df(self, daily_data, col_map):
        df = pd.DataFrame(daily_data)[::-1].reset_index(drop=True)
        for src, dst in col_map.items():
            if src in df.columns:
                df[dst] = pd.to_numeric(df[src], errors='coerce').fillna(0)
        return df

    def check_buy_condition(self, code, daily_data, inst_net_amt=None):
        """
        1D-Rebound 종가베팅 매수 조건 (AND 조건).
        Returns: (bool, str)
        """
        if len(daily_data) < 22:
            return False, "FAIL: LACK_OF_DATA (<22일)"

        df = self._to_df(daily_data, {
            'stck_oprc':    'oprc',
            'stck_hgpr':    'hgpr',
            'stck_lwpr':    'lwpr',
            'stck_clpr':    'clpr',
            'acml_tr_pbmn': 'trd_amt',
            'acml_vol':     'vol',
        })

        curr = df.iloc[-1]
        oprc = curr['oprc']
        hgpr = curr['hgpr']
        lwpr = curr['lwpr']
        clpr = curr['clpr']

        if clpr <= 0 or oprc <= 0 or hgpr <= 0:
            return False, "FAIL: PRICE_ZERO"

        # [1] 거래대금 급증
        trd_note = " 거래대금:N/A"
        avg_trd_5d = df['trd_amt'].iloc[-6:-1].mean()
        if avg_trd_5d > 0:
            today_trd = curr['trd_amt']
            trd_ratio = today_trd / avg_trd_5d
            if trd_ratio < 1.5:
                return False, (
                    "FAIL: TRADE_AMT_LOW "
                    "(오늘:{:.0f}백만 5일평균:{:.0f}백만 비율:{:.2f}배)".format(
                        today_trd, avg_trd_5d, trd_ratio)
                )
            trd_note = " 거래대금:{:.1f}배".format(trd_ratio)

        # [2] 양봉 + 위꼬리 <= 15%
        if clpr <= oprc:
            return False, "FAIL: NOT_YANGBONG (시가:{:,.0f} 종가:{:,.0f})".format(oprc, clpr)

        body_range = hgpr - lwpr
        upper_shadow_ratio = 0.0
        if body_range > 0:
            upper_shadow_ratio = (hgpr - clpr) / body_range
            if upper_shadow_ratio > 0.15:
                return False, (
                    "FAIL: UPPER_SHADOW_HIGH "
                    "(위꼬리:{:.1%} > 15%, 고:{:,.0f} 종:{:,.0f} 저:{:,.0f})".format(
                        upper_shadow_ratio, hgpr, clpr, lwpr)
                )

        # [3] 정배열 (MA5 > MA20)
        ma5  = df['clpr'].tail(5).mean()
        ma20 = df['clpr'].tail(20).mean()
        if ma5 <= ma20:
            return False, "FAIL: NOT_ALIGNED (MA5:{:,.0f} <= MA20:{:,.0f})".format(ma5, ma20)

        # [4] 현재가 > MA5
        if clpr <= ma5:
            return False, "FAIL: BELOW_MA5 (종가:{:,.0f} <= MA5:{:,.0f})".format(clpr, ma5)

        # [5] 과열 아님 (MA 갭 20% 미만)
        ma_gap_pct = (ma5 - ma20) / ma20 * 100
        if ma_gap_pct > 20.0:
            return False, "FAIL: OVERHEAT (MA5가 MA20 대비 +{:.1f}% 과열)".format(ma_gap_pct)

        # [6] 기관/외인 소프트 필터
        inst_note = ""
        if inst_net_amt is not None:
            inst_eok = inst_net_amt / 100
            if inst_eok >= 0:
                inst_note = " 기관순매수:{:.0f}억✅".format(inst_eok)
            else:
                inst_note = " 기관순매도:{:.0f}억⚠".format(abs(inst_eok))

        return True, (
            "OK (위꼬리:{:.1%}{} MA5:{:,.0f} MA20:{:,.0f} MA갭:{:.1f}%{})".format(
                upper_shadow_ratio, trd_note, ma5, ma20, ma_gap_pct, inst_note)
        )

    def run_entry_check(self):
        """15:15 - S-Class 관심종목 대상 종가베팅 후보 탐색"""
        cash, tot_eval, holdings = self.broker.get_balance()
        if tot_eval is None:
            write_log("[1D-Rebound] 잔고 조회 실패, 진입 체크 스킵")
            return

        holding_codes = {h["code"] for h in holdings}

        budget_per_stock = tot_eval * 0.25
        cash_reserve     = tot_eval * 0.25
        max_positions    = 3

        can_buy = max(0, max_positions - len(holding_codes))
        available_cash = max(0, cash - cash_reserve)
        slots_by_cash = int(available_cash / budget_per_stock) if budget_per_stock > 0 else 0
        can_buy = min(can_buy, slots_by_cash)

        if can_buy <= 0:
            write_log(
                "[1D-Rebound] 매수 슬롯 없음 "
                "(보유:{}/{}종목, 현금:{:,.0f}원, 예비:{:,}원)".format(
                    len(holding_codes), max_positions, cash, int(cash_reserve))
            )
            return

        watchlist = self.gs_manager.get_past_lead_stocks([0, 1]) if self.gs_manager else []
        if not watchlist:
            write_log("[1D-Rebound] S-Class 관심종목 없음, 매수 스킵")
            return

        candidates = []
        for stock in watchlist:
            code = stock["code"]
            name = stock["name"]
            if code in holding_codes or code in self.bought_today:
                continue

            daily_data = self.broker.get_daily_prices(code, count=25)

            inst_net_amt = None
            try:
                inst_net_amt = self.broker.get_inst_net_amount(code)
            except AttributeError:
                pass

            ok, reason = self.check_buy_condition(code, daily_data, inst_net_amt=inst_net_amt)
            write_log("[1D-Rebound 매수체크] {}({}): {}".format(name, code, reason))

            if ok and daily_data:
                curr_price = float(daily_data[0].get('stck_clpr', 0))
                if curr_price > 0:
                    qty = math.floor(budget_per_stock / curr_price)
                    if qty > 0:
                        candidates.append({
                            "code": code, "name": name,
                            "price": curr_price, "qty": qty,
                        })
            time.sleep(0.3)

        self.buy_candidates = candidates[:can_buy]
        write_log(
            "[1D-Rebound] 매수 후보 {}종목 확정 -> 15:20 시장가 실행 예정".format(
                len(self.buy_candidates))
        )

    def execute_buy(self):
        """15:20 - 매수 후보 시장가 주문"""
        if not self.buy_candidates:
            write_log("[1D-Rebound] 15:20 매수 후보 없음")
            return

        for cand in self.buy_candidates:
            code  = cand["code"]
            name  = cand["name"]
            qty   = cand["qty"]
            price = cand["price"]
            write_log("[1D-Rebound 매수] {}({}) {}주 시장가 주문 (기준가:{:,.0f})".format(
                name, code, qty, price))
            if self.broker.place_order(code, qty, is_buy=True):
                trade_id = str(uuid.uuid4())[:8]
                if self.gs_manager:
                    self.gs_manager.log_buy(
                        trade_id, code, name, price, qty,
                        "1D_RB", self.mode
                    )
                self.bought_today.add(code)
            time.sleep(0.5)

        self.buy_candidates.clear()

    def monitor_and_sell(self):
        """장 중 손절 모니터링: -3% 이탈 즉시 전량 손절"""
        _, _, holdings = self.broker.get_balance()
        if holdings is None:
            write_log("[1D-Rebound] 잔고 조회 불가, 모니터링 유예")
            return

        for h in holdings:
            code      = h["code"]
            name      = h["name"]
            buy_price = h["buy_price"]
            cur_price = h["current_price"]
            qty       = h["qty"]

            if buy_price <= 0:
                continue

            profit_rt = (cur_price - buy_price) / buy_price * 100

            if profit_rt <= -3.0:
                write_log("[1D-Rebound 손절] {}({}) {:.2f}% -> {}주 전량 즉시 매도".format(
                    name, code, profit_rt, qty))
                if self.broker.place_order(code, qty, is_buy=False):
                    if self.gs_manager:
                        self.gs_manager.log_sell(
                            code, cur_price, qty,
                            is_partial=False, strategy_type="1D_RB"
                        )
            elif profit_rt <= -2.0:
                write_log("[1D-Rebound 경고] {}({}) {:.2f}% - 매수 평단 근접".format(
                    name, code, profit_rt))

            time.sleep(0.3)

    def morning_sell(self):
        """
        다음날 09:00 갭 확인:
        +1% 이상 갭상승 -> 즉시 전량 익절
        -2% 이하 갭하락 -> 즉시 전량 손절
        그 사이           -> 09:10 강제 청산 대기
        """
        _, _, holdings = self.broker.get_balance()
        if not holdings:
            write_log("[1D-Rebound] 09:00 보유 종목 없음")
            return

        for h in holdings:
            code      = h["code"]
            name      = h["name"]
            buy_price = h["buy_price"]
            cur_price = h["current_price"]
            qty       = h["qty"]

            if buy_price <= 0:
                continue

            profit_rt = (cur_price - buy_price) / buy_price * 100

            if profit_rt >= 1.0:
                write_log("[1D-Rebound 익절] {}({}) 갭상승 +{:.2f}% -> {}주 전량 매도 ✅".format(
                    name, code, profit_rt, qty))
                if self.broker.place_order(code, qty, is_buy=False):
                    if self.gs_manager:
                        self.gs_manager.log_sell(
                            code, cur_price, qty,
                            is_partial=False, strategy_type="1D_RB"
                        )
            elif profit_rt <= -2.0:
                write_log("[1D-Rebound 갭하락손절] {}({}) {:.2f}% -> {}주 즉시 손절".format(
                    name, code, profit_rt, qty))
                if self.broker.place_order(code, qty, is_buy=False):
                    if self.gs_manager:
                        self.gs_manager.log_sell(
                            code, cur_price, qty,
                            is_partial=False, strategy_type="1D_RB"
                        )
            else:
                write_log("[1D-Rebound 관망] {}({}) {:.2f}% - 09:10 강제 청산 대기".format(
                    name, code, profit_rt))

            time.sleep(0.3)

    def force_close(self):
        """09:10 강제 청산: 오버나잇 절대 금지 - 미처분 전량 시장가 매도"""
        _, _, holdings = self.broker.get_balance()
        if not holdings:
            return

        for h in holdings:
            code  = h["code"]
            name  = h["name"]
            qty   = h["qty"]
            price = h["current_price"]
            write_log("[1D-Rebound 강제청산] {}({}) 09:10 미처분 -> {}주 전량 강제 매도".format(
                name, code, qty))
            if self.broker.place_order(code, qty, is_buy=False):
                if self.gs_manager:
                    self.gs_manager.log_sell(
                        code, price, qty,
                        is_partial=False, strategy_type="1D_RB"
                    )
            time.sleep(0.3)

    def tick(self, now):
        """
        타임라인:
          00:00 자정   -> 일일 상태 초기화
          08:20        -> 세션 초기화
          08:30        -> 장 개시 준비 로그
          09:00 (1회)  -> 전날 포지션 갭 확인 -> 익절/손절
          09:10 (1회)  -> 미처분 강제 청산 (오버나잇 금지)
          09:10~15:14  -> 30초마다 손절 모니터링 (-3%)
          15:15 (1회)  -> 당일 종가베팅 후보 탐색
          15:20 (1회)  -> 시장가 매수 실행
        """
        if now.hour == 0 and now.minute == 0 and now.second < 5:
            self.reset_daily_state()

        if now.hour == 8 and now.minute >= 20 and not self.has_executed_morning_reset:
            write_log("[@1D-Rebound] 08:20 세션 초기화")
            self.has_executed_morning_reset = True

        if now.hour == 8 and now.minute >= 30 and not self.has_executed_morning_report:
            write_log("[@1D-Rebound] 장 개시 대기 중 (전날 포지션 09:00 갭 확인 예정)")
            self.has_executed_morning_report = True

        if now.hour == 9 and now.minute == 0 and not self.has_executed_morning_sell:
            write_log("[@1D-Rebound] 09:00 갭 확인 매도 시작 ({})".format(now.strftime('%H:%M:%S')))
            self.morning_sell()
            self.has_executed_morning_sell = True

        if now.hour == 9 and now.minute == 10 and not self.has_executed_force_close:
            write_log("[@1D-Rebound] 09:10 미처분 강제 청산 ({})".format(now.strftime('%H:%M:%S')))
            self.force_close()
            self.has_executed_force_close = True

        in_intraday = (
            (now.hour == 9 and now.minute >= 10) or
            (10 <= now.hour <= 14) or
            (now.hour == 15 and now.minute < 15)
        )
        if in_intraday and now.second % 30 == 0:
            self.monitor_and_sell()

        if now.hour == 15 and now.minute == 15 and not self.has_executed_entry_check:
            write_log("[@1D-Rebound] 15:15 종가베팅 후보 탐색 ({})".format(now.strftime('%H:%M:%S')))
            if not self.broker.check_market_crash():
                self.run_entry_check()
            else:
                write_log("[1D-Rebound] 코스피 급락 감지 - 당일 종가베팅 중단")
            self.has_executed_entry_check = True

        if now.hour == 15 and now.minute == 20 and not self.has_executed_entry_buy:
            write_log("[@1D-Rebound] 15:20 시장가 매수 실행 ({})".format(now.strftime('%H:%M:%S')))
            self.execute_buy()
            self.has_executed_entry_buy = True
