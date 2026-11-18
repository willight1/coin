# 투표를 더 빨리 반응하게 (2026-11-18, 사용자 요청 "투표를 강화, 차트·시장에 반응이 빠르게"). 후보·기준은 돌리기 전에 고정.
#   기준 = 지금 봇(분석가 평균, desk.py M1). 이미 탈락이라 다시 안 잼: 1분~1시간봉 규칙, 여러 시간대 다수결 정리, 15분 RSI 진입 필터, 성적 1등 고르기.
#   F1a 굴림 일봉(최신): 일봉 가격 규칙을 4시간마다 '방금 끝난 24시간' 봉으로 다시 계산 (지금은 하루 한 번 09시에만 바뀐다)
#   F1b 굴림 일봉(평균): 4시간씩 어긋난 일봉 6벌의 의견 평균
#   F2  추세 분석가를 일봉 추세 / 단기 추세(4시간 규칙) 둘로 나눠 같은 비중 (4시간 규칙 몫 11% → 25%)
#   F3  8시간·12시간봉 규칙: sweep.bt 합격 기준(BTC·ETH 모두)을 넘는 것만 추세 분석가에 추가
#   조합 = 혼자서 채택 기준을 넘은 후보를 전부 합친 것
# 평가·채택은 desk.py 와 같다: 5배 6칸 중 4칸+ 에서 기준보다 연수익 높음 · 전멸 없음 · 1배 OOS 두 코인 모두 기준 이상.
# 반응 속도: 기준의 방향 전환마다 후보가 같은 방향으로 먼저(+)·늦게(−) 바뀐 시간의 중앙값(±72시간 안), 회전 = 연 |노출 변화| 합.
import sys
import numpy as np, pandas as pd
sys.path.insert(0, "."); sys.path.insert(0, "/Users/juno/orca/coin")
from sweep import bt, D
from stops import chosen
from desk import board, sim5
from bot.hl_rules import rules, NEEDS_COIN, NEEDS_EXTRA, NEEDS_FUNDING, NOT_SELECTABLE

AGG = dict(open="first", high="max", low="min", close="last", value="sum")
PRICE = [n for n in chosen["1d"] if n not in NEEDS_FUNDING | NEEDS_EXTRA | set(NEEDS_COIN)]
STARTS = ("2020-04-01", "2022-06-01", "2024-01-01")
SYMS = ("BTCUSDT", "ETHUSDT")
H = pd.Timedelta(hours=1)


def expo(S, grp):
    return S.T.groupby(S.columns.map(grp.get)).mean().T.mean(axis=1)


def rolling_daily(h4):
    """4시간씩 어긋난 일봉 6벌의 규칙 신호를 4h 격자로. 기준(desk.board)과 같은 규약: 봉 t 의 값 = t 에 끝난 24시간 봉의 신호."""
    ph = []
    for k in range(6):
        R = rules(h4.resample("24h", offset=f"{4 * k}h").agg(AGG).dropna())
        ph.append(pd.DataFrame({n: R[n].shift(1) for n in PRICE}))
    latest = pd.concat(ph).sort_index().reindex(h4.index).ffill().fillna(0)
    return latest, sum(p.reindex(h4.index).ffill().fillna(0) for p in ph) / 6, ph[0].reindex(h4.index).ffill().fillna(0)


def mid_rules(sym, hours):
    h = pd.read_pickle(f"{D}/bn_{sym}_h.pkl"); f = pd.read_pickle(f"{D}/bn_{sym}_f.pkl")
    df = h.resample(f"{hours}h").agg(AGG).dropna()
    fb = f.resample(f"{hours}h").sum().reindex(df.index).fillna(0)
    return {n: (s, bt(df, s, fb)) for n, s in rules(df).items() if n not in NOT_SELECTABLE}


def f1(S, grp, X):
    S = S.copy()
    for n in PRICE:
        S[f"일봉 {n}"] = X[n]
    return S, grp


def f2(S, grp):
    return S, {c: "단기 추세" if g == "추세" and not c.startswith("일봉 ") else g for c, g in grp.items()}


def f3(S, grp, new):
    return S.join(new), grp | {c: "추세" for c in new}


def lead(base, cand):
    def flips(e):
        s = np.sign(e); return s[(s != s.shift()) & (s != 0)]
    fb, fc = flips(base), flips(cand); out = []
    for t, s in fb.items():
        w = fc[(fc.index >= t - 72 * H) & (fc.index <= t + 72 * H) & (fc == s)]
        if len(w):
            out.append((t - w.index[np.abs(w.index - t).argmin()]) / H)
    return (np.median(out) if out else float("nan")), len(out), len(fb)


