"""
backtester.py — 자체 백테스트 엔진
===================================
과거 캔들 데이터를 이용하여 전략을 시뮬레이션합니다.
외부 백테스트 프레임워크 없이 직접 구현한 엔진입니다.

주요 기능:
- 초기 자본 기반 매수/매도/보유 시뮬레이션
- 수수료, 슬리피지 반영
- 손절/익절/트레일링 스탑은 RiskManager 를 그대로 호출 (실거래와 동일 코드)
- 실거래와 동일한 체결 모델: 직전 확정봉 신호 -> 다음 봉 시가 체결
- 포지션 상태 관리
- 거래 로그 저장 (CSV)
- 전략별 성과 지표 계산 (수익률, MDD, 승률, 샤프 등)
- 전략 비교 리포트 생성

사용법:
    bt = Backtester(initial_capital=1_000_000)
    result = bt.run(strategy, df)
    bt.print_summary(result)
    bt.save_report(result, "reports/my_strategy.csv")
"""

import os
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd
import numpy as np

from .config import BT_CFG, RISK_CFG
from .indicators import calc_atr
from .logger import get_logger
from .paths import PROJECT_ROOT
from .risk_manager import RiskManager, MIN_ORDER_KRW
from .strategies import BUY, SELL, HOLD

logger = get_logger(__name__)


# ============================================================
# 백테스트 결과 데이터 클래스
# ============================================================
@dataclass
class BacktestResult:
    """백테스트 실행 결과를 담는 데이터 클래스"""
    strategy_name: str = ""             # 전략 이름
    strategy_description: str = ""      # 전략 설명

    # 기본 결과
    initial_capital: float = 0.0        # 초기 자본
    final_capital: float = 0.0          # 최종 자본
    total_return_pct: float = 0.0       # 총 수익률 (%)
    period_days: int = 0                # 거래 기간 (일)
    cagr_pct: float = 0.0              # 연환산 수익률 (%)

    # 거래 통계
    total_trades: int = 0               # 총 거래 횟수
    winning_trades: int = 0             # 이긴 거래 수
    losing_trades: int = 0             # 진 거래 수
    win_rate: float = 0.0              # 승률 (%)
    avg_profit_pct: float = 0.0        # 평균 수익 (%)
    avg_loss_pct: float = 0.0          # 평균 손실 (%)
    profit_factor: float = 0.0         # Profit Factor (총이익/총손실)
    avg_return_per_trade: float = 0.0  # 거래당 평균 수익률 (%)

    # 리스크 지표
    max_drawdown_pct: float = 0.0       # 최대 낙폭 MDD (%)
    sharpe_like_ratio: float = 0.0      # 샤프 비율 유사 지표

    # 거래 로그
    trade_log: list = field(default_factory=list)     # 개별 거래 기록
    equity_curve: list = field(default_factory=list)  # 자산 추이


