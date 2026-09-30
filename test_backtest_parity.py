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


def check_block_log_dedup() -> None:
    """숫자만 다른 같은 차단 사유는 한 번만 기록되고, 주문/신호 변경 뒤엔 다시 기록된다."""
    import bot.trader as tr_mod
    t = Trader(strategy=get_all_strategies()[0], client=FakeClient(make_candles(5)),
               risk_manager=RiskManager(), market="KRW-TEST")
    t.log_signal_change_only = True
    logged = []
    orig = tr_mod.logger.info
    tr_mod.logger.info = logged.append
    try:
        for pct in ("0.45", "0.43", "0.41"):
            t._log_block(f"  매수 차단: 추격매수 제한 초과 (현재 {pct}%)")
        t._log_block("  매수 차단: 최대 포지션 수 초과: 1/1")
        t._log_block("  매수 차단: 최대 포지션 수 초과: 1/1")
        t._last_block_key = None  # 주문 실행/신호 변경 시 초기화
        t._log_block("  매수 차단: 최대 포지션 수 초과: 1/1")
    finally:
        tr_mod.logger.info = orig
    assert len(logged) == 3, logged


def check_telegram() -> None:
    """토큰 없으면 전송 안 함, 같은 에러는 한 번만, 다른 에러는 보냄, INFO 는 무시."""
    import logging
    from bot import notifier
    os.environ.pop("TELEGRAM_BOT_TOKEN", None)
    assert notifier.notify("x") is False

    sent = []
    h = notifier.TelegramErrorHandler(repeat_after_sec=600, send=sent.append)
    log = logging.getLogger("telegram-check")
    log.propagate = False
    log.addHandler(h)
    log.error("API 오류: 타임아웃")
    log.error("API 오류: 타임아웃")
    log.error("다른 오류")
    log.info("정보")
    assert len(sent) == 2, sent


def check_telegram_commands() -> None:
    """내 chat 의 명령만 받고, 같은 update 는 두 번 처리하지 않고, /status 가 상태를 답한다."""
    from bot import notifier
    saved = {k: os.environ.get(k) for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")}
    os.environ.update(TELEGRAM_BOT_TOKEN="T", TELEGRAM_CHAT_ID="111")
    updates = [
        {"update_id": 5, "message": {"chat": {"id": 111}, "text": "/status@juno_coin_bot"}},
        {"update_id": 6, "message": {"chat": {"id": 999}, "text": "/status"}},  # 남의 chat
        {"update_id": 7, "message": {"chat": {"id": 111}, "text": "hello"}},    # 명령 아님
    ]

    class Resp:
        status_code = 200

        def __init__(self, result):
            self._r = result

        def json(self):
            return {"result": self._r}

    orig_get, orig_notify = notifier.requests.get, notifier.notify
    sent = []
    try:
        notifier._update_offset = 0
        notifier.requests.get = lambda url, params, timeout: Resp(
            [u for u in updates if u["update_id"] >= params["offset"]])
        assert notifier.poll_commands() == ["/status"]
        assert notifier.poll_commands() == []          # offset 이 넘어가서 재처리 없음

        notifier.notify = lambda text: sent.append(text) or True
        strategy = get_all_strategies()[-1]            # 일봉 SMA 추세 (status_text 있음)
        df = make_candles(80)
        t = Trader(strategy=strategy, client=FakeClient(df), risk_manager=RiskManager(),
                   market="KRW-TEST", candle_unit=1)
        t.state_path = None
        t.client.cursor = 79
        t._tick()
        t.handle_command("/status")
        t.handle_command("/sell")                     # 주문 명령은 없다 -> 안내만
    finally:
        notifier.requests.get, notifier.notify = orig_get, orig_notify
        for k, v in saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)

    status = [m for m in sent if "] 상태 —" in m]
    assert status and "현재가" in status[0] and "SMA50" in status[0], sent
    assert any("모르는 명령: /sell" in m for m in sent), sent


def check_market_summary() -> None:
    """꾸준히 오르면 '상승', 꾸준히 내리면 '하락', 데이터가 짧으면 안내."""
    from bot.market_report import market_summary
    from bot.strategies import SmaTrendStrategy

    def daily(step: float) -> pd.DataFrame:
        p = 100.0 * np.cumprod(np.full(300, 1 + step))
        return pd.DataFrame({"open": p, "high": p * 1.01, "low": p * 0.99, "close": p})

    up = market_summary(daily(0.005), SmaTrendStrategy(50))
    down = market_summary(daily(-0.005))
    assert "추세: 상승" in up and "전략 기준: SMA50" in up and "예측이 아닙니다" in up, up
    assert "추세: 하락" in down and "전략 기준" not in down, down
    assert "부족" in market_summary(daily(0.01).head(10))


def check_live_order_payload() -> None:
    """실주문 페이로드: 소수점 금액은 정수 원, 소액 수량은 지수 표기 없이 8자리."""
    from bot.upbit_client import UpbitClient
    c = UpbitClient(access_key="a", secret_key="b", dry_run=False)
    sent = []
    c._request = lambda method, endpoint, params=None, data=None, auth=False: sent.append(data) or {}
    c.order_market_buy(market="KRW-BTC", price=497500.5)
    c.order_market_sell(market="KRW-BTC", volume=0.00001)
    assert sent[0]["price"] == "497500", sent
    assert sent[1]["volume"] == "0.00001000", sent


def check_no_entry_on_startup_candle() -> None:
    """추세 한가운데서 봇을 켜도, 켜기 전에 확정된 봉으로는 사지 않고 다음 봉에서 산다."""
    from bot.strategies import SmaTrendStrategy
    rows = []
    for i in range(120):
        p = 50_000_000 * (1.002 ** i)                 # 꾸준한 상승 -> 계속 BUY 신호
        rows.append({"open": p, "high": p, "low": p, "close": p, "volume": 100.0,
                     "candle_date_time_kst": f"2026-01-{1 + i // 24:02d}T{i % 24:02d}:00:00"})
    df = pd.DataFrame(rows)
    t = Trader(strategy=SmaTrendStrategy(50), client=FakeClient(df), market="KRW-TEST",
               candle_unit=1)
    t.state_path = None
    t.virtual_krw_balance = 1_000_000
    t.client.cursor = 100
    t._tick()
    assert t.virtual_coin_balance == 0, "시작 시점 봉으로 매수하면 안 된다"
    t._tick()                                          # 같은 봉에서 다시 틱 -> 여전히 보류
    assert t.virtual_coin_balance == 0
    t.client.cursor = 101                              # 새 봉 확정
    t._tick()
    assert t.virtual_coin_balance > 0, "새 봉이 확정되면 매수해야 한다"


def main() -> None:
    check_rules()
    check_dry_run_state()
    check_block_log_dedup()
    check_telegram()
    check_telegram_commands()
    check_market_summary()
    check_live_order_payload()
    check_no_entry_on_startup_candle()
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
