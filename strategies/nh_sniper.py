import math
import sys
import time
import uuid
import pandas as pd
from datetime import datetime, timedelta

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

    def __init__(self, broker, gs_manager=None, mode="모의"):
        super().__init__(broker, gs_manager)
        self.mode = mode  # "모의" or "실전" — 매매일지 기록용
        self.bought_today = set()
        self.partial_sold_tracker = set()
        self.daily_starting_eval = 0
        self.buy_candidates = []

        self.has_executed_morning_reset = False
        self.has_executed_morning_report = False
        self.has_executed_entry_check = False
        self.has_executed_entry_buy = False
        self.has_executed_s_class_scan = False

        # 잔고 조회 연속 실패 백오프 (VTS 서버 오류 시 로그 폭주 방지)
        self._balance_fail_count = 0
        self._balance_pause_until = None

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

    def check_buy_condition(self, code, daily_data, inst_net_amt=None):
        """
        NH-Sniper 매수 조건 (AND 조건):
        ※ S-Class 스캔(등락률10%+·신고가)이 장대양봉을 보장하므로 별도 체크 불필요
        1. 등락률 3% 이하 (전일 급등 후 눌림)
        2. 거래량 전일비 50% 이하 (거래량 감소 음봉)
        3. 정배열 확인 (MA5 > MA20) ← 역배열·첫번째 도달 실패 방지
        4. 현재가 > 5일 이동평균선

        inst_net_amt: 기관 순매수대금(백만원). None이면 체크 생략.
                      양수(기관 순매수) → OK, 음수 → 경고 로그만 (Hard 차단 아님)
        """
        if len(daily_data) < 20:
            return False, "FAIL: LACK_OF_DATA"

        df = self._to_df(daily_data, {
            'stck_clpr': 'clpr', 'acml_vol': 'vol',
            'stck_hgpr': 'hgpr', 'stck_lwpr': 'lwpr'
        })

        curr = df.iloc[-1]
        prev = df.iloc[-2]

        if prev['clpr'] <= 0 or curr['clpr'] <= 0:
            return False, "FAIL: PRICE_ZERO"

        # [1] 캔들 범위 5% 이내 — (고가-저가)/저가 ≤ 5%
        # 종가 등락률이 아닌 캔들 전체 범위로 판단: 위꼬리·아래꼬리 포함한 변동폭이 좁아야 함
        # 기존 "종가 등락률 3% 이하" 대비 장중 급등락(긴 꼬리) 케이스도 올바르게 필터링
        candle_range = 0.0
        if curr['lwpr'] > 0:
            candle_range = (curr['hgpr'] - curr['lwpr']) / curr['lwpr'] * 100
            if candle_range > 5.0:
                change_rate = (curr['clpr'] - prev['clpr']) / prev['clpr'] * 100
                return False, (f"FAIL: CANDLE_WIDE ({candle_range:.2f}%)"
                               f" 종가등락:{change_rate:+.2f}%")

        # [2] 거래량 전일비 50% 이하
        vol_ratio = 0.0
        if prev['vol'] > 0:
            vol_ratio = curr['vol'] / prev['vol'] * 100
            if vol_ratio > 50.0:
                return False, f"FAIL: VOLUME_HIGH ({vol_ratio:.1f}%)"

        # [3] 정배열 확인 (MA5 > MA20) — 역배열이면 돌파 실패 확률 높음
        ma5  = df['clpr'].tail(5).mean()
        ma20 = df['clpr'].tail(20).mean()
        if ma5 <= ma20:
            return False, f"FAIL: NOT_ALIGNED (MA5:{ma5:,.0f} ≤ MA20:{ma20:,.0f})"

        # [4] 현재가 MA5 위 (눌림 후 지지)
        if curr['clpr'] <= ma5:
            return False, f"FAIL: BELOW_MA5 (현재:{curr['clpr']:,.0f} MA5:{ma5:,.0f})"

        # 기관 순매수 — 소프트 우선순위 (Hard 차단 아님, 로그로만 표시)
        inst_note = ""
        if inst_net_amt is not None:
            inst_eok = inst_net_amt / 100  # 백만원 → 억원
            if inst_eok >= 0:
                inst_note = f" 기관순매수:{inst_eok:.0f}억✅"
            else:
                inst_note = f" 기관순매도:{abs(inst_eok):.0f}억⚠"

        change_rate = (curr['clpr'] - prev['clpr']) / prev['clpr'] * 100
        return True, (f"OK (캔들범위:{candle_range:.2f}% 종가등락:{change_rate:+.2f}%"
                      f" 거래량비:{vol_ratio:.1f}% MA5:{ma5:,.0f} MA20:{ma20:,.0f}{inst_note})")

    def check_ts_condition(self, code, daily_data):
        """
        TS 조건 2단계:
        - 1단계: 종가 < 5일선 → 잔여 50% 매도
        - 2단계: 종가 < 20일선 → 전량 매도
        반환: (signal, reason)
          signal: None | 'half' | 'all'
        """
        if len(daily_data) < 21:
            return None, "FAIL: LACK_OF_DATA"

        df = self._to_df(daily_data, {'stck_clpr': 'clpr'})
        curr_close = df['clpr'].iloc[-1]
        ma5  = df['clpr'].tail(5).mean()
        ma20 = df['clpr'].tail(20).mean()

        if curr_close < ma20:
            return 'all', f"MA20 TS발동-전량 (종가:{curr_close:,.0f} < MA20:{ma20:,.0f})"
        if curr_close < ma5:
            return 'half', f"MA5 TS발동-50% (종가:{curr_close:,.0f} < MA5:{ma5:,.0f})"
        return None, f"TS 없음 (MA5:{ma5:,.0f} MA20:{ma20:,.0f} 위 유지)"

    def run_entry_check(self):
        """15:15 — 기존 보유 TS 체크 + 신규 매수 후보 탐색"""
        cash, tot_eval, holdings = self.broker.get_balance()
        if tot_eval is None:
            write_log("[NH-Sniper] 잔고 조회 실패, 진입 체크 스킵")
            return

        holding_codes = {h["code"] for h in holdings}

        # TS 체크: MA5 이탈→50% / MA20 이탈→전량
        for h in holdings:
            code = h["code"]
            name = h["name"]
            daily_data = self.broker.get_daily_prices(code, count=25)
            ts_signal, ts_reason = self.check_ts_condition(code, daily_data)
            write_log(f"[NH-Sniper TS체크] {name}({code}): {ts_reason}")
            if ts_signal == 'all':
                write_log(f"[NH-Sniper TS매도-전량] {name}({code}) MA20이탈 → {h['qty']}주 전량 매도")
                if self.broker.place_order(code, h["qty"], is_buy=False):
                    if self.gs_manager:
                        self.gs_manager.log_sell(code, h["current_price"], h["qty"],
                                                 is_partial=False, strategy_type="NH_Sniper")
                    holding_codes.discard(code)
            elif ts_signal == 'half' and code not in self.partial_sold_tracker:
                sell_qty = math.floor(h["qty"] / 2)
                if sell_qty > 0:
                    write_log(f"[NH-Sniper TS매도-50%] {name}({code}) MA5이탈 → {sell_qty}주 매도")
                    if self.broker.place_order(code, sell_qty, is_buy=False):
                        if self.gs_manager:
                            self.gs_manager.log_sell(code, h["current_price"], sell_qty,
                                                     is_partial=True, strategy_type="NH_Sniper")
                        self.partial_sold_tracker.add(code)
            time.sleep(0.3)

        # 매수 후보 탐색 — 소액 안전 사이징 (종목당 25%, 최대 3종목, 현금 25% 유지)
        budget_per_stock = tot_eval * 0.25
        cash_reserve     = tot_eval * 0.25   # 항상 유지할 예비현금
        max_positions    = 3

        can_buy = max(0, max_positions - len(holding_codes))

        # 현금 가드: 매수 후에도 cash_reserve 이상 남아 있어야 매수
        available_cash = max(0, cash - cash_reserve)
        slots_by_cash  = int(available_cash // budget_per_stock) if budget_per_stock > 0 else 0
        can_buy = min(can_buy, slots_by_cash)

        if can_buy <= 0:
            write_log(
                f"[NH-Sniper] 매수 슬롯 없음 "
                f"(보유:{len(holding_codes)}/{max_positions}종목, "
                f"현금:{cash:,.0f}원, 예비유지:{int(cash_reserve):,}원, "
                f"종목당예산:{int(budget_per_stock):,}원)"
            )
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

            daily_data = self.broker.get_daily_prices(code, count=25)

            # 기관 순매수대금 조회 (가능한 경우)
            inst_net_amt = None
            try:
                inst_net_amt = self.broker.get_inst_net_amount(code)
            except AttributeError:
                pass  # 브로커가 미구현 시 체크 생략

            ok, reason = self.check_buy_condition(code, daily_data, inst_net_amt=inst_net_amt)
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
                                            cand["price"], qty, "NH_Sniper", self.mode)
                self.bought_today.add(code)
            time.sleep(0.5)

        self.buy_candidates.clear()

    def monitor_and_sell(self):
        """장 중 익절(+20%→50%) / 손절(-3%) 모니터링"""
        # 연속 실패 백오프: 3회 연속 실패 시 5분간 호출 중단 (서버 부하·로그 폭주 방지)
        if self._balance_pause_until and datetime.now() < self._balance_pause_until:
            return

        _, tot_eval, holdings = self.broker.get_balance()
        if tot_eval is None:
            self._balance_fail_count += 1
            if self._balance_fail_count >= 3:
                self._balance_pause_until = datetime.now() + timedelta(minutes=5)
                write_log(f"[NH-Sniper] 잔고 조회 {self._balance_fail_count}회 연속 실패 → 5분간 모니터링 유예")
            else:
                write_log("[NH-Sniper] 잔고 조회 불가, 모니터링 유예")
            return

        self._balance_fail_count = 0
        self._balance_pause_until = None

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

            # 익절: +25% → 50% 부분 매도 (1회)
            if profit_rt >= 25.0 and code not in self.partial_sold_tracker:
                sell_qty = math.floor(qty / 2)
                if sell_qty > 0:
                    write_log(f"[NH-Sniper 익절] {name}({code}) +{profit_rt:.2f}% → {sell_qty}주 50% 매도")
                    if self.broker.place_order(code, sell_qty, is_buy=False):
                        if self.gs_manager:
                            self.gs_manager.log_sell(code, cur_price, sell_qty,
                                                     is_partial=True, strategy_type="NH_Sniper")
                        self.partial_sold_tracker.add(code)

            # 손절: -3% → 전량 매도
            elif profit_rt <= -3.0:
                write_log(f"[NH-Sniper 손절] {name}({code}) {profit_rt:.2f}% → {qty}주 전량 매도")
                if self.broker.place_order(code, qty, is_buy=False):
                    if self.gs_manager:
                        self.gs_manager.log_sell(code, cur_price, qty,
                                                 is_partial=False, strategy_type="NH_Sniper")

            time.sleep(0.3)

    def tick(self, now):
        # 자정 일일 초기화
        if now.hour == 0 and now.minute == 0 and now.second < 5:
            self.reset_daily_state()

        # 주말 가드 — 토/일은 모든 동작 차단
        # (VTS 모의서버 주말 비운영: 잔고 API가 SSL EOF/OPSQ0008로 실패해 로그 폭주했었음)
        if now.weekday() >= 5:
            return

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

        # 15:15~15:19 (1회) — 매수 후보 탐색 + TS 체크
        # second == 0 조건 제거: 1초 루프에서 정각을 놓칠 경우 대비해 분 단위 체크
        if now.hour == 15 and now.minute == 15 and not self.has_executed_entry_check:
            write_log(f"[@NH-Sniper] 15:15 진입 체크 시작 ({now.strftime('%H:%M:%S')})")
            if not self.broker.check_market_crash():
                self.run_entry_check()
            else:
                write_log("[NH-Sniper] 코스피 급락 감지 — 당일 매수 중단")
            self.has_executed_entry_check = True

        # 15:20~15:24 (1회) — 시장가 매수 실행
        if now.hour == 15 and now.minute == 20 and not self.has_executed_entry_buy:
            write_log(f"[@NH-Sniper] 15:20 시장가 매수 실행 ({now.strftime('%H:%M:%S')})")
            self.execute_buy()
            self.has_executed_entry_buy = True

        # 15:30+ (1회) — S-Class 주도주 스캐닝 (종가 확정 후)
        # 1차 방어: weekday >= 5 (토/일) 차단
        # 2차 방어: brokers.py 내부에서 삼성전자 날짜 비교 → 평일 공휴일도 차단
        if ((now.hour == 15 and now.minute >= 30) or now.hour > 15) and not self.has_executed_s_class_scan:
            if now.weekday() >= 5:  # 토(5), 일(6) — 주말 1차 차단
                write_log(f"[@NH-Sniper] 주말 감지 — S-Class 스캔 건너뜀 ({now.strftime('%A')})")
                self.has_executed_s_class_scan = True
            else:
                # 평일 공휴일은 brokers.py 삼성전자 날짜 비교로 내부 차단됨
                write_log(f"[@NH-Sniper] S-Class 주도주 스캐닝 시작 ({now.strftime('%H:%M:%S')})")
                self.broker.scan_s_class_targets()
                self.has_executed_s_class_scan = True
