"""
risk_manager.py — 리스크 관리 모듈
===================================
실거래에서 자금을 보호하기 위한 안전장치를 제공합니다.
모든 주문은 이 모듈의 검증을 통과해야 실행됩니다.

주요 기능:
- 1회 최대 매수 금액 제한
- 최소 KRW 잔고 유지
- 최대 동시 포지션 수 제한
- 손절/트레일링 스탑 퍼센트 관리
- 동일 신호 연속 진입 방지
- 쿨다운 (최근 주문 후 대기 시간)
- 변동성 과다 시 진입 제한
- API 실패 시 주문 중단
"""

import time
from dataclasses import dataclass, field

from .config import RISK_CFG
from .logger import get_logger

logger = get_logger(__name__)


@dataclass
class RiskState:
    """리스크 관리 상태를 추적하는 데이터 클래스"""
    # 현재 보유 포지션 수
    current_positions: int = 0

    # 마지막 주문 시간 (Unix timestamp)
    last_order_time: float = 0.0

    # 마지막 신호
    last_signal: str = ""

    # 연속 동일 신호 카운트
    consecutive_same_signal: int = 0

    # API 실패 플래그
    api_failed: bool = False

    # 최근 거래 가격 기록 (손절/트레일링 스탑 판단용)
    entry_prices: dict = field(default_factory=dict)  # {market: price}
    peak_prices: dict = field(default_factory=dict)   # {market: 최고가}