# ============================================================
# 백테스터 엔진
# ============================================================
class Backtester:
    """
    전략을 과거 데이터에 적용하여 성과를 시뮬레이션합니다.

    Args:
        initial_capital: 초기 자본 (KRW)
        fee_rate: 수수료율
        slippage_rate: 슬리피지율
        candle_seconds: 봉 하나의 길이(초) — 쿨다운 판정에 사용
    """

    def __init__(self, initial_capital: float | None = None,
                 fee_rate: float | None = None,
                 slippage_rate: float | None = None,
                 candle_seconds: int = 60):
        # 0 도 유효한 값(비용 0 진단 등) — `x or 기본값` 은 0 을 기본값으로 되돌린다
        def _pick(v, default):
            return default if v is None else v
        self.initial_capital = _pick(initial_capital, BT_CFG.initial_capital)
        self.fee_rate = _pick(fee_rate, BT_CFG.fee_rate)
        self.slippage_rate = _pick(slippage_rate, BT_CFG.slippage_rate)
        self.candle_seconds = candle_seconds
        # 실거래(trader.py)와 동일한 왕복 비용 추정치
        self.roundtrip_cost_pct = (self.fee_rate + self.slippage_rate) * 2 * 100

    def run(self, strategy, df: pd.DataFrame,
            risk_manager: RiskManager | None = None) -> BacktestResult:
        """
        전략을 백테스트합니다.

        실거래(trader.py)와 동일한 의사결정 경로를 사용합니다:
        - 신호는 직전 확정봉 기준 (signals.shift(1)), 매수·추세매도 모두 다음 봉 시가 체결
        - 체결은 다음 봉 시가 + 슬리피지, 수수료는 금액에서 차감
        - 손절/익절/추세매도/트레일링 판단은 RiskManager 를 그대로 호출
        - 매수 금액, 쿨다운, 최대 포지션 수, 추격매수 제한, 최소주문금액 모두 적용

        Args:
            strategy: BaseStrategy 인스턴스
            df: OHLCV 데이터프레임 (시간 오름차순)
                필수 컬럼: open, high, low, close, volume
            risk_manager: 리스크 설정을 바꿔 실험할 때 주입 (기본: .env 설정)

        Returns:
            BacktestResult: 백테스트 결과
        """
        logger.info(f"백테스트 시작: {strategy.name}")
        logger.info(f"  초기 자본: {self.initial_capital:,.0f} KRW")
        logger.info(f"  데이터 기간: {len(df)}봉")
        logger.info(f"  수수료: {self.fee_rate * 100:.3f}%, "
                     f"슬리피지: {self.slippage_rate * 100:.3f}%")

        market = "BACKTEST"
        rm = risk_manager or RiskManager(**strategy.risk_overrides)
        rm.reset()

        cash = self.initial_capital   # 현금 보유량
        position = 0.0                # 보유 수량
        position_cost = 0.0           # 보유분 총 투입금액 (수수료 포함)
        trade_log = []
        equity_curve = []
        trade_returns = []

        # ---- 신호: 직전 확정봉 기준 (실거래와 동일하게 한 봉 지연) ----
        signals = strategy.generate_signals_series(df).shift(1).fillna(HOLD)
        signal_prices = df["close"].shift(1)

        # ---- 변동성: 실거래는 직전 확정봉까지의 ATR 과 그 평균을 쓴다 ----
        atr_series = calc_atr(df["high"], df["low"], df["close"], 14).shift(1)
        avg_atr_series = atr_series.rolling(window=200, min_periods=30).mean()

        opens = df["open"].to_numpy(dtype=float)
        highs = df["high"].to_numpy(dtype=float)
        lows = df["low"].to_numpy(dtype=float)
        closes = df["close"].to_numpy(dtype=float)
        atrs = atr_series.to_numpy(dtype=float)
        avg_atrs = avg_atr_series.to_numpy(dtype=float)

        def _nz(v: float) -> float:
            return 0.0 if (v is None or np.isnan(v)) else float(v)

        def _close_position(exit_price: float, action: str, timestamp,
                            now: float) -> None:
            """보유분 전량을 청산하고 거래를 기록합니다."""
            nonlocal cash, position, position_cost
            proceeds = position * exit_price * (1 - self.fee_rate)
            pnl_pct = ((proceeds / position_cost - 1) * 100
                       if position_cost > 0 else 0.0)
            trade_log.append({
                "timestamp": timestamp,
                "action": action,
                "price": exit_price,
                "volume": position,
                "pnl_pct": pnl_pct,
                "cash_after": cash + proceeds,
            })
            trade_returns.append(pnl_pct)
            cash += proceeds
            position = 0.0
            position_cost = 0.0
            rm.record_sell(market, now=now)

        # ---- 봉별 시뮬레이션 ----
        for i in range(len(df)):
            signal = signals.iloc[i]
            bar_open, bar_high, bar_low, bar_close = (
                opens[i], highs[i], lows[i], closes[i]
            )
            now = i * self.candle_seconds
            timestamp = (df["candle_date_time_kst"].iloc[i]
                         if "candle_date_time_kst" in df.columns
                         else str(df.index[i]))

            # ---- 보유 중: 손절 / 익절 / 추세매도 / 트레일링 ----
            if position > 0:
                entry_price = rm.state.entry_prices.get(market, 0.0)
                # 고점은 봉 고가로 갱신 (트레일링 판단 기준)
                rm.update_peak_price(market, bar_high)
                peak_price = rm.state.peak_prices.get(market, 0.0)

                # 추세매도: 직전 확정봉 SELL 신호 -> 이번 봉 시가 체결 (매수와 같은 체결 모델).
                # 트레이더는 새 봉이 열리자마자 현재가로 팔므로 봉 안의 손절/트레일링보다 먼저다.
                if (signal == SELL
                        and rm.check_min_net_profit_exit(
                            market, bar_open, self.roundtrip_cost_pct)[0]
                        and rm.check_sell(signal, market, bar_open)[0]):
                    _close_position(bar_open * (1 - self.slippage_rate),
                                    "TREND_EXIT", timestamp, now)

                # 손절: 봉 저가가 손절선을 건드리면 손절선에서 체결
                elif rm.check_stop_loss(market, bar_low)[0]:
                    level = entry_price * (1 - rm.stop_loss_pct)
                    _close_position(level * (1 - self.slippage_rate),
                                    "STOP_LOSS", timestamp, now)

                # 익절은 종가 기준으로 판단
                elif rm.check_take_profit_net(
                        market, bar_close, self.roundtrip_cost_pct)[0]:
                    _close_position(bar_close * (1 - self.slippage_rate),
                                    "TAKE_PROFIT", timestamp, now)

                # 트레일링: 봉 저가가 되돌림 기준을 건드리면 그 선에서 체결
                elif rm.check_trailing_stop(market, bar_low)[0]:
                    level = peak_price * (1 - rm.trailing_stop_pct)
                    _close_position(level * (1 - self.slippage_rate),
                                    "TRAILING_STOP", timestamp, now)

            # ---- 매수 신호 처리 ----
            if signal == BUY:
                allowed, reason = rm.check_buy(
                    signal, cash, bar_open,
                    atr=_nz(atrs[i]), avg_atr=_nz(avg_atrs[i]), now=now,
                )
                if not allowed:
                    pass
                else:
                    buy_price = bar_open * (1 + self.slippage_rate)
                    chase_ok, _ = rm.check_chase(signal_prices.iloc[i], bar_open)
                    if not chase_ok:
                        pass
                    else:
                        amount = rm.get_buy_amount(cash)
                        if reason.startswith("고변동 경고"):
                            amount *= rm.high_vol_buy_scale

                        if amount >= MIN_ORDER_KRW and amount <= cash:
                            volume = amount * (1 - self.fee_rate) / buy_price
                            trade_log.append({
                                "timestamp": timestamp,
                                "action": "BUY",
                                "price": buy_price,
                                "volume": volume,
                                "pnl_pct": 0,
                                "cash_after": cash - amount,
                            })
                            cash -= amount
                            position += volume
                            position_cost += amount
                            rm.record_buy(market, buy_price, now=now)

            # ---- 자산 추이 기록 ----
            equity_curve.append({
                "timestamp": timestamp,
                "equity": cash + position * bar_close,
                "cash": cash,
                "position_value": position * bar_close,
            })

        # ---- 마지막 미청산 포지션 정리 ----
        if position > 0:
            last_price = float(closes[-1]) * (1 - self.slippage_rate)
            _close_position(last_price, "FINAL_EXIT",
                            equity_curve[-1]["timestamp"],
                            (len(df) - 1) * self.candle_seconds)

        # ---- 성과 지표 계산 ----
        result = self._calc_metrics(
            strategy_name=strategy.name,
            strategy_description=strategy.description,
            final_capital=cash,
            trade_returns=trade_returns,
            trade_log=trade_log,
            equity_curve=equity_curve,
            num_candles=len(df),
        )

        logger.info(f"백테스트 완료: {strategy.name}")
        logger.info(f"  최종 자본: {result.final_capital:,.0f} KRW")
        logger.info(f"  총 수익률: {result.total_return_pct:.2f}%")
        logger.info(f"  거래 횟수: {result.total_trades}, 승률: {result.win_rate:.1f}%")

        return result

    def _calc_metrics(self, strategy_name: str, strategy_description: str,
                      final_capital: float, trade_returns: list,
                      trade_log: list, equity_curve: list,
                      num_candles: int) -> BacktestResult:
        """성과 지표를 계산합니다."""
        result = BacktestResult()
        result.strategy_name = strategy_name
        result.strategy_description = strategy_description
        result.initial_capital = self.initial_capital
        result.final_capital = final_capital
        result.trade_log = trade_log
        result.equity_curve = equity_curve

        # 총 수익률
        result.total_return_pct = (
            (final_capital - self.initial_capital) / self.initial_capital * 100
        )

        # 기간 추정 (봉 길이 기준 일수 환산)
        result.period_days = max(
            int(num_candles * self.candle_seconds // 86_400), 1
        )

        # CAGR (연환산 수익률)
        years = result.period_days / 365.0
        if years > 0 and final_capital > 0:
            result.cagr_pct = (
                ((final_capital / self.initial_capital) ** (1 / years) - 1) * 100
            )

        # 거래 통계
        result.total_trades = len(trade_returns)
        if result.total_trades > 0:
            wins = [r for r in trade_returns if r > 0]
            losses = [r for r in trade_returns if r <= 0]

            result.winning_trades = len(wins)
            result.losing_trades = len(losses)
            result.win_rate = len(wins) / len(trade_returns) * 100

            result.avg_profit_pct = np.mean(wins) if wins else 0.0
            result.avg_loss_pct = np.mean(losses) if losses else 0.0
            result.avg_return_per_trade = np.mean(trade_returns)

            # Profit Factor = 총 이익 / 총 손실
            total_profit = sum(wins) if wins else 0.0
            total_loss = abs(sum(losses)) if losses else 0.0
            result.profit_factor = (
                total_profit / total_loss if total_loss > 0 else float("inf")
            )

        # MDD (최대 낙폭)
        if equity_curve:
            equities = [e["equity"] for e in equity_curve]
            peak = equities[0]
            max_dd = 0.0
            for eq in equities:
                if eq > peak:
                    peak = eq
                dd = (peak - eq) / peak * 100 if peak > 0 else 0
                if dd > max_dd:
                    max_dd = dd
            result.max_drawdown_pct = max_dd

        # 샤프 비율 유사 지표
        # (거래 수익률의 평균 / 표준편차, 무위험 수익률은 0으로 가정)
        if len(trade_returns) > 1:
            avg_ret = np.mean(trade_returns)
            std_ret = np.std(trade_returns, ddof=1)
            result.sharpe_like_ratio = (
                avg_ret / std_ret if std_ret > 0 else 0.0
            )

        return result

    # ============================================================
    # 결과 출력
    # ============================================================
    def print_summary(self, result: BacktestResult) -> None:
        """백테스트 결과를 콘솔에 출력합니다."""
        print("\n" + "=" * 60)
        print(f"  전략: {result.strategy_name}")
        print(f"  설명: {result.strategy_description}")
        print("=" * 60)
        print(f"  초기 자본      : {result.initial_capital:>15,.0f} KRW")
        print(f"  최종 자본      : {result.final_capital:>15,.0f} KRW")
        print(f"  총 수익률      : {result.total_return_pct:>14.2f} %")
        print(f"  CAGR           : {result.cagr_pct:>14.2f} %")
        print("-" * 60)
        print(f"  거래 횟수      : {result.total_trades:>15}")
        print(f"  승률           : {result.win_rate:>14.1f} %")
        print(f"  이긴 거래      : {result.winning_trades:>15}")
        print(f"  진 거래        : {result.losing_trades:>15}")
        print(f"  평균 수익      : {result.avg_profit_pct:>14.2f} %")
        print(f"  평균 손실      : {result.avg_loss_pct:>14.2f} %")
        print(f"  거래당 평균    : {result.avg_return_per_trade:>14.4f} %")
        print(f"  Profit Factor  : {result.profit_factor:>14.2f}")
        print("-" * 60)
        print(f"  최대 낙폭(MDD) : {result.max_drawdown_pct:>14.2f} %")
        print(f"  샤프 비율(유사): {result.sharpe_like_ratio:>14.4f}")
        print("=" * 60 + "\n")

    # ============================================================
    # 결과 저장
    # ============================================================
    def save_report(self, result: BacktestResult, filepath: str = "") -> str:
        """
        백테스트 결과를 CSV로 저장합니다.

        Args:
            result: BacktestResult 인스턴스
            filepath: 저장 경로 (기본: reports/전략이름_날짜.csv)

        Returns:
            str: 저장된 파일 경로
        """
        if not filepath:
            report_dir = os.path.join(PROJECT_ROOT, "reports")
            os.makedirs(report_dir, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_name = result.strategy_name.replace(" ", "_").replace("/", "_")
            filepath = os.path.join(report_dir, f"{safe_name}_{timestamp}.csv")

        # 거래 로그를 DataFrame으로 변환하여 저장
        if result.trade_log:
            df_log = pd.DataFrame(result.trade_log)
            df_log.to_csv(filepath, index=False, encoding="utf-8-sig")
            logger.info(f"거래 로그 저장: {filepath}")
        else:
            logger.warning("저장할 거래 로그가 없습니다.")

        return filepath

    def save_comparison_report(self, results: list[BacktestResult],
                               filepath: str = "") -> str:
        """
        여러 전략의 비교 리포트를 CSV로 저장합니다.

        Args:
            results: BacktestResult 리스트
            filepath: 저장 경로

        Returns:
            str: 저장된 파일 경로
        """
        if not filepath:
            report_dir = os.path.join(PROJECT_ROOT, "reports")
            os.makedirs(report_dir, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filepath = os.path.join(report_dir, f"comparison_{timestamp}.csv")

        rows = []
        for r in results:
            rows.append({
                "전략": r.strategy_name,
                "설명": r.strategy_description,
                "초기자본": r.initial_capital,
                "최종자본": round(r.final_capital, 0),
                "수익률(%)": round(r.total_return_pct, 2),
                "CAGR(%)": round(r.cagr_pct, 2),
                "거래횟수": r.total_trades,
                "승률(%)": round(r.win_rate, 1),
                "평균수익(%)": round(r.avg_profit_pct, 2),
                "평균손실(%)": round(r.avg_loss_pct, 2),
                "ProfitFactor": round(r.profit_factor, 2),
                "MDD(%)": round(r.max_drawdown_pct, 2),
                "샤프유사": round(r.sharpe_like_ratio, 4),
            })

        df_cmp = pd.DataFrame(rows)
        df_cmp.to_csv(filepath, index=False, encoding="utf-8-sig")
        logger.info(f"비교 리포트 저장: {filepath}")

        return filepath
