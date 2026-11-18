"""하이퍼리퀴드 봇 투표단 재선정 — 거래가 끝날 때(hl_live)와 매주(hl_weekly)에 돌린다.

1) 바이낸스 최신 데이터 받기 (증분)
2) bot/hl_rules.py 규칙 전부를 일봉·4h 에서 측정 (research/leverage_2026-11/sweep.py)
3) 합격(OOS>0, PF>1.2, 3구간 중 2개+, 30회+, BTC·ETH 모두)한 규칙만 state/hl_signals.json 에 쓴다.
   합격이 4개 미만이면 이전 선택을 유지하고 경고만 보낸다. 기준은 고정 — 느슨하게 해서 통과시키지 않는다.
봇(hl_signals.vote)은 이 파일을 읽어 다음 재조정부터 쓴다.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "research", "leverage_2026-11"))
from bot import notifier                                   # noqa: E402
from bot.hl_rules import DEFAULT_CHOSEN, NOT_SELECTABLE     # noqa: E402
from fetch_bn import fetch_all                             # noqa: E402
from sweep import fmt_table, run_sweep                     # noqa: E402

SIGNALS = os.path.join(ROOT, "state", "hl_signals.json")
KST = timezone(timedelta(hours=9))
MIN_SIGNALS = 4


def load_chosen() -> dict:
    try:
        with open(SIGNALS) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"chosen": DEFAULT_CHOSEN, "updated": None, "history": []}


def select(sweep) -> dict[str, list[str]]:
    return {tf: [name for name, r in res.items() if name not in NOT_SELECTABLE and all(x["ok"] for x in r.values())]
            for tf, res in sweep.items()}


def main(reason: str = "수동") -> str:
    fetch_all()
    sweep = run_sweep(("1d", "4h"))
    table = fmt_table(sweep)
    cur = load_chosen(); old = cur["chosen"]
    new = select(sweep)
    total = sum(len(v) for v in new.values())
    now = datetime.now(KST).isoformat(timespec="seconds")
    added = {tf: sorted(set(new[tf]) - set(old.get(tf, []))) for tf in new}
    removed = {tf: sorted(set(old.get(tf, [])) - set(new[tf])) for tf in new}
    changed = any(added[tf] or removed[tf] for tf in new)
    if total < MIN_SIGNALS:
        msg = f"⚠ 재측정 결과 합격 지표가 {total}개라 투표단을 바꾸지 않았어요 (최소 {MIN_SIGNALS}개). 이전 {sum(len(v) for v in old.values())}개 유지."
        cur["history"].append({"time": now, "reason": reason, "kept": True, "passed": new})
    else:
        cur["chosen"] = new
        cur["updated"] = now
        cur["history"].append({"time": now, "reason": reason, "added": added, "removed": removed, "total": total})
        if changed:
            parts = []
            for tf in new:
                for n in added[tf]: parts.append(f"+ {tf} {n}")
                for n in removed[tf]: parts.append(f"− {tf} {n}")
            msg = f"투표단 변경 ({reason}) → {total}개\n" + "\n".join(parts)
        else:
            msg = f"재측정 결과 투표단 변화 없음 ({total}개 그대로)"
    cur["history"] = cur["history"][-50:]
    cur["last_table"] = table
    os.makedirs(os.path.dirname(SIGNALS), exist_ok=True)
    tmp = SIGNALS + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cur, f, ensure_ascii=False, indent=1)
    os.replace(tmp, SIGNALS)
    notifier.notify(f"[하이퍼리퀴드 전략 점검]\n{msg}")
    print(msg); print(table)
    return msg


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "수동")
