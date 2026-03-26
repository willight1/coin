"""
review_agent.py — 실행 로그 리뷰 에이전트
========================================
트레이딩 로그를 분석하여 일일 성과를 요약하고 전략 개선 제안을 제공합니다.
"""

from __future__ import annotations

import os
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from .logger import get_logger
from .paths import PROJECT_ROOT

logger = get_logger(__name__)


@dataclass
class ReviewSummary:
    log_path: str
    signals: Counter = field(default_factory=Counter)
    buy_attempts: int = 0
    buy_failures: int = 0
    sell_attempts: int = 0
    sell_failures: int = 0
    errors: int = 0
    warnings: int = 0
    buy_blocks: Counter = field(default_factory=Counter)
    sell_blocks: Counter = field(default_factory=Counter)
    start_equity: float | None = None
    end_equity: float | None = None
    last_equity: float | None = None
    snapshot_market: str = ""
    snapshot_count: int = 0

    @property
    def buy_success(self) -> int:
        return max(self.buy_attempts - self.buy_failures, 0)

    @property
    def sell_success(self) -> int:
        return max(self.sell_attempts - self.sell_failures, 0)

    @property
    def daily_return_pct(self) -> float | None:
        end_equity = self.end_equity if self.end_equity is not None else self.last_equity
        if self.start_equity is None or end_equity is None:
            return None
        if self.start_equity <= 0:
            return None
        return (end_equity - self.start_equity) / self.start_equity * 100


