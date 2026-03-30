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
    final_signal: str = ""


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
                final_signal="",
            )
        return GateDecision(
            allow=False,
            size_multiplier=0.0,
            reason=f"fail-closed: {reason}",
            confidence=0.0,
            source="RULE",
            final_signal="HOLD",
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

    def _build_volume_profile(self, df: pd.DataFrame, bins: int = 12) -> list[dict]:
        """최근 구간 볼륨 프로파일(간이 매물대)을 계산합니다."""
        if df.empty:
            return []
        closes = df["close"].astype(float)
        vols = df["volume"].astype(float)
        low = float(closes.min())
        high = float(closes.max())
        if high <= low:
            return []

        edges = [low + (high - low) * i / bins for i in range(bins + 1)]
        bucket_vol = [0.0 for _ in range(bins)]
        for price, vol in zip(closes.tolist(), vols.tolist()):
            idx = int((price - low) / (high - low) * bins)
            idx = min(max(idx, 0), bins - 1)
            bucket_vol[idx] += vol

        total = sum(bucket_vol) or 1.0
        nodes: list[dict] = []
        for i, v in enumerate(bucket_vol):
            center = (edges[i] + edges[i + 1]) / 2
            nodes.append(
                {
                    "price_center": round(center, 3),
                    "volume_share": round(v / total, 4),
                }
            )
        nodes.sort(key=lambda x: x["volume_share"], reverse=True)
        return nodes[:3]

    def _build_indicator_snapshot(self, df: pd.DataFrame) -> dict:
        """AI 입력용 핵심 지표 스냅샷."""
        if len(df) < 35:
            return {}

        close = df["close"].astype(float)
        high = df["high"].astype(float)
        low = df["low"].astype(float)
        vol = df["volume"].astype(float)

        ema_fast = close.ewm(span=9, adjust=False).mean()
        ema_mid = close.ewm(span=21, adjust=False).mean()
        ema_slow = close.ewm(span=50, adjust=False).mean()

        delta = close.diff()
        gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
        loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
        rs = gain / loss.replace(0, float("nan"))
        rsi = 100 - (100 / (1 + rs))

        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        macd_sig = macd.ewm(span=9, adjust=False).mean()

        prev_close = close.shift(1)
        tr = pd.concat(
            [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
            axis=1,
        ).max(axis=1)
        atr = tr.rolling(14, min_periods=14).mean()
        atr_pct = (atr / close) * 100

        sma20 = close.rolling(20, min_periods=20).mean()
        std20 = close.rolling(20, min_periods=20).std()
        bb_up = sma20 + std20 * 2
        bb_dn = sma20 - std20 * 2

        vol_sma20 = vol.rolling(20, min_periods=20).mean()

        return {
            "ema9": float(ema_fast.iloc[-1]),
            "ema21": float(ema_mid.iloc[-1]),
            "ema50": float(ema_slow.iloc[-1]),
            "rsi14": float(rsi.iloc[-1]),
            "macd": float(macd.iloc[-1]),
            "macd_signal": float(macd_sig.iloc[-1]),
            "atr_pct": float(atr_pct.iloc[-1]),
            "bb_upper": float(bb_up.iloc[-1]),
            "bb_mid": float(sma20.iloc[-1]),
            "bb_lower": float(bb_dn.iloc[-1]),
            "vol_ratio20": float((vol.iloc[-1] / vol_sma20.iloc[-1]) if vol_sma20.iloc[-1] else 0.0),
        }

    def evaluate_signal(
        self,
        signal: str,
        market: str,
        strategy_name: str,
        signal_candle: str,
        current_price: float,
        signal_price: float,
        df: pd.DataFrame,
        has_position: bool,
    ) -> GateDecision:
        """
        신호 변경 시점에만 호출하는 범용 게이트.
        BUY/SELL 신호를 분석해 ALLOW/BLOCK/REDUCE_SIZE를 반환합니다.
        """
        if signal not in ("BUY", "SELL"):
            return GateDecision(allow=True, source="RULE", reason="NO_ACTION_SIGNAL", final_signal=signal)
        if not self.enabled:
            return GateDecision(allow=True, source="RULE", reason="LLM_GATE_DISABLED", final_signal=signal)
        if not self.api_key:
            return self._default_decision("OPENAI_API_KEY 누락")

        tail = df.tail(max(30, min(len(df), self.max_input_candles))).copy()
        indicator = self._build_indicator_snapshot(tail)
        profile = self._build_volume_profile(tail)
        candles = tail[["open", "high", "low", "close", "volume"]].to_dict(orient="records")

        payload = {
            "signal": signal,
            "market": market,
            "strategy": strategy_name,
            "signal_candle": signal_candle,
            "has_position": has_position,
            "current_price": round(float(current_price), 6),
            "signal_price": round(float(signal_price), 6),
            "indicator": indicator,
            "volume_profile_top3": profile,
            "recent_candles": candles,
            "rule": (
                "BUY는 과열 추격/비정상 변동이면 BLOCK 또는 REDUCE_SIZE. "
                "SELL은 포지션 보호가 최우선이며 무리한 BLOCK을 피한다."
            ),
            "output_schema": {
                "action": "ALLOW|BLOCK|REDUCE_SIZE",
                "final_signal": "BUY|SELL|HOLD",
                "size_multiplier": "0.0~1.0",
                "confidence": "0.0~1.0",
                "reason": "short string",
            },
        }

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
                                "You are a fast risk gate for crypto spot trading. "
                                "Return JSON only."
                            ),
                        },
                        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
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
            final_signal = str(parsed.get("final_signal", signal)).upper()
            if final_signal not in ("BUY", "SELL", "HOLD"):
                final_signal = signal
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
                    final_signal="HOLD",
                )

            if action == "ALLOW":
                return GateDecision(True, 1.0, reason, conf, "LLM", final_signal)
            if action == "REDUCE_SIZE":
                if signal == "SELL":
                    # 현물 전량청산 모델에서는 SELL 축소는 의미가 약하므로 허용으로 처리
                    return GateDecision(True, 1.0, f"sell_reduce_ignored:{reason}", conf, "LLM", final_signal)
                if mult <= 0:
                    return GateDecision(False, 0.0, f"reduce_to_zero:{reason}", conf, "LLM", "HOLD")
                return GateDecision(True, mult, reason, conf, "LLM", final_signal)
            return GateDecision(False, 0.0, reason, conf, "LLM", "HOLD")
        except Exception as e:
            logger.warning(f"LLM 신호 게이트 예외: {e}")
            return self._default_decision(str(e))
