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
        risk_manager=RiskManager(**strategy.risk_overrides),
        market="KRW-TEST",
        candle_unit=1,
        candle_count=200,
    )
    trader.virtual_krw_balance = BT_CFG.initial_capital
    trader.state_path = None  # 가상 잔고 파일을 읽거나 남기지 않는다

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


def check_rules() -> None:
    """패리티 캔들로는 발동하지 않는 판정들을 직접 확인한다."""
    rm = RiskManager(max_chase_pct=0.004)
    assert rm.check_chase(100.0, 100.3)[0]           # +0.3% 허용
    assert not rm.check_chase(100.0, 100.5)[0]       # +0.5% 차단
    assert rm.check_chase(0, 200.0)[0]               # 신호가 없음 -> 통과
    assert rm.check_chase(float("nan"), 200.0)[0]
    assert not RiskManager(max_chase_pct=0).check_chase(100.0, 100.01)[0]  # 0 은 유효값

    rm = RiskManager(stop_loss_pct=0)                # 0 = 손절 비활성
    rm.record_buy("M", 100.0, now=1)
    assert not rm.check_stop_loss("M", 1.0)[0]
    rm = RiskManager(stop_loss_pct=0.03)
    rm.record_buy("M", 100.0, now=1)
    assert rm.check_stop_loss("M", 97.0)[0] and not rm.check_stop_loss("M", 97.5)[0]


def check_dry_run_state() -> None:
    """DRY_RUN 가상 잔고가 재시작 후에도 그대로 복원되는지 확인한다."""
    import tempfile
    path = os.path.join(tempfile.mkdtemp(), "state.json")
    strategy = get_all_strategies()[0]

    t = Trader(strategy=strategy, client=FakeClient(make_candles(5)),
               risk_manager=RiskManager(), market="KRW-TEST")
    t.state_path = path
    t.virtual_krw_balance, t.virtual_coin_balance, t.virtual_avg_buy_price = 123.0, 0.5, 1000.0
    t.risk_manager.record_buy("KRW-TEST", 990.0, now=1)
    t._save_dry_run_state()

    t2 = Trader(strategy=strategy, client=FakeClient(make_candles(5)),
                risk_manager=RiskManager(), market="KRW-TEST")
    t2.state_path = path
    t2._sync_existing_position()
    assert (t2.virtual_krw_balance, t2.virtual_coin_balance,
            t2.virtual_avg_buy_price) == (123.0, 0.5, 1000.0)
    assert t2.risk_manager.state.entry_prices["KRW-TEST"] == 990.0
    assert t2.risk_manager.state.current_positions == 1


def main() -> None:
    check_rules()
    check_dry_run_state()
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
