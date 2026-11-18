# 15분·30분봉 전수 측정 (같은 규칙 33개, 같은 합격 기준, 편도 0.05%, 실제 펀딩)
import os, sys, time
import pandas as pd
sys.path.insert(0, "."); sys.path.insert(0, "/Users/juno/orca/coin")
from fetch_bn import klines
import sweep
from bot.hl_rules import rules

D = os.path.dirname(os.path.abspath(__file__))
for s in ("BTCUSDT", "ETHUSDT"):
    p = f"{D}/bn_{s}_15m.pkl"
    if not os.path.exists(p):
        t = time.time(); df = klines(s, "15m", 0); df.to_pickle(p); print(s, "15분봉", len(df), f"{time.time()-t:.0f}초", flush=True)
res_all = {}
for tf, rule in (("30m", "30min"), ("15m", "15min")):
    res = {}
    for s in ("BTCUSDT", "ETHUSDT"):
        m15 = pd.read_pickle(f"{D}/bn_{s}_15m.pkl")
        df = m15 if tf == "15m" else m15.resample(rule).agg(dict(open="first", high="max", low="min", close="last", value="sum")).dropna()
        f = pd.read_pickle(f"{D}/bn_{s}_f.pkl")
        fb = f.resample(rule).sum().reindex(df.index).fillna(0)
        t = time.time(); R = rules(df); print(tf, s, len(df), "봉, 규칙 계산", f"{time.time()-t:.0f}초", flush=True)
        for name, sig in R.items():
            res.setdefault(name, {})[s] = sweep.bt(df, sig, fb)
    res_all[tf] = res
print(sweep.fmt_table(res_all))
