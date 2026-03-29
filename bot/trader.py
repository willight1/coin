"""
trader.py — 실거래 엔진
========================
검증을 통과한 전략 1개를 실행하여 자동 매매합니다.

핵심 원칙:
- 기본 주문 판단은 전략 함수가 반환한 BUY / SELL / HOLD 를 사용합니다.
- 선택적으로 BUY에 한해 LLM 게이트를 보조 필터로 사용할 수 있습니다.
- DRY_RUN / LIVE 모드를 분리합니다.
- KeyboardInterrupt로 안전 종료합니다.
- 예외 발생 시 안전 대기 후 재시도합니다.

사용법:
    trader = Trader(strategy, upbit_client, risk_manager)
    trader.run()  # 무한 루프 (Ctrl+C로 종료)
"""

import os
import time
import traceback
from datetime import datetime

import pandas as pd

from .config import UPBIT_CFG, BOT_CFG, RISK_CFG, BT_CFG
from .logger import get_logger
from .upbit_client import UpbitClient, UpbitClientError
from .risk_manager import RiskManager
from .strategies import BaseStrategy, BUY, SELL, HOLD
from .indicators import calc_atr
from .llm_gate import LLMGate

logger = get_logger(__name__)
MIN_ORDER_KRW = 5000.0


class Trader:
    """
    실거래 엔진.
    검증된 전략 1개로 주기적으로 시세를 조회하고, 신호를 계산하여
    주문을 실행합니다.

    Args:
        strategy: 실거래에 사용할 전략 (검증 통과 전략)
        client: 업비트 API 클라이언트
        risk_manager: 리스크 관리자
        market: 거래 대상 마켓
        interval: 조회 주기 (초)
        candle_unit: 분봉 단위
        candle_count: 조회할 캔들 수
    """

    def __init__(self, strategy: BaseStrategy,
                 client: UpbitClient | None = None,
                 risk_manager: RiskManager | None = None,
                 market: str = "",
                 interval: int = 0,
                 candle_unit: int = 1,
                 candle_count: int = 200,
                 max_chase_pct: float = 0):
        self.strategy = strategy
        self.client = client or UpbitClient(dry_run=BOT_CFG.dry_run)
        self.risk_manager = risk_manager or RiskManager()
        self.market = market or UPBIT_CFG.market
        self.interval = interval or BOT_CFG.interval_seconds
        self.candle_unit = candle_unit
        self.candle_count = candle_count
        self.max_chase_pct = max_chase_pct or RISK_CFG.max_chase_pct
        self.last_signal_order_candle = ""
        self.last_snapshot_candle = ""
        self.last_notified_signal = ""
        self.log_signal_change_only = (
            os.getenv("BOT_LOG_SIGNAL_CHANGE_ONLY", "true").strip().lower()
            in ("1", "true", "yes", "y")
        )
        self.state_synced = False
        self.llm_gate = LLMGate()
        self.estimated_roundtrip_cost_pct = (
            (BT_CFG.fee_rate + BT_CFG.slippage_rate) * 2 * 100
        )

        # 거래 대상 화폐 코드 (예: "BTC")
        self.currency = self.market.split("-")[1] if "-" in self.market else ""

        logger.info(f"트레이더 초기화: 전략={strategy.name}, "
                     f"마켓={self.market}, 주기={self.interval}초")
        logger.info(f"  DRY_RUN: {self.client.dry_run}")
        logger.info(f"  추격매수 제한: {self.max_chase_pct * 100:.2f}%")
        logger.info(f"  최소 순이익 매도 기준: {RISK_CFG.min_net_profit_pct:.3f}%")
        logger.info(f"  LLM 게이트 사용: {self.llm_gate.enabled}")
        logger.info(f"  신호변경 로그 모드: {self.log_signal_change_only}")

    def run(self) -> None:
        """
        메인 트레이딩 루프.
        Ctrl+C로 안전 종료할 수 있습니다.
        """
        logger.info("=" * 50)
        logger.info("실거래 엔진 시작")
        logger.info(f"  전략: {self.strategy.name}")
        logger.info(f"  마켓: {self.market}")
        logger.info(f"  DRY_RUN: {self.client.dry_run}")
        logger.info("=" * 50)
        self._sync_existing_position()
        self._log_account_snapshot("START")

        if not self.client.dry_run:
            logger.warning("⚠️  LIVE 모드입니다! 실제 주문이 체결됩니다!")
            logger.warning("⚠️  5초 후 시작합니다. Ctrl+C로 취소할 수 있습니다.")
            time.sleep(5)

        try:
            while True:
                try:
                    self._tick()
                except UpbitClientError as e:
                    logger.error(f"API 오류: {e}")
                    self.risk_manager.set_api_failed(True)
                    logger.info("60초 후 재시도합니다...")
                    time.sleep(60)
                    self.risk_manager.set_api_failed(False)
                except Exception as e:
                    logger.error(f"예상치 못한 오류: {e}")
                    logger.error(traceback.format_exc())
                    logger.info("30초 후 재시도합니다...")
                    time.sleep(30)

                # 다음 주기까지 대기
                if not self.log_signal_change_only:
                    logger.info(f"다음 조회까지 {self.interval}초 대기...")
                time.sleep(self.interval)

        except KeyboardInterrupt:
            logger.info("\n사용자 종료 요청 (Ctrl+C)")
            self._safe_shutdown()

    def run_once(self) -> None:
        """
        트레이딩 사이클을 1회만 실행합니다.
        멀티마켓 오케스트레이션에서 사용합니다.
        """
        try:
            self._sync_existing_position()
            self._tick()
        except UpbitClientError as e:
            logger.error(f"[{self.market}] API 오류: {e}")
            self.risk_manager.set_api_failed(True)
        except Exception as e:
            logger.error(f"[{self.market}] 예상치 못한 오류: {e}")
            logger.error(traceback.format_exc())

    def _sync_existing_position(self) -> None:
        """
        시작 시 기존 보유 포지션을 리스크 상태에 1회 동기화합니다.
        봇 실행 전 수동 매수된 물량도 손절/트레일링/매도 로직에서 인식됩니다.
        """
        if self.state_synced or not self.currency:
            return

        try:
            volume = self.client.get_balance(self.currency)
            if volume <= 0:
                self.state_synced = True
                return

            avg_buy_price = self.client.get_avg_buy_price(self.currency)
            if avg_buy_price <= 0:
                ticker = self.client.get_ticker(self.market)
                avg_buy_price = float(ticker.get("trade_price", 0))

            if avg_buy_price <= 0:
                logger.warning(
                    f"[동기화] 기존 보유 감지({self.market})했지만 기준 가격 조회 실패"
                )
                self.state_synced = True
                return

            self.risk_manager.state.entry_prices[self.market] = avg_buy_price
            self.risk_manager.state.peak_prices[self.market] = avg_buy_price
            self.risk_manager.state.current_positions = max(
                self.risk_manager.state.current_positions, 1
            )
            logger.info(
                f"[동기화] 기존 보유 반영: {self.market} volume={volume:.8f}, "
                f"avg_buy={avg_buy_price:,.0f}"
            )
            self.state_synced = True
        except UpbitClientError as e:
            logger.info(f"[동기화] 기존 보유 반영 실패(재시도 예정): {e}")
        except Exception as e:
            logger.info(f"[동기화] 기존 보유 반영 예외(재시도 예정): {e}")

    def _tick(self) -> None:
        """
        한 번의 트레이딩 사이클을 실행합니다.

        1. 시세 데이터 조회
        2. 전략 신호 계산
        3. 손절/추세이탈/트레일링스탑 확인
        4. 리스크 조건 확인
        5. 주문 실행
        """
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if not self.log_signal_change_only:
            logger.info(f"[{now}] 트레이딩 사이클 시작")

        # ---- 1. 캔들 데이터 조회 ----
        candles = self.client.get_candles_minutes(
            unit=self.candle_unit,
            market=self.market,
            count=self.candle_count,
        )

        if not candles:
            logger.info("캔들 데이터가 비어 있습니다.")
            return

        # 업비트 API는 최신 순으로 반환 → 시간 오름차순으로 정렬
        df = pd.DataFrame(candles)
        df = df.sort_values("candle_date_time_kst").reset_index(drop=True)

        # 컬럼명 표준화
        df = df.rename(columns={
            "opening_price": "open",
            "high_price": "high",
            "low_price": "low",
            "trade_price": "close",
            "candle_acc_trade_volume": "volume",
        })

        required_cols = ["open", "high", "low", "close", "volume"]
        for col in required_cols:
            if col not in df.columns:
                logger.error(f"필수 컬럼 '{col}'이 없습니다.")
                return

        current_price = df["close"].iloc[-1]
        if not self.log_signal_change_only:
            logger.info(f"  현재가: {current_price:,.0f} KRW")

        if len(df) < 2:
            logger.info("신호 계산에 필요한 캔들이 부족합니다.")
            return

        # 진행 중인 현재 봉은 제외하고 직전 확정봉 기준으로 신호를 계산합니다.
        signal_df = df.iloc[:-1].copy()
        signal = self.strategy.generate_signal(signal_df)
        signal_price = signal_df["close"].iloc[-1]
        signal_candle = str(
            signal_df["candle_date_time_kst"].iloc[-1]
            if "candle_date_time_kst" in signal_df.columns
            else signal_df.index[-1]
        )
        if signal != self.last_notified_signal:
            prev = self.last_notified_signal or "NONE"
            logger.info(
                f"  전략 신호 변경: {prev} -> {signal} "
                f"(확정봉: {signal_candle}, 현재가: {current_price:,.0f} KRW)"
            )
            self.last_notified_signal = signal
        elif not self.log_signal_change_only:
            logger.info(f"  전략 신호: {signal} (확정봉: {signal_candle})")

        # 분 단위 계좌 스냅샷 (수익률 분석용)
        if signal_candle != self.last_snapshot_candle:
            self._log_account_snapshot("TICK")
            self.last_snapshot_candle = signal_candle

        # ---- 3. 손절/추세이탈/트레일링스탑 확인 ----
        if self.risk_manager.state.current_positions > 0:
            stop, stop_reason = self.risk_manager.check_stop_loss(
                self.market, current_price)
            if stop:
                logger.warning(f"  {stop_reason}")
                self._execute_sell(current_price, reason="STOP_LOSS")
                return

            take_profit, take_profit_reason = self.risk_manager.check_take_profit_net(
                self.market,
                current_price,
                estimated_cost_pct=self.estimated_roundtrip_cost_pct,
            )
            if take_profit:
                logger.info(f"  {take_profit_reason}")
                self._execute_sell(current_price, reason="TAKE_PROFIT")
                return

            # 추세선(전략) 이탈은 트레일링 스탑보다 우선
            if signal == SELL:
                if self._is_duplicate_signal_order(signal_candle):
                    logger.info("  매도 차단: 같은 확정봉에서 중복 신호 주문 방지")
                    return
                min_profit_ok, min_profit_reason = (
                    self.risk_manager.check_min_net_profit_exit(
                        self.market,
                        current_price,
                        estimated_cost_pct=self.estimated_roundtrip_cost_pct,
                    )
                )
                if not min_profit_ok:
                    logger.info(f"  매도 보류: {min_profit_reason}")
                    return
                allowed, reason = self.risk_manager.check_sell(
                    signal, self.market, current_price)
                if allowed:
                    if self._execute_sell(current_price, reason="TREND_EXIT"):
                        self.last_signal_order_candle = signal_candle
                else:
                    logger.info(f"  매도 차단: {reason}")
                return

            trailing, trailing_reason = self.risk_manager.check_trailing_stop(
                self.market, current_price)
            if trailing:
                logger.info(f"  {trailing_reason}")
                self._execute_sell(current_price, reason="TRAILING_STOP")
                return

        # ---- 4. 매수 처리 ----
        if signal == BUY:
            # ATR 기반 변동성 체크 (선택적)
            atr = 0
            avg_atr = 0
            if len(signal_df) >= 30:
                atr_series = calc_atr(
                    signal_df["high"], signal_df["low"], signal_df["close"], 14
                )
                if not atr_series.dropna().empty:
                    atr = atr_series.iloc[-1]
                    avg_atr = atr_series.dropna().mean()

            # KRW 잔고 조회
            try:
                krw_balance = self.client.get_balance("KRW")
                # 인증/API가 복구되면 주문 차단 플래그를 자동 해제
                if self.risk_manager.state.api_failed:
                    self.risk_manager.set_api_failed(False)
            except UpbitClientError as e:
                logger.error(f"  KRW 잔고 조회 실패: {e}")
                self.risk_manager.set_api_failed(True)
                return
            except Exception as e:
                logger.error(f"  KRW 잔고 조회 중 예외 발생: {e}")
                self.risk_manager.set_api_failed(True)
                return

            allowed, reason = self.risk_manager.check_buy(
                signal, krw_balance, current_price, atr, avg_atr)

            if allowed:
                if self._is_duplicate_signal_order(signal_candle):
                    logger.info("  매수 차단: 같은 확정봉에서 중복 신호 주문 방지")
                    return

                if signal_price > 0:
                    chase_pct = (current_price - signal_price) / signal_price
                    if chase_pct > self.max_chase_pct:
                        logger.info(
                            "  매수 차단: 추격매수 제한 초과 "
                            f"(현재 {chase_pct * 100:.2f}% > "
                            f"허용 {self.max_chase_pct * 100:.2f}%)"
                        )
                        return

                buy_amount = self.risk_manager.get_buy_amount(krw_balance)
                if buy_amount > 0:
                    if buy_amount < MIN_ORDER_KRW:
                        logger.info(
                            "  매수 차단: 최소주문금액 미만 "
                            f"({buy_amount:,.0f} KRW < {MIN_ORDER_KRW:,.0f} KRW)"
                        )
                        return

                    gate = self.llm_gate.evaluate_buy(
                        market=self.market,
                        strategy_name=self.strategy.name,
                        signal_candle=signal_candle,
                        current_price=float(current_price),
                        signal_price=float(signal_price),
                        df=signal_df,
                    )
                    if not gate.allow:
                        logger.info(
                            "  매수 차단: LLM 게이트 "
                            f"({gate.source}, conf={gate.confidence:.2f}) {gate.reason}"
                        )
                        return

                    if gate.size_multiplier < 1.0:
                        adjusted = buy_amount * gate.size_multiplier
                        logger.info(
                            "  매수 금액 조정: LLM 게이트 "
                            f"{buy_amount:,.0f} -> {adjusted:,.0f} KRW "
                            f"(x{gate.size_multiplier:.2f}, conf={gate.confidence:.2f})"
                        )
                        buy_amount = adjusted

                    if self._execute_buy(buy_amount, current_price):
                        self.last_signal_order_candle = signal_candle
                else:
                    logger.info("  매수 금액이 0원 — 매수 건너뜀")
            else:
                logger.info(f"  매수 차단: {reason}")

        # ---- 5. 매도 처리 ----
        elif signal == SELL:
            if self._is_duplicate_signal_order(signal_candle):
                logger.info("  매도 차단: 같은 확정봉에서 중복 신호 주문 방지")
                return

            min_profit_ok, min_profit_reason = (
                self.risk_manager.check_min_net_profit_exit(
                    self.market,
                    current_price,
                    estimated_cost_pct=self.estimated_roundtrip_cost_pct,
                )
            )
            if not min_profit_ok:
                logger.info(f"  매도 보류: {min_profit_reason}")
                return

            allowed, reason = self.risk_manager.check_sell(
                signal, self.market, current_price)

            if allowed:
                if self._execute_sell(current_price, reason="TREND_EXIT"):
                    self.last_signal_order_candle = signal_candle
            else:
                logger.info(f"  매도 차단: {reason}")

        else:
            if not self.log_signal_change_only:
                logger.info("  신호: HOLD — 대기")

    def _execute_buy(self, amount: float, current_price: float) -> bool:
        """
        매수 주문을 실행합니다.

        Args:
            amount: 매수 금액 (KRW)
            current_price: 참고용 현재가
        """
        logger.info(f"  >> 매수 실행: {amount:,.0f} KRW")

        try:
            result = self.client.order_market_buy(
                market=self.market, price=amount)
            logger.info(f"  >> 주문 결과: {result}")
            self.risk_manager.record_buy(self.market, current_price)
            return True
        except Exception as e:
            logger.error(f"  >> 매수 주문 실패: {e}")
            self.risk_manager.set_api_failed(True)
            return False

    def _execute_sell(self, current_price: float, reason: str = "") -> bool:
        """
        매도 주문을 실행합니다.

        Args:
            current_price: 참고용 현재가
            reason: 매도 사유 (TREND_EXIT, STOP_LOSS, TRAILING_STOP)
        """
        logger.info(f"  >> 매도 실행 (사유: {reason})")

        try:
            # 보유 수량 조회
            volume = self.client.get_balance(self.currency)

            if volume <= 0:
                logger.info("  >> 매도할 수량이 없습니다.")
                self.risk_manager.record_sell(self.market)
                return False

            result = self.client.order_market_sell(
                market=self.market, volume=volume)
            logger.info(f"  >> 주문 결과: {result}")
            self.risk_manager.record_sell(self.market)
            return True

        except Exception as e:
            logger.error(f"  >> 매도 주문 실패: {e}")
            self.risk_manager.set_api_failed(True)
            return False

    def _is_duplicate_signal_order(self, signal_candle: str) -> bool:
        """같은 확정봉에서 신호 기반 주문이 중복되는 것을 방지합니다."""
        return self.last_signal_order_candle == signal_candle

    def _safe_shutdown(self) -> None:
        """안전 종료 처리"""
        logger.info("안전 종료 중...")
        logger.info(f"  현재 포지션 수: "
                     f"{self.risk_manager.state.current_positions}")
        self._log_account_snapshot("END")
        logger.info("  미체결 주문은 업비트 앱에서 확인하세요.")
        logger.info("트레이더 종료 완료.")

    def _log_account_snapshot(self, label: str) -> None:
        """계좌 스냅샷(추정 총자산)을 로그로 남깁니다."""
        try:
            krw_balance = self.client.get_balance("KRW")
            coin_balance = self.client.get_balance(self.currency)
            ticker = self.client.get_ticker(self.market)
            current_price = float(ticker.get("trade_price", 0))
            coin_value = coin_balance * current_price
            equity = krw_balance + coin_value
            logger.info(
                f"[계좌] {label} market={self.market} equity={equity:.2f} "
                f"krw={krw_balance:.2f} coin={coin_balance:.8f} "
                f"coin_value={coin_value:.2f} price={current_price:.2f}"
            )
        except UpbitClientError as e:
            logger.info(f"[계좌] {label} 스냅샷 실패: {e}")
        except Exception as e:
            logger.info(f"[계좌] {label} 스냅샷 예외: {e}")
