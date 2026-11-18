# 메인 에이전트(표 합치는 방식) 측정 (2026-11-15, 방식·기준은 돌리기 전에 고정).
# 분석가 = 지금 투표단(state/hl_signals.json) 규칙을 묶은 것: 추세(가격 규칙) / 파생(펀딩·청산) / 거시·심리(스테이블·공포탐욕). 의견 = 묶음 평균.
#   M0 지금: 전체 규칙 평균            M1 분석가 동일 가중 평균
#   M2 최근 90일 성적 1등 분석가만      M2' 최근 90일 성적 1등 규칙 하나만      M3 최근 90일 성적이 +인 분석가만, 성적 비례 가중
# 성적 = 그 의견대로 1배로 탔을 때 최근 N일 손익 합(비용·펀딩 포함, 이미 끝난 봉만 → 미래 정보 없음).
# 평가: 1배(sweep.bt) + 지금 봇 조건 5배(완충 30%·이익 보호·장중 청산, 2020-04~/2022-06~/2024-01~).
# 채택: 5배 6칸 중 4칸+ 에서 M0 보다 연수익이 높고, 전멸 없고, 1배 OOS 가 두 코인 모두 M0 이상. 아니면 M0 유지. N=90 고정(30·180 은 참고).
import sys
import numpy as np, pandas as pd
sys.path.insert(0, "."); sys.path.insert(0, "/Users/juno/orca/coin")
from sweep import load, load_ext, bt
from stops import chosen, COST, MM
from bot.hl_rules import analyst, rules, NEEDS_FUNDING

BAND, TRAIL, KEEP = 0.30, 0.05, 0.25
DERIV = NEEDS_FUNDING | {"청산 급증(z>2) 뒤 3일 역방향, 아니면 이평50"}
MACRO = {"스테이블코인 시총 30일 증가 부호", "이평50 + 탐욕 80초과면 롱 절반"}
group = lambda n: "파생" if n in DERIV else "거시·심리" if n in MACRO else "추세"


def board(sym):
    """4h 격자의 규칙별 신호(봉 마감 값) DataFrame 과 규칙→분석가."""
    d, _, fd = load(sym, "1d"); h4, fb4, _ = load(sym, "4h")
    Rd, R4 = rules(d, fd, load_ext(sym)), rules(h4)              # 코인별 입력(미결제약정·계정 롱숏·옵션 변동성)까지
    cols = {f"일봉 {n}": Rd[n].shift(1).reindex(h4.index, method="ffill") for n in chosen["1d"]}
    cols |= {f"4h {n}": R4[n] for n in chosen["4h"]}
    S = pd.DataFrame(cols).fillna(0)
    return h4, fb4, S, {c: analyst(c.split(" ", 1)[1]) for c in S}          # 봇과 같은 분류(hl_rules.analyst)


def score(h4, fb4, sig, bars):
    """sig 대로 1배로 탔을 때 최근 bars 봉 손익 합. 봉 i 의 값은 봉 i 마감까지 확정된 손익만 쓴다."""
    pos = sig.shift(1).fillna(0)
    r = h4.open.shift(-1) / h4.open - 1
    u = pos.mul(r, axis=0) - pos.diff().abs().fillna(0) * COST - pos.mul(fb4, axis=0)
    return u.shift(1).rolling(bars).sum()          # 봉 i 마감 시점엔 봉 i 의 손익(다음 시가까지)이 아직 없다


def methods(h4, fb4, S, grp, days=90):
    A = pd.DataFrame({g: S[[c for c in S if grp[c] == g]].mean(axis=1) for g in dict.fromkeys(grp.values())})
    sa, sr = score(h4, fb4, A, days * 6), score(h4, fb4, S, days * 6)
    pick = lambda X, sc: pd.Series(X.values[np.arange(len(X)), np.nan_to_num(sc.values, nan=-9).argmax(axis=1)], index=X.index)
    w = sa.clip(lower=0).fillna(0)
    m3 = (A * w).sum(axis=1) / w.sum(axis=1).replace(0, np.nan)
    return {"M0 지금(전체 평균)": S.mean(axis=1), "M1 분석가 평균": A.mean(axis=1), f"M2 1등 분석가({days}일)": pick(A, sa),
            f"M2' 1등 규칙({days}일)": pick(S, sr), f"M3 성적 가중({days}일)": m3.fillna(0)}, A