class ReviewAgent:
    """로그 기반 리뷰 에이전트"""

    def __init__(self, base_dir: str = ""):
        self.base_dir = base_dir or PROJECT_ROOT

    def _resolve_log_path(self, date_str: str = "") -> str:
        if not date_str:
            date_str = datetime.now().strftime("%Y-%m-%d")
        return os.path.join(self.base_dir, "logs", f"trade_{date_str}.log")

    def analyze(self, date_str: str = "") -> ReviewSummary:
        log_path = self._resolve_log_path(date_str)
        summary = ReviewSummary(log_path=log_path)

        if not os.path.exists(log_path):
            raise FileNotFoundError(f"로그 파일이 없습니다: {log_path}")

        re_signal = re.compile(r"전략 신호:\s*(BUY|SELL|HOLD)")
        re_buy_block = re.compile(r"매수 차단:\s*(.+)$")
        re_sell_block = re.compile(r"매도 차단:\s*(.+)$")
        re_snapshot = re.compile(
            r"\[계좌\]\s*(START|END|TICK)\s+market=([A-Z0-9\-]+)\s+equity=([0-9.]+)"
        )

        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()

        for line in lines:
            if "[ERROR" in line:
                summary.errors += 1
            if "[WARNING" in line:
                summary.warnings += 1

            m_signal = re_signal.search(line)
            if m_signal:
                summary.signals[m_signal.group(1)] += 1

            if ">> 매수 실행" in line:
                summary.buy_attempts += 1
            if ">> 매수 주문 실패" in line:
                summary.buy_failures += 1

            if ">> 매도 실행" in line:
                summary.sell_attempts += 1
            if ">> 매도 주문 실패" in line:
                summary.sell_failures += 1

            m_buy_block = re_buy_block.search(line)
            if m_buy_block:
                summary.buy_blocks[m_buy_block.group(1).strip()] += 1

            m_sell_block = re_sell_block.search(line)
            if m_sell_block:
                summary.sell_blocks[m_sell_block.group(1).strip()] += 1

            m_snapshot = re_snapshot.search(line)
            if m_snapshot:
                label, market, equity = m_snapshot.groups()
                equity_val = float(equity)
                summary.snapshot_market = market
                summary.snapshot_count += 1
                if label == "START" and summary.start_equity is None:
                    summary.start_equity = equity_val
                if label == "END":
                    summary.end_equity = equity_val
                summary.last_equity = equity_val

        return summary

    def suggest(self, summary: ReviewSummary) -> list[str]:
        suggestions: list[str] = []

        buy_signals = summary.signals.get("BUY", 0)
        sell_signals = summary.signals.get("SELL", 0)
        hold_signals = summary.signals.get("HOLD", 0)

        if buy_signals == 0:
            suggestions.append(
                "BUY 신호가 0회입니다. 진입 조건(돌파 기간/거래량 배수/레짐 기준) 완화를 검토하세요."
            )
        if hold_signals > buy_signals + sell_signals:
            suggestions.append(
                "HOLD 비중이 높습니다. 횡보장 진입 규칙(RSI/BB 임계값)을 완화하면 거래 기회가 늘어납니다."
            )
        if summary.buy_blocks:
            top_reason, top_count = summary.buy_blocks.most_common(1)[0]
            suggestions.append(
                f"매수 차단 최다 사유는 '{top_reason}' ({top_count}회)입니다. 해당 리스크 파라미터를 점검하세요."
            )
        if summary.errors > 0:
            suggestions.append(
                "오류 로그가 있습니다. 인증/IP/DNS 안정성 점검 후 실거래를 지속하세요."
            )
        if summary.daily_return_pct is not None and summary.daily_return_pct < 0:
            suggestions.append(
                "일일 수익률이 음수입니다. 손절 폭 축소, 시간 손절, 추격매수 제한 강화 조합을 테스트하세요."
            )
        if not suggestions:
            suggestions.append(
                "큰 이슈는 보이지 않습니다. 동일 설정으로 며칠 누적 후 성과 안정성을 확인하세요."
            )
        return suggestions

    def build_report(self, summary: ReviewSummary) -> str:
        ret = summary.daily_return_pct
        ret_str = f"{ret:.2f}%" if ret is not None else "산출 불가 (START/END 스냅샷 필요)"

        lines = [
            "# 일일 트레이딩 리뷰",
            "",
            f"- 로그 파일: `{summary.log_path}`",
            f"- 신호 횟수: BUY={summary.signals.get('BUY', 0)}, "
            f"SELL={summary.signals.get('SELL', 0)}, HOLD={summary.signals.get('HOLD', 0)}",
            f"- 매수 시도/성공/실패: {summary.buy_attempts}/{summary.buy_success}/{summary.buy_failures}",
            f"- 매도 시도/성공/실패: {summary.sell_attempts}/{summary.sell_success}/{summary.sell_failures}",
            f"- 오류/경고: ERROR={summary.errors}, WARNING={summary.warnings}",
            f"- 하루 수익률(추정): {ret_str}",
            "- 수익률 산식: (종료 자산 - 시작 자산) / 시작 자산 × 100",
            "- 수익률 기준: 계좌 스냅샷(실제 KRW 잔고 + 코인 평가금액)으로 계산되어 수수료/슬리피지 영향이 반영됨",
        ]

        if summary.start_equity is not None:
            lines.append(f"- 시작 자산: {summary.start_equity:,.0f} KRW")
        if summary.end_equity is not None:
            lines.append(f"- 종료 자산: {summary.end_equity:,.0f} KRW")
        elif summary.last_equity is not None:
            lines.append(f"- 마지막 스냅샷 자산: {summary.last_equity:,.0f} KRW")
        if summary.snapshot_market:
            lines.append(f"- 스냅샷 기준 마켓: {summary.snapshot_market}")

        if summary.buy_blocks:
            lines.append("")
            lines.append("## 매수 차단 상위")
            for reason, count in summary.buy_blocks.most_common(5):
                lines.append(f"- {reason}: {count}회")

        if summary.sell_blocks:
            lines.append("")
            lines.append("## 매도 차단 상위")
            for reason, count in summary.sell_blocks.most_common(5):
                lines.append(f"- {reason}: {count}회")

        lines.append("")
        lines.append("## 전략 보완 제안")
        for suggestion in self.suggest(summary):
            lines.append(f"- {suggestion}")

        return "\n".join(lines) + "\n"

    def run(self, date_str: str = "") -> str:
        summary = self.analyze(date_str)
        report = self.build_report(summary)

        report_dir = os.path.join(self.base_dir, "reports")
        os.makedirs(report_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = os.path.join(report_dir, f"daily_review_{ts}.md")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(report)

        logger.info(f"리뷰 리포트 저장: {out_path}")
        print(report)
        return out_path
