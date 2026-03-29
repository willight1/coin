"""LLM 게이트: 규칙 전략의 매수 주문을 보조 필터링합니다."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

import pandas as pd
import requests

from .logger import get_logger

logger = get_logger(__name__)


def _env_bool(key: str, default: bool) -> bool:
    val = os.getenv(key, str(default)).strip().lower()
    return val in ("1", "true", "yes", "y")


@dataclass
class GateDecision:
    allow: bool
    size_multiplier: float = 1.0
    reason: str = ""
    confidence: float = 0.0
    source: str = "RULE"


class LLMGate:
    """
    LLM 기반 매수 게이트.
    - SELL/손절/트레일링은 절대 차단하지 않고 BUY만 보조 필터링합니다.
    """

    def __init__(self) -> None:
        self.enabled = _env_bool("LLM_GATE_ENABLED", False)
        self.api_key = os.getenv("OPENAI_API_KEY", "").strip()
        self.model = os.getenv("LLM_GATE_MODEL", os.getenv("OPENAI_MODEL", "gpt-4o-mini")).strip()
        self.timeout = int(os.getenv("LLM_GATE_TIMEOUT_SECONDS", "8"))
        self.min_conf = float(os.getenv("LLM_GATE_MIN_CONFIDENCE", "0.55"))
        self.fail_open = _env_bool("LLM_GATE_FAIL_OPEN", True)
        self.max_input_candles = int(os.getenv("LLM_GATE_MAX_INPUT_CANDLES", "120"))

    def _build_payload(
        self,
        market: str,
        strategy_name: str,
        signal_candle: str,
        current_price: float,
        signal_price: float,
        df: pd.DataFrame,
    ) -> dict:
        tail = df.tail(max(20, min(len(df), self.max_input_candles))).copy()
        recent = tail[["open", "high", "low", "close", "volume"]].to_dict(orient="records")

        return {
            "market": market,
            "strategy": strategy_name,
            "signal_candle": signal_candle,
            "current_price": round(float(current_price), 6),
            "signal_price": round(float(signal_price), 6),
            "recent_candles": recent,
            "task": (
                "BUY 신호의 실행 여부를 판단하라. "
                "과열 추격/변동성 급등/비정상 거래량은 보수적으로 차단. "
                "JSON만 반환."
            ),
            "output_schema": {
                "action": "ALLOW|BLOCK|REDUCE_SIZE",
                "size_multiplier": "0.0~1.0",
                "confidence": "0.0~1.0",
                "reason": "short string",
            },
        }

    def _default_decision(self, reason: str) -> GateDecision:
        if self.fail_open:
            return GateDecision(
                allow=True,
                size_multiplier=1.0,
                reason=f"fail-open: {reason}",
                confidence=0.0,
                source="RULE",
            )
        return GateDecision(
            allow=False,
            size_multiplier=0.0,
            reason=f"fail-closed: {reason}",
            confidence=0.0,
            source="RULE",
        )

    def evaluate_buy(
        self,
        market: str,
        strategy_name: str,
        signal_candle: str,
        current_price: float,
        signal_price: float,
        df: pd.DataFrame,
    ) -> GateDecision:
        if not self.enabled:
            return GateDecision(allow=True, source="RULE", reason="LLM_GATE_DISABLED")
        if not self.api_key:
            return self._default_decision("OPENAI_API_KEY 누락")

        prompt_data = self._build_payload(
            market=market,
            strategy_name=strategy_name,
            signal_candle=signal_candle,
            current_price=current_price,
            signal_price=signal_price,
            df=df,
        )

        try:
            response = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "temperature": 0.0,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "You are a strict trading risk gate. "
                                "Return JSON only. No markdown."
                            ),
                        },
                        {"role": "user", "content": json.dumps(prompt_data, ensure_ascii=False)},
                    ],
                },
                timeout=self.timeout,
            )
            if response.status_code != 200:
                return self._default_decision(f"HTTP {response.status_code}")

            content = (
                response.json()
                .get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
                .strip()
            )
            if not content:
                return self._default_decision("빈 응답")

            parsed = json.loads(content)
            action = str(parsed.get("action", "BLOCK")).upper()
            conf = float(parsed.get("confidence", 0.0))
            reason = str(parsed.get("reason", "")).strip() or "no_reason"
            mult = float(parsed.get("size_multiplier", 1.0))
            mult = max(0.0, min(1.0, mult))

            if conf < self.min_conf:
                return GateDecision(
                    allow=False,
                    size_multiplier=0.0,
                    reason=f"low_confidence({conf:.2f})<{self.min_conf:.2f}",
                    confidence=conf,
                    source="LLM",
                )

            if action == "ALLOW":
                return GateDecision(
                    allow=True,
                    size_multiplier=1.0,
                    reason=reason,
                    confidence=conf,
                    source="LLM",
                )
            if action == "REDUCE_SIZE":
                if mult <= 0:
                    return GateDecision(
                        allow=False,
                        size_multiplier=0.0,
                        reason=f"reduce_to_zero: {reason}",
                        confidence=conf,
                        source="LLM",
                    )
                return GateDecision(
                    allow=True,
                    size_multiplier=mult,
                    reason=reason,
                    confidence=conf,
                    source="LLM",
                )

            return GateDecision(
                allow=False,
                size_multiplier=0.0,
                reason=reason,
                confidence=conf,
                source="LLM",
            )

        except Exception as e:
            logger.warning(f"LLM 게이트 예외: {e}")
            return self._default_decision(str(e))

