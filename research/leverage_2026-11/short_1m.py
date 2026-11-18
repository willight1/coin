# 1분봉 전수 측정 (2026-11-15, short_tf.py 와 같은 규칙·같은 합격 기준, 편도 0.05%, 실제 펀딩). 기간 2023-01~ (코인당 약 200만 봉)
import os, sys, time
from concurrent.futures import ThreadPoolExecutor
import pandas as pd
sys.path.insert(0, "."); sys.path.insert(0, "/Users/juno/orca/coin")
from fetch_bn import klines
import sweep
from bot.hl_rules import rules

D = os.path.dirname(os.path.abspath(__file__))
SYMS = ("BTCUSDT", "ETHUSDT")
START = int(pd.Timestamp("2023-01-01", tz="UTC").timestamp() * 1000)


def get(s):
    p = f"{D}/bn_{s}_1m.pkl"
    if not os.path.exists(p):
        t = time.time(); df = klines(s, "1m", START); df.to_pickle(p); print(s, "1분봉", len(df), f"{time.time()-t:.0f}초", flush=True)


with ThreadPoolExecutor(2) as ex:
    list(ex.map(get, SYMS))
res = {}
for s in SYMS:
    df = pd.read_pickle(f"{D}/bn_{s}_1m.pkl")
    f = pd.read_pickle(f"{D}/bn_{s}_f.pkl")
    fb = f.resample("1min").sum().reindex(df.index).fillna(0)
    t = time.time(); R = rules(df); print(s, len(df), "봉, 규칙 계산", f"{time.time()-t:.0f}초", flush=True)
    for name, sig in R.items():
        res.setdefault(name, {})[s] = sweep.bt(df, sig, fb)
    print(s, "백테스트 끝", f"{time.time()-t:.0f}초", flush=True)
print(sweep.fmt_table({"1m": res}))
