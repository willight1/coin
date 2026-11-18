# 코인 늘리기 측정 (2026-11-15, 경우·기준은 돌리기 전에 고정). 한 계좌(교차증거금)로 합쳐 지금 봇처럼 돌린다:
#   코인마다 목표 = (1/N) × 투표 노출 × 배수(목표변동성 60% ÷ 20일 변동성, 코인별 하한~거래소 상한) × 이익 보호, 완충 30%, 편도 0.05%, 실제 펀딩.
#   청산: 4h 봉 안에서 모든 코인이 동시에 최악(롱은 저가·숏은 고가)으로 갔다고 보고 계좌 평가 ≤ 유지증거금(1.25%×명목)이면 전멸(보수적).
# 경우: A 지금(BTC·ETH 5배) / B 4코인 모두 5배 / C1·C2·C3 BTC·ETH 5배 + BNB·DOGE 하한 1·2·3배
# 합격: 세 구간 모두 전멸 없음 + 2구간 이상에서 연수익이 A보다 높고 최대낙폭이 A보다 크지 않음.
import sys
import numpy as np, pandas as pd
sys.path.insert(0, "."); sys.path.insert(0, "/Users/juno/orca/coin")
from stops import expo_series, COST, MM

BAND, TRAIL, KEEP = 0.30, 0.05, 0.25
HI = {"BTCUSDT": 40, "ETHUSDT": 25, "BNBUSDT": 10, "DOGEUSDT": 10}
DATA = {}


def data(sym):
    if sym not in DATA:
        DATA[sym] = expo_series(sym)
    return DATA[sym]


def run(floors: dict, start, tv=0.6):
    syms = list(floors); n = len(syms)
    idx = None
    for s in syms:
        idx = data(s)[0].index if idx is None else idx.intersection(data(s)[0].index)
    idx = idx[idx >= pd.Timestamp(start, tz="UTC")]
    A = {}
    for s in syms:
        h4, fb4, e = data(s)
        rv = np.log(h4.open / h4.open.shift()).rolling(120).std() * np.sqrt(6 * 365)
        lev = np.clip((tv / rv).fillna(1.0), floors[s], HI[s])
        A[s] = dict(o=h4.open.reindex(idx).values, h=h4.high.reindex(idx).values, l=h4.low.reindex(idx).values,
                    c=h4.close.reindex(idx).values, f=fb4.reindex(idx).fillna(0).values,
                    tgt=(e.shift(1).fillna(0) * lev).reindex(idx).values / n)
    E, eq = 1.0, []
    held = dict.fromkeys(syms, 0.0)
    g = {s: [0, 0.0, 0.0, 1.0] for s in syms}                       # side, entry, best, cut
    for i in range(len(idx) - 1):
        P, worst, gain = {}, 0.0, 0.0
        for s in syms:
            a, st = A[s], g[s]
            d = int(np.sign(a["tgt"][i]))
            if d != st[0]:
                st[:] = [d, a["o"][i], 0.0, 1.0]
            p = a["tgt"][i] * st[3]
            if held[s] and p and np.sign(p) == np.sign(held[s]) and abs(p - held[s]) < BAND * abs(p):
                p = held[s]
            E *= 1 - abs(p - held[s]) * COST
            P[s] = p
            if p:
                worst += p * ((a["l"][i] / a["o"][i] - 1) if p > 0 else (a["h"][i] / a["o"][i] - 1))
                gain += p * (a["o"][i + 1] / a["o"][i] - 1) - p * a["f"][i]
        if 1 + worst <= MM * sum(abs(p) for p in P.values()):
            eq.append(0.0)
            return pd.Series(eq, index=idx[:len(eq)]), idx[i]
        E *= 1 + gain
        for s in syms:
            a, st = A[s], g[s]
            held[s] = P[s] * (a["o"][i + 1] / a["o"][i]) / (1 + gain) if P[s] else 0.0
            if st[0] and st[3] > KEEP:
                fav = st[0] * (a["c"][i] / st[1] - 1); st[2] = max(st[2], fav)
                if (1 + st[2]) / (1 + fav) - 1 >= TRAIL:
                    st[3] = KEEP
        eq.append(E)
    return pd.Series(eq, index=idx[:len(eq)]), None


def stat(s, dead):
    yrs = (s.index[-1] - s.index[0]).days / 365.25
    return (s.iloc[-1] ** (1 / yrs) - 1 if s.iloc[-1] > 0 else -1.0), float((1 - s / s.cummax()).max()), dead


if __name__ == "__main__":
    B, E_, N, Dg = "BTCUSDT", "ETHUSDT", "BNBUSDT", "DOGEUSDT"
    cases = {"A 지금 (BTC·ETH 5배)": {B: 5, E_: 5}, "B 4코인 모두 5배": {B: 5, E_: 5, N: 5, Dg: 5},
             "C1 +BNB·DOGE 하한 1배": {B: 5, E_: 5, N: 1, Dg: 1}, "C2 +BNB·DOGE 하한 2배": {B: 5, E_: 5, N: 2, Dg: 2},
             "C3 +BNB·DOGE 하한 3배": {B: 5, E_: 5, N: 3, Dg: 3}}
    starts = ("2020-10-22", "2022-06-01", "2024-01-01")
    res = {name: [stat(*run(fl, st)) for st in starts] for name, fl in cases.items()}
    base = res["A 지금 (BTC·ETH 5배)"]
    print("시작 시점: " + " / ".join(starts))
    for name, r in res.items():
        better = sum(x[0] > b[0] and x[1] <= b[1] for x, b in zip(r, base))
        ok = name.startswith("A") or (all(x[2] is None for x in r) and better >= 2)
        print(f"{name:22} " + " | ".join(f"연 {c * 100:+5.0f}% 낙폭 {m * 100:3.0f}%" + (f" 전멸 {d:%Y-%m-%d}" if d is not None else "") for c, m, d in r)
              + ("" if name.startswith("A") else f"  → {'합격' if ok else '탈락'} (A보다 나은 구간 {better}/3)"), flush=True)
