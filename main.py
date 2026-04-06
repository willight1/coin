"""
main.py — CLI 진입점
=====================
프로젝트의 메인 실행 파일입니다.
모드별로 흐름을 분리합니다:

- backtest : 모든 전략 백테스트
- validate : 검증 통과 전략 선정
- trade    : 선정된 전략으로 실거래/모의거래

사용법:
    python main.py --mode backtest
    python main.py --mode validate
    python main.py --mode trade
    python main.py --mode trade --strategy "RSI 과매도/과매수"
"""

import argparse
import sys
import os
import time
from datetime import datetime

import pandas as pd
import numpy as np

from bot.config import UPBIT_CFG, BOT_CFG, BT_CFG, RISK_CFG
from bot.logger import get_logger
from bot.upbit_client import UpbitClient
from bot.strategies import get_all_strategies, get_strategy_by_name
from bot.backtester import Backtester
from bot.validator import Validator
from bot.risk_manager import RiskManager
from bot.trader import Trader
from bot.review_agent import ReviewAgent

logger = get_logger(__name__)


# ============================================================
# 샘플 데이터 생성 (API 키 없을 때 백테스트용)
# ============================================================
def generate_sample_data(num_candles: int = 2000,
                         start_price: float = 50_000_000) -> pd.DataFrame:
    """
    API 키가 없을 때 백테스트 용도로 랜덤 캔들 데이터를 생성합니다.
    실제 시장 데이터를 대체하는 용도이며, 랜덤 워크 기반입니다.

    Args:
        num_candles: 생성할 캔들 수
        start_price: 시작 가격

    Returns:
        pd.DataFrame: OHLCV 데이터프레임
    """
    np.random.seed(42)  # 재현성을 위한 고정 시드

    prices = [start_price]
    for _ in range(num_candles - 1):
        # 랜덤 워크: ±2% 변동
        change = np.random.normal(0, 0.01)
        new_price = prices[-1] * (1 + change)
        prices.append(max(new_price, 1000))  # 최소 가격 보장

    data = []
    for i, price in enumerate(prices):
        # 랜덤 OHLC 생성
        high = price * (1 + abs(np.random.normal(0, 0.005)))
        low = price * (1 - abs(np.random.normal(0, 0.005)))
        open_price = price * (1 + np.random.normal(0, 0.003))
        volume = abs(np.random.normal(100, 30))

        data.append({
            "open": open_price,
            "high": max(high, open_price, price),
            "low": min(low, open_price, price),
            "close": price,
            "volume": volume,
            "candle_date_time_kst": f"2024-01-01T00:{i:04d}:00",
        })

    return pd.DataFrame(data)


def load_market_data(client: UpbitClient, market: str,
                     count: int = 2000, unit: int = 1) -> pd.DataFrame:
    """
    업비트 API에서 시세 데이터를 조회합니다.
    200개 제한을 우회하기 위해 여러 번 요청하여 합칩니다.
    API 키가 없거나 실패하면 샘플 데이터를 반환합니다.

    Args:
        client: 업비트 API 클라이언트
        market: 마켓 코드
        count: 목표 캔들 수 (200 초과 시 페이지네이션)
        unit: 분봉 단위 (1, 3, 5, 15, 30, 60, 240)

    Returns:
        pd.DataFrame: OHLCV 데이터프레임
    """
    try:
        logger.info(f"업비트 API에서 {unit}분봉 데이터 조회: {market}, 목표 {count}개")
        all_candles = []
        remaining = count
        to_param = None  # 페이지네이션용 시각 파라미터

        while remaining > 0:
            batch_size = min(remaining, 200)
            params = {"market": market, "count": batch_size}
            if to_param:
                params["to"] = to_param

            candles = client._request(
                "GET", f"/v1/candles/minutes/{unit}", params=params
            )

            if not candles:
                break

            all_candles.extend(candles)
            remaining -= len(candles)

            # 다음 페이지: 조회한 캔들 중 가장 오래된 시각 사용
            oldest = candles[-1].get("candle_date_time_utc", "")
            if oldest:
                to_param = oldest
            else:
                break

            # 업비트 API 호출 제한 (초당 10회) 준수
            if remaining > 0:
                time.sleep(0.15)

            logger.info(f"  수집 중... {len(all_candles)}/{count}")

        if all_candles:
            df = pd.DataFrame(all_candles)
            df = df.sort_values("candle_date_time_kst").reset_index(drop=True)
            # 중복 제거 (페이지네이션 경계에서 발생 가능)
            df = df.drop_duplicates(
                subset=["candle_date_time_kst"], keep="first"
            ).reset_index(drop=True)
            df = df.rename(columns={
                "opening_price": "open",
                "high_price": "high",
                "low_price": "low",
                "trade_price": "close",
                "candle_acc_trade_volume": "volume",
            })
            logger.info(f"시세 데이터 조회 완료: {len(df)}개 캔들")
            return df

    except Exception as e:
        logger.warning(f"API 데이터 조회 실패: {e}")

    logger.info("샘플 데이터를 사용합니다.")
    return generate_sample_data(count)


