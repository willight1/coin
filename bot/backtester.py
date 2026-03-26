"""
backtester.py — 자체 백테스트 엔진
===================================
과거 캔들 데이터를 이용하여 전략을 시뮬레이션합니다.
외부 백테스트 프레임워크 없이 직접 구현한 엔진입니다.

주요 기능:
- 초기 자본 기반 매수/매도/보유 시뮬레이션
- 수수료, 슬리피지 반영
- 손절/트레일링 스탑 자동 처리
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
from .logger import get_logger
from .paths import PROJECT_ROOT

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
        stop_loss_pct: 손절 퍼센트
        trailing_stop_pct: 트레일링 스탑 퍼센트
    """

    def __init__(self, initial_capital: float = 0, fee_rate: float = 0,
                 slippage_rate: float = 0, stop_loss_pct: float = 0,
                 trailing_stop_pct: float = 0):
        self.initial_capital = initial_capital or BT_CFG.initial_capital
        self.fee_rate = fee_rate or BT_CFG.fee_rate
        self.slippage_rate = slippage_rate or BT_CFG.slippage_rate
        self.stop_loss_pct = stop_loss_pct or RISK_CFG.stop_loss_pct
        self.trailing_stop_pct = trailing_stop_pct or RISK_CFG.trailing_stop_pct
        self.min_net_profit_pct = RISK_CFG.min_net_profit_pct

    def run(self, strategy, df: pd.DataFrame) -> BacktestResult:
        """
        전략을 백테스트합니다.

        Args:
            strategy: BaseStrategy 인스턴스
            df: OHLCV 데이터프레임 (시간 오름차순)
                필수 컬럼: open, high, low, close, volume

        Returns:
            BacktestResult: 백테스트 결과
        """
        logger.info(f"백테스트 시작: {strategy.name}")
        logger.info(f"  초기 자본: {self.initial_capital:,.0f} KRW")
        logger.info(f"  데이터 기간: {len(df)}봉")
        logger.info(f"  수수료: {self.fee_rate * 100:.3f}%, "
                     f"슬리피지: {self.slippage_rate * 100:.3f}%")

        # ---- 초기 상태 ----
        cash = self.initial_capital       # 현금 보유량
        position = 0.0                     # 보유 수량
        entry_price = 0.0                  # 매수 가격
        peak_price = 0.0                   # 보유 중 최고가 (트레일링 스탑용)
        trade_log = []                     # 거래 기록
        equity_curve = []                  # 자산 추이
        trade_returns = []                 # 각 거래 수익률

        # ---- 전략 신호 생성 (벡터 연산) ----
        signals = strategy.generate_signals_series(df)

        # ---- 봉별 시뮬레이션 ----
        for i in range(len(df)):
            row = df.iloc[i]
            current_price = row["close"]
            signal = signals.iloc[i]

            # 타임스탬프 (있으면 사용, 없으면 인덱스)
            timestamp = (row.get("candle_date_time_kst")
                         or row.get("candle_date_time_utc")
                         or str(df.index[i]))

            # -- 포지션 보유 중 손절/추세이탈/트레일링스탑 체크 --
            if position > 0 and entry_price > 0:
                peak_price = max(peak_price, current_price)
                price_change_pct = (current_price - entry_price) / entry_price

                # 손절 조건
                if price_change_pct <= -self.stop_loss_pct:
                    sell_price = current_price * (1 - self.slippage_rate)
                    proceeds = position * sell_price * (1 - self.fee_rate)
                    pnl_pct = (sell_price / entry_price - 1) * 100

                    trade_log.append({
                        "timestamp": timestamp,
                        "action": "STOP_LOSS",
                        "price": sell_price,
                        "volume": position,
                        "pnl_pct": pnl_pct,
                        "cash_after": cash + proceeds,
                    })
                    trade_returns.append(pnl_pct)
                    cash += proceeds
                    position = 0.0
                    entry_price = 0.0
                    peak_price = 0.0

                # 추세 이탈(전략 SELL) 조건
                elif signal == "SELL":
                    sell_price = current_price * (1 - self.slippage_rate)
                    proceeds = position * sell_price * (1 - self.fee_rate)
                    pnl_pct = (sell_price / entry_price - 1) * 100
                    if pnl_pct < self.min_net_profit_pct:
                        # 수수료/슬리피지 후 순이익 기준 미달이면 추세매도 보류
                        pass
                    else:
                        trade_log.append({
                            "timestamp": timestamp,
                            "action": "TREND_EXIT",
                            "price": sell_price,
                            "volume": position,
                            "pnl_pct": pnl_pct,
                            "cash_after": cash + proceeds,
                        })
                        trade_returns.append(pnl_pct)
                        cash += proceeds
                        position = 0.0
                        entry_price = 0.0
                        peak_price = 0.0

                # 트레일링 스탑 조건
                elif (self.trailing_stop_pct > 0 and peak_price > entry_price):
                    pullback_pct = (peak_price - current_price) / peak_price
                    if pullback_pct >= self.trailing_stop_pct:
                        sell_price = current_price * (1 - self.slippage_rate)
                        proceeds = position * sell_price * (1 - self.fee_rate)
                        pnl_pct = (sell_price / entry_price - 1) * 100

                        trade_log.append({
                            "timestamp": timestamp,
                            "action": "TRAILING_STOP",
                            "price": sell_price,
                            "volume": position,
                            "pnl_pct": pnl_pct,
                            "cash_after": cash + proceeds,
                        })
                        trade_returns.append(pnl_pct)
                        cash += proceeds
                        position = 0.0
                        entry_price = 0.0
                        peak_price = 0.0

            # -- 매수 신호 처리 --
            if signal == "BUY" and position == 0 and cash > 0:
                buy_price = current_price * (1 + self.slippage_rate)
                buy_amount = cash * (1 - self.fee_rate)  # 수수료 제외 금액
                volume = buy_amount / buy_price

                trade_log.append({
                    "timestamp": timestamp,
                    "action": "BUY",
                    "price": buy_price,
                    "volume": volume,
                    "pnl_pct": 0,
                    "cash_after": 0,
                })

                position = volume
                entry_price = buy_price
                peak_price = buy_price
                cash = 0.0

            # -- 매도 신호 처리 --
            elif signal == "SELL" and position > 0:
                sell_price = current_price * (1 - self.slippage_rate)
                proceeds = position * sell_price * (1 - self.fee_rate)
                pnl_pct = (sell_price / entry_price - 1) * 100
                if pnl_pct < self.min_net_profit_pct:
                    # 최소 순이익 기준 미달이면 보류
                    pass
                else:
                    trade_log.append({
                        "timestamp": timestamp,
                        "action": "SELL",
                        "price": sell_price,
                        "volume": position,
                        "pnl_pct": pnl_pct,
                        "cash_after": proceeds,
                    })
                    trade_returns.append(pnl_pct)
                    cash = proceeds
                    position = 0.0
                    entry_price = 0.0
                    peak_price = 0.0

            # -- 자산 추이 기록 --
            equity = cash + (position * current_price if position > 0 else 0)
            equity_curve.append({
                "timestamp": timestamp,
                "equity": equity,
                "cash": cash,
                "position_value": position * current_price if position > 0 else 0,
            })

        # ---- 마지막 미청산 포지션 정리 ----
        if position > 0:
            last_price = df["close"].iloc[-1] * (1 - self.slippage_rate)
            proceeds = position * last_price * (1 - self.fee_rate)
            pnl_pct = (last_price / entry_price - 1) * 100
            trade_returns.append(pnl_pct)
            cash += proceeds
            position = 0.0
            peak_price = 0.0

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

        # 기간 추정 (분봉 → 일수 환산, 대략적)
        result.period_days = max(num_candles // (24 * 60), 1)

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
