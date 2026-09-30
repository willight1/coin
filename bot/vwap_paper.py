"""
vwap_paper.py — VWAP 회귀 가상거래 (지정가 방식, 실제 주문 없음)
================================================================
연구 결과(CLAUDE.md 'VWAP 회귀'): 수수료 전 엣지가 비용과 비슷한 유일한 데이트레이딩 후보.
지정가로 슬리피지를 없애면 비용을 넘을 수 있는지, 실제 체결이 되는지를 가상으로 확인한다.

규칙 (세션 = 09:00 시작):
- 매수: 가격이 세션 VWAP x (1 - VWAP_ENTRY_PCT) '아래로' 내려가면 그 지정가에 체결
- 매도: 가격이 세션 VWAP '위로' 올라가면 VWAP 지정가에 체결
- 세션이 끝날 때(09:00) 보유 중이면 시장가 청산
- 지정가 체결은 가격이 지정가를 '통과'해야 인정한다 (닿기만 하면 대기열 뒤라 체결 안 될 수 있다)

이 모듈은 주문 API 를 호출하지 않는다. 시세 조회만 한다.
"""

import csv
import json
import os
import time
from datetime import datetime, timedelta

import pandas as pd

from . import notifier
from .logger import get_logger
from .paths import PROJECT_ROOT
from .upbit_client import UpbitClient

logger = get_logger(__name__)

MAKER_FEE = 0.0005     # 업비트 KRW 마켓 지정가 수수료
TAKER_COST = 0.001     # 강제 청산(시장가): 수수료 0.05% + 슬리피지 0.05%


def session_start(now: datetime) -> datetime:
    """09:00 에 시작하는 세션의 시작 시각."""
    start = now.replace(hour=9, minute=0, second=0, microsecond=0)
    return start if now >= start else start - timedelta(days=1)


def session_vwap(candles: pd.DataFrame, start: datetime) -> float | None:
    """세션 시작 이후 봉들의 거래량 가중 평균가 (진행 중인 봉 포함)."""
    ts = pd.to_datetime(candles["candle_date_time_kst"])
    s = candles[ts >= pd.Timestamp(start)]
    if s.empty or s["candle_acc_trade_volume"].sum() <= 0:
        return None
    tp = (s["high_price"] + s["low_price"] + s["trade_price"]) / 3
    return float((tp * s["candle_acc_trade_volume"]).sum() / s["candle_acc_trade_volume"].sum())


def decide(holding: bool, price: float, vwap: float, entry_pct: float) -> tuple[str, float] | None:
    """
    가상 지정가 체결 판단. 백테스트와 가상거래가 같이 쓴다.
    Returns: ("BUY", 체결가) / ("SELL", 체결가) / None
    """
    if not holding:
        limit = vwap * (1 - entry_pct)
        return ("BUY", limit) if price < limit else None
    return ("SELL", vwap) if price > vwap else None


