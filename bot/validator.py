"""
validator.py — 전략 검증 모듈
==============================
"AI가 계산식을 생각하는 것"과 "실제로 돈이 되는 전략"은 다릅니다.
이 모듈은 백테스트 결과를 기반으로 전략의 신뢰성을 검증합니다.

주요 기능:
- 인샘플(In-Sample) / 아웃오브샘플(Out-of-Sample) 분리 검증
- 워크포워드 스타일 단순 검증
- 과최적화 방지 규칙
- 전략 순위 비교
- 실거래 후보 선택

검증 규칙:
1. 최소 거래 횟수 이상이어야 함
2. MDD가 허용 범위 이내여야 함
3. 승률만 높고 손익비가 나쁜 전략 제외
4. Profit Factor가 기준 이상이어야 함
5. 인샘플과 아웃오브샘플 성과 편차가 너무 크면 경고
"""

from dataclasses import dataclass, field

import pandas as pd
import numpy as np

from .backtester import Backtester, BacktestResult
from .strategies import BaseStrategy
from .logger import get_logger

logger = get_logger(__name__)


# ============================================================
# 검증 결과 데이터 클래스
# ============================================================
@dataclass
class ValidationResult:
    """전략 검증 결과"""
    strategy_name: str = ""
    passed: bool = False                # 검증 통과 여부
    reasons: list = field(default_factory=list)  # 실패/경고 사유
    warnings: list = field(default_factory=list)  # 경고 메시지

    # 인샘플 결과
    in_sample_result: BacktestResult | None = None
    # 아웃오브샘플 결과
    out_sample_result: BacktestResult | None = None

    # 종합 점수 (0~100)
    score: float = 0.0


# ============================================================
# 검증 설정
# ============================================================
@dataclass
class ValidationConfig:
    """검증 기준 파라미터"""
    min_trades: int = 10                    # 최소 거래 횟수
    max_mdd_pct: float = 30.0              # 최대 허용 MDD (%)
    min_win_rate: float = 30.0             # 최소 승률 (%)
    min_profit_factor: float = 1.0         # 최소 Profit Factor
    min_avg_return: float = -1.0           # 최소 거래당 평균 수익률 (%)
    max_performance_gap: float = 50.0      # 인샘플-아웃오브 최대 성과 편차 (%)
    in_sample_ratio: float = 0.7           # 인샘플 비율 (70%)
    min_sharpe: float = 0.0                # 최소 샤프 유사 지표


