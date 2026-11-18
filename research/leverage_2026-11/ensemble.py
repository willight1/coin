# 살아남은 지표 묶음(투표) + 변동성 맞춤 레버리지 + 4시간마다 재조정 + 장중 강제청산 판정
# 편도 0.05%(거래 명목 기준), 바이낸스 실제 펀딩, 유지증거금 1.25%
import sys, numpy as np, pandas as pd
sys.path.insert(0, "."); sys.path.insert(0, "/Users/juno/orca/coin"); from sweep import load, load_ext; from bot.hl_rules import rules
COST, MM = 0.0005, 0.0125

def signals(sym):
    d, _, fd = load(sym, "1d"); h4, fb4, _ = load(sym, "4h")
    Rd = rules(d, fd, load_ext()); R4 = rules(h4, None)
    picks = {  # ★ 받은 것만 (1시간봉은 전부 탈락)
        "일봉 이평50": Rd["이동평균50 위/아래"], "일봉 이평50+ADX25": Rd["이평50 + ADX>25"], "일봉 RSI추세": Rd["RSI 추세(70돌파 롱/30이탈 숏)"],
        "일봉 이평100": Rd["이동평균100 위/아래"],
        "4h 이평200": R4["이동평균200 위/아래"], "4h 정배열": R4["이평 정배열/역배열"], "4h RSI추세": R4["RSI 추세(70돌파 롱/30이탈 숏)"],
        "4h 모멘텀90": R4["90봉 모멘텀 부호"],
    }
    # 일봉 신호는 그날 종가가 나온 뒤(다음 날 00:00 UTC 봉부터) 쓰도록 하루 밀어서 4h 격자에 얹는다
    S = pd.DataFrame({k: (v.shift(1).reindex(h4.index, method="ffill") if k.startswith("일봉") else v.reindex(h4.index))
                      for k, v in picks.items()}, index=h4.index).fillna(0)
    return h4, fb4, S

def run(h4, fb4, expo, L_max, target_vol):
    o, hi, lo = h4.open.values, h4.high.values, h4.low.values
    r4 = pd.Series(o).pct_change().shift(-1)                              # 시가→다음 시가
    rv = np.log(h4.open / h4.open.shift()).rolling(120).std() * np.sqrt(6 * 365)
    lev = (target_vol / rv).clip(upper=L_max).fillna(1.0).values if target_vol else np.full(len(h4), L_max)
    pos = (expo.shift(1).fillna(0).values * lev)                           # 신호 다음 봉 시가 체결
    f = fb4.values; E = 1.0; eq = np.ones(len(h4)); prev = 0.0; liq = 0
    for i in range(len(h4) - 1):
        p = pos[i]
        E *= 1 - abs(p - prev) * COST
        if p:
            adverse = (lo[i] / o[i] - 1) if p > 0 else -(hi[i] / o[i] - 1)
            if abs(p) * adverse <= -(1 - MM * abs(p)):                      # 명목 손실이 (증거금 − 유지증거금) 초과
                E = 0.0; liq += 1; eq[i:] = 0; break
            E *= 1 + p * r4.iloc[i] - p * f[i]
        eq[i] = E; prev = p
    eq[-1] = E
    s = pd.Series(eq, index=h4.index)
    if s.iloc[-1] <= 0: return dict(cagr=-1, mdd=1, oos=-1, liq=liq, lev=np.nanmean(np.abs(pos)))
    k = int(len(s) * 0.7); yrs = (h4.index[-1] - h4.index[0]).days / 365.25
    y = s.resample("YE").last(); y = y / y.shift(1).fillna(1) - 1
    return dict(cagr=s.iloc[-1] ** (1 / yrs) - 1, mdd=(1 - s / s.cummax()).max(), oos=s.iloc[-1] / s.iloc[k] - 1,
                liq=liq, lev=float(np.nanmean(np.abs(pos))), years=" ".join(f"{i.year%100}:{v*100:+.0f}" for i, v in y.items()))

if __name__ == "__main__":
    for sym in ("BTCUSDT", "ETHUSDT"):
        h4, fb4, S = signals(sym)
        vote = S.mean(axis=1)                                                 # -1~+1, 전부 동의해야 ±1
        agree = vote.where(vote.abs() >= 0.5, 0)                              # 과반 동의 때만 진입, 크기는 동의 비율
        print(f"\n===== {sym} ({h4.index[0]:%Y-%m}~)  연수익 / MDD / OOS / 강제청산 / 평균배수")
        for name, expo in (("이평50 단독", S["일봉 이평50"]), ("투표 평균(비례)", vote), ("과반 동의", agree), ("전원 동의만", vote.where(vote.abs() == 1, 0))):
            for L, tv in ((1, 0), (2, 0), (3, 0), (2, 0.6), (3, 0.6), (3, 0.8), (5, 0.6), (5, 0.8), (5, 1.0), (10, 0.8)):
                r = run(h4, fb4, expo, L, tv)
                tag = f"{L}배 고정" if not tv else f"상한 {L}배·목표변동성 {int(tv*100)}%"
                print(f"{name:10} {tag:22} 연 {r['cagr']*100:+6.0f}% MDD {r['mdd']*100:4.0f}% OOS {r['oos']*100:+6.0f}% 청산 {r['liq']} 평균 {r['lev']:.1f}배"
                      + (f" | {r.get('years','')}" if L in (1, 3) else ""))
