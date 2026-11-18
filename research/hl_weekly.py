"""하이퍼리퀴드 봇 주간 복기. 매주 월요일 09:30 launchd(com.juno.hlweekly)가 돌린다.

1) 지난 7일 실제 매매·평가금액 요약 (logs/hl_trades.csv, logs/hl_equity.csv)
2) 투표단 재선정(research/hl_reselect.py) — 거래가 한 번도 안 끝난 주에도 최소 한 번은 다시 재도록
3) reports/hl_weekly_<날짜>.md 에 저장, 텔레그램으로 요약
"""

from __future__ import annotations

import csv
import os
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bot import notifier          # noqa: E402
import hl_reselect                # noqa: E402

KST = timezone(timedelta(hours=9))


def rows(path):
    try:
        with open(path, newline="") as f:
            return list(csv.DictReader(f))
    except FileNotFoundError:
        return []


def week_summary(now: datetime) -> str:
    since = (now - timedelta(days=7)).isoformat()
    tr = [r for r in rows(os.path.join(ROOT, "logs", "hl_trades.csv")) if r["time"] >= since]
    week = [r for r in rows(os.path.join(ROOT, "logs", "hl_equity.csv")) if r["time"] >= since]
    lines = [f"## 지난 7일 실제 매매 ({since[:10]} ~ {now:%Y-%m-%d})", ""]
    if len(week) >= 2:
        a, b = float(week[0]["equity_usd"]), float(week[-1]["equity_usd"])
        vals = [float(r["equity_usd"]) for r in week]
        peak, mdd = vals[0], 0.0
        for v in vals:
            peak = max(peak, v); mdd = max(mdd, 1 - v / peak if peak else 0)
        lines.append(f"- 평가금액 ${a:,.2f} → ${b:,.2f} ({(b / a - 1) * 100:+.2f}%), 주중 최대 낙폭 {mdd * 100:.1f}%")
    else:
        lines.append("- 평가금액 기록이 아직 2개 미만")
    lines.append(f"- 체결 {len(tr)}번: " + (", ".join(f"{t['coin']} {t['side']} ${float(t['notional_usd']):,.0f}" for t in tr[-12:]) or "없음"))
    notional = sum(float(t["notional_usd"]) for t in tr)
    lines.append(f"- 거래 명목 합 ${notional:,.0f}, 추정 수수료 ${notional * 0.00045:,.2f}")
    return "\n".join(lines)


def main() -> None:
    now = datetime.now(KST)
    summary = week_summary(now)
    try:
        verdict = hl_reselect.main("주간 복기")
        table = hl_reselect.load_chosen().get("last_table", "")
    except Exception as e:                       # 네트워크 등 — 복기는 남기고 측정만 건너뜀
        verdict, table = f"(지표 재측정 실패: {e})", ""
    report = f"# 하이퍼리퀴드 봇 주간 복기 {now:%Y-%m-%d}\n\n{summary}\n\n## 투표단 재선정\n\n{verdict}\n\n```\n{table}\n```\n"
    os.makedirs(os.path.join(ROOT, "reports"), exist_ok=True)
    path = os.path.join(ROOT, "reports", f"hl_weekly_{now:%Y-%m-%d}.md")
    with open(path, "w") as f:
        f.write(report)
    notifier.notify(f"[하이퍼리퀴드 주간 복기]\n{summary.split(chr(10), 2)[-1]}\n\n보고서: reports/{os.path.basename(path)}")
    print(path)


if __name__ == "__main__":
    main()