# ============================================================
# 모드별 실행 함수
# ============================================================
def run_backtest() -> None:
    """
    모든 전략을 백테스트합니다.
    결과를 콘솔에 출력하고 CSV로 저장합니다.
    """
    logger.info("=" * 50)
    logger.info("백테스트 모드 시작")
    logger.info("=" * 50)

    # 데이터 로드
    client = UpbitClient(dry_run=True)
    df = load_market_data(client, UPBIT_CFG.market, count=2000, unit=1)

    if df.empty:
        logger.error("시세 데이터가 비어 있습니다. 종료합니다.")
        return

    # 백테스터 생성
    bt = Backtester()

    # 모든 전략 백테스트
    strategies = get_all_strategies()
    results = []

    for strategy in strategies:
        logger.info(f"\n전략 백테스트: {strategy.name}")
        result = bt.run(strategy, df)
        bt.print_summary(result)
        bt.save_report(result)
        results.append(result)

    # 비교 리포트 저장
    if results:
        bt.save_comparison_report(results)
        print("\n모든 백테스트 완료! reports/ 디렉토리에서 결과를 확인하세요.")


def run_validate() -> None:
    """
    모든 전략을 백테스트 + 검증합니다.
    검증 통과 전략을 추천합니다.
    """
    logger.info("=" * 50)
    logger.info("검증 모드 시작")
    logger.info("=" * 50)

    # 데이터 로드
    client = UpbitClient(dry_run=True)
    df = load_market_data(client, UPBIT_CFG.market, count=2000, unit=1)

    if df.empty:
        logger.error("시세 데이터가 비어 있습니다. 종료합니다.")
        return

    # 백테스터 & 검증기 생성
    bt = Backtester()
    validator = Validator()

    # 모든 전략 백테스트
    strategies = get_all_strategies()
    results = []

    for strategy in strategies:
        result = bt.run(strategy, df)
        results.append(result)

    # 검증 비교
    validations = validator.compare_strategies(results)
    validator.print_comparison(validations)

    # 실거래 후보 선택
    best_name = validator.select_live_strategy(results)
    if best_name:
        print(f"\n✅ 실거래 추천 전략: {best_name}")
        print(f"   다음 명령으로 실거래를 시작하세요:")
        print(f'   python main.py --mode trade --strategy "{best_name}"')
    else:
        print("\n❌ 검증을 통과한 전략이 없습니다.")
        print("   전략 파라미터를 조정하거나 더 많은 데이터로 시도하세요.")


