"""하이퍼리퀴드 API. 시세·캔들은 공개 info API(키 없음), 주문은 공식 SDK(HL_PRIVATE_KEY 있을 때만)."""

from __future__ import annotations

import logging
import time

import pandas as pd
import requests

logger = logging.getLogger(__name__)

INFO_URL = "https://api.hyperliquid.xyz/info"
TAKER_FEE = 0.00045            # 기본 등급 테이커 수수료 (지정가로 체결되면 0.015%)


class HlClient:
    def __init__(self, private_key: str = "", wallet: str = ""):
        self.live = bool(private_key)
        self.wallet = wallet
        self.meta = self._info({"type": "metaAndAssetCtxs"})[0]["universe"]
        self.idx = {a["name"]: i for i, a in enumerate(self.meta)}
        if self.live:
            from eth_account import Account
            from hyperliquid.exchange import Exchange
            from hyperliquid.info import Info
            acct = Account.from_key(private_key)
            # API 지갑(매매 전용)이면 account_address 에 본 지갑 주소를 준다
            self.ex = Exchange(acct, account_address=wallet or acct.address)
            self.info_sdk = Info(skip_ws=True)
            self.wallet = wallet or acct.address

    def _info(self, body: dict, tries: int = 3) -> list | dict:
        for k in range(tries):
            try:
                r = requests.post(INFO_URL, json=body, timeout=10)
                if r.status_code == 429:
                    time.sleep(1 + k)
                    continue
                r.raise_for_status()
                return r.json()
            except requests.RequestException as e:
                if k == tries - 1:
                    raise
                logger.info(f"하이퍼리퀴드 조회 실패, 다시 시도 ({k + 1}/{tries}): {e}")
                time.sleep(1 + k)
        raise RuntimeError("하이퍼리퀴드 조회 실패")

    def candles(self, coin: str, interval: str, bars: int) -> pd.DataFrame:
        """확정봉만 돌려준다(진행 중인 마지막 봉 제외). index = 봉 시작(UTC)."""
        ms = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000, "1h": 3_600_000,
              "4h": 14_400_000, "1d": 86_400_000}[interval]
        now = int(time.time() * 1000)
        rows = self._info({"type": "candleSnapshot",
                           "req": {"coin": coin, "interval": interval, "startTime": now - ms * (bars + 2), "endTime": now}})
        df = pd.DataFrame(rows)
        df = df[df["T"] < now]                                  # T = 봉 끝 시각. 아직 안 끝난 봉 제외
        df.index = pd.to_datetime(df["t"], unit="ms", utc=True)
        return df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})[
            ["open", "high", "low", "close", "volume"]].astype(float).tail(bars)

    def ctx(self) -> dict[str, dict]:
        """코인별 {mark, mid, funding(시간당), oi}."""
        meta, ctxs = self._info({"type": "metaAndAssetCtxs"})
        return {a["name"]: {"mark": float(c["markPx"]), "mid": float(c["midPx"] or c["markPx"]),
                            "funding": float(c["funding"]), "oi": float(c["openInterest"])}
                for a, c in zip(meta["universe"], ctxs)}

    def sz_decimals(self, coin: str) -> int:
        return self.meta[self.idx[coin]]["szDecimals"]

    def max_leverage(self, coin: str) -> float:
        return float(self.meta[self.idx[coin]]["maxLeverage"])

    # ---------------- 실거래 (SDK). 가상 모드에서는 부르지 않는다
    def account(self) -> tuple[float, dict[str, dict]]:
        """(계좌 평가금액 USD, 코인별 {size(부호), entry, liq(청산가), lev(거래소 배수), upnl, margin}). self.withdrawable 도 갱신."""
        st = self.info_sdk.user_state(self.wallet)
        self.withdrawable = float(st.get("withdrawable") or 0)
        pos = {}
        for p in st["assetPositions"]:
            q = p["position"]
            if float(q["szi"]):
                pos[q["coin"]] = {"size": float(q["szi"]), "entry": float(q["entryPx"] or 0),
                                  "liq": float(q["liquidationPx"]) if q.get("liquidationPx") else None,
                                  "lev": float((q.get("leverage") or {}).get("value") or 0),
                                  "upnl": float(q.get("unrealizedPnl") or 0), "margin": float(q.get("marginUsed") or 0)}
        return float(st["marginSummary"]["accountValue"]), pos

    def funding(self, since_ms: int) -> list[tuple[int, str, float]]:
        """받은(+)·낸(−) 펀딩비 [(시각ms, 코인, USDC)]. 한 번에 최대 500건."""
        rows = self._info({"type": "userFunding", "user": self.wallet, "startTime": since_ms})
        return [(int(r["time"]), r["delta"]["coin"], float(r["delta"]["usdc"])) for r in rows]

    def fills_pnl(self, coin: str, since_ms: int) -> tuple[float, float, float | None]:
        """since_ms 이후 그 코인 체결의 (실현 손익 합, 수수료 합, 평균 체결가). 거래소가 계산한 값."""
        rows = [r for r in self._info({"type": "userFillsByTime", "user": self.wallet, "startTime": since_ms}) if r["coin"] == coin]
        sz = sum(float(r["sz"]) for r in rows)
        return (sum(float(r["closedPnl"]) for r in rows), sum(float(r["fee"]) for r in rows),
                sum(float(r["px"]) * float(r["sz"]) for r in rows) / sz if sz else None)

    def set_leverage(self, coin: str, lev: int) -> None:
        self.ex.update_leverage(max(1, lev), coin, is_cross=True)

    def px_round(self, coin: str, px: float) -> float:
        """거래소 가격 규칙: 유효숫자 5자리, 소수는 (6 − 수량 소수 자릿수)자리까지."""
        return round(float(f"{px:.5g}"), 6 - self.sz_decimals(coin))

    def stops(self, coin: str) -> list[dict]:
        """그 코인에 걸려 있는 트리거 주문(손절) [{oid, size(부호 = 주문 방향), px(발동 가격)}]."""
        return [{"oid": o["oid"], "size": float(o["sz"]) * (1 if o["side"] == "B" else -1), "px": float(o["triggerPx"])}
                for o in self._info({"type": "frontendOpenOrders", "user": self.wallet}) if o["coin"] == coin and o.get("isTrigger")]

    def stop_order(self, coin: str, size: float, trigger_px: float) -> int:
        """손절 주문: 가격이 trigger_px 에 닿으면 시장가로 size(부호 = 주문 방향)만큼 포지션을 줄인다(reduce-only). 거래소에 걸려 있어 봇이 꺼져도 작동한다."""
        buy = size > 0
        r = self.ex.order(coin, buy, abs(size), self.px_round(coin, trigger_px * (1.05 if buy else 0.95)),      # 시장가 트리거라 지정가 칸은 넉넉한 한도
                          {"trigger": {"triggerPx": trigger_px, "isMarket": True, "tpsl": "sl"}}, reduce_only=True)
        st = r["response"]["data"]["statuses"][0] if r.get("status") == "ok" else {"error": str(r)}
        if isinstance(st, dict) and "error" in st:
            raise RuntimeError(f"{coin} 손절 주문 거절: {st['error']}")
        return (st.get("resting") or {}).get("oid", 0) if isinstance(st, dict) else 0

    def cancel(self, coin: str, oid: int) -> None:
        self.ex.cancel(coin, oid)

    def market_order(self, coin: str, size: float, slippage: float = 0.005) -> dict:
        """size 부호 = 방향. 시장가(IOC, 슬리피지 상한 0.5%)."""
        r = self.ex.market_open(coin, size > 0, abs(size), None, slippage)
        if r.get("status") != "ok":
            raise RuntimeError(f"주문 실패: {r}")
        st = r["response"]["data"]["statuses"][0]
        if "error" in st:
            raise RuntimeError(f"주문 거절: {st['error']}")
        return st.get("filled", st)

    def maker_order(self, coin: str, size: float, wait_sec: float, poll: float = 5.0, reprice_sec: float = 15.0, reduce_only: bool = False) -> float:
        """지정가만: 최우선 호가(매수는 매수호가, 매도는 매도호가)에 post-only(Alo)로 걸고, 호가가 우리 가격에서 멀어지면
        reprice_sec 뒤 취소하고 새 최우선 호가에 남은 수량을 다시 건다(따라가기). wait_sec 이 지나면 남은 건 취소.
        실제로 체결된 수량(부호 = 방향)을 돌려준다 — 주문 응답이 아니라 계좌 포지션 변화로 센다(이 계좌는 이 봇만 거래).
        취소가 확인되지 않으면 예외(그때는 다시 걸면 안 된다: 남은 주문이 나중에 체결될 수 있다)."""
        dec = self.sz_decimals(coin); tol = 10 ** -dec / 2
        size_now = lambda: self.account()[1].get(coin, {}).get("size", 0.0)
        best = lambda: float(self.info_sdk.l2_snapshot(coin)["levels"][0 if size > 0 else 1][0]["px"])
        before, end = size_now(), time.time() + wait_sec
        while time.time() < end:
            left = round(size - (size_now() - before), dec)
            px = best()
            if abs(left) < tol or abs(left) * px < 10.0:        # 다 됐거나, 남은 게 거래소 최소 주문(10달러) 미만
                break
            r = self.ex.order(coin, size > 0, abs(left), px, {"limit": {"tif": "Alo"}}, reduce_only=reduce_only)
            st = r["response"]["data"]["statuses"][0] if r.get("status") == "ok" else {"error": str(r)}
            if "error" in st:                                   # post-only 거절(그사이 호가가 움직임) 등 → 잠깐 뒤 새 호가로 다시
                logger.info(f"{coin} 지정가 주문 안 걸림: {st['error']}")
                time.sleep(min(poll, max(0.0, end - time.time())))
                continue
            oid, placed = (st.get("resting") or st.get("filled"))["oid"], time.time()
            while time.time() < end:
                time.sleep(min(poll, max(0.0, end - time.time())))
                if abs(size_now() - before - size) < tol:
                    return size
                if time.time() - placed >= reprice_sec and best() != px:   # 우리보다 앞선 호가가 생겼다 → 취소하고 다시 건다
                    break
            try:
                self.ex.cancel(coin, oid)                       # 그사이 다 체결됐으면 거절 응답이 오지만 아래 확인으로 충분
            finally:
                if any(o["oid"] == oid for o in self.info_sdk.open_orders(self.wallet)):
                    raise RuntimeError(f"{coin} 지정가 주문 취소가 확인되지 않음 (oid {oid})")
        return round(size_now() - before, dec)
