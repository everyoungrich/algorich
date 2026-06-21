import math
import time
import uuid
from datetime import date, datetime

import pandas as pd

from utils import write_log
from .base import BaseStrategy


class NHHunterStrategy(BaseStrategy):
    """
    NH-Hunter: 신고가(근접) 종가베팅 — 다음날 단기 익절 전략

    ■ 매수 조건 (15:00 후보 탐색 → 15:20 시장가 실행)
      - NH-Hunter Audit PASS 종목 (H1~H7 7가지 조건 충족)
      - 시황 조건: 코스피 종목 → 코스피 종가 > MA5
                   코스닥 종목 → 코스닥 종가 > MA5
      - 포지션: 총자산 25% / 최대 3종목 / 현금 25% 항상 유지
      - 상한가 종목 제외 (is_upper_limit = True)

    ■ 매도 조건
      A. 장 중 +15% 도달     → 전량 익절
      B. 장 중 -5% 이탈      → 전량 손절
      C. 익일 09:00 갭 +5%   → 전량 익절
      D. 익일 09:00 갭 -3%   → 전량 손절
      E. 매수 3일 경과 15:15 → 강제 청산

    ■ 타임라인
      09:00 (1회)  → morning_sell  (갭 익절/손절)
      09:10 (1회)  → force_close   (3일 경과 강제 청산 — 오버나잇 3회 초과 방지)
      09:10~14:59  → monitor_and_sell 30초마다 (장 중 +15%/-5%)
      15:00 (1회)  → run_entry_check (NH-Hunter 후보 탐색)
      15:15 (1회)  → check_aged_positions (3일 경과 청산 — 종가 기준)
      15:20 (1회)  → execute_buy (시황 조건 포함 시장가 매수)
    """

    name = "NH-Hunter"

    PROFIT_TARGET  = 15.0   # 장 중 익절 목표 (%)
    STOP_LOSS      = -5.0   # 장 중 손절 기준 (%)
    GAP_UP_EXIT    =  5.0   # 익일 갭상승 익절 기준 (%)
    GAP_DOWN_STOP  = -3.0   # 익일 갭하락 손절 기준 (%)
    MAX_HOLD_DAYS  = 3      # 최대 보유 일수

    def __init__(self, broker, gs_manager=None, mode="모의"):
        super().__init__(broker, gs_manager)
        self.mode           = mode
        self.bought_today   = set()
        self.buy_candidates = []
        self.position_dates = {}   # code → datetime.date (매수일)

        # 시황 MA5 캐시 (당일 1회 조회)
        self._index_ma5_cache = {}  # "0001"|"1001" → bool|None

        # 일별 실행 플래그
        self.has_executed_morning_reset  = False
        self.has_executed_morning_report = False
        self.has_executed_morning_sell   = False
        self.has_executed_force_close    = False
        self.has_executed_entry_check    = False
        self.has_executed_aged_check     = False
        self.has_executed_entry_buy      = False

    # ──────────────────────────────────────────────────────────────────
    # 유틸리티
    # ──────────────────────────────────────────────────────────────────

    def reset_daily_state(self):
        """자정 상태 초기화"""
        self.has_executed_morning_reset  = False
        self.has_executed_morning_report = False
        self.has_executed_morning_sell   = False
        self.has_executed_force_close    = False
        self.has_executed_entry_check    = False
        self.has_executed_aged_check     = False
        self.has_executed_entry_buy      = False
        self.bought_today.clear()
        self.buy_candidates.clear()
        self._index_ma5_cache.clear()
        if self.gs_manager:
            self.gs_manager.logged_today.clear()

    @staticmethod
    def _market_index_code(stock_code: str) -> str:
        """
        종목코드로 시장 구분.
        코스피: '0'으로 시작하는 6자리 (e.g. 005930)
        코스닥: 그 외 (e.g. 035720, 196170 등)
        """
        return "0001" if stock_code.startswith("0") else "1001"

    def _check_index_above_ma5(self, index_code: str) -> bool:
        """시장 지수 MA5 조건 — 캐시 적용 (당일 1회 API 호출)"""
        if index_code in self._index_ma5_cache:
            return self._index_ma5_cache[index_code]

        result = None
        try:
            result = self.broker.get_index_above_ma5(index_code)
        except AttributeError:
            write_log(f"[NH-Hunter] broker.get_index_above_ma5 미구현 — 시황 조건 무시")

        if result is None:
            write_log(f"[NH-Hunter] 시황 조회 실패({index_code}) — 매수 허용(폴백)")
            result = True   # API 실패 시 매수 허용

        label = "코스피" if index_code == "0001" else "코스닥"
        write_log(f"[NH-Hunter 시황] {label} MA5 {'위 ✅' if result else '아래 ❌'}")
        self._index_ma5_cache[index_code] = result
        return result

    def _holding_days(self, code: str) -> int:
        """보유 일수 (매수일 ~ 오늘, 캘린더 기준)"""
        buy_date = self.position_dates.get(code)
        if buy_date is None:
            return 0
        return (date.today() - buy_date).days

    # ──────────────────────────────────────────────────────────────────
    # 매도 로직
    # ──────────────────────────────────────────────────────────────────

    def morning_sell(self):
        """
        익일 09:00 갭 확인:
          갭 +5% 이상  → 전량 익절
          갭 -3% 이하  → 전량 손절
          그 사이      → 장 중 모니터링 계속
        """
        _, _, holdings = self.broker.get_balance()
        if not holdings:
            write_log("[NH-Hunter] 09:00 보유 종목 없음")
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

            if profit_rt >= self.GAP_UP_EXIT:
                write_log(
                    f"[NH-Hunter 갭익절] {name}({code}) "
                    f"갭+{profit_rt:.2f}% → {qty}주 전량 익절 ✅"
                )
                if self.broker.place_order(code, qty, is_buy=False):
                    if self.gs_manager:
                        self.gs_manager.log_sell(
                            code, cur_price, qty,
                            is_partial=False, strategy_type="NH_Hunter"
                        )
                    self.position_dates.pop(code, None)

            elif profit_rt <= self.GAP_DOWN_STOP:
                write_log(
                    f"[NH-Hunter 갭손절] {name}({code}) "
                    f"갭{profit_rt:.2f}% → {qty}주 즉시 손절"
                )
                if self.broker.place_order(code, qty, is_buy=False):
                    if self.gs_manager:
                        self.gs_manager.log_sell(
                            code, cur_price, qty,
                            is_partial=False, strategy_type="NH_Hunter"
                        )
                    self.position_dates.pop(code, None)

            else:
                write_log(
                    f"[NH-Hunter 관망] {name}({code}) "
                    f"{profit_rt:+.2f}% — 장 중 +15%/-5% 모니터링"
                )
            time.sleep(0.3)

    def monitor_and_sell(self):
        """장 중 +15% 익절 / -5% 손절"""
        _, _, holdings = self.broker.get_balance()
        if holdings is None:
            write_log("[NH-Hunter] 잔고 조회 불가, 모니터링 유예")
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

            if profit_rt >= self.PROFIT_TARGET:
                write_log(
                    f"[NH-Hunter 익절] {name}({code}) "
                    f"+{profit_rt:.2f}% 목표 도달 → {qty}주 전량 매도 ✅"
                )
                if self.broker.place_order(code, qty, is_buy=False):
                    if self.gs_manager:
                        self.gs_manager.log_sell(
                            code, cur_price, qty,
                            is_partial=False, strategy_type="NH_Hunter"
                        )
                    self.position_dates.pop(code, None)

            elif profit_rt <= self.STOP_LOSS:
                write_log(
                    f"[NH-Hunter 손절] {name}({code}) "
                    f"{profit_rt:.2f}% 손절선 이탈 → {qty}주 전량 매도"
                )
                if self.broker.place_order(code, qty, is_buy=False):
                    if self.gs_manager:
                        self.gs_manager.log_sell(
                            code, cur_price, qty,
                            is_partial=False, strategy_type="NH_Hunter"
                        )
                    self.position_dates.pop(code, None)

            time.sleep(0.3)

    def check_aged_positions(self):
        """15:15 — MAX_HOLD_DAYS 경과 종목 당일 종가로 강제 청산"""
        _, _, holdings = self.broker.get_balance()
        if not holdings:
            return

        for h in holdings:
            code  = h["code"]
            name  = h["name"]
            qty   = h["qty"]
            price = h["current_price"]
            days  = self._holding_days(code)

            if days >= self.MAX_HOLD_DAYS:
                write_log(
                    f"[NH-Hunter 기간청산] {name}({code}) "
                    f"보유 {days}일 경과 → {qty}주 종가 강제 청산"
                )
                if self.broker.place_order(code, qty, is_buy=False):
                    if self.gs_manager:
                        self.gs_manager.log_sell(
                            code, price, qty,
                            is_partial=False, strategy_type="NH_Hunter"
                        )
                    self.position_dates.pop(code, None)
            time.sleep(0.3)

    def force_close(self):
        """09:10 — MAX_HOLD_DAYS 초과 오버나잇 방지 (이미 3일이 넘은 종목 청산)"""
        _, _, holdings = self.broker.get_balance()
        if not holdings:
            return

        for h in holdings:
            code  = h["code"]
            name  = h["name"]
            qty   = h["qty"]
            price = h["current_price"]
            days  = self._holding_days(code)

            if days > self.MAX_HOLD_DAYS:
                write_log(
                    f"[NH-Hunter 강제청산] {name}({code}) "
                    f"보유 {days}일 초과 → {qty}주 09:10 강제 매도"
                )
                if self.broker.place_order(code, qty, is_buy=False):
                    if self.gs_manager:
                        self.gs_manager.log_sell(
                            code, price, qty,
                            is_partial=False, strategy_type="NH_Hunter"
                        )
                    self.position_dates.pop(code, None)
            time.sleep(0.3)

    # ──────────────────────────────────────────────────────────────────
    # 매수 로직
    # ──────────────────────────────────────────────────────────────────

    def run_entry_check(self):
        """
        15:00 — 당일 NH-Hunter Audit PASS 종목 탐색.
        Google Sheets에서 오늘 날짜 기준 nh_hunter PASS 목록을 가져온다.
        시황 조건은 15:20 execute_buy()에서 최종 적용.
        """
        cash, tot_eval, holdings = self.broker.get_balance()
        if tot_eval is None:
            write_log("[NH-Hunter] 잔고 조회 실패, 진입 체크 스킵")
            return

        holding_codes    = {h["code"] for h in holdings}
        budget_per_stock = tot_eval * 0.25
        cash_reserve     = tot_eval * 0.25
        max_positions    = 3

        can_buy = max(0, max_positions - len(holding_codes))
        available_cash = max(0, cash - cash_reserve)
        slots_by_cash = (
            int(available_cash / budget_per_stock) if budget_per_stock > 0 else 0
        )
        can_buy = min(can_buy, slots_by_cash)

        if can_buy <= 0:
            write_log(
                f"[NH-Hunter] 매수 슬롯 없음 "
                f"(보유:{len(holding_codes)}/{max_positions}종목, "
                f"현금:{cash:,.0f}원, 예비:{int(cash_reserve):,}원)"
            )
            return

        # NH-Hunter PASS 목록 조회 (오늘/어제 포함 최근 1일)
        nh_pass_list = []
        if self.gs_manager:
            try:
                nh_pass_list = self.gs_manager.get_nh_hunter_pass_today()
            except AttributeError:
                # gs_manager에 메서드 없으면 S-Class 풀로 폴백
                watchlist = self.gs_manager.get_past_lead_stocks([0]) or []
                nh_pass_list = [{"code": s["code"], "name": s["name"]} for s in watchlist]

        if not nh_pass_list:
            write_log("[NH-Hunter] 오늘 NH-Hunter PASS 종목 없음, 매수 스킵")
            return

        candidates = []
        for stock in nh_pass_list:
            code = stock.get("code", "")
            name = stock.get("name", "")
            if code in holding_codes or code in self.bought_today:
                continue

            # 상한가 제외 플래그
            if stock.get("is_upper_limit"):
                write_log(f"[NH-Hunter 제외] {name}({code}) 상한가 — 종가베팅 불가")
                continue

            # 현재가 조회
            daily_data = self.broker.get_daily_prices(code, count=5)
            if not daily_data:
                write_log(f"[NH-Hunter 제외] {name}({code}) 시세 조회 실패")
                continue

            curr_price = float(daily_data[0].get("stck_clpr", 0))
            if curr_price <= 0:
                continue

            qty = math.floor(budget_per_stock / curr_price)
            if qty <= 0:
                write_log(
                    f"[NH-Hunter 제외] {name}({code}) "
                    f"예산부족(예산:{budget_per_stock:,.0f} / 가격:{curr_price:,.0f})"
                )
                continue

            candidates.append({
                "code":    code,
                "name":    name,
                "price":   curr_price,
                "qty":     qty,
                "market":  self._market_index_code(code),  # 시황 체크용
            })
            write_log(
                f"[NH-Hunter 후보] {name}({code}) "
                f"현재가:{curr_price:,.0f} 수량:{qty}주 "
                f"({'코스피' if code.startswith('0') else '코스닥'})"
            )
            time.sleep(0.3)

        self.buy_candidates = candidates[:can_buy]
        write_log(
            f"[NH-Hunter] 매수 후보 {len(self.buy_candidates)}종목 확정 "
            f"→ 15:20 시황 확인 후 시장가 실행 예정"
        )

    def execute_buy(self):
        """
        15:20 — 시황 조건 최종 확인 후 시장가 매수.
        코스피 종목: 코스피 종가 > MA5
        코스닥 종목: 코스닥 종가 > MA5
        조건 미충족 종목은 스킵.
        """
        if not self.buy_candidates:
            write_log("[NH-Hunter] 15:20 매수 후보 없음")
            return

        for cand in self.buy_candidates:
            code         = cand["code"]
            name         = cand["name"]
            qty          = cand["qty"]
            price        = cand["price"]
            market_code  = cand["market"]

            # ── 시황 조건 체크 ─────────────────────────────────────────
            market_label = "코스피" if market_code == "0001" else "코스닥"
            index_ok = self._check_index_above_ma5(market_code)
            if not index_ok:
                write_log(
                    f"[NH-Hunter 시황제외] {name}({code}) "
                    f"{market_label} 종가 < MA5 → 매수 스킵"
                )
                continue

            # ── 주문 실행 ──────────────────────────────────────────────
            write_log(
                f"[NH-Hunter 매수] {name}({code}) {qty}주 시장가 주문 "
                f"(기준가:{price:,.0f} / {market_label} MA5 위 ✅)"
            )
            if self.broker.place_order(code, qty, is_buy=True):
                trade_id = str(uuid.uuid4())[:8]
                if self.gs_manager:
                    self.gs_manager.log_buy(
                        trade_id, code, name, price, qty,
                        "NH_Hunter", self.mode
                    )
                self.bought_today.add(code)
                self.position_dates[code] = date.today()
            time.sleep(0.5)

        self.buy_candidates.clear()

    # ──────────────────────────────────────────────────────────────────
    # 메인 틱 루프
    # ──────────────────────────────────────────────────────────────────

    def tick(self, now: datetime):
        """
        타임라인:
          00:00         → 일일 상태 초기화
          08:20 (1회)   → 세션 초기화 로그
          09:00 (1회)   → morning_sell (갭 +5% 익절 / -3% 손절)
          09:10 (1회)   → force_close (3일 초과 강제 청산)
          09:10~14:59   → monitor_and_sell 30초마다
          15:00 (1회)   → run_entry_check (후보 탐색)
          15:15 (1회)   → check_aged_positions (3일 경과 종가 청산)
          15:20 (1회)   → execute_buy (시황 조건 포함 매수 실행)
        """
        # 자정 초기화
        if now.hour == 0 and now.minute == 0 and now.second < 5:
            self.reset_daily_state()

        # 08:20 세션 초기화
        if now.hour == 8 and now.minute >= 20 and not self.has_executed_morning_reset:
            write_log("[@NH-Hunter] 08:20 세션 초기화")
            self.has_executed_morning_reset = True

        if now.hour == 8 and now.minute >= 30 and not self.has_executed_morning_report:
            write_log("[@NH-Hunter] 장 개시 대기 중 (전날 포지션 09:00 갭 확인 예정)")
            self.has_executed_morning_report = True

        # 09:00 갭 확인 매도
        if now.hour == 9 and now.minute == 0 and not self.has_executed_morning_sell:
            write_log(f"[@NH-Hunter] 09:00 갭 확인 ({now.strftime('%H:%M:%S')})")
            self.morning_sell()
            self.has_executed_morning_sell = True

        # 09:10 장기 보유 강제 청산
        if now.hour == 9 and now.minute == 10 and not self.has_executed_force_close:
            write_log(f"[@NH-Hunter] 09:10 장기 보유 강제 청산 ({now.strftime('%H:%M:%S')})")
            self.force_close()
            self.has_executed_force_close = True

        # 09:10~14:59 장 중 모니터링 (30초 주기)
        in_intraday = (
            (now.hour == 9 and now.minute >= 10) or
            (10 <= now.hour <= 14) or
            (now.hour == 15 and now.minute < 0)
        )
        if in_intraday and now.second % 30 == 0:
            self.monitor_and_sell()

        # 15:00 후보 탐색
        if now.hour == 15 and now.minute == 0 and not self.has_executed_entry_check:
            write_log(f"[@NH-Hunter] 15:00 종가베팅 후보 탐색 ({now.strftime('%H:%M:%S')})")
            self.run_entry_check()
            self.has_executed_entry_check = True

        # 15:15 보유 3일 경과 종목 종가 청산
        if now.hour == 15 and now.minute == 15 and not self.has_executed_aged_check:
            write_log(f"[@NH-Hunter] 15:15 보유 기간 점검 ({now.strftime('%H:%M:%S')})")
            self.check_aged_positions()
            self.has_executed_aged_check = True

        # 15:20 시황 조건 포함 시장가 매수
        if now.hour == 15 and now.minute == 20 and not self.has_executed_entry_buy:
            write_log(f"[@NH-Hunter] 15:20 매수 실행 ({now.strftime('%H:%M:%S')})")
            self.execute_buy()
            self.has_executed_entry_buy = True
