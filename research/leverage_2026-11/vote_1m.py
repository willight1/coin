# 분봉 투표 따라가기 (2026-11-17, 사용자 요청: "1분봉 투표가 60% 넘으면 그 방향으로 롱·숏, 포지션은 1분~하루, 20배")
# 대시보드 '시간대별 방향판'(bot/hl_tfboard.py)과 같은 계산: 지금 투표단의 가격 규칙을 그 봉에 그대로 적용해 롱·숏 표 비율을 센다.
# 돌리기 전에 정한 것:
#   봉 1m·5m·15m·1h (전부 1분봉에서 묶음, 2023-01~), 문턱 60·70·80·90%
#   방식 A = 문턱을 넘은 쪽으로, 아니면 현금 / B = 반대쪽이 문턱을 넘을 때까지 들고 감
#   비용 편도 0.015%(지정가가 다음 봉 시가에 전부 채워진다고 가정 = 낙관) · 0 · 0.045%(시장가), 실제 펀딩
#   합격 기준은 sweep.bt 그대로(OOS>0·PF>1.2·3구간 중 2+·30회+, BTC·ETH 모두) — 지정가 비용 기준
#   배수: 거래마다 계좌 전액 N배, 진입가 대비 장중(고가·저가) 청산이면 전액 손실. 시작 3곳에서 99% 잃기까지 날 수
import os, sys, time
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, "/Users/juno/orca/coin")
from bot import hl_signals as sig
from bot.hl_rules import NEEDS_COIN, NEEDS_EXTRA, NEEDS_FUNDING, rules

D = os.path.dirname(os.path.abspath(__file__))
SYMS = {"BTCUSDT": 40, "ETHUSDT": 25}                     # 하이퍼리퀴드 최대 배수 → 유지 증거금률 = 1 ÷ (최대 배수 × 2)
TFS = (("1m", "1min"), ("5m", "5min"), ("15m", "15min"), ("1h", "1h"))
THS = (0.6, 0.7, 0.8, 0.9)
MAKER, TAKER = 0.00015, 0.00045
STARTS = ("2023-01-01", "2024-07-01", "2025-11-17")
NAMES = sorted({n for v in sig.chosen()[0].values() for n in v} - NEEDS_EXTRA - NEEDS_FUNDING - set(NEEDS_COIN))


def bt(df, s, fb, cost, maxlev=None):
    """sweep.bt 와 같은 계산(다음 봉 시가 체결, 봉별 복리). maxlev 를 주면 20배·5배 파산까지 날 수도 같이."""
    o = df.open.values; pos = s.shift(1).fillna(0).values[:-1]
    prev = np.r_[0.0, pos[:-1]]; f = fb.values[:-1]
    pnl = pos * (o[1:] / o[:-1] - 1) - np.abs(pos - prev) * cost - pos * f
    eq = np.cumprod(1 + pnl); n = len(eq); k = int(n * 0.7)
    yrs = (df.index[-1] - df.index[0]).days / 365.25
    st = np.flatnonzero(pos != prev)                                   # 포지션이 바뀐 봉 = 구간 시작
    if not len(st):
        return None
    tr = np.expm1(np.add.reduceat(np.log1p(pnl), st)); side = pos[st]; held = side != 0
    t = tr[held]
    pf = t[t > 0].sum() / -t[t < 0].sum() if (t < 0).any() else np.inf
    seg = [eq[x[-1]] / eq[x[0]] - 1 for x in np.array_split(np.arange(n), 3)]
    oos = eq[-1] / eq[k] - 1
    out = dict(cagr=eq[-1] ** (1 / yrs) - 1, mdd=(1 - eq / np.maximum.accumulate(eq)).max(), oos=oos, pf=pf, n=int(held.sum()), nyr=held.sum() / yrs,
               win=(t > 0).mean() if len(t) else 0, seg=seg, hold=np.median(np.diff(np.r_[st, n])[held]) if held.any() else 0,
               ok=bool(oos > 0 and pf > 1.2 and sum(x > 0 for x in seg) >= 2 and held.sum() >= 30))
    if maxlev:                                                         # 거래 단위: 진입가 대비 장중 청산 판정
        en = np.r_[st[1:], n]; entry = o[st]; exit_ = o[en]
        lo = np.minimum.reduceat(df.low.values[:-1], st) / entry - 1; hi = np.maximum.reduceat(df.high.values[:-1], st) / entry - 1
        fsum = np.add.reduceat(f, st)
        ret = side * (exit_ / entry - 1) - 2 * cost - side * fsum
        mm = 1 / (maxlev * 2); tt = df.index[:-1][st]; te = df.index[en]
        for L in (5, 20):
            dn, up = (1 - 1 / L) / (1 - mm) - 1, (1 + 1 / L) / (1 + mm) - 1
            liq = ((side > 0) & (lo <= dn)) | ((side < 0) & (hi >= up))
            mult = np.where(liq, 0.0, np.maximum(1 + L * ret, 0.0))
            days = []
            for s0 in STARTS:
                m = held & (tt >= pd.Timestamp(s0, tz="UTC"))
                e = np.cumprod(mult[m]); dead = np.flatnonzero(e <= 0.01)
                days.append((te[m][dead[0]] - pd.Timestamp(s0, tz="UTC")).days if len(dead) else f"생존 ×{e[-1]:.2f}" if len(e) else "-")
            out[f"x{L}"] = days
    return out


