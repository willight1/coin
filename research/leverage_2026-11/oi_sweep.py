# 새 지표 규칙 측정 (2026-11-15). 규칙은 데이터 보기 전에 고정. bt·합격 기준은 sweep.py 와 같다(일봉, 1배, 롱·숏 대칭).
# 입력: 바이낸스 과거 지표(fetch_metrics.py: 미결제약정 oi, 상위 트레이더 롱숏 top_ls, 전체 계정 롱숏 acct_ls — UTC 하루의 마지막 값),
#       Deribit 옵션 내재변동성 지수 DVOL(공개 API, 일봉 종가). 일봉 d 의 신호는 그날 마감까지 나온 값만 → bt 가 다음 날 시가에 체결.
# z(x) = 90일 평균·표준편차 기준. 평가는 입력 시작 + 180일부터(워밍업). 같은 기간 이평50 을 기준으로 같이 적는다.
# 합격: 고정 기준(OOS>0·PF>1.2·3구간 중 2개+·30회+) BTC·ETH 모두 + '이평50 +' 변형은 같은 기간 이평50 보다 OOS 가 낮지 않을 것.
import os, sys
import numpy as np, pandas as pd, requests
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sweep import D, bt, load
from bot.hl_rules import sign

z90 = lambda x: (x - x.rolling(90).mean()) / x.rolling(90).std()


def dvol(cur):
    p = f"{D}/dvol_{cur}.pkl"
    if not os.path.exists(p):
        rows, end = [], int(pd.Timestamp.now(tz="UTC").timestamp() * 1000)
        while True:
            r = requests.get("https://www.deribit.com/api/v2/public/get_volatility_index_data",
                             params={"currency": cur, "start_timestamp": 1609459200000, "end_timestamp": end, "resolution": "1D"}, timeout=20).json()["result"]
            rows += r["data"]
            if not r.get("continuation"):
                break
            end = r["continuation"]
        s = pd.Series({pd.Timestamp(x[0], unit="ms", tz="UTC"): float(x[4]) for x in rows}).sort_index()
        s[~s.index.duplicated()].to_pickle(p)
    return pd.read_pickle(p)


def oi_rules(df, m):
    c = df.close; base = sign(c - c.rolling(50).mean())
    oi, top, acct = (m[k].reindex(df.index) for k in ("oi", "top_ls", "acct_ls"))
    zt, za, zo = z90(top), z90(acct), z90(oi)
    diff = np.log(top) - np.log(acct)
    return {
        "가격·미결제약정 30일 같이 증가한 방향": sign(c / c.shift(30) - 1).where(oi > oi.shift(30), 0),
        "이평50 + 미결제약정 7일 증가일 때만": base.where(oi > oi.shift(7), 0),
        "이평50 + 미결제약정 과열(z>2) 시 절반": base.where(~(zo > 2), base * 0.5),
        "상위 트레이더 롱숏 z 순방향(>1 롱/<-1 숏)": (zt > 1) * 1.0 - (zt < -1) * 1.0,
        "상위 트레이더 롱숏 z 역방향": (zt < -1) * 1.0 - (zt > 1) * 1.0,
        "전체 계정 롱숏 z 역방향(>1 숏/<-1 롱)": (za < -1) * 1.0 - (za > 1) * 1.0,
        "이평50 + 계정 쏠림(|z|>1.5) 내 편이면 절반": base.where(~(((base > 0) & (za > 1.5)) | ((base < 0) & (za < -1.5))), base * 0.5),
        "상위−전체 롱숏 차이 30일 평균이 180일 평균 위/아래": sign(diff.rolling(30).mean() - diff.rolling(180).mean()),
    }, base


def dvol_rules(df, v):
    c = df.close; base = sign(c - c.rolling(50).mean()); v = v.reindex(df.index).ffill(limit=2)
    return {"이평50 + 옵션 변동성 급등(z>2) 시 절반": base.where(~(z90(v) > 2), base * 0.5),
            "이평50 + 옵션 변동성 30일 하락일 때만": base.where(v < v.shift(30), 0)}, base


def show(title, res):
    print(f"\n===== {title}")
    b = res.pop("(기준) 이평50")
    print("  (기준) 이평50".ljust(40) + " | ".join(f"{s[:3]} 연{x['cagr']*100:+4.0f}% MDD{x['mdd']*100:3.0f}% OOS{x['oos']*100:+5.0f}% PF{x['pf']:4.2f} {x['n']:3d}회" for s, x in b.items()))
    for name, r in res.items():
        ok = all(x["ok"] for x in r.values()) and (not name.startswith("이평50") or all(r[s]["oos"] >= b[s]["oos"] for s in r))
        print(f"{'★' if ok else ' '} {name:36} | " + " | ".join(
            f"{s[:3]} 연{x['cagr']*100:+4.0f}% MDD{x['mdd']*100:3.0f}% OOS{x['oos']*100:+5.0f}% PF{x['pf']:4.2f} {x['n']:3d}회 [{'/'.join(f'{v*100:+.0f}' for v in x['seg'])}]{'✓' if x['ok'] else ' '}"
            for s, x in r.items()), flush=True)


if __name__ == "__main__":
    for title, inp, mk in (("미결제약정·롱숏 비율 (바이낸스)", lambda s: pd.read_pickle(f"{D}/bn_{s}_metrics.pkl"), oi_rules),
                           ("옵션 내재변동성 DVOL (Deribit)", lambda s: dvol(s[:3]), dvol_rules)):
        res = {}
        for sym in ("BTCUSDT", "ETHUSDT"):
            df, fb, _ = load(sym, "1d"); x = inp(sym)
            R, base = mk(df, x)
            t0 = x.index[0] + pd.Timedelta(days=180)
            cut = lambda s: s[s.index >= t0]
            for name, sig in ({"(기준) 이평50": base} | R).items():
                res.setdefault(name, {})[sym] = bt(cut(df), cut(sig.fillna(0)), cut(fb))
            print(f"{sym[:3]} {title}: 입력 {x.index[0]:%Y-%m-%d}~{x.index[-1]:%Y-%m-%d} ({len(x)}일), 평가 {t0:%Y-%m-%d}~")
        show(title, res)
