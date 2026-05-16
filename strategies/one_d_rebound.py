import math
import time
import uuid
import pandas as pd
from datetime import datetime

from utils import write_log
from .base import BaseStrategy


class OneDReboundStrategy(BaseStrategy):
    """
    1D-Rebound: EMA3 눌림목 종가베팅
    (구 Strategy1DRebound — 레거시 보존)
    """
    name = "1D-Rebound"

    def __init__(self, broker, gs_manager=None):
        super().__init__(broker, gs_manager)
        self.daily_starting_eval = 0
        self.bought_today = set()
        self.custom_sl_tracker = {}
        self.partial_sold_tracker = set()
        self.cached_watchlist = []

        self.has_executed_morning_reset = False
        self.has_executed_morning_report = False
        self.has_executed_time_cut = False
        self.has_executed_s_class_scan = False

    def reset_daily_state(self):
        self.has_executed_time_cut = False
        self.has_executed_morning_reset = False
        self.has_executed_morning_report = False
        self.has_executed_s_class_scan = False
        self.partial_sold_tracker.clear()
        self.bought_today.clear()
        self.custom_sl_tracker.clear()
        self.daily_starting_eval = 0
        if self.gs_manager:
            self.gs_manager.logged_today.clear()

    def check_3day_line_support(self, df_daily_list):
        if len(df_daily_list) < 5:
            return False, "FAIL: LACK_OF_DATA"

        now = datetime.now()
        current_time = int(now.strftime("%H%M"))
        if not (1510 <= current_time <= 1520):
            return False, f"WAITING_TIME (Current {current_time})"

        df = pd.DataFrame(df_daily_list)[::-1].reset_index(drop=True)
        df[['clpr', 'lwpr', 'oprc', 'vol']] = (
            df[['stck_clpr', 'stck_lwpr', 'stck_oprc', 'acml_vol']].apply(pd.to_numeric)
        )

        if len(df) < 5:
            return False, "FAIL: LACK_OF_DATA"

        curr = df.iloc[-1]
        prev = df.iloc[-2]

        if curr['vol'] > prev['vol'] * 0.8:
            return False, "FAIL: VOLUME_NOT_DECREASED"

        df['ema_3'] = df['clpr'].ewm(span=3, adjust=False).mean()
        curr_ema3 = df['ema_3'].iloc[-1]

        disparity = (curr_ema3 - curr['lwpr']) / curr_ema3
        if 0 <= disparity <= 0.015:
            body_size = abs(curr['clpr'] - curr['oprc'])
            lower_tail = min(curr['clpr'], curr['oprc']) - curr['lwpr']
            if lower_tail > body_size:
                return True, "SUCCESS: BUY_SIGNAL_CONFIRMED"

        return False, "FAIL: NO_SUPPORT_AT_EMA3"

    def run_strategy_at_specific_time(self):
        cash, tot_eval, holdings = self.broker.get_balance()
        if tot_eval is None or tot_eval == 0:
            return
        budget_per_stock = tot_eval * 0.25

        holding_codes = [h["code"] for h in holdings]
        can_buy_count = max(0, 3 - len(holdings))

        if can_buy_count <= 0 or cash < budget_per_stock * 0.9:
            return

        past_lead_stocks = self.gs_manager.get_past_lead_stocks([1, 2]) if self.gs_manager else []
        if not past_lead_stocks:
            return

        for cand in past_lead_stocks:
            if can_buy_count <= 0:
                break
            code = cand["code"]
            name = cand["name"]

            if code in holding_codes or code in self.bought_today:
                continue

            raw_data = self.broker.get_daily_prices(code, count=250)
            is_buy, reason = self.check_3day_line_support(raw_data)

            if is_buy:
                curr_price = float(raw_data[0]['stck_clpr']) if raw_data else 0
                if curr_price == 0:
                    continue
                qty = math.floor(budget_per_stock / curr_price)
                if qty > 0:
                    write_log(f"[@1D-Rebound] {name}({code}) 매수 조건 충족! ({reason})")
                    if self.broker.place_order(code, qty, is_buy=True):
                        trade_id = str(uuid.uuid4())[:8]
                        if self.gs_manager:
                            self.gs_manager.log_buy(trade_id, code, name, curr_price, qty, "1D_RB")
                        can_buy_count -= 1
                        holding_codes.append(code)
                        self.bought_today.add(code)
                        self.custom_sl_tracker[code] = curr_price * 0.998
            time.sleep(0.2)

    def force_time_cut(self):
        _, _, holdings = self.broker.get_balance()
        if holdings is None:
            return
        for h in holdings:
            code = h["code"]
            if code in self.bought_today:
                continue
            write_log(f"[종가 타임컷] 15:18 전량 매도: {h['name']}({code})")
            if self.broker.place_order(code, h["qty"], is_buy=False):
                if self.gs_manager:
                    self.gs_manager.log_sell(code, h["current_price"], h["qty"],
                                             is_partial=False)
                if code in self.custom_sl_tracker:
                    del self.custom_sl_tracker[code]
            time.sleep(1)

    def monitor_and_sell_morning(self):
        cash, tot_eval, holdings = self.broker.get_balance()

        if tot_eval is None:
            write_log("[@System] 네트워크 불안정으로 잔고 확인 불가. 다음 사이클로 이동")
            return

        if self.daily_starting_eval == 0:
            self.daily_starting_eval = max(tot_eval, 1)
            write_log(f"[안전감시] 오늘 시가 총 자산: {self.daily_starting_eval:,.0f}원")

        if self.daily_starting_eval > 1 and tot_eval < self.daily_starting_eval * 0.90:
            drop_rate = (self.daily_starting_eval - tot_eval) / self.daily_starting_eval
            write_log(f"[비상정지] 자산 시가 대비 {drop_rate:.1%} 하락 → 봇 즉시 종료")
            import sys
            sys.exit(1)

        for h in holdings:
            code = h["code"]
            name = h["name"]
            buy_price = h["buy_price"]
            cur_price = h["current_price"]
            qty = h["qty"]

            now = datetime.now()
            is_morning = now.hour == 9 and now.minute <= 5

            sell_triggered = False
            sell_qty = 0
            reason = ""
            is_partial = False

            if is_morning and cur_price >= buy_price * 1.05 and code not in self.partial_sold_tracker:
                sell_qty = math.floor(qty / 2)
                if sell_qty > 0:
                    reason = "09:05 이내 +5% 도달 (50% 익절)"
                    sell_triggered = True
                    is_partial = True
                    self.partial_sold_tracker.add(code)

            elif cur_price >= buy_price * 1.15:
                sell_qty = qty
                reason = "장중 +15% 도달 (전량 익절)"
                sell_triggered = True

            else:
                if code in self.custom_sl_tracker:
                    sl_price = self.custom_sl_tracker[code]
                    if cur_price <= sl_price:
                        sell_qty = qty
                        reason = "장중 하드컷 손절 (-0.2% 붕괴)"
                        sell_triggered = True

            if sell_triggered and sell_qty > 0:
                write_log(f"[{reason}] {name}({code}) {sell_qty}주 시장가 매도")
                if self.broker.place_order(code, sell_qty, is_buy=False):
                    if self.gs_manager:
                        self.gs_manager.log_sell(code, cur_price, sell_qty, is_partial=is_partial)
                    if sell_qty == qty and code in self.custom_sl_tracker:
                        del self.custom_sl_tracker[code]
            time.sleep(0.5)

    def tick(self, now):
        if now.hour == 0 and now.minute == 0:
            self.reset_daily_state()

        if now.hour == 8 and now.minute >= 20 and not self.has_executed_morning_reset:
            write_log("[@System] 08:20 1D-Rebound 세션 초기화")
            self.cached_watchlist.clear()
            self.custom_sl_tracker.clear()
            self.has_executed_morning_reset = True

        if now.hour == 8 and now.minute >= 30 and not self.has_executed_morning_report:
            write_log("[@1D-Rebound] 장 개시 대기 중")
            self.has_executed_morning_report = True

        if 9 <= now.hour <= 15:
            if now.second % 30 == 0 and not (now.hour == 15 and now.minute >= 18):
                self.monitor_and_sell_morning()

            if now.hour == 15 and 10 <= now.minute <= 20:
                if now.second == 15:
                    if not self.broker.check_market_crash():
                        self.run_strategy_at_specific_time()
                    else:
                        write_log("[안전장치] 코스피 급락 감지 — 1D-Rebound 매수 중단")

            if now.hour == 15 and 18 <= now.minute <= 20:
                if not self.has_executed_time_cut:
                    self.force_time_cut()
                    self.has_executed_time_cut = True

        if ((now.hour == 15 and now.minute >= 30) or now.hour > 15) and not self.has_executed_s_class_scan:
            write_log("[@1D-Rebound] 15:30 S-Class 주도주 스캐닝 시작")
            self.broker.scan_s_class_targets()
            self.has_executed_s_class_scan = True
