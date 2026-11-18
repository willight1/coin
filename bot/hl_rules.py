"""지표 규칙 라이브러리 — 봇(hl_signals)과 측정기(research/leverage_2026-11/sweep.py)가 같은 함수를 쓴다.

rules(df) → {규칙 이름: 포지션 시리즈(-1 숏 / 0 현금 / +1 롱)}. 봉 마감 값으로 판단, 체결은 다음 봉.
롱·숏은 대칭. 어떤 규칙을 투표에 쓸지는 state/hl_signals.json 이 정한다 (research/hl_reselect.py 가 합격 기준으로 고름).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .indicators import calc_rsi

sign = np.sign


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def true_range(df: pd.DataFrame) -> pd.Series:
    return pd.concat([df["high"] - df["low"], (df["high"] - df["close"].shift()).abs(),
                      (df["low"] - df["close"].shift()).abs()], axis=1).max(axis=1)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return true_range(df).ewm(alpha=1 / n, adjust=False).mean()


def adx(df: pd.DataFrame, n: int = 14) -> pd.Series:
    up, dn = df["high"].diff(), -df["low"].diff()
    pdm = ((up > dn) & (up > 0)) * up
    mdm = ((dn > up) & (dn > 0)) * dn
    a = atr(df, n)
    pdi = 100 * pdm.ewm(alpha=1 / n, adjust=False).mean() / a
    mdi = 100 * mdm.ewm(alpha=1 / n, adjust=False).mean() / a
    return (100 * (pdi - mdi).abs() / (pdi + mdi)).ewm(alpha=1 / n, adjust=False).mean()


def state(enter_l, exit_l, enter_s, exit_s) -> pd.Series:
    """진입·청산 신호 → 포지션 상태(-1/0/1). 롱·숏 각각 독립으로 켜고 끈다."""
    def run(e: pd.Series, x: pd.Series) -> np.ndarray:
        p, on = np.zeros(len(e)), 0
        e, x = e.fillna(False).values, x.fillna(False).values
        for i in range(len(e)):
            if on and x[i]:
                on = 0
            if not on and e[i]:
                on = 1
            p[i] = on
        return p
    return pd.Series(run(enter_l, exit_l) - run(enter_s, exit_s), index=enter_l.index)


def trail_dir(df: pd.DataFrame, n: int, mult: float) -> pd.Series:
    """ATR 추적선 방향(슈퍼트렌드식). 종가가 추적선을 넘으면 방향 전환."""
    hl2 = (df["high"] + df["low"]) / 2
    a = atr(df, n)
    up, dn, c = (hl2 + mult * a).values, (hl2 - mult * a).values, df["close"].values
    d = np.zeros(len(c)); fu, fd, cur = up[0], dn[0], 1
    for i in range(1, len(c)):
        fu = up[i] if up[i] < fu or c[i - 1] > fu else fu
        fd = dn[i] if dn[i] > fd or c[i - 1] < fd else fd
        if cur == 1 and c[i] < fd:
            cur = -1
        elif cur == -1 and c[i] > fu:
            cur = 1
        d[i] = cur
    out = pd.Series(d, index=df.index)
    out.iloc[:n] = 0
    return out


def psar_dir(df: pd.DataFrame, step: float = 0.02, cap: float = 0.2) -> pd.Series:
    h, l = df["high"].values, df["low"].values
    n = len(h); d = np.zeros(n)
    if n < 3:
        return pd.Series(d, index=df.index)
    bull, sar, ep, af = True, l[0], h[0], step
    for i in range(1, n):
        sar = sar + af * (ep - sar)
        if bull:
            sar = min(sar, l[i - 1], l[i - 2] if i >= 2 else l[i - 1])
            if l[i] < sar:
                bull, sar, ep, af = False, ep, l[i], step
            elif h[i] > ep:
                ep, af = h[i], min(af + step, cap)
        else:
            sar = max(sar, h[i - 1], h[i - 2] if i >= 2 else h[i - 1])
            if h[i] > sar:
                bull, sar, ep, af = True, ep, h[i], step
            elif l[i] < ep:
                ep, af = l[i], min(af + step, cap)
        d[i] = 1 if bull else -1
    return pd.Series(d, index=df.index)


def wma(s: pd.Series, n: int) -> pd.Series:
    w = np.arange(1, n + 1)
    return s.rolling(n).apply(lambda x: np.dot(x, w) / w.sum(), raw=True)


def hma(s: pd.Series, n: int) -> pd.Series:
    return wma(2 * wma(s, n // 2) - wma(s, n), int(np.sqrt(n)))


def rules(df: pd.DataFrame, fund_daily: pd.Series | None = None, extra: pd.DataFrame | None = None) -> dict[str, pd.Series]:
    """extra = 일봉 외부 데이터(열 fng=공포탐욕, stable=스테이블코인 시총, liq24=코인니스 24시간 청산, 코인별 oi·acct_ls·dvol). 있는 열만 쓴다. 일봉에서만 넘긴다."""
    c, h, l, o = df["close"], df["high"], df["low"], df["open"]
    v = df["value"] if "value" in df else c * df["volume"]
    R: dict[str, pd.Series] = {}
    # ---- 기존 23개 (2026-11-14 전수 측정) ----
    for n in (20, 50, 100, 200):
        R[f"이동평균{n} 위/아래"] = sign(c - c.rolling(n).mean())
    e20, e50, e100 = ema(c, 20), ema(c, 50), ema(c, 100)
    R["이평 정배열/역배열"] = ((e20 > e50) & (e50 > e100)) * 1 - ((e20 < e50) & (e50 < e100)) * 1
    for n, m in ((20, 10), (55, 20)):
        R[f"돈치언 {n}봉 돌파"] = state(c > h.rolling(n).max().shift(), c < l.rolling(m).min().shift(),
                                   c < l.rolling(n).min().shift(), c > h.rolling(m).max().shift())
    r = calc_rsi(c, 14)
    R["RSI 추세(70돌파 롱/30이탈 숏)"] = state((r.shift() < 70) & (r >= 70), r < 50, (r.shift() > 30) & (r <= 30), r > 50)
    R["RSI 역추세(30아래 롱/70위 숏)"] = state(r < 30, r > 50, r > 70, r < 50)
    macd = ema(c, 12) - ema(c, 26)
    R["MACD 히스토그램 부호"] = sign(macd - ema(macd, 9))
    mid, sd = c.rolling(20).mean(), c.rolling(20).std()
    R["볼린저 돌파"] = state(c > mid + 2 * sd, c < mid, c < mid - 2 * sd, c > mid)
    R["볼린저 회귀"] = state(c < mid - 2 * sd, c > mid, c > mid + 2 * sd, c < mid)
    a = adx(df); base = sign(c - c.rolling(50).mean())
    R["이평50 + ADX>20 추세일 때만"] = base * (a > 20)
    R["이평50 + ADX>25"] = base * (a > 25)
    R["이평50 + 거래대금 평균 이상"] = base.where(v > v.rolling(20).mean(), np.nan).ffill().fillna(0)
    for n in (20, 90):
        R[f"{n}봉 모멘텀 부호"] = sign(c - c.shift(n))
    obv = (sign(c.diff()) * v).cumsum()
    R["OBV 20봉 기울기"] = sign(obv - obv.shift(20))
    rng = (h - l).shift()
    R["변동성 돌파(k=0.5) 당일"] = ((h >= o + 0.5 * rng) * 1 - (l <= o - 0.5 * rng) * 1)
    if fund_daily is not None:
        f = fund_daily.reindex(df.index).ffill()
        f7 = f.rolling(7).mean(); z = (f7 - f7.rolling(365, min_periods=90).mean()) / f7.rolling(365, min_periods=90).std()
        R["펀딩 과열 역방향(z>1 숏/z<-1 롱)"] = state(z < -1, z > 0, z > 1, z < 0)
        R["펀딩 순방향(z>1 롱/z<-1 숏)"] = state(z > 1, z < 0, z < -1, z > 0)
        hot = z > 1.28
        R["이평50 + 펀딩 과열 시 롱 절반"] = base.where(~((base > 0) & hot), 0.5)
        R["이평50 + 펀딩 과열 시 롱 금지"] = base.where(~((base > 0) & hot), 0)
    # ---- 추가 13개 (2026-11-14 저녁) ----
    tenkan = (h.rolling(9).max() + l.rolling(9).min()) / 2
    kijun = (h.rolling(26).max() + l.rolling(26).min()) / 2
    spa = ((tenkan + kijun) / 2).shift(26); spb = ((h.rolling(52).max() + l.rolling(52).min()) / 2).shift(26)
    top, bot = pd.concat([spa, spb], axis=1).max(axis=1), pd.concat([spa, spb], axis=1).min(axis=1)
    R["일목균형표(구름 위+전환>기준 롱)"] = ((c > top) & (tenkan > kijun)) * 1 - ((c < bot) & (tenkan < kijun)) * 1
    R["슈퍼트렌드(10,3)"] = trail_dir(df, 10, 3.0)
    R["ATR 추적(22,3)"] = trail_dir(df, 22, 3.0)
    R["파라볼릭 SAR"] = psar_dir(df)
    e20k, a10 = ema(c, 20), atr(df, 10)
    R["켈트너 채널 돌파"] = state(c > e20k + 2 * a10, c < e20k, c < e20k - 2 * a10, c > e20k)
    k = 100 * (c - l.rolling(14).min()) / (h.rolling(14).max() - l.rolling(14).min()); k = k.rolling(3).mean()
    R["스토캐스틱 추세(80돌파 롱/20이탈 숏)"] = state((k.shift() < 80) & (k >= 80), k < 50, (k.shift() > 20) & (k <= 20), k > 50)
    R["스토캐스틱 역추세(20아래 롱/80위 숏)"] = state(k < 20, k > 50, k > 80, k < 50)
    tp = (h + l + c) / 3
    cci = (tp - tp.rolling(20).mean()) / (0.015 * tp.rolling(20).apply(lambda x: np.mean(np.abs(x - x.mean())), raw=True))
    R["CCI(20) 100돌파 추세"] = state((cci.shift() < 100) & (cci >= 100), cci < 0, (cci.shift() > -100) & (cci <= -100), cci > 0)
    wr = -100 * (h.rolling(14).max() - c) / (h.rolling(14).max() - l.rolling(14).min())
    R["윌리엄스 %R 역추세"] = state(wr < -80, wr > -50, wr > -20, wr < -50)
    pos_f = (tp * v).where(tp > tp.shift(), 0.0).rolling(14).sum(); neg_f = (tp * v).where(tp < tp.shift(), 0.0).rolling(14).sum()
    mfi = 100 - 100 / (1 + pos_f / neg_f.replace(0, np.nan))
    R["MFI(14) 50 위/아래"] = sign(mfi - 50)
    hh = h.rolling(26).apply(lambda x: 100 * (25 - (len(x) - 1 - np.argmax(x))) / 25, raw=True)
    ll = l.rolling(26).apply(lambda x: 100 * (25 - (len(x) - 1 - np.argmin(x))) / 25, raw=True)
    R["아룬(25) 70/30"] = ((hh > 70) & (ll < 30)) * 1 - ((ll > 70) & (hh < 30)) * 1
    hm = hma(c, 55)
    R["헐 이평55 위+상승"] = ((c > hm) & (hm > hm.shift())) * 1 - ((c < hm) & (hm < hm.shift())) * 1
    vw = (c * v).rolling(50).sum() / v.rolling(50).sum()
    R["거래량가중 이평50 위/아래"] = sign(c - vw)
    e8, e21, e55 = ema(c, 8), ema(c, 21), ema(c, 55)
    R["삼중 EMA(8/21/55) 정배열"] = ((e8 > e21) & (e21 > e55)) * 1 - ((e8 < e21) & (e21 < e55)) * 1
    # ---- 외부 데이터 (2026-11-14 밤, research/leverage_2026-11/extra_sweep.py 에서 합격한 것만) ----
    if extra is not None:
        e = extra.reindex(df.index).ffill()
        if "stable" in e:
            R["스테이블코인 시총 30일 증가 부호"] = sign(e["stable"] / e["stable"].shift(30) - 1)
        if "fng" in e:
            R["이평50 + 탐욕 80초과면 롱 절반"] = base.where(~((base > 0) & (e["fng"] > 80)), 0.5)
        if "liq24" in e:                         # 2026-11-15 news_sweep.py 합격. 청산 집계 없는 날(2021-09 전)은 이평50
            lq = extra["liq24"].reindex(df.index).ffill(limit=1)   # 측정과 같게 빈 날은 하루만 채운다(2023 전엔 집계가 드문드문)
            z = (lq - lq.rolling(365, min_periods=90).mean()) / lq.rolling(365, min_periods=90).std()
            spike = pd.Series(np.where(z > 2, -np.sign(c / c.shift() - 1), np.nan), index=df.index).ffill(limit=2)
            R["청산 급증(z>2) 뒤 3일 역방향, 아니면 이평50"] = spike.where(spike.notna(), base)
        # ---- 2026-11-15 oi_sweep.py 합격 3개. 코인별 입력(hl_signals.metrics_daily·dvol_daily). 측정과 같게 빈 날은 채우지 않는다(DVOL 만 이틀)
        z90 = lambda x: (x - x.rolling(90).mean()) / x.rolling(90).std()
        if "oi" in e:
            R["이평50 + 미결제약정 과열(z>2) 시 절반"] = base.where(~(z90(extra["oi"].reindex(df.index)) > 2), base * 0.5)
        if "acct_ls" in e:
            z = z90(extra["acct_ls"].reindex(df.index))
            R["전체 계정 롱숏 z 역방향(>1 숏/<-1 롱)"] = (z < -1) * 1.0 - (z > 1) * 1.0
        if "dvol" in e:
            R["이평50 + 옵션 변동성 급등(z>2) 시 절반"] = base.where(~(z90(extra["dvol"].reindex(df.index).ffill(limit=2)) > 2), base * 0.5)
    return {k: s.fillna(0) for k, s in R.items()}


# 봇이 실제로 투표에 쓰는 규칙 (state/hl_signals.json 이 없을 때의 기본값 = 2026-11-14 전수 측정 통과 8개)
DEFAULT_CHOSEN: dict[str, list[str]] = {
    "1d": ["이동평균50 위/아래", "이평50 + ADX>25", "RSI 추세(70돌파 롱/30이탈 숏)", "이동평균100 위/아래"],
    "4h": ["이동평균200 위/아래", "이평 정배열/역배열", "RSI 추세(70돌파 롱/30이탈 숏)", "90봉 모멘텀 부호"],
}
# 선택 대상에서 빼는 것: 장중 체결 규칙(봇은 봉 마감에만 판단)
NOT_SELECTABLE = {"변동성 돌파(k=0.5) 당일"}
# 봇이 따로 받아야 하는 입력 (bot/hl_signals.daily_inputs). 이 규칙이 투표단에 있을 때만 받는다.
NEEDS_FUNDING = {"펀딩 과열 역방향(z>1 숏/z<-1 롱)", "펀딩 순방향(z>1 롱/z<-1 숏)",
                 "이평50 + 펀딩 과열 시 롱 절반", "이평50 + 펀딩 과열 시 롱 금지"}
NEEDS_EXTRA = {"스테이블코인 시총 30일 증가 부호", "이평50 + 탐욕 80초과면 롱 절반", "청산 급증(z>2) 뒤 3일 역방향, 아니면 이평50"}
# 코인별 일봉 입력이 필요한 규칙 → 입력 열. 이 입력은 늦게 시작하므로(바이낸스 지표 BTC 2020-09·ETH 2021-12, DVOL 2021-03)
# 측정기(sweep.run_sweep)는 입력 시작 + 180일부터 채점한다.
NEEDS_COIN = {"이평50 + 미결제약정 과열(z>2) 시 절반": "oi", "전체 계정 롱숏 z 역방향(>1 숏/<-1 롱)": "acct_ls",
              "이평50 + 옵션 변동성 급등(z>2) 시 절반": "dvol"}

# 분석가(서브에이전트) 묶음. 메인은 분석가 의견(묶음 평균)을 같은 비중으로 평균한다 (2026-11-15 desk.py: 전체 평균·1등 고르기보다 나음)
_DERIV = NEEDS_FUNDING | {"청산 급증(z>2) 뒤 3일 역방향, 아니면 이평50", "이평50 + 미결제약정 과열(z>2) 시 절반",
                          "전체 계정 롱숏 z 역방향(>1 숏/<-1 롱)"}
_MACRO = {"스테이블코인 시총 30일 증가 부호", "이평50 + 탐욕 80초과면 롱 절반", "이평50 + 옵션 변동성 급등(z>2) 시 절반"}


def analyst(name: str) -> str:
    """규칙 이름(앞의 '일봉 '·'4h ' 는 떼고 본다) → 분석가."""
    n = name.split(" ", 1)[1] if name.startswith(("일봉 ", "4h ")) else name
    return "파생" if n in _DERIV else "거시·심리" if n in _MACRO else "추세"
