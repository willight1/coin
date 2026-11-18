"""
dt_snapshot.py — BTC 자율 에이전트 입력 데이터 (docs/BTC_DAYTRADE.md 3장)
=========================================================================
봉 10종(1분~주봉) 200개씩 + 호가 + 체결 + 유의종목 + 이벤트. 지표는 **확정봉**으로만 계산하고
진행 중인 봉은 따로 둔다. 시세 조회만 한다.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from .indicators import calc_atr, calc_ema, calc_macd, calc_rsi
from .paths import PROJECT_ROOT

KST = timezone(timedelta(hours=9))
UNITS = (1, 3, 5, 10, 15, 30, 60, 240, 1440, "week")
RAW_UNITS = (5, 15, 60, 240, 1440)
EVENTS_PATH = os.path.join(PROJECT_ROOT, "state", "dt_events.json")
# 프로세스 하나 = 코인 하나 (main.py --market). 코인마다 따로 띄우고 예산·상태 폴더도 따로 쓴다
MARKET = os.getenv("DT_MARKET", "KRW-BTC").strip().upper()
COIN = MARKET.split("-", 1)[1]
COIN_KO = {"BTC": "비트코인", "ETH": "이더리움", "XRP": "리플", "SOL": "솔라나"}.get(COIN, COIN)


def endpoint(unit) -> str:
    if unit == "week":
        return "/v1/candles/weeks"
    if unit == 1440:
        return "/v1/candles/days"
    return f"/v1/candles/minutes/{unit}"


def unit_minutes(unit) -> int:
    return 7 * 1440 if unit == "week" else int(unit)


def to_df(candles: list[dict]) -> pd.DataFrame:
    """업비트 캔들 → 오름차순 df (open/high/low/close/volume/value/candle_date_time_kst)."""
    df = pd.DataFrame(candles)
    if df.empty:
        return df
    df = df.rename(columns={"opening_price": "open", "high_price": "high", "low_price": "low",
                            "trade_price": "close", "candle_acc_trade_volume": "volume",
                            "candle_acc_trade_price": "value"})
    df = df.drop_duplicates("candle_date_time_kst").sort_values("candle_date_time_kst")
    return df.reset_index(drop=True)[["candle_date_time_kst", "open", "high", "low", "close",
                                      "volume", "value"]]


def split_closed(df: pd.DataFrame, unit, now: datetime) -> tuple[pd.DataFrame, pd.Series | None]:
    """(확정봉, 진행 중 봉). 봉 시작 + 길이 > 지금이면 진행 중."""
    if df.empty:
        return df, None
    start = pd.Timestamp(df["candle_date_time_kst"].iloc[-1])
    end = start + pd.Timedelta(minutes=unit_minutes(unit))
    if end > pd.Timestamp(now.astimezone(KST).replace(tzinfo=None)):
        return df.iloc[:-1].reset_index(drop=True), df.iloc[-1]
    return df, None


def fetch_candles(client, unit, count: int = 200, to: str | None = None) -> pd.DataFrame:
    params = {"market": MARKET, "count": min(count, 200)}
    if to:
        params["to"] = to
    return to_df(client._request("GET", endpoint(unit), params=params))


def fetch_history(client, unit, count: int) -> pd.DataFrame:
    """200개 제한을 페이지네이션으로 넘긴다 (점수판 90일치)."""
    parts, to, left = [], None, count
    while left > 0:
        raw = client._request("GET", endpoint(unit),
                              params={"market": MARKET, "count": min(left, 200), **({"to": to} if to else {})})
        if not raw:
            break
        parts.append(to_df(raw))
        left -= len(raw)
        to = raw[-1]["candle_date_time_utc"]
        time.sleep(0.15)
    if not parts:
        return pd.DataFrame()
    df = pd.concat(parts).drop_duplicates("candle_date_time_kst").sort_values("candle_date_time_kst")
    return df.reset_index(drop=True)


# ---------------------------------------------------------------- 지표
def adx(df: pd.DataFrame, n: int = 14) -> tuple[pd.Series, pd.Series, pd.Series]:
    h, l, c = df["high"], df["low"], df["close"]
    up, dn = h.diff(), -l.diff()
    pdm = up.where((up > dn) & (up > 0), 0.0)
    mdm = dn.where((dn > up) & (dn > 0), 0.0)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    a = 1 / n
    atr = tr.ewm(alpha=a, adjust=False).mean()
    pdi = 100 * pdm.ewm(alpha=a, adjust=False).mean() / atr
    mdi = 100 * mdm.ewm(alpha=a, adjust=False).mean() / atr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=a, adjust=False).mean(), pdi, mdi


def _r(x, d=2):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if not np.isfinite(x) else round(x, d)


def indicators(df: pd.DataFrame) -> dict:
    """확정봉 df 의 마지막 시점 지표 요약 (3-2)."""
    if len(df) < 30:
        return {"bars": len(df)}
    c, h, l = df["close"], df["high"], df["low"]
    val = df["value"] if "value" in df else c * df["volume"]
    last = c.iloc[-1]
    ema = {p: calc_ema(c, p) for p in (9, 21, 50, 100)}
    e = [ema[p].iloc[-1] for p in (9, 21, 50, 100)]
    align = "정배열" if e[0] > e[1] > e[2] > e[3] else "역배열" if e[0] < e[1] < e[2] < e[3] else "혼조"
    a, pdi, mdi = adx(df)
    rsi = calc_rsi(c, 14)
    cross = np.where((rsi.shift(1) < 70) & (rsi >= 70))[0]
    macd = calc_macd(c)
    hist = macd[2] if isinstance(macd, tuple) else macd.iloc[:, -1]
    lo14, hi14 = l.rolling(14).min(), h.rolling(14).max()
    k = 100 * (c - lo14) / (hi14 - lo14).replace(0, np.nan)
    d = k.rolling(3).mean()
    atr = calc_atr(h, l, c, 14)
    mid, sd = c.rolling(20).mean(), c.rolling(20).std()
    bw = (4 * sd) / mid
    pb = (c - (mid - 2 * sd)) / (4 * sd).replace(0, np.nan)
    bw100 = bw.tail(100)
    hi20, lo20 = h.rolling(20).max().iloc[-1], l.rolling(20).min().iloc[-1]
    obv = (np.sign(c.diff()).fillna(0) * df["volume"]).cumsum()
    v20 = df["volume"].tail(20).sum()
    up = (c > df["open"]).tail(20)
    return {
        "close": _r(last, 0),
        "chg1_pct": _r((last / c.iloc[-2] - 1) * 100),
        "chg12_pct": _r((last / c.iloc[-13] - 1) * 100) if len(c) > 13 else None,
        "donchian20_pos": _r((last - lo20) / (hi20 - lo20) if hi20 > lo20 else 0.5),
        "to_high20_pct": _r((hi20 / last - 1) * 100), "to_low20_pct": _r((lo20 / last - 1) * 100),
        "ema_align": align,
        "gap_ema50_pct": _r((last / e[2] - 1) * 100), "gap_ema100_pct": _r((last / e[3] - 1) * 100),
        "adx14": _r(a.iloc[-1], 1), "pdi": _r(pdi.iloc[-1], 1), "mdi": _r(mdi.iloc[-1], 1),
        "rsi14": _r(rsi.iloc[-1], 1), "rsi14_prev": _r(rsi.iloc[-2], 1),
        "bars_since_rsi70_cross": int(len(c) - 1 - cross[-1]) if len(cross) else None,
        "macd_hist": _r(hist.iloc[-1], 0), "macd_hist_prev": _r(hist.iloc[-2], 0),
        "stoch_k": _r(k.iloc[-1], 1), "stoch_d": _r(d.iloc[-1], 1),
        "atr14_pct": _r(atr.iloc[-1] / last * 100),
        "bb_pctb": _r(pb.iloc[-1]), "bb_width_pct": _r(bw.iloc[-1] * 100),
        "bb_width_pctile100": _r((bw100 <= bw.iloc[-1]).mean() * 100, 0),
        "value_vs_avg20": _r(val.iloc[-1] / val.tail(21).iloc[:-1].mean()),
        "value_z": _r((val.iloc[-1] - val.tail(20).mean()) / (val.tail(20).std() or np.nan)),
        "obv_slope20": _r((obv.iloc[-1] - obv.iloc[-21]) / v20 if v20 else 0, 3),
        "up_bar_value_share20": _r(val.tail(20)[up].sum() / val.tail(20).sum() if val.tail(20).sum() else 0),
    }


def raw_rows(df: pd.DataFrame, n: int = 8) -> list:
    """[시각, 시가, 고가, 저가, 종가, 거래대금(백만원)]"""
    t = df.tail(n)
    return [[r.candle_date_time_kst[5:16], int(r.open), int(r.high), int(r.low), int(r.close),
             round(r.value / 1e6)] for r in t.itertuples()]


def session_start(now: datetime) -> datetime:
    now = now.astimezone(KST)
    s = now.replace(hour=9, minute=0, second=0, microsecond=0)
    return s if now >= s else s - timedelta(days=1)


def session_vwap(df15: pd.DataFrame, now: datetime) -> float | None:
    """세션(09:00) 이후 15분봉(진행 중 포함)의 거래량 가중 평균가."""
    if df15.empty:
        return None
    s = df15[pd.to_datetime(df15["candle_date_time_kst"]) >= pd.Timestamp(session_start(now).replace(tzinfo=None))]
    if s.empty or s["volume"].sum() <= 0:
        return None
    tp = (s["high"] + s["low"] + s["close"]) / 3
    return float((tp * s["volume"]).sum() / s["volume"].sum())


def orderbook_info(ob: dict) -> dict:
    u = ob.get("orderbook_units", [])
    if not u:
        return {}
    ask, bid = float(u[0]["ask_price"]), float(u[0]["bid_price"])
    mid = (ask + bid) / 2

    def imb(n):
        a = sum(float(x["ask_size"]) for x in u[:n]); b = sum(float(x["bid_size"]) for x in u[:n])
        return {"ask_btc": round(a, 3), "bid_btc": round(b, 3), "imbalance": _r((b - a) / (a + b) if a + b else 0)}
    return {"best_ask": ask, "best_bid": bid, "mid": mid, "spread_bp": _r((ask - bid) / mid * 1e4),
            "top5": imb(5), "top15": imb(15)}


def trades_info(ticks: list[dict]) -> dict:
    if not ticks:
        return {}
    vol = [float(t["trade_volume"]) for t in ticks]
    buy = sum(v for v, t in zip(vol, ticks) if t.get("ask_bid") == "BID")
    ts = [int(t["timestamp"]) for t in ticks]
    krw = [v * float(t["trade_price"]) for v, t in zip(vol, ticks)]
    recent = [k for k, s in zip(krw, ts) if s >= max(ts) - 60_000]
    return {"n": len(ticks), "buy_ratio": _r(buy / sum(vol) if sum(vol) else 0),
            "span_sec": round((max(ts) - min(ts)) / 1000),
            "block_1m": any(k >= 100_000_000 for k in recent),   # 1분 내 1억원 이상 단일 체결
            "max_1m_krw_m": round(max(recent, default=0) / 1e6)}


def load_events() -> list[dict]:
    try:
        with open(EVENTS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def next_events(now: datetime, events: list[dict] | None = None) -> dict:
    """{"FOMC": 남은 시간(h), "CPI": ...}. 지난 이벤트는 60분 동안 음수로 보인다."""
    out = {}
    for e in events if events is not None else load_events():
        try:
            t = datetime.fromisoformat(e["time"])
        except (KeyError, ValueError):
            continue
        h = (t - now).total_seconds() / 3600
        if h > -1 and (e["name"] not in out or h < out[e["name"]]):
            out[e["name"]] = round(h, 2)
    return out


def market_warning(client) -> bool | None:
    try:
        for m in client._request("GET", "/v1/market/all", params={"is_details": "true"}):
            if m.get("market") == MARKET:
                ev = m.get("market_event") or {}
                return bool(ev.get("warning")) or m.get("market_warning", "NONE") != "NONE"
    except Exception:
        return None
    return None


def collect(client, now: datetime | None = None) -> dict:
    """모든 봉 + 시장 정보. 반환: {"closed": {unit: df}, "open_bar": {unit: Series}, "market": {...}}"""
    now = now or datetime.now(KST)
    closed, open_bar = {}, {}
    for u in UNITS:
        df = fetch_candles(client, u)
        closed[u], open_bar[u] = split_closed(df, u, now)
        if u == 15:
            vwap = session_vwap(df, now)
        time.sleep(0.12)
    tk = client._request("GET", "/v1/ticker", params={"markets": MARKET})[0]
    ob = orderbook_info(client._request("GET", "/v1/orderbook", params={"markets": MARKET})[0])
    ticks = client._request("GET", "/v1/trades/ticks", params={"market": MARKET, "count": 200})
    price = float(tk["trade_price"])
    return {"closed": closed, "open_bar": open_bar, "market": {
        "time": now.isoformat(timespec="seconds"), "price": price,
        "change_pct": _r(float(tk.get("signed_change_rate", 0)) * 100),
        "value_24h_krw_bn": _r(float(tk.get("acc_trade_price_24h", 0)) / 1e9, 1),
        "session_vwap": _r(vwap, 0), "vwap_gap_pct": _r((price / vwap - 1) * 100) if vwap else None,
        "orderbook": ob, "trades": trades_info(ticks), "warning": market_warning(client),
        "events_h": next_events(now)}}


def candles_section(data: dict) -> dict:
    out = {}
    for u in UNITS:
        df, ob = data["closed"][u], data["open_bar"][u]
        key = "1w" if u == "week" else "1d" if u == 1440 else f"{u}m"
        sec = {"ind": indicators(df)}
        if u in RAW_UNITS:
            sec["raw"] = raw_rows(df)
        if ob is not None:
            sec["forming"] = [ob["candle_date_time_kst"][5:16], int(ob["open"]), int(ob["high"]),
                              int(ob["low"]), int(ob["close"]), round(ob["value"] / 1e6)]
        out[key] = sec
    return out
