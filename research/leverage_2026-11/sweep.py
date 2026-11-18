# 지표 규칙 일괄 측정. 규칙은 bot/hl_rules.py (봇과 같은 코드). 신호는 봉 마감, 체결은 다음 봉 시가.
# 편도 0.05%, 바이낸스 실제 펀딩(2019-09~, 그 전 0). 1배.
# 합격(먼저 고정): OOS(뒤 30%) > 0, PF > 1.2, 3등분 중 2개 이상 +, 거래 30회 이상 — BTC·ETH 둘 다
import os, sys
import numpy as np, pandas as pd
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from bot.hl_rules import NEEDS_COIN, rules  # noqa: E402

D = os.getenv("SWEEP_DATA", os.path.dirname(os.path.abspath(__file__)))
COST = 0.0005


def bt(df, sig, fund):
    o = df.open; r = (o.shift(-1) / o - 1)
    pos = sig.shift(1).fillna(0)
    f = fund.reindex(df.index).fillna(0) if fund is not None else 0
    pnl = pos * r - (pos - pos.shift(1).fillna(0)).abs() * COST - pos * f
    pnl = pnl.iloc[:-1]; eq = (1 + pnl).cumprod()
    n = len(eq); k = int(n * 0.7); segs = np.array_split(np.arange(n), 3)
    runid = ((pos != pos.shift()) | (pos == 0)).cumsum().iloc[:-1]
    held = pos.iloc[:-1] != 0
    tr = pnl[held].groupby(runid[held]).apply(lambda x: (1 + x).prod() - 1)
    pf = tr[tr > 0].sum() / -tr[tr < 0].sum() if (tr < 0).any() else np.inf
    yrs = (df.index[-1] - df.index[0]).days / 365.25
    seg = [eq.iloc[s[-1]] / eq.iloc[s[0]] - 1 for s in segs]
    oos = eq.iloc[-1] / eq.iloc[k] - 1
    return dict(cagr=eq.iloc[-1] ** (1 / yrs) - 1, mdd=(1 - eq / eq.cummax()).max(), oos=oos,
                pf=pf, n=len(tr), win=(tr > 0).mean() if len(tr) else 0, seg=seg,
                ok=bool(oos > 0 and pf > 1.2 and sum(s > 0 for s in seg) >= 2 and len(tr) >= 30))


def load(sym, tf):
    d = pd.read_pickle(f"{D}/bn_{sym}_d.pkl"); f = pd.read_pickle(f"{D}/bn_{sym}_f.pkl")
    if tf == "1d":
        df = d
    else:
        h = pd.read_pickle(f"{D}/bn_{sym}_h.pkl")
        df = h if tf == "1h" else h.resample("4h").agg(dict(open="first", high="max", low="min", close="last", value="sum")).dropna()
    rule = {"1d": "D", "4h": "4h", "1h": "h"}[tf]
    fb = f.resample(rule).sum().reindex(df.index).fillna(0)     # 봉 안에 정산된 펀딩 합
    fd = f.resample("D").sum()
    fd_bar = fd.reindex(df.index.floor("D")); fd_bar.index = df.index
    return df, fb, fd_bar


def load_ext(sym=None):
    """일봉 외부 데이터(fng, stable, liq24) + sym 을 주면 코인별 입력(oi, acct_ls, dvol). fetch_bn.fetch_all 이 bn_ext.pkl·bn_{sym}_x.pkl 로 저장."""
    e = pd.read_pickle(f"{D}/bn_ext.pkl")
    p = f"{D}/bn_{sym}_x.pkl"
    return e.join(pd.read_pickle(p), how="outer") if sym and os.path.exists(p) else e


def run_sweep(tfs=("1d", "4h"), syms=("BTCUSDT", "ETHUSDT")):
    """{tf: {규칙: {sym: 지표}}}"""
    out = {}
    for tf in tfs:
        res = {}
        for sym in syms:
            df, fb, fd = load(sym, tf)
            ext = load_ext(sym) if tf == "1d" else None
            for name, sig in rules(df, fd, ext).items():
                col = NEEDS_COIN.get(name)
                if col:                                     # 늦게 시작하는 입력: 입력 시작 + 180일부터 채점 (oi_sweep.py 와 같게)
                    t0 = ext[col].first_valid_index() + pd.Timedelta(days=180)
                    res.setdefault(name, {})[sym] = bt(df[df.index >= t0], sig[sig.index >= t0], fb[fb.index >= t0])
                else:
                    res.setdefault(name, {})[sym] = bt(df, sig, fb)
        out[tf] = res
    return out


def fmt_table(sweep) -> str:
    lines = []
    for tf, res in sweep.items():
        lines.append(f"\n===== {tf}  연수익 / MDD / OOS / PF / 거래수 / 3구간")
        for name, r in res.items():
            both = all(x["ok"] for x in r.values())
            line = f"{'★' if both else ' '} {name:28}"
            for sym, x in r.items():
                seg = "/".join(f"{s*100:+.0f}" for s in x["seg"])
                line += f" | {sym[:3]} {x['cagr']*100:+5.0f}% {x['mdd']*100:3.0f}% OOS{x['oos']*100:+5.0f}% PF{x['pf']:4.2f} {x['n']:4d}회 [{seg}]{'✓' if x['ok'] else ' '}"
            lines.append(line)
    return "\n".join(lines)


if __name__ == "__main__":
    print(fmt_table(run_sweep(tuple(sys.argv[1:]) or ("1d", "4h", "1h"))))
