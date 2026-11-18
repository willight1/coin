"""
dt_executor.py — BTC 자율 에이전트 주문 (docs/BTC_DAYTRADE.md 8장)
=================================================================
- 시장성 주문은 최유리 지정가 IOC(ord_type=best). 순서: /v1/orders/test → /v1/orders → /v1/order 상태 확인.
- 주문 POST 는 재전송하지 않는다. 응답이 끊기면 identifier 로 조회한다(중복 주문 방지).
- 매수 95% 미만 체결이면 남은 금액으로 1회 재시도. 매도 잔량은 1회 재시도 후 시장가.
- live=False(DT_LIVE_CONFIRM≠yes)면 주문 API 를 부르지 않고 호가로 가상 체결한다.
"""

from __future__ import annotations

import math
import time
import uuid as uuidlib
from dataclasses import dataclass, field

from .logger import get_logger
from .dt_snapshot import MARKET
from .upbit_client import UpbitClientError, format_volume

logger = get_logger(__name__)

FEE = 0.0005
MIN_KRW = 5_000
TICK = 1_000          # 호가 단위 기본값(BTC). 시작할 때 load_tick() 이 업비트에서 받아 온다


@dataclass
class Fill:
    side: str
    volume: float = 0.0
    funds: float = 0.0         # 체결 금액 (수수료 제외)
    fee: float = 0.0
    uuids: list = field(default_factory=list)
    mid: float = 0.0

    @property
    def avg(self) -> float:
        return self.funds / self.volume if self.volume else 0.0

    @property
    def slip_bp(self) -> float:
        if not (self.mid and self.volume):
            return 0.0
        return (self.avg / self.mid - 1) * 1e4 * (1 if self.side == "bid" else -1)

    def add(self, o: dict) -> None:
        tr = o.get("trades") or []
        self.volume += sum(float(t["volume"]) for t in tr) if tr else float(o.get("executed_volume") or 0)
        self.funds += sum(float(t["funds"]) for t in tr) if tr else float(o.get("executed_funds") or 0)
        self.fee += float(o.get("paid_fee") or 0)
        self.uuids.append(o.get("uuid", ""))


