# 새로 합격한 규칙을 분석가 묶음에 넣었을 때 (2026-11-15). desk.py 의 M0(전체 평균)·M1(분석가 평균)을 지금 25개 / +새 규칙으로 비교.
# 추가: 파생 ← 이평50+미결제약정 과열 절반, 전체 계정 롱숏 역방향 / 거시·심리 ← 이평50+옵션 변동성 급등 절반, 이평50+나스닥 동조 / 추세 ← 이평50+상대강도 20일, 주봉 MACD
import sys
import pandas as pd
sys.path.insert(0, "."); sys.path.insert(0, "/Users/juno/orca/coin")
import desk
from sweep import D, bt, load
from oi_sweep import oi_rules, dvol_rules, dvol
from more_rules import fred, lagged
from bot.hl_rules import rules, sign

NEW = {"이평50 + 미결제약정 과열(z>2) 시 절반": "파생", "전체 계정 롱숏 z 역방향(>1 숏/<-1 롱)": "파생",
       "이평50 + 옵션 변동성 급등(z>2) 시 절반": "거시·심리", "이평50 + 나스닥 같은 방향일 때만": "거시·심리",
       "이평50 + 상대강도 20일 같은 방향일 때만": "추세", "주봉 MACD 히스토그램 부호": "추세"}


def extra(sym, h4):
    d, _, _ = load(sym, "1d"); c = d.close; base = sign(c - c.rolling(50).mean())
    R = {}
    R |= oi_rules(d, pd.read_pickle(f"{D}/bn_{sym}_metrics.pkl"))[0]
    R |= dvol_rules(d, dvol(sym[:3]))[0]
    ndx = fred("NASDAQ100"); nq = lagged(sign(ndx - ndx.rolling(50).mean()), 1, d.index)
    R["이평50 + 나스닥 같은 방향일 때만"] = base.where(nq == base, 0)
    oc = load("ETHUSDT" if sym == "BTCUSDT" else "BTCUSDT", "1d")[0].close.reindex(d.index).ffill()
    R["이평50 + 상대강도 20일 같은 방향일 때만"] = base.where(sign((c / c.shift(20)) / (oc / oc.shift(20)) - 1) == base, 0)
    cols = {f"일봉 {n}": R[n].fillna(0).shift(1).reindex(h4.index, method="ffill") for n in NEW if n in R}
    w = d.resample("W-MON", label="left", closed="left").agg(dict(open="first", high="max", low="min", close="last", value="sum")).dropna()
    mw = rules(w)["MACD 히스토그램 부호"]; mw.index = mw.index + pd.Timedelta(days=7)      # 그 주가 끝난 시각부터 쓸 수 있다
    cols["주봉 MACD 히스토그램 부호"] = mw.reindex(h4.index, method="ffill")
    return pd.DataFrame(cols).fillna(0)


if __name__ == "__main__":
    STARTS = ("2020-04-01", "2022-06-01", "2024-01-01")
    for sym in ("BTCUSDT", "ETHUSDT"):
        h4, fb4, S, grp = desk.board(sym)
        X = extra(sym, h4); gx = {c: NEW[c.replace("일봉 ", "")] for c in X}
        sets = {"지금 25개": (S, grp), "+새 지표 3개": (pd.concat([S, X.iloc[:, :3]], axis=1), grp | {c: gx[c] for c in X.columns[:3]}),
                "+새 3개+나스닥·상대강도·주봉": (pd.concat([S, X], axis=1), grp | gx)}
        print(f"\n== {sym[:3]}  (5배: {' / '.join(STARTS)} 시작, 연수익·최대낙폭)")
        for label, (S2, g2) in sets.items():
            M, _ = desk.methods(h4, fb4, S2, g2, 90)
            for name in ("M0 지금(전체 평균)", "M1 분석가 평균"):
                e = M[name]; x = bt(h4, e, fb4)
                five = [desk.sim5(h4, fb4, e, int(h4.index.searchsorted(pd.Timestamp(s, tz="UTC")))) for s in STARTS]
                print(f"  {label:20} {name[:2]} ({len(S2.columns)}표) 1배 연 {x['cagr']*100:+4.0f}% MDD {x['mdd']*100:3.0f}% OOS {x['oos']*100:+5.0f}% | 5배 "
                      + " / ".join(f"{c*100:+5.0f}%·{m*100:3.0f}%" + (" 전멸" if dd is not None else "") for c, m, dd in five), flush=True)