def run_trade(strategy_name: str = "") -> None:
    """
    선정된 전략으로 실거래 또는 모의거래를 실행합니다.

    Args:
        strategy_name: 사용할 전략 이름 (비어있으면 첫 번째 전략 사용)
    """
    logger.info("=" * 50)
    logger.info("실거래 모드 시작")
    logger.info("=" * 50)

    # 전략 선택
    if strategy_name:
        strategy = get_strategy_by_name(strategy_name)
        if strategy is None:
            logger.error(f"전략을 찾을 수 없습니다: '{strategy_name}'")
            available = ["AI 자율 매매"] + [s.name for s in get_all_strategies()]
            logger.info(f"사용 가능한 전략: {available}")
            return
    else:
        preferred_name = "AI 자율 매매"
        strategy = get_strategy_by_name(preferred_name)
        if strategy is None:
            strategies = get_all_strategies()
            strategy = strategies[0]
            logger.info(
                f"기본 선호 전략을 찾지 못해 첫 번째 전략을 사용합니다: {strategy.name}"
            )
        else:
            logger.info(f"전략이 지정되지 않아 기본 선호 전략을 사용합니다: {strategy.name}")

    if getattr(strategy, "ai_driven", False):
        if not os.getenv("OPENAI_API_KEY", "").strip():
            logger.error("AI 자율 매매 전략은 OPENAI_API_KEY가 필요합니다.")
            logger.error(".env 파일에 OPENAI_API_KEY를 설정하세요.")
            return

    markets = tuple(dict.fromkeys(UPBIT_CFG.markets or (UPBIT_CFG.market,)))
    if not markets:
        markets = (UPBIT_CFG.market,)

    if not BOT_CFG.dry_run:
        # LIVE 모드 사전 검증
        if not UPBIT_CFG.access_key or not UPBIT_CFG.secret_key:
            logger.error("LIVE 모드에는 API 키가 필요합니다.")
            logger.error(".env 파일에 UPBIT_ACCESS_KEY, UPBIT_SECRET_KEY를 설정하세요.")
            return

        print("\n" + "!" * 50)
        print("  ⚠️  LIVE 모드 — 실제 주문이 체결됩니다!")
        print("  → BOT_DRY_RUN=true 로 변경하면 모의거래입니다.")
        print("!" * 50)

        confirm = input("\n정말 LIVE 모드로 시작하시겠습니까? (yes/no): ").strip()
        if confirm.lower() != "yes":
            logger.info("사용자가 취소했습니다.")
            return

    try:
        if len(markets) == 1:
            # 단일 마켓 모드
            market = markets[0]
            client = UpbitClient(dry_run=BOT_CFG.dry_run)
            risk_manager = RiskManager()
            trader = Trader(
                strategy=strategy,
                client=client,
                risk_manager=risk_manager,
                market=market,
                candle_unit=1,
            )
            trader.run()
            return

        # 멀티마켓 모드
        logger.info(f"멀티마켓 모드 시작: {list(markets)}")
        traders = []
        for market in markets:
            traders.append(
                Trader(
                    strategy=strategy,
                    client=UpbitClient(dry_run=BOT_CFG.dry_run),
                    risk_manager=RiskManager(),
                    market=market,
                    interval=BOT_CFG.interval_seconds,
                    candle_unit=1,
                )
            )

        inter_market_delay = 0.15
        while True:
            for t in traders:
                t.run_once()
                time.sleep(inter_market_delay)
            logger.info(f"다음 조회까지 {BOT_CFG.interval_seconds}초 대기...")
            time.sleep(BOT_CFG.interval_seconds)
    except KeyboardInterrupt:
        logger.info("\n사용자 종료 요청 (Ctrl+C)")
        if len(markets) > 1:
            for t in traders:
                t._safe_shutdown()
    finally:
        # 트레이드 모드 종료 시 자동 리뷰 저장
        try:
            review_date = datetime.now().strftime("%Y-%m-%d")
            out_path = ReviewAgent().run(date_str=review_date)
            logger.info(f"자동 리뷰 생성 완료: {out_path}")
        except Exception as e:
            logger.warning(f"자동 리뷰 생성 실패: {e}")

def run_review(date_str: str = "") -> None:
    """
    실행 로그를 분석하여 일일 수익률/전략 리뷰를 생성합니다.

    Args:
        date_str: 대상 날짜 (YYYY-MM-DD), 비우면 오늘 날짜
    """
    logger.info("=" * 50)
    logger.info("리뷰 모드 시작")
    logger.info("=" * 50)
    agent = ReviewAgent()
    try:
        agent.run(date_str=date_str)
    except FileNotFoundError as e:
        logger.error(str(e))


# ============================================================
# CLI 파서
# ============================================================
def main():
    """메인 진입점"""
    parser = argparse.ArgumentParser(
        description="업비트 규칙 기반 자동매매 시스템",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
사용 예시:
  python main.py --mode backtest              # 모든 전략 백테스트
  python main.py --mode validate              # 검증 통과 전략 선정
  python main.py --mode trade                 # 모의거래 (DRY_RUN)
  python main.py --mode trade --strategy "RSI 과매도/과매수"  # 특정 전략
  python main.py --mode review                # 오늘 로그 리뷰
  python main.py --mode review --date 2026-03-25
        """,
    )

    parser.add_argument(
        "--mode", "-m",
        required=True,
        choices=["backtest", "validate", "trade", "review"],
        help="실행 모드 (backtest / validate / trade / review)",
    )
    parser.add_argument(
        "--strategy", "-s",
        default="",
        help="실거래에 사용할 전략 이름 (trade 모드에서 사용)",
    )
    parser.add_argument(
        "--date",
        default="",
        help="리뷰 대상 날짜 (YYYY-MM-DD, review 모드에서 사용)",
    )

    args = parser.parse_args()

    # 로고 출력
    print("""
    ╔══════════════════════════════════════════════╗
    ║   업비트 규칙 기반 자동매매 시스템           ║
    ║   Quant-Style Rule-Based Trading Engine      ║
    ╚══════════════════════════════════════════════╝
    """)

    if args.mode == "backtest":
        run_backtest()
    elif args.mode == "validate":
        run_validate()
    elif args.mode == "trade":
        run_trade(args.strategy)
    elif args.mode == "review":
        run_review(args.date)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
