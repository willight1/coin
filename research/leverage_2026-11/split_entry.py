# 분할 진입 측정 (2026-11-15). 규칙은 돌리기 전에 고정. 시뮬레이터 조건은 fresh_start.py 와 같다
# (5배 하한·변동성 배수, 완충 30%, 이익 보호 5%→25%, 4h 종가 판정·다음 봉 시가, 편도 0.05%, 실제 펀딩, 장중 청산 판정).
# 1) 추세 중간에 켰을 때: A 바로 전액 / B 새 추세까지 대기 / C 켠 거래는 절반, 새 추세부터 전액
# 2) 모든 새 거래에 적용: 3분할(첫날 1/3, 1일 뒤 2/3, 2일 뒤 전액) / 불타기(처음 1/2, 거래 이익 +3% 넘으면 전액)
import sys
import numpy as np, pandas as pd
sys.path.insert(0, "."); sys.path.insert(0, "/Users/juno/orca/coin")
from stops import expo_series, COST, MM

BAND, TRAIL, KEEP = 0.30, 0.05, 0.25


def run(h4, fb4, expo, i0, n, mode="A", lo=5, hi=50, tv=0.6):
    o, hh, ll, c = h4.open.values, h4.high.values, h4.low.values, h4.close.values
    rv = (np.log(h4.open / h4.open.shift()).rolling(120).std() * np.sqrt(6 * 365)).values
    lev = np.clip(np.nan_to_num(tv / rv, nan=1.0), lo, hi)
    tgt, f = expo.shift(1).fillna(0).values * lev, fb4.values
    E, held, eq = 1.0, 0.0, []
    side, entry, best, cut, t0 = 0, 0.0, 0.0, 1.0, i0
    first = int(np.sign(tgt[i0])); waiting = mode == "B" and first != 0; firsttrade = first != 0
    for i in range(i0, min(i0 + n, len(h4) - 1)):
        d = int(np.sign(tgt[i]))
        if d != first:
            waiting = firsttrade = False
        if d != side:
            side, entry, best, cut, t0 = d, o[i], 0.0, 1.0, i
        m = 1.0
        if mode == "C" and firsttrade:
            m = 0.5
        elif mode == "ramp":
            k = i - t0; m = 1 / 3 if k < 6 else 2 / 3 if k < 12 else 1.0
        elif mode == "pyr":
            m = 1.0 if best >= 0.03 else 0.5
        p = 0.0 if waiting else tgt[i] * cut * m
        if held and p and np.sign(p) == np.sign(held) and abs(p - held) < BAND * abs(p):
            p = held
        E *= 1 - abs(p - held) * COST
        if p:
            adverse = (ll[i] / o[i] - 1) if p > 0 else -(hh[i] / o[i] - 1)
            if adverse <= -(1 - MM * abs(p)) / abs(p):
                eq.append(0.0); return 0.0, eq
            r = o[i + 1] / o[i] - 1
            E *= 1 + p * r - p * f[i]
            held = p * (1 + r) / (1 + p * r)
        else:
            held = 0.0
        eq.append(E)
        if side and cut > KEEP and not waiting:
            fav = side * (c[i] / entry - 1); best = max(best, fav)
            if (1 + best) / (1 + fav) - 1 >= TRAIL:
                cut = KEEP
    return E, eq


if __name__ == "__main__":
    for sym in ("BTCUSDT", "ETHUSDT"):
        h4, fb4, e = expo_series(sym)
        ts = pd.date_range("2020-01-06", "2025-06-30", freq="W-MON", tz="UTC")
        st = [h4.index.searchsorted(t) for t in ts]
        ex = np.abs(e.shift(1).fillna(0).values)
        n = 180 * 6
        R = {m: np.array([run(h4, fb4, e, i, n, m)[0] for i in st]) for m in ("A", "B", "C")}
        strong = ex[st] >= 0.9
        for name, msk in (("전체", np.ones(len(st), bool)), ("투표 강할 때(지금처럼)", strong)):
            print(f"{sym[:3]} 켠 날 180일 {name:14} n={msk.sum():3d} " + " | ".join(
                f"{m} 중앙 {np.median(R[m][msk]) * 100 - 100:+4.0f}% 평균 {R[m][msk].mean() * 100 - 100:+4.0f}%" for m in "ABC"))
        for lo_, start in ((1, "2017-09-01"), (5, "2020-04-01"), (5, "2022-06-01"), (5, "2024-01-01")):
            i0 = int(h4.index.searchsorted(pd.Timestamp(start, tz="UTC")))
            out = []
            for m in ("A", "ramp", "pyr"):
                E, eq = run(h4, fb4, e, i0, len(h4), m, lo=lo_)
                s = pd.Series(eq); yrs = len(s) / 6 / 365.25; k = int(len(s) * 0.7)
                cagr = E ** (1 / yrs) - 1 if E > 0 else -1; mdd = (1 - s / s.cummax()).max()
                oos = s.iloc[-1] / s.iloc[k] - 1 if E > 0 and s.iloc[k] > 0 else -1
                out.append(f"{ {'A': '지금', 'ramp': '3분할', 'pyr': '불타기'}[m]} 연{cagr * 100:+5.0f}% MDD{mdd * 100:3.0f}% OOS{oos * 100:+6.0f}%" + (" 전멸" if E == 0 else ""))
            print(f"{sym[:3]} 전체 규칙 하한{lo_}배 {start[:7]}~  " + " | ".join(out), flush=True)