# ============================================================
# 검증 엔진
# ============================================================
class Validator:
    """
    전략 검증을 수행합니다.
    백테스트 결과를 기반으로 과최적화, 안정성 등을 평가합니다.
    """

    def __init__(self, config: ValidationConfig | None = None):
        self.config = config or ValidationConfig()

    def validate_strategy(self, backtest_result: BacktestResult
                          ) -> ValidationResult:
        """
        단일 백테스트 결과를 검증합니다.

        Args:
            backtest_result: 백테스트 결과

        Returns:
            ValidationResult: 검증 결과 (pass/fail + 사유)
        """
        vr = ValidationResult()
        vr.strategy_name = backtest_result.strategy_name
        vr.in_sample_result = backtest_result
        vr.passed = True  # 일단 통과로 시작, 조건 위반 시 False로 변경

        cfg = self.config

        # ---- 규칙 1: 최소 거래 횟수 ----
        if backtest_result.total_trades < cfg.min_trades:
            vr.passed = False
            vr.reasons.append(
                f"거래 횟수 부족: {backtest_result.total_trades} < "
                f"최소 {cfg.min_trades}회"
            )

        # ---- 규칙 2: 최대 MDD ----
        if backtest_result.max_drawdown_pct > cfg.max_mdd_pct:
            vr.passed = False
            vr.reasons.append(
                f"MDD 초과: {backtest_result.max_drawdown_pct:.2f}% > "
                f"최대 {cfg.max_mdd_pct:.1f}%"
            )

        # ---- 규칙 3: 최소 승률 ----
        if (backtest_result.total_trades >= cfg.min_trades
                and backtest_result.win_rate < cfg.min_win_rate):
            vr.passed = False
            vr.reasons.append(
                f"승률 부족: {backtest_result.win_rate:.1f}% < "
                f"최소 {cfg.min_win_rate:.1f}%"
            )

        # ---- 규칙 4: Profit Factor ----
        if (backtest_result.total_trades >= cfg.min_trades
                and backtest_result.profit_factor < cfg.min_profit_factor):
            vr.passed = False
            vr.reasons.append(
                f"Profit Factor 부족: {backtest_result.profit_factor:.2f} < "
                f"최소 {cfg.min_profit_factor:.2f}"
            )

        # ---- 규칙 5: 승률만 높고 손익비가 나쁜 전략 ----
        #     승률 > 60%인데 평균 손실이 평균 수익의 3배 이상이면 위험
        if backtest_result.win_rate > 60.0 and backtest_result.avg_profit_pct > 0:
            if (abs(backtest_result.avg_loss_pct) >
                    backtest_result.avg_profit_pct * 3):
                vr.passed = False
                vr.reasons.append(
                    f"손익비 불균형: 평균 손실({backtest_result.avg_loss_pct:.2f}%)이 "
                    f"평균 수익({backtest_result.avg_profit_pct:.2f}%)의 3배 초과"
                )

        # ---- 경고: 수익률은 높지만 거래 횟수가 적음 ----
        if (backtest_result.total_return_pct > 10
                and backtest_result.total_trades < 20):
            vr.warnings.append(
                f"높은 수익률({backtest_result.total_return_pct:.1f}%)이지만 "
                f"거래 횟수({backtest_result.total_trades})가 적어 "
                f"통계적 신뢰도 낮음"
            )

        # ---- 종합 점수 계산 ----
        vr.score = self._calc_score(backtest_result)

        return vr

    def validate_with_split(self, strategy: BaseStrategy,
                            df: pd.DataFrame,
                            backtester: Backtester
                            ) -> ValidationResult:
        """
        인샘플/아웃오브샘플로 분리하여 검증합니다.
        (워크포워드 스타일 단순 검증)

        Args:
            strategy: 검증할 전략
            df: 전체 OHLCV 데이터
            backtester: Backtester 인스턴스

        Returns:
            ValidationResult: 검증 결과
        """
        split_idx = int(len(df) * self.config.in_sample_ratio)

        if split_idx < 50 or len(df) - split_idx < 20:
            logger.warning("데이터가 부족하여 분할 검증을 할 수 없습니다.")
            # 전체 데이터로 단일 검증
            result = backtester.run(strategy, df)
            return self.validate_strategy(result)

        # 인샘플로 백테스트
        df_in = df.iloc[:split_idx].copy().reset_index(drop=True)
        result_in = backtester.run(strategy, df_in)

        # 아웃오브샘플로 백테스트
        df_out = df.iloc[split_idx:].copy().reset_index(drop=True)
        result_out = backtester.run(strategy, df_out)

        # 인샘플 결과 검증
        vr = self.validate_strategy(result_in)
        vr.out_sample_result = result_out

        # ---- 과최적화 체크: 인샘플/아웃오브 성과 편차 ----
        if result_in.total_return_pct > 0:
            gap = abs(result_in.total_return_pct - result_out.total_return_pct)
            gap_ratio = gap / abs(result_in.total_return_pct) * 100

            if gap_ratio > self.config.max_performance_gap:
                vr.warnings.append(
                    f"과최적화 의심: 인샘플 수익률({result_in.total_return_pct:.2f}%) vs "
                    f"아웃오브샘플 수익률({result_out.total_return_pct:.2f}%) — "
                    f"편차 {gap_ratio:.1f}%"
                )

        # 아웃오브샘플에서 큰 손실 발생 시 경고
        if result_out.total_return_pct < -10:
            vr.warnings.append(
                f"아웃오브샘플에서 큰 손실: {result_out.total_return_pct:.2f}%"
            )
            vr.passed = False
            vr.reasons.append("아웃오브샘플 검증 실패 (손실 -10% 초과)")

        logger.info(
            f"분할 검증 [{strategy.name}] — "
            f"인샘플: {result_in.total_return_pct:.2f}%, "
            f"아웃오브: {result_out.total_return_pct:.2f}%"
        )

        return vr

    def _calc_score(self, result: BacktestResult) -> float:
        """
        전략의 종합 점수를 계산합니다 (0~100).

        수익률, 승률, MDD, Profit Factor, 샤프를 종합 평가합니다.
        """
        score = 50.0  # 기본 점수

        # 수익률 반영 (±10점)
        score += min(max(result.total_return_pct, -10), 10)

        # 승률 반영
        if result.win_rate > 50:
            score += (result.win_rate - 50) * 0.3
        else:
            score -= (50 - result.win_rate) * 0.3

        # MDD 반영 (낮을수록 좋음)
        if result.max_drawdown_pct < 10:
            score += 10
        elif result.max_drawdown_pct < 20:
            score += 5
        elif result.max_drawdown_pct > 30:
            score -= 10

        # Profit Factor 반영
        if result.profit_factor > 2.0:
            score += 10
        elif result.profit_factor > 1.5:
            score += 5
        elif result.profit_factor < 1.0:
            score -= 10

        # 샤프 반영
        score += min(result.sharpe_like_ratio * 5, 10)

        return max(0.0, min(100.0, score))

    # ============================================================
    # 전략 비교 및 선택
    # ============================================================
    def compare_strategies(self, results: list[BacktestResult]
                           ) -> list[ValidationResult]:
        """
        여러 전략의 검증 결과를 비교합니다.
        점수 내림차순으로 정렬하여 반환합니다.

        Args:
            results: 백테스트 결과 리스트

        Returns:
            list[ValidationResult]: 검증 결과 (점수 내림차순)
        """
        validations = []
        for result in results:
            vr = self.validate_strategy(result)
            validations.append(vr)
            status = "통과" if vr.passed else "실패"
            logger.info(
                f"[{status}] {vr.strategy_name} — "
                f"점수: {vr.score:.1f}, 사유: {vr.reasons or '없음'}"
            )

        # 점수 내림차순 정렬
        validations.sort(key=lambda v: v.score, reverse=True)

        return validations

    def select_live_strategy(self, results: list[BacktestResult]
                             ) -> BaseStrategy | None:
        """
        검증을 통과한 전략 중 최고 점수 전략을 실거래 후보로 선택합니다.

        Args:
            results: 백테스트 결과 리스트

        Returns:
            str 또는 None: 선택된 전략 이름 (통과 전략이 없으면 None)
        """
        validations = self.compare_strategies(results)

        # 검증 통과한 전략만 필터
        passed = [v for v in validations if v.passed]

        if not passed:
            logger.warning("검증을 통과한 전략이 없습니다.")
            return None

        best = passed[0]
        logger.info(
            f"실거래 후보 전략 선택: {best.strategy_name} "
            f"(점수: {best.score:.1f})"
        )

        return best.strategy_name

    def print_comparison(self, validations: list[ValidationResult]) -> None:
        """검증 결과 비교를 콘솔에 출력합니다."""
        print("\n" + "=" * 80)
        print("  전략 검증 결과 비교")
        print("=" * 80)
        print(f"  {'전략':<25} {'결과':<8} {'점수':>8} {'수익률':>10} "
              f"{'승률':>8} {'MDD':>8} {'PF':>8}")
        print("-" * 80)

        for vr in validations:
            r = vr.in_sample_result
            status = "✅ 통과" if vr.passed else "❌ 실패"
            ret = r.total_return_pct if r else 0
            wr = r.win_rate if r else 0
            mdd = r.max_drawdown_pct if r else 0
            pf = r.profit_factor if r else 0

            print(f"  {vr.strategy_name:<25} {status:<8} "
                  f"{vr.score:>7.1f} {ret:>9.2f}% "
                  f"{wr:>7.1f}% {mdd:>7.2f}% {pf:>7.2f}")

            # 실패 사유 출력
            for reason in vr.reasons:
                print(f"    └─ ❌ {reason}")
            for warning in vr.warnings:
                print(f"    └─ ⚠️  {warning}")

        print("=" * 80 + "\n")
