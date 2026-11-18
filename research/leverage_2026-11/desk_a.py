# '규칙 카드 A'(cex_pattern.rule_a: 일봉 EMA50 방향 + 1시간 RSI 눌림·반등 진입, 손절 4 ATR, 120시간 보유)를 봇의 표로 넣었을 때 (2026-11-16).
# 지금 투표단(state/hl_signals.json, 28개)과 비교: (가) 추세 분석가의 표 하나로, (나) 네 번째 분석가 '단기 타이밍'으로. M1(분석가 평균), 5배 봇 조건 6칸.
# A 의 상태(+1·0·−1)는 1시간봉으로 만들고 4시간봉 마감 시점 값을 표로 쓴다(봇은 4시간마다만 점검).
import sys
import pandas as pd
sys.path.insert(0, "."); sys.path.insert(0, "/Users/juno/orca/coin")
import desk
from sweep import bt, load
from cex_pattern import rule_a

if __name__ == "__main__":
    STARTS = ("2020-04-01", "2022-06-01", "2024-01-01")
    for sym in ("BTCUSDT", "ETHUSDT"):
        h4, fb4, S, grp = desk.board(sym)
        h1, _, _ = load(sym, "1h"); d, _, _ = load(sym, "1d")
        a = rule_a(h1, d)[0]; a.index = a.index - pd.Timedelta(hours=3)           # 4시간봉의 마지막 1시간봉이 끝난 시점의 상태
        A = a.reindex(h4.index).fillna(0).rename("1h 규칙 카드 A")
        sets = {"지금 투표단": (S, grp), "+A (추세 분석가의 표)": (pd.concat([S, A], axis=1), grp | {A.name: "추세"}),
                "+A (네 번째 분석가)": (pd.concat([S, A], axis=1), grp | {A.name: "단기 타이밍"})}
        print(f"\n== {sym[:3]}  (5배: {' / '.join(STARTS)} 시작, 연수익·최대낙폭)")
        for label, (S2, g2) in sets.items():
            e = desk.methods(h4, fb4, S2, g2, 90)[0]["M1 분석가 평균"]; x = bt(h4, e, fb4)
            five = [desk.sim5(h4, fb4, e, int(h4.index.searchsorted(pd.Timestamp(s, tz="UTC")))) for s in STARTS]
            print(f"  {label:22} ({len(S2.columns)}표) 1배 연 {x['cagr']*100:+4.0f}% MDD {x['mdd']*100:3.0f}% OOS {x['oos']*100:+5.0f}% | 5배 "
                  + " / ".join(f"{c*100:+5.0f}%·{m*100:3.0f}%" + (" 전멸" if dd is not None else "") for c, m, dd in five), flush=True)