def sim5(h4, fb4, expo, i0, lo=5, hi=50, tv=0.6):
    o, hh, ll, c = h4.open.values, h4.high.values, h4.low.values, h4.close.values
    rv = (np.log(h4.open / h4.open.shift()).rolling(120).std() * np.sqrt(6 * 365)).values
    lev = np.clip(np.nan_to_num(tv / rv, nan=1.0), lo, hi)
    tgt, f = expo.shift(1).fillna(0).values * lev, fb4.values
    E, held, eq = 1.0, 0.0, []
    side, entry, best, cut = 0, 0.0, 0.0, 1.0
    for i in range(i0, len(h4) - 1):
        d = int(np.sign(tgt[i]))
        if d != side:
            side, entry, best, cut = d, o[i], 0.0, 1.0
        p = tgt[i] * cut
        if held and p and np.sign(p) == np.sign(held) and abs(p - held) < BAND * abs(p):
            p = held
        E *= 1 - abs(p - held) * COST
        if p:
            adverse = (ll[i] / o[i] - 1) if p > 0 else -(hh[i] / o[i] - 1)
            if adverse <= -(1 - MM * abs(p)) / abs(p):
                return -1.0, 1.0, h4.index[i]
            r = o[i + 1] / o[i] - 1
            E *= 1 + p * r - p * f[i]
            held = p * (1 + r) / (1 + p * r)
        else:
            held = 0.0
        eq.append(E)
        if side and cut > KEEP:
            fav = side * (c[i] / entry - 1); best = max(best, fav)
            if (1 + best) / (1 + fav) - 1 >= TRAIL:
                cut = KEEP
    s = pd.Series(eq); yrs = len(s) / 6 / 365.25
    return E ** (1 / yrs) - 1, float((1 - s / s.cummax()).max()), None


if __name__ == "__main__":
    STARTS = ("2020-04-01", "2022-06-01", "2024-01-01")
    out = {}
    for sym in ("BTCUSDT", "ETHUSDT"):
        h4, fb4, S, grp = board(sym)
        print(f"\n== {sym[:3]} 규칙 {len(S.columns)}개 — " + ", ".join(f"{g} {sum(v == g for v in grp.values())}" for g in dict.fromkeys(grp.values())))
        for days in (90, 30, 180):
            M, A = methods(h4, fb4, S, grp, days)
            if days == 90:
                for g in A:
                    x = bt(h4, A[g], fb4)
                    print(f"  분석가 {g:6} 1배: 연 {x['cagr'] * 100:+4.0f}% MDD {x['mdd'] * 100:3.0f}% OOS {x['oos'] * 100:+5.0f}%")
            for name, e in M.items():
                if days != 90 and name.startswith(("M0", "M1")):
                    continue
                x = bt(h4, e, fb4)
                five = [sim5(h4, fb4, e, int(h4.index.searchsorted(pd.Timestamp(s, tz="UTC")))) for s in STARTS]
                out[(sym, name)] = (x, five)
                print(f"  {name:20} 1배 연 {x['cagr'] * 100:+4.0f}% MDD {x['mdd'] * 100:3.0f}% OOS {x['oos'] * 100:+5.0f}% | 5배 "
                      + " / ".join(f"{c * 100:+5.0f}%·{m * 100:3.0f}%" + (" 전멸" if dd is not None else "") for c, m, dd in five), flush=True)
    print("\n채택 판정 (90일):")
    for name in [n for (s, n) in out if s == "BTCUSDT" and not n.startswith("M0") and ("90" in n or n.startswith("M1"))]:
        cells = wins = 0; dead = False; oos = True
        for sym in ("BTCUSDT", "ETHUSDT"):
            x, five = out[(sym, name)]; x0, five0 = out[(sym, "M0 지금(전체 평균)")]
            oos &= x["oos"] >= x0["oos"]
            for a, b in zip(five, five0):
                cells += 1; wins += a[0] > b[0]; dead |= a[2] is not None
        print(f"  {name:20} 5배 {wins}/{cells}칸 우세 · 전멸 {'있음' if dead else '없음'} · 1배 OOS {'둘 다 이상' if oos else '미달'} → {'채택' if wins >= 4 and not dead and oos else 'M0 유지'}")
