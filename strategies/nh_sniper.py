import math
import sys
import time
import uuid
import pandas as pd
from datetime import datetime

from utils import write_log
from .base import BaseStrategy


class NHSniperStrategy(BaseStrategy):
    """
    NH-Sniper: 신고가 상승음봉 정밀타격
    - 매수: 15:15 조건 체크 → 15:20 시장가 실행
    - 익절: +20% → 50% 매도
    - TS:   종가 < 5일선 → 전량 매도
    - 손절: -3% → 전량 매도
    """
    name = "NH-Sniper"

    def __init__(self, broker, gs_manager=None):
        super().__init__(broker, gs_manager)
        self.bought_today = set()
        self.partial_sold_tracker = set()
        self.daily_starting_eval = 0
        self.buy_candidates = []

        self.has_executed_morning_reset = False
        self.has_executed_morning_report = False
        self.has_executed_entry_check = False
        self.has_executed_entry_buy = False
        self.has_executed_s_class_scan = False

    def reset_daily_state(self):
        self.has_executed_morning_reset = False
        self.has_executed_morning_report = False
        self.has_executed_entry_check = False
        self.has_executed_entry_buy = False
        self.has_executed_s_class_scan = False
        self.bought_today.clear()
        self.partial_sold_tracker.clear()
        self.buy_candidates.clear()
        self.daily_starting_eval = 0
        if self.gs_manager:
            self.gs_manager.logged_today.clear()

    def _to_df(self, daily_data, col_map):
        """daily_data(최신순) → 오름차순 DataFrame, 지정 컬럼 숫자 변환 후 rename"""
        df = pd.DataFrame(daily_data)[::-1].reset_index(drop=True)
        for src, dst in col_map.items():
            if src in df.columns:
                df[dst] = pd.to_numeric(df[src], errors='coerce').fillna(0)
        return df

    def check_buy_condition(self, code, daily_data):
        """
        NH-Sniper 매수 3조건:
        1. 등락률 3% 이하
        2. 거래량 전일비 50% 이하
        3. 현재가 > 5일 이동평균선
        """
        if len(daily_data) < 10:
            return False, "FAIL: LACK_OF_DATA"

        df = self._to_df(daily_data, {'stck_clpr': 'clpr', 'acml_vol': 'vol'})

        curr = df.iloc[-1]
        prev = df.iloc[-2]

        if prev['clpr'] <= 0:
            return False, "FAIL: PREV_CLOSE_ZERO"

        change_rate = (curr['clpr'] - prev['clpr']) / prev['clpr'] * 100
        if change_rate > 3.0:
            return False, f"FAIL: CHANGE_RATE_HIGH ({change_rate:.2f}%)"

        if prev['vol'] > 0:
            vol_ratio = curr['vol'] / prev['vol'] * 100
            if vol_ratio > 50.0:
                return False, f"FAIL: VOLUME_HIGH ({vol_ratio:.1f}%)"

        ma5 = df['clpr'].tail(5).mean()
        if curr['clpr'] <= ma5:
            return False, f"FAIL: BELOW_MA5 (현재:{curr['clpr']:,.0f} MA5:{ma5:,.0f})"

        return True, f"OK (등락률:{change_rate:.2f}%, MA5:{ma5:,.0f}, 거래량비:{vol_ratio:.1f}%)" \
            if prev['vol'] > 0 else f"OK (등락률:{change_rate:.2f}%, MA5:{ma5:,.0f})"

    def check_ts_condition(self, code, daily_data):
        """TS 조건: 현재 종가 < 5일 이동평균선"""
        if len(daily_data) < 6:
            return False, "FAIL: LACK_OF_DATA"

        df = self._to_df(daily_data, {'stck_clpr': 'clpr'})
        curr_close = df['clpr'].iloc[-1]
        ma5 = df['clpr'].tail(5).mean()

        if curr_close < ma5:
            return True, f"TS발동 (종가:{curr_close:,.0f} < MA5:{ma5:,.0f})"
        return False, f"MA5 위 유지 (종가:{curr_close:,.0f} >= MA5:{ma5:,.0f})"

    def run_entry_check(self):
        """15:15 — 기존 보유 TS 체크 + 신규 매수 후보 탐색"""
        cash, tot_eval, holdings = self.broker.get_balance()
        if tot_eval is None:
            write_log("[NH-Sniper] 잔고 조회 실패, 진입 체크 스킵")
            return

        holding_codes = {h["code"] for h in holdings}

        # TS 체크: 5일선 이탈 종목 전량 매도
        for h in holdings:
            code = h["code"]
            name = h["name"]
            daily_data = self.broker.get_daily_prices(code, count=10)
            ts_hit, ts_reason = self.check_ts_condition(code, daily_data)
            write_log(f"[NH-Sniper TS체크] {name}({code}): {ts_reason}")
            if ts_hit:
                write_log(f"[NH-Sniper TS매도] {name}({code}) {h['qty']}주 전량 매도")
                if self.broker.place_order(code, h["qty"], is_buy=False):
                    if self.gs_manager:
                        self.gs_manager.log_sell(code, h["current_price"], h["qty"],
                                                 is_partial=False, strategy_type="NH_Sniper")
                    holding_codes.discard(code)
            time.sleep(0.3)

        # 매수 후보 탐색
        budget_per_stock = tot_eval * 0.10
        can_buy = max(0, 10 - len(holding_codes))

        if can_buy <= 0 or cash < budget_per_stock * 0.9:
            write_log(f"[NH-Sniper] 매수 슬롯 없음 (보유:{len(holding_codes)}종목, 현금:{cash:,.0f}원)")
            return

        watchlist = self.gs_manager.get_past_lead_stocks([1, 2]) if self.gs_manager else []
        if not watchlist:
            write_log("[NH-Sniper] S-Class 관심종목 없음, 매수 스킵")
            return

        candidates = []
        for stock in watchlist:
            code = stock["code"]
            name = stock["name"]
            if code in holding_codes or code in self.bought_today:
                continue

            daily_data = self.broker.get_daily_prices(code, count=10)
            ok, reason = self.check_buy_condition(code, daily_data)
            write_log(f"[NH-Sniper 매수체크] {name}({code}): {reason}")

            if ok and daily_data:
                curr_price = float(daily_data[0].get('stck_clpr', 0))
                if curr_price > 0:
                    qty = math.floor(budget_per_stock / curr_price)
                    if qty > 0:
                        candidates.append({
                            "code": code, "name": name,
                            "price": curr_price, "qty": qty
                        })
            time.sleep(0.3)

        self.buy_candidates = candidates[:can_buy]
        write_log(f"[NH-Sniper] 매수 후보 {len(self.buy_candidates)}종목 확정 → 15:20 시장가 실행 예정")

    def execute_buy(self):
        """15:20 — 매수 후보 시장가 주문"""
        if not self.buy_candidates:
            write_log("[NH-Sniper] 15:20 매수 후보 없음")
            return

        for cand in self.buy_candidates:
            code = cand["code"]
            name = cand["name"]
            qty = cand["qty"]
            write_log(f"[NH-Sniper 매수] {name}({code}) {qty}주 시장가 주문")
            if self.broker.place_order(code, qty, is_buy=True):
                trade_id = str(uuid.uuid4())[:8]
                if self.gs_manager:
                    self.gs_manager.log_buy(trade_id, code, name,
                                            cand["price"], qty, "NH_Sniper")
                self.bought_today.add(code)
            time.sleep(0.5)

        self.buy_candidates.clear()

    def monitor_and_sell(self):
        """장 중 익절(+20%→50%) / 손절(-3%) 모니터링"""
        _, tot_eval, holdings = self.broker.get_balance()
        if tot_eval is None:
            write_log("[NH-Sniper] 잔고 조회 불가, 모니터링 유예")
            return

        if self.daily_starting_eval == 0:
            self.daily_starting_eval = max(tot_eval, 1)
            write_log(f"[NH-Sniper] 오늘 시가 총 자산: {self.daily_starting_eval:,.0f}원")

        if self.daily_starting_eval > 1 and tot_eval < self.daily_starting_eval * 0.90:
            drop = (self.daily_starting_eval - tot_eval) / self.daily_starting_eval
            write_log(f"[비상정지] 자산 시가 대비 {drop:.1%} 하락 → 봇 즉시 종료")
            sys.exit(1)

        for h in holdings:
            code = h["code"]
            name = h["name"]
            buy_price = h["buy_price"]
            cur_price = h["current_price"]
            qty = h["qty"]

            if buy_price <= 0:
                continue

            profit_rt = (cur_price - buy_price) / buy_price * 100

            if profit_rt >= 20.0 and code not in self.partial_sold_tracker:
                sell_qty = math.floor(qty / 2)
                if sell_qty > 0:
                    write_log(f"[NH-Sniper 익절] {name}({code}) +{profit_rt:.2f}% → {sell_qty}주 50% 매도")
                    if self.broker.place_order(code, sell_qty, is_buy=False):
                        if self.gs_manager:
                            self.gs_manager.log_sell(code, cur_price, sell_qty,
                                                     is_partial=True, strategy_type="NH_Sniper")
                        self.partial_sold_tracker.add(code)

            elif profit_rt <= -3.0:
                write_log(f"[NH-Sniper 손절] {name}({code}) {profit_rt:.2f}% → {qty}주 전량 매도")
                if self.broker.place_order(code, qty, is_buy=False):
                    if self.gs_manager:
                        self.gs_manager.log_sell(code, cur_price, qty,
                                                 is_partial=False, strategy_type="NH_Sniper")

            time.sleep(0.3)

    def tick(self, now):
        # 자정 일일 초기화
        if now.hour == 0 and now.minute == 0:
            self.reset_daily_state()

        # 08:20 세션 초기화
        if now.hour == 8 and now.minute >= 20 and not self.has_executed_morning_reset:
            write_log("[@System] 08:20 NH-Sniper 세션 초기화")
            self.has_executed_morning_reset = True

        # 08:30 장 시작 준비 완료
        if now.hour == 8 and now.minute >= 30 and not self.has_executed_morning_report:
            write_log("[@NH-Sniper] 장 개시 대기 중 (모든 시스템 준비 완료)")
            self.has_executed_morning_report = True

        # 09:00~15:14 — 30초마다 익절/손절 모니터링
        in_intraday = (now.hour >= 9) and (now.hour < 15 or (now.hour == 15 and now.minute < 15))
        if in_intraday and now.second % 30 == 0:
            self.monitor_and_sell()

        # 15:15 (1회) — 매수 후보 탐색 + TS 체크
        if now.hour == 15 and now.minute == 15 and now.second == 0 and not self.has_executed_entry_check:
            if not self.broker.check_market_crash():
                self.run_entry_check()
            else:
                write_log("[NH-Sniper] 코스피 급락 감지 — 당일 매수 중단")
            self.has_executed_entry_check = True

        # 15:20 (1회) — 시장가 매수 실행
        if now.hour == 15 and now.minute == 20 and now.second == 0 and not self.has_executed_entry_buy:
            self.execute_buy()
            self.has_executed_entry_buy = True

        # 15:30+ (1회) — S-Class 주도주 스캐닝
        if ((now.hour == 15 and now.minute >= 30) or now.hour > 15) and not self.has_executed_s_class_scan:
            write_log("[@NH-Sniper] 15:30 S-Class 주도주 스캐닝 시작")
            self.broker.scan_s_class_targets()
            self.has_executed_s_class_scan = True