if __name__ == "__main__":
    mid = {hrs: {sym: mid_rules(sym, hrs) for sym in SYMS} for hrs in (8, 12)}
    passed = {hrs: [n for n in mid[hrs]["BTCUSDT"] if all(mid[hrs][s][n][1]["ok"] for s in SYMS)] for hrs in mid}
    for hrs, names in passed.items():
        print(f"{hrs}시간봉 규칙 {len(mid[hrs]['BTCUSDT'])}개 중 합격(두 코인 모두) {len(names)}개")
        for n in names:
            print("   ★", n, " | ".join(f"{s[:3]} 연 {mid[hrs][s][n][1]['cagr'] * 100:+.0f}% MDD {mid[hrs][s][n][1]['mdd'] * 100:.0f}% OOS {mid[hrs][s][n][1]['oos'] * 100:+.0f}% PF {mid[hrs][s][n][1]['pf']:.2f} {mid[hrs][s][n][1]['n']}회" for s in SYMS))
    out, data = {}, {}
    for sym in SYMS:
        h4, fb4, S, grp = board(sym)
        latest, avg, ph0 = rolling_daily(h4)
        same = np.mean([(ph0[n] == S[f"일봉 {n}"]).iloc[300:].mean() for n in PRICE])
        print(f"\n== {sym[:3]} (측정기 확인: 어긋남 0 인 굴림 일봉이 기준 일봉 신호와 같은 비율 {same * 100:.1f}%)")
        new = pd.DataFrame({f"{hrs}h {n}": mid[hrs][sym][n][0].shift(freq=f"{hrs - 4}h").reindex(h4.index).ffill() for hrs in passed for n in passed[hrs]}, index=h4.index).fillna(0)
        V = {"기준 (지금 봇)": lambda S, g: (S, g), "F1a 굴림 일봉(최신)": lambda S, g: f1(S, g, latest), "F1b 굴림 일봉(평균)": lambda S, g: f1(S, g, avg),
             "F2 단기 추세 분리": f2, "F3 8·12시간봉 추가": lambda S, g: f3(S, g, new)}
        data[sym] = (h4, fb4, S, grp, V)
        for name, fn in V.items():
            e = expo(*fn(S, grp)); x = bt(h4, e, fb4)
            five = [sim5(h4, fb4, e, int(h4.index.searchsorted(pd.Timestamp(s, tz="UTC")))) for s in STARTS]
            out[(sym, name)] = (x, five, e)
            ld = lead(out[(sym, "기준 (지금 봇)")][2], e)
            print(f"  {name:16} 1배 연 {x['cagr'] * 100:+4.0f}% MDD {x['mdd'] * 100:3.0f}% OOS {x['oos'] * 100:+5.0f}% | 5배 "
                  + " / ".join(f"{c * 100:+5.0f}%·{m * 100:3.0f}%" + (" 전멸" if dd is not None else "") for c, m, dd in five)
                  + f" | 방향 전환 {ld[0]:+.0f}시간 빠름({ld[1]}/{ld[2]}번) · 회전 연 {e.diff().abs().sum() / (len(e) / 6 / 365.25):.0f}", flush=True)

    def judge(name):
        cells = wins = 0; dead = False; oos = True
        for sym in SYMS:
            x, five, _ = out[(sym, name)]; x0, five0, _ = out[(sym, "기준 (지금 봇)")]
            oos &= x["oos"] >= x0["oos"]
            for a, b in zip(five, five0):
                cells += 1; wins += a[0] > b[0]; dead |= a[2] is not None
        ok = wins >= 4 and not dead and oos
        print(f"  {name:16} 5배 {wins}/{cells}칸 우세 · 전멸 {'있음' if dead else '없음'} · 1배 OOS {'둘 다 이상' if oos else '미달'} → {'채택 후보' if ok else '기준 유지'}")
        return ok

    print("\n채택 판정:")
    names = [n for n in data["BTCUSDT"][4] if not n.startswith("기준")]
    ok = [n for n in names if judge(n)]
    if len(ok) > 1:
        cname = "조합 " + "+".join(n.split()[0] for n in ok)
        for sym in SYMS:
            h4, fb4, S, grp, V = data[sym]
            for n in sorted(ok, key=lambda n: n.startswith("F2")):       # F2(묶음 나누기)는 맨 뒤 — 새로 들어온 규칙까지 나누게
                S, grp = V[n](S, grp)
            e = expo(S, grp); x = bt(h4, e, fb4)
            out[(sym, cname)] = (x, [sim5(h4, fb4, e, int(h4.index.searchsorted(pd.Timestamp(s, tz="UTC")))) for s in STARTS], e)
            print(f"  {sym[:3]} {cname} 1배 연 {x['cagr'] * 100:+4.0f}% MDD {x['mdd'] * 100:3.0f}% OOS {x['oos'] * 100:+5.0f}% | 5배 "
                  + " / ".join(f"{c * 100:+5.0f}%·{m * 100:3.0f}%" + (" 전멸" if dd is not None else "") for c, m, dd in out[(sym, cname)][1]))
        judge(cname)