class DtExecutor:
    def __init__(self, client, live: bool, sleep=time.sleep):
        self.c = client
        self.live = live
        self.sleep = sleep
        self.errors = 0                 # 연속 주문 오류. 3이면 실행기가 신규 매수를 멈춘다
        self.paper_orders: dict[str, dict] = {}
        self.tick = TICK

    def load_tick(self) -> None:
        """코인마다 호가 단위가 다르다(BTC·ETH 1000원, SOL 100원, XRP 1원). 가격대가 바뀌면 단위도 바뀐다."""
        # ponytail: 시작할 때 한 번만 받는다. 가격이 단위 경계를 넘으면 지정가가 거절될 수 있다 → 그때 주기적 갱신
        try:
            self.tick = float(self.c._request("GET", "/v1/orderbook/instruments",
                                              params={"markets": MARKET})[0]["tick_size"])
        except Exception as e:
            logger.warning(f"호가 단위 조회 실패, {self.tick:g}원으로 둠: {e}")

    # ---------------------------------------------------------- 공통
    def _ident(self) -> str:
        return f"dt-{uuidlib.uuid4().hex}"

    def preflight(self) -> str:
        """키·권한·IP 확인. 실패하면 사유 문자열, 정상이면 ""."""
        try:
            self.c._request("GET", "/v1/accounts", auth=True)
            self.c._request("POST", "/v1/orders/test", auth=True, retry=False, data={
                "market": MARKET, "side": "bid", "ord_type": "best", "time_in_force": "ioc",
                "price": "6000", "identifier": self._ident() + "-t"})
            return ""
        except Exception as e:
            return str(e)[:300]

    def _place(self, body: dict) -> dict:
        ident = self._ident()
        self.c._request("POST", "/v1/orders/test", auth=True, retry=False,
                        data={**body, "identifier": ident + "-t"})
        body = {**body, "identifier": ident}
        try:
            return self.c._request("POST", "/v1/orders", data=body, auth=True, retry=False)
        except UpbitClientError as e:
            if str(e).startswith("API 에러"):
                raise           # 거절이 확실한 응답. 주문은 없다
            logger.warning(f"주문 응답 끊김 → identifier 조회 ({ident}): {e}")
            for _ in range(3):
                try:
                    return self.c._request("GET", "/v1/order", params={"identifier": ident}, auth=True)
                except UpbitClientError as e2:
                    if "404" in str(e2) or "not_found" in str(e2):
                        raise UpbitClientError(f"주문 미접수 확인 (identifier {ident})") from e
                    self.sleep(1)
            raise

    def _wait(self, o: dict, timeout_s: float = 10) -> dict:
        end = time.time() + timeout_s
        while True:
            o = self.c._request("GET", "/v1/order", params={"uuid": o["uuid"]}, auth=True)
            if o.get("state") in ("done", "cancel") or time.time() > end:
                return o
            self.sleep(0.3)

    def _ok(self) -> None:
        self.errors = 0

    def _err(self, what: str, e: Exception) -> None:
        self.errors += 1
        logger.error(f"{what} 실패 (연속 {self.errors}회): {e}")

    # ---------------------------------------------------------- 시장성
    def buy(self, krw: float, mid: float, book: dict | None = None) -> Fill | None:
        f = Fill("bid", mid=mid)
        try:
            if not self.live:
                ask = (book or {}).get("best_ask") or mid
                # 업비트 시장성 매수: price = 체결 금액, 수수료는 별도로 잠긴다 (그래서 주문 금액은 가용 × 0.995)
                f.funds, f.fee = float(int(krw)), int(krw) * FEE
                f.volume = f.funds / ask
                f.uuids.append(f"paper-{uuidlib.uuid4().hex[:8]}")
                self._ok(); return f
            left = int(krw)
            for attempt in range(2):
                o = self._wait(self._place({"market": MARKET, "side": "bid", "ord_type": "best",
                                            "time_in_force": "ioc", "price": str(left)}))
                f.add(o)
                left = int(krw - f.funds)
                if f.funds >= krw * 0.95 or left < MIN_KRW:
                    break
                logger.info(f"매수 부분 체결 {f.funds:,.0f}/{krw:,.0f} → 남은 {left:,}원 재시도")
            self._ok()
            return f if f.volume > 0 else None
        except Exception as e:
            self._err("매수", e)
            return f if f.volume > 0 else None

    def sell(self, volume: float, mid: float, book: dict | None = None) -> Fill | None:
        f = Fill("ask", mid=mid)
        volume = math.floor(volume * 1e8) / 1e8
        try:
            if not self.live:
                bid = (book or {}).get("best_bid") or mid
                f.volume, f.funds = volume, volume * bid
                f.fee = f.funds * FEE
                f.uuids.append(f"paper-{uuidlib.uuid4().hex[:8]}")
                self._ok(); return f
            for ord_type in ("best", "best", "market"):
                left = math.floor((volume - f.volume) * 1e8) / 1e8
                if left * mid < MIN_KRW:
                    break
                body = {"market": MARKET, "side": "ask", "ord_type": ord_type, "volume": format_volume(left)}
                if ord_type == "best":
                    body["time_in_force"] = "ioc"
                f.add(self._wait(self._place(body)))
            self._ok()
            return f if f.volume > 0 else None
        except Exception as e:
            self._err("매도", e)
            return f if f.volume > 0 else None

    # ---------------------------------------------------------- 지정가 (vwap_revert)
    def tick_floor(self, p: float) -> float:
        return p // self.tick * self.tick

    def tick_ceil(self, p: float) -> float:
        return math.ceil(p / self.tick) * self.tick

    def limit(self, side: str, price: int, volume: float) -> str | None:
        """post_only 지정가. 반환 uuid (실패 None)."""
        try:
            if not self.live:
                u = f"paper-{uuidlib.uuid4().hex[:8]}"
                self.paper_orders[u] = {"uuid": u, "side": side, "price": price, "volume": volume,
                                        "state": "wait", "trades": []}
                self._ok(); return u
            o = self._place({"market": MARKET, "side": side, "ord_type": "limit", "time_in_force": "post_only",
                             "price": f"{price:.10g}", "volume": format_volume(volume)})
            self._ok()
            return o["uuid"]
        except Exception as e:
            self._err(f"지정가 {side}", e)
            return None

    def get(self, u: str, price_now: float | None = None) -> dict:
        """주문 상태. 가상 주문은 가격이 지정가를 '통과'해야 체결로 본다."""
        if not self.live:
            o = self.paper_orders[u]
            if o["state"] == "wait" and price_now is not None and (
                    (o["side"] == "bid" and price_now < o["price"]) or (o["side"] == "ask" and price_now > o["price"])):
                o["state"] = "done"
                o["trades"] = [{"volume": o["volume"], "funds": o["volume"] * o["price"]}]
                o["paid_fee"] = o["volume"] * o["price"] * FEE
            return o
        return self.c._request("GET", "/v1/order", params={"uuid": u}, auth=True)

    def cancel(self, u: str) -> dict:
        """취소 후 최종 상태 (부분 체결분 포함)."""
        if not self.live:
            o = self.paper_orders[u]
            if o["state"] == "wait":
                o["state"] = "cancel"
            return o
        try:
            self.c._request("DELETE", "/v1/order", params={"uuid": u}, auth=True)
        except UpbitClientError as e:
            logger.info(f"취소 응답: {e}")   # 이미 체결/취소된 주문
        for _ in range(10):
            o = self.get(u)
            if o.get("state") in ("done", "cancel"):
                return o
            self.sleep(0.3)
        return o