class VwapPaperTrader:
    """한 마켓의 VWAP 가상거래. 상태는 state/vwap_paper_<마켓>.json 에 저장."""

    def __init__(self, market: str, budget_krw: float, entry_pct: float,
                 client: UpbitClient | None = None):
        self.market = market
        self.entry_pct = entry_pct
        self.client = client or UpbitClient(dry_run=True)
        self.state_path = os.path.join(PROJECT_ROOT, "state", f"vwap_paper_{market}.json")
        self.trades_path = os.path.join(PROJECT_ROOT, "logs", "vwap_paper_trades.csv")
        self.s = {"krw": budget_krw, "coin": 0.0, "entry": 0.0, "session": "",
                  "trades": 0, "wins": 0, "start_krw": budget_krw}
        if os.path.exists(self.state_path):
            with open(self.state_path, encoding="utf-8") as f:
                self.s.update(json.load(f))
            logger.info(f"[VWAP 가상 {market}] 상태 복원: {self.s}")

    # ---- 상태/기록 ----
    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.s, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.state_path)

    def _record(self, side: str, price: float, vwap: float, pnl_pct: float | None) -> None:
        new = not os.path.exists(self.trades_path)
        os.makedirs(os.path.dirname(self.trades_path), exist_ok=True)
        with open(self.trades_path, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["time", "market", "side", "price", "vwap", "pnl_pct", "equity"])
            w.writerow([datetime.now().isoformat(timespec="seconds"), self.market, side,
                        round(price, 4), round(vwap, 4),
                        "" if pnl_pct is None else round(pnl_pct, 4), round(self.equity(price))])

    def equity(self, price: float) -> float:
        return self.s["krw"] + self.s["coin"] * price

    def _notify(self, text: str) -> None:
        notifier.notify(f"[VWAP 가상 {self.market}] {text}")

    # ---- 체결 ----
    def _buy(self, price: float, vwap: float) -> None:
        krw = self.s["krw"]
        self.s["coin"] = krw * (1 - MAKER_FEE) / price
        self.s["krw"], self.s["entry"] = 0.0, price
        self._save(); self._record("BUY", price, vwap, None)
        logger.info(f"[VWAP 가상 {self.market}] 매수 {price:,.2f} (VWAP {vwap:,.2f})")
        self._notify(f"매수 @ {price:,.0f} (VWAP {vwap:,.0f} −{self.entry_pct*100:.1f}%)")

    def _sell(self, price: float, vwap: float, cost: float, reason: str) -> None:
        proceeds = self.s["coin"] * price * (1 - cost)
        cost_basis = self.s["coin"] * self.s["entry"] / (1 - MAKER_FEE)
        pnl = proceeds / cost_basis - 1 if cost_basis > 0 else 0.0
        self.s["krw"], self.s["coin"], self.s["entry"] = proceeds, 0.0, 0.0
        self.s["trades"] += 1; self.s["wins"] += int(pnl > 0)
        self._save(); self._record("SELL:" + reason, price, vwap, pnl * 100)
        total = (self.s["krw"] / self.s["start_krw"] - 1) * 100
        logger.info(f"[VWAP 가상 {self.market}] 매도({reason}) {price:,.2f} 손익 {pnl*100:+.3f}%")
        self._notify(f"매도({reason}) @ {price:,.0f}  이번 {pnl*100:+.2f}%\n"
                     f"누적 {self.s['trades']}회 (승 {self.s['wins']}) {total:+.2f}%")

    # ---- 한 틱 ----
    def tick(self, now: datetime | None = None) -> None:
        now = now or datetime.now()
        start = session_start(now)
        key = start.strftime("%Y-%m-%d")
        price = float(self.client.get_ticker(self.market)["trade_price"])

        # 세션이 바뀌었는데 보유 중이면 시장가 청산 (당일 청산 규칙)
        if self.s["session"] and self.s["session"] != key:
            if self.s["coin"] > 0:
                self._sell(price * (1 - 0.0005), price, MAKER_FEE, "세션종료")  # 슬리피지 0.05% + 수수료
            total = (self.s["krw"] / self.s["start_krw"] - 1) * 100
            self._notify(f"일일 요약 ({self.s['session']}) — 누적 {self.s['trades']}회, "
                         f"승 {self.s['wins']}, 누적 {total:+.2f}%")
        if self.s["session"] != key:
            self.s["session"] = key; self._save()

        candles = pd.DataFrame(self.client.get_candles_minutes(unit=15, market=self.market, count=100))
        vwap = session_vwap(candles, start) if not candles.empty else None
        if vwap is None or now < start + timedelta(minutes=15):
            return  # 세션 초반 15분은 VWAP 이 불안정해 거래하지 않는다

        action = decide(self.s["coin"] > 0, price, vwap, self.entry_pct)
        if action and action[0] == "BUY":
            self._buy(action[1], vwap)
        elif action and action[0] == "SELL":
            self._sell(action[1], vwap, MAKER_FEE, "VWAP")


def run(markets: list[str], budget_krw: float, entry_pct: float, interval: int = 10) -> None:
    traders = [VwapPaperTrader(m, budget_krw, entry_pct) for m in markets]
    logger.info(f"VWAP 가상거래 시작: {markets}, 마켓당 {budget_krw:,.0f}원, 진입 VWAP −{entry_pct*100:.1f}%")
    notifier.notify(f"[VWAP 가상] 시작 — {', '.join(markets)} / 마켓당 {budget_krw:,.0f}원 "
                    f"/ VWAP −{entry_pct*100:.1f}% 지정가 (실제 주문 없음)")
    try:
        while True:
            for t in traders:
                try:
                    t.tick()
                except Exception as e:
                    logger.error(f"[VWAP 가상 {t.market}] 오류: {e}")
                time.sleep(0.2)
            time.sleep(interval)
    except KeyboardInterrupt:
        notifier.notify("[VWAP 가상] 종료")
        logger.info("VWAP 가상거래 종료")
