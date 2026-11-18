"""
dt_guard.py — BTC 자율 에이전트 리스크 가드 (docs/BTC_DAYTRADE.md 7장)
=====================================================================
코드가 강제하는 한도. 에이전트(LLM)의 출력은 여기를 지나야 주문이 된다. 순수 함수만 둔다(테스트 쉽게).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .config import DtConfig
from .paths import PROJECT_ROOT

STOP_FILE = os.path.join(PROJECT_ROOT, "state", "DT_STOP")
MIN_ORDER_KRW = 6_000   # 업비트 최소 5,000원 + 수수료·호가 여유


@dataclass
class BuyContext:
    stop_file: bool          # state/DT_STOP 존재
    weekly_stop: bool        # 주간 손실 정지 (/dt_go 로 해제)
    halt_reason: str         # 그 밖의 정지 (킬, 주문 오류 3회, 잔고 불일치). 빈 문자열이면 정상
    day_pnl_krw: float       # 오늘 실현 + 미실현
    buys_today: int
    holding: bool
    cooldown_left_min: float
    confidence: float
    spread_bp: float | None
    warning: bool | None     # 유의종목
    discretionary: bool
    size_pct: float          # 예산 대비 %
    krw_available: float     # 이 에이전트가 쓸 수 있는 원화 (가용 잔고)


def order_amount(cfg: DtConfig, size_pct: float, krw_available: float) -> float:
    return min(cfg.budget_krw * size_pct / 100, krw_available * 0.995)


def check_buy(cfg: DtConfig, c: BuyContext) -> tuple[bool, str, float]:
    """7장 매수 직전 확인 1~10. (허용, 거절 사유, 주문 금액)"""
    if c.stop_file:
        return False, "1 state/DT_STOP 있음", 0
    if c.weekly_stop:
        return False, "2 주간 손실 정지 (/dt_go 로 재개)", 0
    if c.halt_reason:
        return False, f"2 정지 상태: {c.halt_reason}", 0
    if c.day_pnl_krw <= -cfg.budget_krw * cfg.daily_loss_pct / 100:
        return False, f"3 당일 손실 한도 ({c.day_pnl_krw:,.0f}원)", 0
    if cfg.max_buys_per_day > 0 and c.buys_today >= cfg.max_buys_per_day:   # 0 = 제한 없음
        return False, f"4 당일 매수 {c.buys_today}회 ≥ {cfg.max_buys_per_day}", 0
    if c.holding:
        return False, "5 이미 보유 중 (포지션 1개, 추가 매수 없음)", 0
    if c.cooldown_left_min > 0:
        return False, f"6 쿨다운 {c.cooldown_left_min:.0f}분 남음", 0
    if c.confidence < cfg.min_confidence:
        return False, f"7 확신도 {c.confidence:.2f} < {cfg.min_confidence}", 0
    if c.spread_bp is None or c.spread_bp > cfg.max_spread_bp:
        return False, f"8 스프레드 {c.spread_bp}bp > {cfg.max_spread_bp}", 0
    if c.warning:
        return False, "8 유의종목", 0
    if c.discretionary and c.size_pct > cfg.discretionary_max_pct:
        return False, f"9 재량 비중 {c.size_pct:.0f}% > {cfg.discretionary_max_pct:.0f}%", 0
    amt = order_amount(cfg, c.size_pct, c.krw_available)
    if amt < MIN_ORDER_KRW:
        return False, f"10 주문 금액 {amt:,.0f}원 < {MIN_ORDER_KRW:,}원", 0
    return True, "", amt


def weekly_loss_hit(cfg: DtConfig, pnl_7d_krw: float) -> bool:
    return pnl_7d_krw <= -cfg.budget_krw * cfg.weekly_loss_pct / 100


def kill_hit(cfg: DtConfig, equity_krw: float) -> bool:
    """시작 예산 대비 평가금액(예산 + 누적 실현 + 미실현)이 킬 기준만큼 줄었는가."""
    return equity_krw <= cfg.budget_krw * (1 - cfg.kill_drawdown_pct / 100)


def clamp_stop(cfg: DtConfig, pct) -> float:
    try:
        pct = float(pct)
    except (TypeError, ValueError):
        pct = 3.0
    return min(max(pct, cfg.stop_min_pct), cfg.stop_max_pct)
