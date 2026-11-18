# 지정가 우선 주문 측정 (2026-11-15, 규칙은 돌리기 전에 고정).
# 지금 봇 조건(5배 하한·완충 30%·이익 보호, split_entry.run 'A' 와 같은 루프)의 재조정 시점마다 바이낸스 1분봉(2023-01~)으로:
#   4h 봉 시작가 L 에 지정가(post-only) → T분 안에 가격이 L 을 '통과'하면(매수: 1분 저가 < L) L 에 체결(메이커 0.015%)
#   아니면 T분 뒤 1분봉 시가에 시장가(테이커 0.045%) — 그사이 밀린 가격이 추가 비용.
# 실효 비용(거래 명목 대비) = 수수료 + 밀린 가격. 시장가 즉시 = 0.045%.
# 합격: T=5분의 명목 가중 평균 실효 비용 < 0.045% (BTC·ETH 모두, 기간 3등분 중 2개+). T=1·15분은 참고.
import sys
import numpy as np, pandas as pd
sys.path.insert(0, "."); sys.path.insert(0, "/Users/juno/orca/coin")
from stops import expo_series, MM

BAND, TRAIL, KEEP = 0.30, 0.05, 0.25
TAKER, MAKER = 0.00045, 0.00015


def events(h4, fb4, expo, i0, lo=5, hi=50, tv=0.6):
    """[(봉 시작 시각, 거래 크기(자산 대비 배, 부호=방향))]"""
    o, hh, ll, c = h4.open.values, h4.high.values, h4.low.values, h4.close.values
    rv = (np.log(h4.open / h4.open.shift()).rolling(120).std() * np.sqrt(6 * 365)).values
    lev = np.clip(np.nan_to_num(tv / rv, nan=1.0), lo, hi)
    tgt, f = expo.shift(1).fillna(0).values * lev, fb4.values
    E, held, out = 1.0, 0.0, []
    side, entry, best, cut = 0, 0.0, 0.0, 1.0
    for i in range(i0, len(h4) - 1):
        d = int(np.sign(tgt[i]))
        if d != side:
            side, entry, best, cut = d, o[i], 0.0, 1.0
        p = tgt[i] * cut
        if held and p and np.sign(p) == np.sign(held) and abs(p - held) < BAND * abs(p):
            p = held
        if abs(p - held) > 1e-9:
            out.append((h4.index[i], p - held))
        E *= 1 - abs(p - held) * TAKER
        if p:
            adverse = (ll[i] / o[i] - 1) if p > 0 else -(hh[i] / o[i] - 1)
            if adverse <= -(1 - MM * abs(p)) / abs(p):
                break
            r = o[i + 1] / o[i] - 1
            E *= 1 + p * r - p * f[i]
            held = p * (1 + r) / (1 + p * r)
        else:
            held = 0.0
        if side and cut > KEEP:
            fav = side * (c[i] / entry - 1); best = max(best, fav)
            if (1 + best) / (1 + fav) - 1 >= TRAIL:
                cut = KEEP
    return out


def measure(m1, ev, T):
    rows = []
    for t, dp in ev:
        w = m1.loc[t:t + pd.Timedelta(minutes=T)]
        if len(w) < T + 1 or w.index[0] != t:
            continue
        L, s = w.open.iloc[0], np.sign(dp)
        through = (w.low.iloc[:T].min() < L) if s > 0 else (w.high.iloc[:T].max() > L)
        cost = MAKER if through else TAKER + s * (w.open.iloc[T] / L - 1)
        rows.append((t, abs(dp), through, cost))
    return pd.DataFrame(rows, columns=["t", "w", "fill", "cost"])


if __name__ == "__main__":
    for sym in ("BTCUSDT", "ETHUSDT"):
        h4, fb4, e = expo_series(sym)
        m1 = pd.read_pickle(f"bn_{sym}_1m.pkl")
        i0 = int(h4.index.searchsorted(m1.index[0])) + 1
        ev = events(h4, fb4, e, i0)
        yrs = (h4.index[-1] - h4.index[i0]).days / 365.25
        print(f"\n== {sym[:3]} {h4.index[i0]:%Y-%m}~ 재조정 {len(ev)}번 (연 {len(ev) / yrs:.0f}번), 회전 연 {sum(abs(d) for _, d in ev) / yrs:.0f}배")
        for T in (1, 5, 15):
            r = measure(m1, ev, T)
            avg = lambda x: (x.cost * x.w).sum() / x.w.sum()
            th = [avg(r.iloc[k * len(r) // 3:(k + 1) * len(r) // 3]) for k in range(3)]
            ok = avg(r) < TAKER and sum(x < TAKER for x in th) >= 2
            miss = r[~r.fill]
            print(f"  T={T:2d}분: 체결률 {r.fill.mean() * 100:.0f}% · 실효 비용 {avg(r) * 100:.4f}% (시장가 0.0450%) · "
                  f"못 채운 주문의 밀린 가격 평균 {(miss.cost - TAKER).mean() * 100:+.3f}% · "
                  f"연 수수료+밀림 {avg(r) * r.w.sum() / yrs * 100:.1f}% vs 시장가 {TAKER * r.w.sum() / yrs * 100:.1f}% · "
                  f"3등분 {' / '.join(f'{x * 100:.4f}%' for x in th)} → {'합격' if ok else '탈락'}", flush=True)
