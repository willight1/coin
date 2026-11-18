# 이익 보호 규칙 측정 (2026-11-15). 지금 봇 = 20표 투표 × 변동성 배수, 완충 30%.
# 판정은 4h 종가, 체결은 다음 봉 시가(봇과 같음). 정리하면 투표 방향이 바뀔 때(새 거래)까지 그 비율로 묶어 둔다.
# 채택 기준(먼저 고정): 하한 1배 전체에서 BTC·ETH 둘 다 MDD 감소, 연수익 −1%p 이내, OOS 악화 없음.
import sys
import numpy as np, pandas as pd
sys.path.insert(0, "."); sys.path.insert(0, "/Users/juno/orca/coin")
from stops import expo_series, COST, MM

BAND = 0.30


def sim3(h4, fb4, expo, lo, hi=50, tv=0.6, guard=None, start=None):
    o, hh, ll, c = h4.open.values, h4.high.values, h4.low.values, h4.close.values
    rv = (np.log(h4.open / h4.open.shift()).rolling(120).std() * np.sqrt(6 * 365)).values
    lev = np.clip(np.nan_to_num(tv / rv, nan=1.0), lo, hi)
    tgt, f = expo.shift(1).fillna(0).values * lev, fb4.values
    i0 = 0 if start is None else int(h4.index.searchsorted(pd.Timestamp(start, tz="UTC")))
    E, held, turn, eq, dead = 1.0, 0.0, 0.0, [], None
    side, entry, best, cut, fires = 0, 0.0, 0.0, 1.0, 0
    for i in range(i0, len(h4) - 1):
        d = int(np.sign(tgt[i]))
        if d != side:                                            # 새 거래(방향 바뀜·비움)
            side, entry, best, cut = d, o[i], 0.0, 1.0
        p = tgt[i] * cut
        if held and p and np.sign(p) == np.sign(held) and abs(p - held) < BAND * abs(p):
            p = held
        turn += abs(p - held); E *= 1 - abs(p - held) * COST
        if p:
            adverse = (ll[i] / o[i] - 1) if p > 0 else -(hh[i] / o[i] - 1)
            if adverse <= -(1 - MM * abs(p)) / abs(p):
                E = 0.0; dead = h4.index[i]; eq.append(E); break
            r = o[i + 1] / o[i] - 1
            E *= 1 + p * r - p * f[i]
            held = p * (1 + r) / (1 + p * r)
        else:
            held = 0.0
        eq.append(E)
        if side and guard and cut > 0:                           # 이 봉 종가로 판정 → 다음 봉부터 적용
            fav = side * (c[i] / entry - 1); best = max(best, fav)
            kind, x, keep = guard
            if kind == "stages":                                 # x = [(되돌림, 남길 비율), ...]
                dd = (1 + best) / (1 + fav) - 1
                keep = min([k for t, k in x if dd >= t], default=1.0); hit = True
            else:
                hit = ((kind == "giveback" and best >= x and fav <= best / 2) or
                       (kind == "breakeven" and best >= x and fav <= 0) or
                       (kind == "trail" and (1 + best) / (1 + fav) - 1 >= x))
            if hit and keep < cut:
                cut = keep; fires += 1
    s = pd.Series(eq, index=h4.index[i0:i0 + len(eq)])
    yrs = (s.index[-1] - s.index[0]).days / 365.25
    k = int(len(s) * 0.7); Ef = s.iloc[-1]
    return dict(cagr=(Ef ** (1 / yrs) - 1) if Ef > 0 else -1.0, mdd=float((1 - s / s.cummax()).max()),
                oos=(Ef / s.iloc[k] - 1) if Ef > 0 and s.iloc[k] > 0 else -1.0, dead=dead, fires=fires, turn=turn / yrs)


CASES = [("지금 (보호 없음)", None)] + \
    [(f"이익 {int(x*100)}% 후 절반 반납 → {'절반' if k else '전부'} 정리", ("giveback", x, k)) for x in (0.05, 0.10) for k in (0.5, 0.0)] + \
    [(f"이익 {int(x*100)}% 후 본전 → 전부 정리", ("breakeven", x, 0.0)) for x in (0.05, 0.10)] + \
    [(f"최고점 대비 {int(x*100)}% 되돌림 → 전부 정리", ("trail", x, 0.0)) for x in (0.05, 0.10, 0.15)]

if __name__ == "__main__":
    res = {}
    for sym in ("BTCUSDT", "ETHUSDT"):
        h4, fb4, e = expo_series(sym)
        for lo, start in ((1, None), (5, "2020-01-01")):
            print(f"\n== {sym} 하한 {lo}배" + (f" ({start}~)" if start else " (2017~ 전체)"))
            for name, g in CASES:
                r = sim3(h4, fb4, e, lo, guard=g, start=start); res[(sym, lo, name)] = r
                print(f"  {name:28} 연 {r['cagr']*100:+5.0f}%  MDD {r['mdd']*100:3.0f}%  OOS {r['oos']*100:+6.0f}%  발동 {r['fires']:3d}회  회전 연 {r['turn']:4.0f}배"
                      + (f"  ← {r['dead']:%Y-%m-%d} 전멸" if r['dead'] is not None else ""))
    print("\n== 채택 기준 (하한 1배, BTC·ETH 둘 다: MDD↓, 연수익 −1%p 이내, OOS 악화 없음)")
    for name, g in CASES[1:]:
        ok = all(res[(s, 1, name)]["mdd"] < res[(s, 1, CASES[0][0])]["mdd"]
                 and res[(s, 1, name)]["cagr"] >= res[(s, 1, CASES[0][0])]["cagr"] - 0.01
                 and res[(s, 1, name)]["oos"] >= res[(s, 1, CASES[0][0])]["oos"] for s in ("BTCUSDT", "ETHUSDT"))
        print(f"  {'합격' if ok else '탈락'}  {name}")
