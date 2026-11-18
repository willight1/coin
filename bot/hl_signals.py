"""하이퍼리퀴드 추세 봇 신호: 투표단(state/hl_signals.json, 기본은 2026-11-14 전수 측정 통과 8개)의 투표 평균 + 변동성 맞춤 레버리지.

- 규칙 계산은 bot/hl_rules.py (측정기와 같은 코드). 투표단은 research/hl_reselect.py 가 거래 종료·주간마다 합격 기준으로 다시 고른다.
- 노출 = 분석가(추세·파생·거시·심리)별 투표 평균을 다시 같은 비중으로 평균(-1~+1). 배수 = 목표 연변동성 ÷ 최근 20일(4h 120봉) 실현변동성, min_lev~max_lev.
측정: 바이낸스 2017-08~2026-11, 기본 8개·목표변동성 60%·상한 3배 → BTC 연 +59% MDD 47%, ETH +56% MDD 44% (strategy-research 스킬).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import json
import logging
import os
import re
import time

import requests

from .hl_rules import DEFAULT_CHOSEN, NEEDS_COIN, NEEDS_EXTRA, NEEDS_FUNDING, analyst, rules
from .paths import PROJECT_ROOT

logger = logging.getLogger(__name__)

VOL_WINDOW_BARS = 120          # 4h × 120 = 20일
BARS_PER_YEAR = 6 * 365
SIGNALS_PATH = os.path.join(PROJECT_ROOT, "state", "hl_signals.json")
_cache: dict = {"mtime": None, "chosen": DEFAULT_CHOSEN, "updated": None}


def chosen() -> tuple[dict[str, list[str]], str | None]:
    """지금 투표에 쓰는 규칙 이름 {tf: [...]} 과 마지막 수정 시각. research/hl_reselect.py 가 쓰는 파일을 읽는다."""
    try:
        m = os.path.getmtime(SIGNALS_PATH)
        if m != _cache["mtime"]:
            with open(SIGNALS_PATH) as f:
                j = json.load(f)
            _cache.update(mtime=m, chosen=j.get("chosen") or DEFAULT_CHOSEN, updated=j.get("updated"))
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return _cache["chosen"], _cache["updated"]


# ---- 일봉 외부 입력. 측정기(research/leverage_2026-11/fetch_bn.py)도 이 함수들로 받는다
INPUT_TTL = 6 * 3600
_inputs: dict[str, tuple[float, object]] = {}


def funding_daily(coin: str, days: int = 800) -> pd.Series:
    """바이낸스 {coin}USDT 펀딩비 일별 합 (측정과 같은 출처)."""
    rows, t, end = [], int((time.time() - days * 86400) * 1000), int(time.time() * 1000)
    while t < end:
        r = requests.get("https://fapi.binance.com/fapi/v1/fundingRate",
                         params={"symbol": f"{coin}USDT", "startTime": t, "endTime": end, "limit": 1000}, timeout=15)
        r.raise_for_status(); r = r.json()
        if not r:
            break
        rows += r; t = r[-1]["fundingTime"] + 1
    f = pd.DataFrame(rows).drop_duplicates("fundingTime")
    return pd.Series(f["fundingRate"].astype(float).values, index=pd.to_datetime(f["fundingTime"], unit="ms", utc=True)).resample("D").sum()


COINNESS = os.path.join(PROJECT_ROOT, "logs", "coinness.jsonl")
_LIQ = re.compile(r"24시간 기준으로는 ([\d.]+)억 달러")


def coinness_liq_daily() -> pd.Series:
    """UTC 하루마다 코인니스 '24시간 강제 청산' 집계의 최댓값(억 달러). 파일은 launchd com.juno.coinness 가 쌓는다(2021-09~)."""
    out: dict[pd.Timestamp, float] = {}
    with open(COINNESS, encoding="utf-8") as f:
        for line in f:
            if "24시간 기준으로는" not in line:
                continue
            x = json.loads(line); m = _LIQ.search(x.get("content") or "")
            if m:
                d = pd.Timestamp(x["t"]).floor("D"); out[d] = max(out.get(d, 0.0), float(m.group(1)))
    return pd.Series(out, dtype=float).sort_index()


def external_daily() -> pd.DataFrame:
    """일봉 외부 데이터: fng = 공포탐욕지수(alternative.me), stable = 스테이블코인 시총 달러(DefiLlama), liq24 = 코인니스 24시간 청산(억 달러)."""
    fng = requests.get("https://api.alternative.me/fng/", params={"limit": 0, "format": "json"}, timeout=20).json()["data"]
    st = requests.get("https://stablecoins.llama.fi/stablecoincharts/all", timeout=30).json()
    return pd.DataFrame({
        "fng": pd.Series({pd.Timestamp(int(x["timestamp"]), unit="s", tz="UTC"): float(x["value"]) for x in fng}),
        "stable": pd.Series({pd.Timestamp(int(x["date"]), unit="s", tz="UTC"): float(x["totalCirculatingUSD"].get("peggedUSD", 0)) for x in st}),
        "liq24": coinness_liq_daily(),
    }).sort_index()


_VISION = "https://data.binance.vision/data/futures/um/daily/metrics/{s}/{s}-metrics-{d}.zip"
_FAPI = "https://fapi.binance.com/futures/data/"


def _vision_day(sym: str, d: str) -> dict | None:
    """그날 5분 간격 파일의 마지막 줄. 파일이 없으면(아직 안 올라옴·옛 빈 날) None."""
    import io, zipfile
    for k in range(3):
        try:
            r = requests.get(_VISION.format(s=sym, d=d), timeout=20)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            z = zipfile.ZipFile(io.BytesIO(r.content))
            row = pd.read_csv(z.open(z.namelist()[0])).dropna(subset=["sum_open_interest"]).iloc[-1]
            return {"t": pd.Timestamp(d, tz="UTC"), "oi": float(row["sum_open_interest"]), "top_ls": float(row["sum_toptrader_long_short_ratio"]),
                    "acct_ls": float(row["count_long_short_ratio"]), "taker": float(row["sum_taker_long_short_vol_ratio"])}
        except Exception:
            if k == 2:
                return None


def metrics_daily(coin: str, path: str | None = None, start: str = "2020-09-01") -> pd.DataFrame:
    """바이낸스 {coin}USDT 무기한: 미결제약정 oi(코인 수)·전체 계정 롱숏 비율 acct_ls·상위 트레이더 롱숏 top_ls — UTC 하루의 마지막 값.
    과거는 공개 파일(data.binance.vision, BTC 2020-09~·ETH 2021-12~)을 path 에 쌓고(증분), 파일이 아직 안 나온 최근 날은
    같은 값의 실시간 API(30일치만 준다)로 메운다(저장 안 함 — 파일이 나오면 그 값으로 바뀐다)."""
    from concurrent.futures import ThreadPoolExecutor
    sym = f"{coin}USDT"
    path = path or os.path.join(PROJECT_ROOT, "state", f"hl_metrics_{coin}.pkl")
    old = pd.read_pickle(path) if os.path.exists(path) else None
    last = pd.Timestamp.now(tz="UTC").floor("D") - pd.Timedelta(days=1)
    first = old.index[-1] + pd.Timedelta(days=1) if old is not None and len(old) else pd.Timestamp(start, tz="UTC")
    days = pd.date_range(first, last)
    with ThreadPoolExecutor(16) as ex:
        rows = [r for r in ex.map(lambda d: _vision_day(sym, d.strftime("%Y-%m-%d")), days) if r]
    df = pd.concat([x for x in (old, pd.DataFrame(rows).set_index("t") if rows else None) if x is not None]).sort_index()
    if rows:                                                  # 실계좌 봇과 연습용 봇이 같은 캐시를 쓴다 → 반쯤 쓴 파일을 읽지 않게 바꿔치기
        tmp = f"{path}.{os.getpid()}.tmp"
        df.to_pickle(tmp)
        os.replace(tmp, path)
    for d in days[days > df.index[-1]][-29:]:
        q = {"symbol": sym, "period": "5m", "limit": 1, "endTime": int((d + pd.Timedelta(days=1)).timestamp() * 1000) - 1}
        oi = requests.get(_FAPI + "openInterestHist", params=q, timeout=15).json()
        ls = requests.get(_FAPI + "globalLongShortAccountRatio", params=q, timeout=15).json()
        if oi and ls:
            df.loc[d, ["oi", "acct_ls"]] = float(oi[-1]["sumOpenInterest"]), float(ls[-1]["longShortRatio"])
    return df


def dvol_daily(coin: str) -> pd.Series:
    """Deribit 옵션 내재변동성 지수(DVOL) 일봉 종가 (공개 API, 2021-03~, BTC·ETH)."""
    rows, end = [], int(time.time() * 1000)
    while True:
        r = requests.get("https://www.deribit.com/api/v2/public/get_volatility_index_data", timeout=20,
                         params={"currency": coin, "start_timestamp": 1609459200000, "end_timestamp": end, "resolution": "1D"}).json()["result"]
        rows += r["data"]
        if not r.get("continuation"):
            break
        end = r["continuation"]
    s = pd.Series({pd.Timestamp(x[0], unit="ms", tz="UTC"): float(x[4]) for x in rows}).sort_index()
    return s[~s.index.duplicated()]


def coin_daily(coin: str, path: str | None = None) -> pd.DataFrame:
    """코인별 일봉 입력(열 oi·acct_ls·dvol). 측정기(fetch_bn)도 이 함수로 받는다."""
    m = metrics_daily(coin, path)
    return pd.DataFrame({"oi": m["oi"], "acct_ls": m["acct_ls"], "dvol": dvol_daily(coin)}).sort_index()


def desk(votes: dict[str, float]) -> dict[str, float]:
    """분석가(추세·파생·거시·심리)별 의견 = 그 묶음 규칙 표의 평균."""
    g: dict[str, list[float]] = {}
    for name, v in votes.items():
        g.setdefault(analyst(name), []).append(v)
    return {k: sum(v) / len(v) for k, v in g.items()}


def combine(votes: dict[str, float]) -> float:
    """메인: 분석가 의견을 같은 비중으로 평균 (research/leverage_2026-11/desk.py 'M1')."""
    d = desk(votes)
    return sum(d.values()) / len(d) if d else 0.0


_failed: dict[str, float] = {}


def _cached(key: str, fn):
    t, v = _inputs.get(key, (0.0, None))
    if time.time() - t > INPUT_TTL:
        try:
            v = fn(); _inputs[key] = (time.time(), v); _failed.pop(key, None)
        except Exception as e:                    # 실패 → 이전 값, 없으면 None(그 규칙은 0표)
            _failed[key] = time.time()
            logger.warning(f"외부 입력 {key} 조회 실패, {'이전 값 사용' if v is not None else '이번엔 0표'}: {e}")
    return v


def input_status() -> dict[str, dict]:
    """외부 입력별 마지막으로 받은 시각과 마지막 실패 시각(유닉스 초). 대시보드 '에이전트 상황실'용."""
    return {k: {"ok": int(_inputs[k][0]) if k in _inputs else None, "fail": int(_failed[k]) if k in _failed else None}
            for k in sorted(set(_inputs) | set(_failed))}


def vote(d: pd.DataFrame, h: pd.DataFrame, coin: str | None = None) -> tuple[float, dict[str, float]]:
    """노출(-1~+1)과 지표별 표. 투표단은 chosen()(기본 8개). NaN(워밍업 부족)·입력 없음은 0표. 노출 = 분석가별 평균의 평균(combine)."""
    ch, _ = chosen()
    votes: dict[str, float] = {}
    for tf, df in (("1d", d), ("4h", h)):
        names = ch.get(tf) or []
        if not names:
            continue
        fund = extra = None
        if tf == "1d" and coin:                      # 필요한 규칙이 투표단에 있을 때만 받는다
            if NEEDS_FUNDING & set(names):
                fund = _cached(f"funding_{coin}", lambda: funding_daily(coin))
            if NEEDS_EXTRA & set(names):
                extra = _cached("external", external_daily)
            if set(NEEDS_COIN) & set(names):
                x = _cached(f"coin_{coin}", lambda: coin_daily(coin))
                if x is not None:
                    extra = x if extra is None else extra.join(x, how="outer")
        R = rules(df, fund, extra)
        for n in names:
            v = R[n].iloc[-1] if n in R else 0.0
            votes[f"{'일봉' if tf == '1d' else '4h'} {n}"] = 0.0 if pd.isna(v) else float(v)
    return combine(votes), votes


def realized_vol(h: pd.DataFrame) -> float:
    """확정 4시간봉 종가 로그수익률의 20일 표준편차, 연율화."""
    return float(np.log(h["close"] / h["close"].shift()).tail(VOL_WINDOW_BARS).std() * np.sqrt(BARS_PER_YEAR))


def leverage(h: pd.DataFrame, target_vol: float, max_lev: float, min_lev: float = 0.0) -> float:
    rv = realized_vol(h)
    raw = target_vol / rv if np.isfinite(rv) and rv > 0 else 1.0
    return float(min(max(raw, min_lev), max_lev))