def main():
    print("방향판 규칙", len(NAMES), "개:", ", ".join(NAMES), flush=True)
    rows = []
    for sym, maxlev in SYMS.items():
        m1 = pd.read_pickle(f"{D}/bn_{sym}_1m.pkl"); f = pd.read_pickle(f"{D}/bn_{sym}_f.pkl")
        for tf, rule in TFS:
            t0 = time.time()
            df = m1 if tf == "1m" else m1.resample(rule).agg(dict(open="first", high="max", low="min", close="last", value="sum")).dropna()
            fb = f.resample(rule).sum().reindex(df.index).fillna(0)
            R = rules(df); V = pd.DataFrame({n: R[n] for n in NAMES if n in R})
            L, S = (V > 0).mean(axis=1), (V < 0).mean(axis=1)
            if tf == "1m":                                             # 방향이 맞는가: 문턱을 넘은 뒤 가격이 그 방향으로 갔는지 (비용 전)
                c = df.close
                for th in (0.6, 0.9):
                    d = pd.Series(np.where(L >= th, 1.0, np.where(S >= th, -1.0, np.nan)), index=df.index)
                    line = f"[방향 적중] {sym[:3]} 1분봉 투표 {th:.0%}+ (전체 시간의 {d.notna().mean():.0%})"
                    for k, nm in ((1, "1분"), (5, "5분"), (15, "15분"), (60, "1시간"), (240, "4시간"), (1440, "하루")):
                        fw = (c.shift(-k) / c - 1) * d; fw = fw.dropna()
                        line += f" | {nm} 뒤 평균 {fw.mean() * 100:+.4f}% 적중 {(fw > 0).mean() * 100:.1f}%"
                    print(line, flush=True)
            for th in THS:
                a = pd.Series(np.where(L >= th, 1.0, np.where(S >= th, -1.0, 0.0)), index=df.index)
                for mode, s in (("A", a), ("B", a.replace(0.0, np.nan).ffill().fillna(0.0))):
                    r = bt(df, s, fb, MAKER, maxlev)
                    if r is None:
                        continue
                    r.update(sym=sym[:3], tf=tf, th=th, mode=mode, c0=bt(df, s, fb, 0.0)["cagr"], ct=bt(df, s, fb, TAKER)["cagr"])
                    rows.append(r)
            print(sym, tf, len(df), "봉", f"{time.time() - t0:.0f}초", flush=True)
    print("\n봉 문턱 방식 | 코인: 지정가 연수익 / MDD / OOS / PF / 연 거래수 / 보유 중앙값(봉) / 3구간 | 수수료0 연수익 | 시장가 연수익 | 5배·20배 99% 손실까지 날(시작 2023-01 / 2024-07 / 2025-11)")
    keys = sorted({(r["tf"], r["th"], r["mode"]) for r in rows}, key=lambda k: (["1m", "5m", "15m", "1h"].index(k[0]), k[2], k[1]))
    npass = 0
    for k in keys:
        rs = [r for r in rows if (r["tf"], r["th"], r["mode"]) == k]
        both = len(rs) == 2 and all(r["ok"] for r in rs); npass += both
        for r in rs:
            seg = "/".join(f"{x * 100:+.0f}" for x in r["seg"])
            print(f"{'★' if both else ' '} {k[0]:>3} {k[1]:.0%} {k[2]} | {r['sym']}: {r['cagr'] * 100:+7.1f}% / {r['mdd'] * 100:3.0f}% / OOS {r['oos'] * 100:+6.1f}% / PF {r['pf']:.2f} / {r['nyr']:7.0f}회 / {r['hold']:.0f}봉 / [{seg}]{'✓' if r['ok'] else ' '}"
                  f" | {r['c0'] * 100:+7.1f}% | {r['ct'] * 100:+7.1f}% | 5배 {r['x5']} · 20배 {r['x20']}")
    print(f"\n합격(두 코인 모두): {npass} / {len(keys)}")


if __name__ == "__main__":
    main()
