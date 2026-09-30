"""
market_report.py — 텔레그램 /market 시장 상태 요약 (규칙 기반, AI 없음)
=======================================================================
과거 일봉을 사실대로 요약한다. 예측이 아니다 — 이 프로젝트의 측정상 지표로는
단기 방향을 맞히지 못했다 (CLAUDE.md '측정된 사실').
"""

import pandas as pd

from .indicators import calc_atr


def _pct(a: float, b: float) -> float:
    return (a / b - 1) * 100


def market_summary(df: pd.DataFrame, strategy=None) -> str:
    """
    Args:
        df: 일봉 OHLC (시간 오름차순). 마지막 행은 진행 중인 오늘 봉으로 본다.
        strategy: 있으면 strategy.status_text() 로 전략 기준 한 줄을 덧붙인다.
    """
    if len(df) < 31:
        return "일봉 데이터가 부족해 요약할 수 없습니다."
    price = float(df["close"].iloc[-1])      # 현재가 (진행 중 봉)
    closed = df.iloc[:-1]                    # 확정 봉만으로 지표 계산
    close = closed["close"]
    last = float(close.iloc[-1])
    lines = [f"현재가 {price:,.0f}"]

    # 추세: 확정 종가가 이동평균들 위에 몇 개 있는가
    smas = {n: close.rolling(n).mean().iloc[-1] for n in (20, 50, 200)}
    smas = {n: v for n, v in smas.items() if pd.notna(v)}
    above = [n for n, v in smas.items() if last > v]
    if len(above) == len(smas):
        trend = "상승"
    elif not above:
        trend = "하락"
    else:
        trend = "혼조"
    detail = ", ".join(f"SMA{n} {'위' if n in above else '아래'}" for n in smas)
    lines.append(f"추세: {trend} ({detail})")

    # 최근 수익률 (현재가 기준)
    rets = []
    for days in (7, 30, 90):
        if len(close) >= days:
            rets.append(f"{days}일 {_pct(price, float(close.iloc[-days])):+.1f}%")
    lines.append("수익률: " + " / ".join(rets))

    # 변동성: 최근 ATR 이 최근 90일 평균 대비 얼마인가
    atr = calc_atr(closed["high"], closed["low"], close, 14)
    base = atr.tail(90).mean()
    if pd.notna(atr.iloc[-1]) and pd.notna(base) and base > 0:
        ratio = atr.iloc[-1] / base
        label = "낮음" if ratio < 0.8 else "높음" if ratio > 1.3 else "평소 수준"
        lines.append(f"변동성: {label} (ATR14가 90일 평균의 {ratio:.1f}배)")

    # 고점 대비
    window = closed.tail(365)
    high = float(window["high"].max())
    lines.append(f"고점 대비: {len(window)}일 고점 {high:,.0f}보다 {_pct(price, high):+.1f}%")

    if strategy is not None:
        extra = strategy.status_text(closed)
        if extra:
            lines.append(f"전략 기준: {extra}")

    lines.append("※ 과거 데이터 요약이며 예측이 아닙니다")
    return "\n".join(lines)
