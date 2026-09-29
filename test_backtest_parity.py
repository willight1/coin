"""
test_backtest_parity.py — 백테스터 == 실거래(DRY_RUN) 패리티 점검
================================================================
백테스트 숫자가 실거래 예상치와 같은 의미를 가지려면, 두 경로가
같은 리스크 규칙 / 같은 비용 / 같은 체결 시점을 써야 합니다.
이 테스트는 동일한 캔들을 양쪽에 먹여서 최종 자산이 일치하는지 확인합니다.

실행:
    ./venv/bin/python test_backtest_parity.py
"""

import os

# bot.config 임포트 전에 고정해야 하는 값들
#  - 쿨다운: 백테스터는 봉 시각, 트레이더는 실제 시계를 쓰므로 0으로 두고 비교한다
#    (쿨다운 판정 자체는 RiskManager.check_buy 한 곳에서만 일어난다)
os.environ.setdefault("COOLDOWN_SECONDS", "0")
os.environ.setdefault("BOT_DRY_RUN", "true")
os.environ.setdefault("BOT_LOG_LEVEL", "ERROR")

import numpy as np
import pandas as pd

from bot.backtester import Backtester
from bot.config import BT_CFG
from bot.risk_manager import RiskManager
from bot.strategies import get_all_strategies
from bot.trader import Trader

CANDLES = 800


def make_candles(n: int = CANDLES, seed: int = 7) -> pd.DataFrame:
    """
    패리티 전용 캔들: open == high == low == close.

    봉 안에서 가격이 움직이지 않으므로 '봉 시가 체결'(백테스터)과
    '현재가 체결'(트레이더)이 같은 값이 되어 두 경로를 직접 비교할 수 있다.
    봉 사이의 가격 변화는 그대로 남아 있어 전략은 정상 동작한다.
    """
    rng = np.random.default_rng(seed)
    price = 50_000_000.0
    rows = []
    for i in range(n):
        price *= 1 + rng.normal(0, 0.0015)
        rows.append({
            "open": price, "high": price, "low": price, "close": price,
            "volume": abs(rng.normal(100, 30)) + 1.0,
            "candle_date_time_kst": f"2026-01-01T{i // 60:02d}:{i % 60:02d}:00",
        })
    return pd.DataFrame(rows)


class FakeClient:
    """트레이더에 캔들을 한 봉씩 흘려 넣는 최소 클라이언트."""

    def __init__(self, df: pd.DataFrame):
        self.dry_run = True
        self._df = df
        self.cursor = 0

    def get_candles_minutes(self, unit=1, market="", count=200):
        window = self._df.iloc[max(0, self.cursor - count + 1):self.cursor + 1]
        candles = [{
            "opening_price": r["open"],
            "high_price": r["high"],
            "low_price": r["low"],
            "trade_price": r["close"],
            "candle_acc_trade_volume": r["volume"],
            "candle_date_time_kst": r["candle_date_time_kst"],
        } for _, r in window.iterrows()]
        return list(reversed(candles))  # 업비트는 최신순 반환

    def get_ticker(self, market=""):
        return {"trade_price": float(self._df["close"].iloc[self.cursor])}


def run_trader(strategy, df: pd.DataFrame) -> float:
    """트레이더를 봉 단위로 돌리고 최종 자산(청산 기준)을 반환합니다."""
    client = FakeClient(df)
    trader = Trader(
        strategy=strategy,
        client=client,
        risk_manager=RiskManager(),
        market="KRW-TEST",
        candle_unit=1,
        candle_count=200,
    )
    trader.virtual_krw_balance = BT_CFG.initial_capital

    for i in range(len(df)):
        client.cursor = i
        trader._tick()

    last_price = float(df["close"].iloc[-1])
    equity = trader.virtual_krw_balance
    if trader.virtual_coin_balance > 0:
        # 백테스터의 마지막 청산과 동일한 비용을 적용해서 비교한다
        equity += (trader.virtual_coin_balance * last_price
                   * (1 - BT_CFG.slippage_rate) * (1 - BT_CFG.fee_rate))
    return equity


def main() -> None:
    df = make_candles()
    failures = []

    for strategy in get_all_strategies():
        bt_equity = Backtester().run(strategy, df).final_capital
        tr_equity = run_trader(strategy, df)

        # ponytail: 허용 오차 1%. 트레이더는 업비트 제한 때문에 200봉만 보고,
        # 백테스터는 전체 구간으로 지표를 계산한다. RSI/EMA 가 경로 의존(ewm)이라
        # 워밍업 차이가 남는다. 이걸 없애려면 백테스터도 200봉 창으로 봉마다
        # 지표를 다시 계산해야 하는데(느림), 지금은 오차로 두고 감시만 한다.
        drift_pct = abs(bt_equity - tr_equity) / BT_CFG.initial_capital * 100
        status = "OK " if drift_pct <= 1.0 else "FAIL"
        print(f"  [{status}] {strategy.name:<28} "
              f"백테스트={bt_equity:>12,.0f}  트레이더={tr_equity:>12,.0f}  "
              f"차이={drift_pct:.4f}%")
        if drift_pct > 1.0:
            failures.append((strategy.name, bt_equity, tr_equity, drift_pct))

    assert not failures, (
        "백테스터와 실거래 경로가 어긋났습니다 (초기자본 대비 1% 초과):\n  "
        + "\n  ".join(f"{n}: 백테스트 {b:,.0f} vs 트레이더 {t:,.0f} ({d:.3f}%)"
                      for n, b, t, d in failures)
    )
    print("\n패리티 통과: 백테스트 숫자는 실거래 예상치와 같은 규칙으로 만들어졌습니다.")


if __name__ == "__main__":
    main()