class RiskManager:
    """
    리스크 관리자.
    매수/매도 전 모든 리스크 조건을 확인합니다.

    사용법:
        rm = RiskManager()
        allowed, reason = rm.check_buy(signal, krw_balance, current_price, atr)
        if allowed:
            # 주문 실행
            rm.record_buy("KRW-BTC", buy_price)
        else:
            logger.info(f"매수 차단: {reason}")
    """

    def __init__(self, buy_ratio_pct: float = 0, min_krw_reserve: float = 0,
                 stop_loss_pct: float = 0, take_profit_pct: float = 0,
                 trailing_stop_pct: float = 0, min_net_profit_pct: float = 0,
                 max_positions: int = 0, cooldown_seconds: int = 0,
                 max_consecutive_buy_signals: int = 0):
        self.buy_ratio_pct = buy_ratio_pct or RISK_CFG.buy_ratio_pct
        self.min_krw_reserve = min_krw_reserve or RISK_CFG.min_krw_reserve
        self.stop_loss_pct = stop_loss_pct or RISK_CFG.stop_loss_pct
        self.take_profit_pct = take_profit_pct or RISK_CFG.take_profit_pct
        self.trailing_stop_pct = trailing_stop_pct or RISK_CFG.trailing_stop_pct
        self.min_net_profit_pct = min_net_profit_pct or RISK_CFG.min_net_profit_pct
        self.max_positions = max_positions or RISK_CFG.max_positions
        self.cooldown_seconds = cooldown_seconds or RISK_CFG.cooldown_seconds
        self.max_consecutive_buy_signals = (
            max_consecutive_buy_signals or RISK_CFG.max_consecutive_buy_signals
        )

        # 상태 초기화
        self.state = RiskState()

    # ============================================================
    # 매수 조건 확인
    # ============================================================
    def check_buy(self, signal: str, krw_balance: float,
                  current_price: float = 0,
                  atr: float = 0, avg_atr: float = 0) -> tuple[bool, str]:
        """
        매수 가능 여부를 확인합니다.
        모든 리스크 조건을 순서대로 검사합니다.

        Args:
            signal: 전략 신호 ("BUY", "SELL", "HOLD")
            krw_balance: 현재 KRW 잔고
            current_price: 현재가 (변동성 체크에 사용)
            atr: 현재 ATR (변동성 판단)
            avg_atr: 평균 ATR (변동성 비교 기준)

        Returns:
            tuple: (허용 여부, 사유 문자열)
        """
        # 1. 신호 확인
        if signal != "BUY":
            return False, "매수 신호가 아닙니다"

        # 2. API 실패 상태 확인
        if self.state.api_failed:
            return False, "API 오류 발생 중 — 주문 중단"

        # 3. 최대 포지션 수 확인 (0 이하면 제한 없음)
        if self.max_positions > 0 and self.state.current_positions >= self.max_positions:
            return False, (f"최대 포지션 수 초과: "
                         f"{self.state.current_positions}/{self.max_positions}")

        # 4. 최소 KRW 잔고 유지 확인
        available = krw_balance - self.min_krw_reserve
        if available <= 0:
            return False, (f"KRW 잔고 부족: {krw_balance:,.0f} KRW "
                         f"(최소 유지: {self.min_krw_reserve:,.0f})")

        # 5. 쿨다운 확인
        if self.state.last_order_time > 0:
            elapsed = time.time() - self.state.last_order_time
            if elapsed < self.cooldown_seconds:
                remaining = self.cooldown_seconds - elapsed
                return False, f"쿨다운 중: {remaining:.0f}초 남음"

        # 6. 동일 신호 연속 진입 방지 (0 이하면 비활성)
        if (
            self.max_consecutive_buy_signals > 0
            and self.state.last_signal == "BUY"
            and self.state.consecutive_same_signal >= self.max_consecutive_buy_signals
        ):
            block_from = self.max_consecutive_buy_signals + 1
            return False, f"동일 매수 신호 {block_from}회 이상 연속 — 진입 방지"

        # 7. 변동성 과다 체크 (ATR이 제공된 경우)
        if atr > 0 and avg_atr > 0:
            if atr > avg_atr * 2.0:
                return False, (f"변동성 과다: ATR({atr:,.0f}) > "
                             f"평균ATR({avg_atr:,.0f}) × 2")

        return True, "매수 가능"

    # ============================================================
    # 매도 조건 확인
    # ============================================================
    def check_sell(self, signal: str, market: str = "",
                   current_price: float = 0) -> tuple[bool, str]:
        """
        매도 가능 여부를 확인합니다.

        Args:
            signal: 전략 신호
            market: 마켓 코드
            current_price: 현재가

        Returns:
            tuple: (허용 여부, 사유 문자열)
        """
        # API 실패 시 기존 포지션 매도는 허용 (안전 처분)
        if self.state.api_failed:
            logger.info("API 오류 상태이지만 안전 매도는 허용합니다.")

        if signal != "SELL":
            return False, "매도 신호가 아닙니다"

        if self.state.current_positions <= 0:
            return False, "보유 포지션이 없습니다"

        return True, "매도 가능"

    # ============================================================
    # 손절/트레일링 스탑 판단
    # ============================================================
    def check_stop_loss(self, market: str, current_price: float
                        ) -> tuple[bool, str]:
        """
        손절 조건을 확인합니다.

        Args:
            market: 마켓 코드
            current_price: 현재가

        Returns:
            tuple: (손절 필요 여부, 사유)
        """
        entry_price = self.state.entry_prices.get(market, 0)
        if entry_price <= 0:
            return False, "진입가 기록 없음"

        change_pct = (current_price - entry_price) / entry_price

        if change_pct <= -self.stop_loss_pct:
            return True, (f"손절 도달: {change_pct * 100:.2f}% "
                        f"(기준: -{self.stop_loss_pct * 100:.1f}%)")

        return False, ""

    def update_peak_price(self, market: str, current_price: float) -> None:
        """보유 포지션의 최고가를 갱신합니다."""
        prev_peak = self.state.peak_prices.get(market, 0)
        if current_price > prev_peak:
            self.state.peak_prices[market] = current_price

    def check_trailing_stop(self, market: str, current_price: float
                            ) -> tuple[bool, str]:
        """
        트레일링 스탑 조건을 확인합니다.

        Args:
            market: 마켓 코드
            current_price: 현재가

        Returns:
            tuple: (청산 필요 여부, 사유)
        """
        if self.trailing_stop_pct <= 0:
            return False, ""

        entry_price = self.state.entry_prices.get(market, 0)
        if entry_price <= 0:
            return False, "진입가 기록 없음"

        self.update_peak_price(market, current_price)
        peak_price = self.state.peak_prices.get(market, 0)
        if peak_price <= 0:
            return False, ""

        # 진입가를 넘지 못한 상태에서는 트레일링 스탑을 적용하지 않음
        if peak_price <= entry_price:
            return False, ""

        pullback_pct = (peak_price - current_price) / peak_price
        if pullback_pct >= self.trailing_stop_pct:
            peak_gain_pct = (peak_price - entry_price) / entry_price
            return True, (f"트레일링 스탑 도달: 고점대비 -{pullback_pct * 100:.2f}% "
                        f"(기준: -{self.trailing_stop_pct * 100:.1f}%, "
                        f"최고수익: +{peak_gain_pct * 100:.2f}%)")

        return False, ""

    def check_min_net_profit_exit(self, market: str, current_price: float,
                                  estimated_cost_pct: float = 0.0
                                  ) -> tuple[bool, str]:
        """
        추세 매도(TREND_EXIT) 전에 최소 순이익 기준을 충족하는지 확인합니다.

        Args:
            market: 마켓 코드
            current_price: 현재가
            estimated_cost_pct: 왕복 비용 추정치(%, 수수료+슬리피지 등)

        Returns:
            tuple: (허용 여부, 사유)
        """
        entry_price = self.state.entry_prices.get(market, 0)
        if entry_price <= 0:
            return False, "진입가 기록 없음"

        gross_pct = (current_price - entry_price) / entry_price * 100
        net_pct = gross_pct - estimated_cost_pct
        if net_pct < self.min_net_profit_pct:
            return False, (
                f"순이익 기준 미달: {net_pct:.3f}% < "
                f"{self.min_net_profit_pct:.3f}%"
            )
        return True, "순이익 기준 충족"

    def check_take_profit_net(self, market: str, current_price: float,
                              estimated_cost_pct: float = 0.0
                              ) -> tuple[bool, str]:
        """
        순이익 기준 고정 익절 조건을 확인합니다.

        Args:
            market: 마켓 코드
            current_price: 현재가
            estimated_cost_pct: 왕복 비용 추정치(%, 수수료+슬리피지 등)

        Returns:
            tuple: (익절 도달 여부, 사유)
        """
        if self.take_profit_pct <= 0:
            return False, ""

        entry_price = self.state.entry_prices.get(market, 0)
        if entry_price <= 0:
            return False, "진입가 기록 없음"

        gross_pct = (current_price - entry_price) / entry_price * 100
        net_pct = gross_pct - estimated_cost_pct
        target_pct = self.take_profit_pct * 100
        if net_pct >= target_pct:
            return True, (
                f"익절 도달: 순이익 {net_pct:.2f}% >= 목표 {target_pct:.2f}%"
            )
        return False, ""

    # ============================================================
    # 상태 업데이트
    # ============================================================
    def get_buy_amount(self, krw_balance: float) -> float:
        """
        실제 매수 가능 금액을 계산합니다.
        잔고의 buy_ratio_pct 비율만큼 매수합니다. (기본 30%)

        Args:
            krw_balance: 현재 KRW 잔고

        Returns:
            float: 매수 금액 (KRW)
        """
        available = krw_balance - self.min_krw_reserve
        if available <= 0:
            return 0.0
        buy_amount = available * self.buy_ratio_pct
        return max(buy_amount, 0)

    def record_buy(self, market: str, price: float) -> None:
        """매수 체결 후 상태를 업데이트합니다."""
        self.state.current_positions += 1
        self.state.last_order_time = time.time()
        self.state.entry_prices[market] = price
        self.state.peak_prices[market] = price

        # 동일 신호 카운트
        if self.state.last_signal == "BUY":
            self.state.consecutive_same_signal += 1
        else:
            self.state.consecutive_same_signal = 1
        self.state.last_signal = "BUY"

        logger.info(f"[리스크] 매수 기록: {market} @ {price:,.0f}")

    def record_sell(self, market: str) -> None:
        """매도 체결 후 상태를 업데이트합니다."""
        self.state.current_positions = max(0, self.state.current_positions - 1)
        self.state.last_order_time = time.time()

        if market in self.state.entry_prices:
            del self.state.entry_prices[market]
        if market in self.state.peak_prices:
            del self.state.peak_prices[market]

        # 동일 신호 카운트
        if self.state.last_signal == "SELL":
            self.state.consecutive_same_signal += 1
        else:
            self.state.consecutive_same_signal = 1
        self.state.last_signal = "SELL"

        logger.info(f"[리스크] 매도 기록: {market}")

    def set_api_failed(self, failed: bool = True) -> None:
        """API 실패 상태를 설정합니다."""
        self.state.api_failed = failed
        if failed:
            logger.error("[리스크] API 실패 — 모든 신규 주문 중단")
        else:
            logger.info("[리스크] API 복구 — 주문 재개")

    def reset(self) -> None:
        """리스크 상태를 초기화합니다."""
        self.state = RiskState()
        logger.info("[리스크] 상태 초기화")
