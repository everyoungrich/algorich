import math
import time
import uuid
from datetime import datetime

from utils import write_log
from .base import BaseStrategy


class MA200TrendStrategy(BaseStrategy):
    """
    MA200_TREND: 장기 추세추종 (200일 이동평균 기준)

    - 매수 유니버스: 최근 6개월간 주도주(거래대금 상위 30위 + 등락률10% 이상) 이력이 있는 종목
      (신규 DB 없이 1_Scan_Audit_Log 재사용, 09:05 1회 스캔 — 15:15/15:20 매수 실행 타이밍과 무관하게
      "전일까지" 데이터로 조기 산출)
    - 유니버스 유지 조건: (6개월 고점 - 현재가) / 6개월 고점 <= 50% (드로다운 필터, 매수 트리거 아님)
    - 매수 (15:15 체크 → 15:20 시장가 실행, 기존 NH-Sniper와 동일 컨벤션):
        MA200_BREAKOUT: 전일종가<=전일MA200 AND 금일종가>금일MA200
        MA200_SUPPORT : 최근 N거래일 내 MA200*0.97~1.03 근접 후 금일종가>금일MA200 마감
        추세 필터(공통): 금일MA200 < 20거래일전MA200 이면 매수 보류
    - 사이징: 종목당 10%, 1일 신규 진입 최대 3종목(총 보유수 제한 아님), 초과 시 거래대금 상위 우선
    - 매도(원 매수수량 기준, 각 단계 1회만, 순서/조합 무관):
        1) 실시간(30초 주기): 현재가 >= 매수가*1.10 → 원 수량의 35% 매도
        2) 종가 기준(15:15/15:20): 금일종가 < 금일MA20  → 원 수량의 35% 매도
        3) 종가 기준(15:15/15:20): 금일종가 < 금일MA200 → 잔량 전량 매도 (항상 최우선 청산)
    - 재시작 복구: 보유 종목을 처음 마주칠 때 매매일지에서 동일 종목코드+매수일자+매수단가
      계보를 조회해 원 매수수량과 이미 실행된 매도단계를 복원 (새 컬럼/시트 없음)
    """
    name = "MA200_TREND"

    MA200_PERIOD = 200
    MA20_PERIOD = 20
    TREND_LOOKBACK = 20          # 추세 필터: N거래일 전 MA200과 비교
    SUPPORT_BAND = 0.03          # MA200 ±3% 근접 밴드
    SUPPORT_LOOKBACK_DAYS = 5    # 근접 확인 최근 N거래일 (금일 제외)
    UNIVERSE_MONTHS = 6
    DRAWDOWN_LIMIT = 0.50
    TP_RATE = 0.10               # +10% 익절
    STAGE_SELL_RATIO = 0.35      # 1·2단계 각 35%
    POSITION_PCT = 0.10          # 종목당 10%
    MAX_NEW_ENTRIES_PER_DAY = 3  # 1일 신규 진입 최대 3종목 (총 보유수 제한 아님)

    def __init__(self, broker, gs_manager=None, mode="모의", dry_run=False):
        super().__init__(broker, gs_manager)
        self.mode = mode
        self.dry_run = dry_run           # True: 실주문 없이 유니버스/신호만 관측·기록
        self.universe_pool = []          # [{"code","name"}]
        self.position_state = {}         # code -> {"original_qty","buy_date","buy_price","stage1_done","stage2_done"}
        self.bought_today = set()
        self.new_entries_today = 0
        self.buy_candidates = []         # 15:15 체크 결과 → 15:20 실행 대기

        self.has_executed_morning_reset = False
        self.has_executed_universe_scan = False
        self.has_executed_entry_check = False
        self.has_executed_entry_buy = False

    # ── 일일 초기화 ─────────────────────────────────────────────
    def reset_daily_state(self):
        self.has_executed_morning_reset = False
        self.has_executed_universe_scan = False
        self.has_executed_entry_check = False
        self.has_executed_entry_buy = False
        self.bought_today.clear()
        self.new_entries_today = 0
        self.buy_candidates.clear()
        # universe_pool / position_state는 일자 경계와 무관하게 유지 (보유 포지션은 수 주간 지속되므로)

    # ── 데이터 변환 ─────────────────────────────────────────────
    def _closes(self, daily_data):
        """get_daily_prices(최신순) → 오름차순 종가 리스트"""
        ordered = list(reversed(daily_data))
        return [float(d.get("stck_clpr", 0)) for d in ordered]

    # ── 매수 신호 판정 ──────────────────────────────────────────
    def check_buy_signal(self, daily_data):
        """반환: (signal_type|None, reason_str)"""
        closes = self._closes(daily_data)
        need = self.MA200_PERIOD + self.TREND_LOOKBACK + 1
        if len(closes) < need:
            return None, "FAIL: LACK_OF_DATA"

        ma200_today = sum(closes[-self.MA200_PERIOD:]) / self.MA200_PERIOD
        ma200_prev  = sum(closes[-self.MA200_PERIOD - 1:-1]) / self.MA200_PERIOD
        ma200_20ago = sum(closes[-self.MA200_PERIOD - self.TREND_LOOKBACK:-self.TREND_LOOKBACK]) / self.MA200_PERIOD

        # 추세 필터: MA200이 20거래일 전보다 낮으면(하락 추세) 매수 보류
        if ma200_today < ma200_20ago:
            return None, f"FAIL: TREND_DOWN (MA200:{ma200_today:,.0f} < 20일전:{ma200_20ago:,.0f})"

        today_close = closes[-1]
        prev_close = closes[-2]

        # (A) MA200_BREAKOUT: 전일 종가<=전일MA200, 금일 종가>금일MA200
        if prev_close <= ma200_prev and today_close > ma200_today:
            return "MA200_BREAKOUT", (f"OK BREAKOUT (전일:{prev_close:,.0f}<=MA200전일:{ma200_prev:,.0f}, "
                                       f"금일:{today_close:,.0f}>MA200:{ma200_today:,.0f})")

        # (B) MA200_SUPPORT: 최근 N일 내 MA200 ±3% 근접 후 금일 종가가 MA200 위로 마감
        if today_close > ma200_today:
            band_lo = ma200_today * (1 - self.SUPPORT_BAND)
            band_hi = ma200_today * (1 + self.SUPPORT_BAND)
            recent = closes[-1 - self.SUPPORT_LOOKBACK_DAYS:-1]  # 금일 제외 최근 N일
            touched = any(band_lo <= c <= band_hi for c in recent)
            if touched:
                return "MA200_SUPPORT", (f"OK SUPPORT (최근{self.SUPPORT_LOOKBACK_DAYS}일 내 "
                                          f"MA200±{self.SUPPORT_BAND*100:.0f}% 근접 후 "
                                          f"금일:{today_close:,.0f}>MA200:{ma200_today:,.0f})")

        return None, f"FAIL: NO_SIGNAL (금일:{today_close:,.0f} MA200:{ma200_today:,.0f})"

    # ── 09:05 유니버스 스캔 (1일 1회) ───────────────────────────
    def scan_universe(self):
        if not self.gs_manager:
            return
        raw = self.gs_manager.get_ma200_trend_universe(months=self.UNIVERSE_MONTHS)
        write_log(f"[MA200_TREND] 유니버스 원본 {len(raw)}종목 (최근 {self.UNIVERSE_MONTHS}개월 주도주 이력)")

        pool = []
        for stock in raw:
            code, name = stock["code"], stock["name"]
            daily_data = self.broker.get_daily_prices(code, count=self.MA200_PERIOD + self.TREND_LOOKBACK + 20)
            closes = self._closes(daily_data)
            if len(closes) < self.MA200_PERIOD:
                continue
            six_month_high = max(closes)
            current_price = closes[-1]
            if six_month_high <= 0:
                continue
            drawdown = (six_month_high - current_price) / six_month_high
            if drawdown <= self.DRAWDOWN_LIMIT:
                pool.append({"code": code, "name": name})
            time.sleep(0.2)

        self.universe_pool = pool
        write_log(f"[MA200_TREND] 09:05 유니버스 스캔 완료 → {len(pool)}종목 "
                  f"(드로다운 {self.DRAWDOWN_LIMIT*100:.0f}% 이내 유지)")

    # ── 재시작 복구: 보유 포지션 상태 lazy 로드 ─────────────────
    def _ensure_position_state(self, code, qty, buy_price):
        if code in self.position_state:
            return
        restored = self.gs_manager.get_ma200_trend_position_state(code) if self.gs_manager else None
        if restored and restored["original_qty"] > 0:
            self.position_state[code] = {
                "original_qty": restored["original_qty"],
                "buy_date": restored["buy_date"],
                "buy_price": restored["buy_price"],
                "stage1_done": "MA200_TREND_TAKE_PROFIT_10" in restored["done_reasons"],
                "stage2_done": "MA200_TREND_EXIT_MA20" in restored["done_reasons"],
            }
            write_log(f"[MA200_TREND] {code} 포지션 상태 복원 "
                      f"(원수량:{restored['original_qty']}, "
                      f"1단계완료:{self.position_state[code]['stage1_done']}, "
                      f"2단계완료:{self.position_state[code]['stage2_done']})")
        else:
            # 매매일지 조회 실패/신규 포지션 → 현재 보유수량을 원 수량으로 간주(폴백)
            self.position_state[code] = {
                "original_qty": qty,
                "buy_date": datetime.now().strftime("%Y-%m-%d"),
                "buy_price": buy_price,
                "stage1_done": False,
                "stage2_done": False,
            }

    # ── 실시간 +10% 익절 모니터링 (30초 주기) ───────────────────
    def monitor_take_profit(self):
        _, tot_eval, holdings = self.broker.get_balance()
        if tot_eval is None:
            return

        for h in holdings:
            code, name = h["code"], h["name"]
            buy_price, cur_price, qty = h["buy_price"], h["current_price"], h["qty"]
            if buy_price <= 0 or qty <= 0:
                continue

            self._ensure_position_state(code, qty, buy_price)
            state = self.position_state[code]
            if state["stage1_done"]:
                continue

            profit_rt = (cur_price - buy_price) / buy_price * 100
            if profit_rt >= self.TP_RATE * 100:
                sell_qty = min(math.floor(state["original_qty"] * self.STAGE_SELL_RATIO), qty)
                if sell_qty > 0:
                    if self.dry_run:
                        write_log(f"[MA200_TREND 관측-익절] {name}({code}) +{profit_rt:.2f}% "
                                  f"→ 매도조건 충족(DRY_RUN, 미실행) 원수량35%={sell_qty}주")
                        state["stage1_done"] = True
                    else:
                        write_log(f"[MA200_TREND 익절] {name}({code}) +{profit_rt:.2f}% → "
                                  f"원수량 35% ({sell_qty}주) 매도")
                        if self.broker.place_order(code, sell_qty, is_buy=False):
                            if self.gs_manager:
                                self.gs_manager.log_sell(code, cur_price, sell_qty,
                                                          is_partial=(sell_qty < qty),
                                                          strategy_type="MA200_TREND",
                                                          sell_reason="MA200_TREND_TAKE_PROFIT_10")
                            state["stage1_done"] = True
            time.sleep(0.2)

    # ── 15:15 종가 기준 매도 체크(MA20/MA200 이탈) + 신규 매수 후보 탐색 ──
    def run_entry_check(self):
        _, tot_eval, holdings = self.broker.get_balance()
        if tot_eval is None:
            write_log("[MA200_TREND] 잔고 조회 실패, 15:15 체크 스킵")
            return

        holding_codes = {h["code"] for h in holdings}

        # (1) 보유 종목 종가 기준 이탈 체크
        for h in holdings:
            code, name, qty = h["code"], h["name"], h["qty"]
            buy_price = h["buy_price"]
            if qty <= 0:
                continue
            self._ensure_position_state(code, qty, buy_price)
            state = self.position_state[code]

            daily_data = self.broker.get_daily_prices(code, count=self.MA200_PERIOD + 10)
            closes = self._closes(daily_data)
            if len(closes) < self.MA200_PERIOD:
                time.sleep(0.2)
                continue

            today_close = closes[-1]
            ma20_today = sum(closes[-self.MA20_PERIOD:]) / self.MA20_PERIOD
            ma200_today = sum(closes[-self.MA200_PERIOD:]) / self.MA200_PERIOD

            # MA200 이탈 → 잔량 전량 청산 (항상 최우선, 1·2단계 상태와 무관)
            if today_close < ma200_today:
                if self.dry_run:
                    write_log(f"[MA200_TREND 관측-전량매도] {name}({code}) 종가 {today_close:,.0f} < "
                              f"MA200 {ma200_today:,.0f} → 청산조건 충족(DRY_RUN, 미실행)")
                else:
                    write_log(f"[MA200_TREND 전량매도] {name}({code}) 종가 {today_close:,.0f} < "
                              f"MA200 {ma200_today:,.0f} → 잔량 {qty}주 전량 매도")
                    if self.broker.place_order(code, qty, is_buy=False):
                        if self.gs_manager:
                            self.gs_manager.log_sell(code, today_close, qty,
                                                      is_partial=False, strategy_type="MA200_TREND",
                                                      sell_reason="MA200_TREND_EXIT_MA200")
                        self.position_state.pop(code, None)
                        holding_codes.discard(code)
                time.sleep(0.2)
                continue

            # MA20 이탈 → 원 수량 35% 매도 (1회만)
            if not state["stage2_done"] and today_close < ma20_today:
                sell_qty = min(math.floor(state["original_qty"] * self.STAGE_SELL_RATIO), qty)
                if sell_qty > 0:
                    if self.dry_run:
                        write_log(f"[MA200_TREND 관측-부분매도] {name}({code}) 종가 {today_close:,.0f} < "
                                  f"MA20 {ma20_today:,.0f} → 매도조건 충족(DRY_RUN, 미실행) 원수량35%={sell_qty}주")
                        state["stage2_done"] = True
                    else:
                        write_log(f"[MA200_TREND 부분매도] {name}({code}) 종가 {today_close:,.0f} < "
                                  f"MA20 {ma20_today:,.0f} → 원수량 35% ({sell_qty}주) 매도")
                        if self.broker.place_order(code, sell_qty, is_buy=False):
                            if self.gs_manager:
                                self.gs_manager.log_sell(code, today_close, sell_qty,
                                                          is_partial=(sell_qty < qty),
                                                          strategy_type="MA200_TREND",
                                                          sell_reason="MA200_TREND_EXIT_MA20")
                            state["stage2_done"] = True
            time.sleep(0.2)

        # (2) 신규 매수 후보 탐색 (유니버스 풀 대상)
        can_buy_today = max(0, self.MAX_NEW_ENTRIES_PER_DAY - self.new_entries_today)
        if can_buy_today <= 0:
            write_log(f"[MA200_TREND] 오늘 신규 진입 한도 소진 "
                      f"({self.new_entries_today}/{self.MAX_NEW_ENTRIES_PER_DAY}종목)")
            self.buy_candidates = []
            return
        if not self.universe_pool:
            write_log("[MA200_TREND] 유니버스 풀 없음, 매수 후보 탐색 스킵")
            self.buy_candidates = []
            return

        budget_per_stock = tot_eval * self.POSITION_PCT
        raw_candidates = []
        for stock in self.universe_pool:
            code, name = stock["code"], stock["name"]
            if code in holding_codes or code in self.bought_today:
                continue
            daily_data = self.broker.get_daily_prices(code, count=self.MA200_PERIOD + self.TREND_LOOKBACK + 20)
            signal_type, reason = self.check_buy_signal(daily_data)
            if signal_type:
                write_log(f"[MA200_TREND 매수체크] {name}({code}): {reason}")
                ordered = list(reversed(daily_data))
                closes = self._closes(daily_data)
                curr_price = float(ordered[-1].get("stck_clpr", 0))
                amt = float(ordered[-1].get("acml_tr_pbmn", 0))  # 오늘 거래대금(원) — 우선순위 기준
                ma20_now = sum(closes[-self.MA20_PERIOD:]) / self.MA20_PERIOD
                ma200_now = sum(closes[-self.MA200_PERIOD:]) / self.MA200_PERIOD
                if self.gs_manager:
                    self.gs_manager.log_ma200_trend_signal(
                        code, name, signal_type, curr_price, ma20_now, ma200_now,
                        executed=False, remark="DRY_RUN 관측" if self.dry_run else "선정대기",
                    )
                if curr_price > 0:
                    qty = math.floor(budget_per_stock / curr_price)
                    if qty > 0:
                        raw_candidates.append({
                            "code": code, "name": name, "price": curr_price,
                            "qty": qty, "signal_type": signal_type, "amt": amt,
                        })
            time.sleep(0.2)

        # 3개 초과 신호 → 거래대금 상위 우선, 신규 진입 한도만큼만 채택
        raw_candidates.sort(key=lambda c: c["amt"], reverse=True)
        self.buy_candidates = raw_candidates[:can_buy_today]
        write_log(f"[MA200_TREND] 15:15 매수 후보 {len(self.buy_candidates)}종목 확정 "
                  f"(신호 {len(raw_candidates)}종목 중 거래대금 상위) → 15:20 시장가 실행 예정")

    # ── 15:20 시장가 매수 실행 ───────────────────────────────────
    def execute_buy(self):
        if not self.buy_candidates:
            write_log("[MA200_TREND] 15:20 매수 후보 없음")
            return

        today_str = datetime.now().strftime("%Y-%m-%d")
        for cand in self.buy_candidates:
            code, name, qty = cand["code"], cand["name"], cand["qty"]

            if self.dry_run:
                write_log(f"[MA200_TREND 관측-매수선정] {name}({code}) {qty}주 매수 선정"
                          f"(DRY_RUN, 미실행) ({cand['signal_type']})")
                if self.gs_manager:
                    self.gs_manager.log_ma200_trend_signal(
                        code, name, cand["signal_type"], cand["price"], 0, 0,
                        executed=False, remark="DRY_RUN 최종선정(미실행)",
                    )
                continue

            write_log(f"[MA200_TREND 매수] {name}({code}) {qty}주 시장가 주문 ({cand['signal_type']})")
            if self.broker.place_order(code, qty, is_buy=True):
                trade_id = str(uuid.uuid4())[:8]
                if self.gs_manager:
                    self.gs_manager.log_buy(trade_id, code, name, cand["price"], qty,
                                             "MA200_TREND", self.mode, remark=cand["signal_type"])
                    self.gs_manager.log_ma200_trend_signal(
                        code, name, cand["signal_type"], cand["price"], 0, 0,
                        executed=True, remark="매수실행",
                    )
                self.bought_today.add(code)
                self.new_entries_today += 1
                self.position_state[code] = {
                    "original_qty": qty, "buy_date": today_str, "buy_price": cand["price"],
                    "stage1_done": False, "stage2_done": False,
                }
            time.sleep(0.5)

        self.buy_candidates = []

    # ── 메인 틱 ──────────────────────────────────────────────────
    def tick(self, now):
        if now.hour == 0 and now.minute == 0 and now.second < 5:
            self.reset_daily_state()

        if now.weekday() >= 5:
            return

        if now.hour == 8 and now.minute >= 20 and not self.has_executed_morning_reset:
            write_log("[@System] 08:20 MA200_TREND 세션 초기화")
            self.has_executed_morning_reset = True

        # 09:05 (1회) — 유니버스 스캔 (전일까지 데이터 기반 → 15:15/15:20 타이밍과 무관하게 조기 실행)
        if now.hour == 9 and now.minute == 5 and not self.has_executed_universe_scan:
            write_log(f"[@MA200_TREND] 09:05 유니버스 스캔 시작 ({now.strftime('%H:%M:%S')})")
            self.scan_universe()
            self.has_executed_universe_scan = True

        # 09:00~15:14 — 30초마다 실시간 익절 모니터링
        in_intraday = (now.hour >= 9) and (now.hour < 15 or (now.hour == 15 and now.minute < 15))
        if in_intraday and now.second % 30 == 0:
            self.monitor_take_profit()

        # 15:15 (1회) — 보유 종목 종가 기준 이탈 체크 + 신규 매수 후보 탐색
        if now.hour == 15 and now.minute == 15 and not self.has_executed_entry_check:
            write_log(f"[@MA200_TREND] 15:15 진입/청산 체크 시작 ({now.strftime('%H:%M:%S')})")
            if not self.broker.check_market_crash():
                self.run_entry_check()
            else:
                write_log("[MA200_TREND] 코스피 급락 감지 - 당일 매수/청산 체크 스킵 (NH-Sniper와 동일 정책)")
            self.has_executed_entry_check = True

        # 15:20 (1회) — 시장가 매수 실행
        if now.hour == 15 and now.minute == 20 and not self.has_executed_entry_buy:
            write_log(f"[@MA200_TREND] 15:20 시장가 매수 실행 ({now.strftime('%H:%M:%S')})")
            self.execute_buy()
            self.has_executed_entry_buy = True
