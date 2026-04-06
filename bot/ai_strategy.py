"""
ai_strategy.py — AI 자율 매매 전략
=================================
규칙 기반 지표 조건문 없이, 최근 OHLCV와 지표 요약을 LLM에 전달해
BUY / SELL / HOLD를 직접 생성합니다.
"""

from __future__ import annotations

import json
import math
import os

import pandas as pd
import requests

from .logger import get_logger
from .strategies import BaseStrategy, BUY, SELL, HOLD

logger = get_logger(__name__)


class AIAutonomousStrategy(BaseStrategy):
    """LLM이 직접 매매 신호를 생성하는 전략."""

    ai_driven = True

    def __init__(self) -> None:
        self.api_key = os.getenv("OPENAI_API_KEY", "").strip()
        self.model = os.getenv("AI_TRADER_MODEL", os.getenv("OPENAI_MODEL", "gpt-4o-mini")).strip()
        self.timeout = int(os.getenv("AI_TRADER_TIMEOUT_SECONDS", "10"))
        self.max_input_candles = int(os.getenv("AI_TRADER_MAX_INPUT_CANDLES", "160"))
        self.min_confidence = float(os.getenv("AI_TRADER_MIN_CONFIDENCE", "0.55"))
        self.fail_signal = os.getenv("AI_TRADER_FAIL_SIGNAL", "HOLD").strip().upper()
        self.enabled = os.getenv("AI_TRADER_ENABLED", "true").strip().lower() in ("1", "true", "yes", "y")

        self._last_candle = ""
        self._last_signal = HOLD
        self._last_meta = {"confidence": 0.0, "reason": "init", "size_multiplier": 1.0}

    @property
    def name(self) -> str:
        return "AI 자율 매매"

    @property
    def description(self) -> str:
        return "최근 캔들/지표를 AI가 해석해 BUY/SELL/HOLD 직접 판단"

    def get_last_ai_meta(self) -> dict:
        return dict(self._last_meta)

    def _safe_fail(self, reason: str) -> str:
        signal = self.fail_signal if self.fail_signal in (BUY, SELL, HOLD) else HOLD
        self._last_meta = {"confidence": 0.0, "reason": f"fail:{reason}", "size_multiplier": 0.0}
        return signal

    def _build_indicator_snapshot(self, df: pd.DataFrame) -> dict:
        close = df["close"].astype(float)
        high = df["high"].astype(float)
        low = df["low"].astype(float)
        vol = df["volume"].astype(float)

        ema9 = close.ewm(span=9, adjust=False).mean()
        ema21 = close.ewm(span=21, adjust=False).mean()
        ema50 = close.ewm(span=50, adjust=False).mean()

        delta = close.diff()
        gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
        loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
        rs = gain / loss.replace(0, float("nan"))
        rsi = 100 - (100 / (1 + rs))

        tr = pd.concat(
            [
                high - low,
                (high - close.shift(1)).abs(),
                (low - close.shift(1)).abs(),
            ],
            axis=1,
        ).max(axis=1)
        atr = tr.rolling(14, min_periods=14).mean()
        atr_pct = (atr / close) * 100.0

        vol_sma20 = vol.rolling(20, min_periods=20).mean()
        vol_ratio20 = vol.iloc[-1] / vol_sma20.iloc[-1] if vol_sma20.iloc[-1] else 0.0

        return {
            "ema9": round(float(ema9.iloc[-1]), 6),
            "ema21": round(float(ema21.iloc[-1]), 6),
            "ema50": round(float(ema50.iloc[-1]), 6),
            "rsi14": round(float(rsi.iloc[-1]), 4),
            "atr_pct14": round(float(atr_pct.iloc[-1]), 4),
            "vol_ratio20": round(float(vol_ratio20), 4),
        }

    def _json_safe(self, value):
        """OpenAI 요청 본문에서 NaN/Inf를 제거합니다."""
        if isinstance(value, dict):
            return {k: self._json_safe(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._json_safe(v) for v in value]
        if isinstance(value, float):
            return value if math.isfinite(value) else None
        return value

    def generate_signal(self, df: pd.DataFrame) -> str:
        if not self.enabled:
            self._last_meta = {"confidence": 0.0, "reason": "AI_TRADER_DISABLED", "size_multiplier": 1.0}
            return HOLD

        if len(df) < 40:
            return HOLD

        signal_candle = str(df["candle_date_time_kst"].iloc[-1] if "candle_date_time_kst" in df.columns else df.index[-1])
        if signal_candle == self._last_candle:
            return self._last_signal

        self._last_candle = signal_candle

        if not self.api_key:
            return self._safe_fail("OPENAI_API_KEY_MISSING")

        tail = df.tail(max(40, min(len(df), self.max_input_candles))).copy()
        payload = self._json_safe({
            "task": "다음 1개 봉 기준으로 BUY/SELL/HOLD 중 하나를 결정하라. JSON만 반환.",
            "signal_candle": signal_candle,
            "latest_close": round(float(tail["close"].iloc[-1]), 6),
            "indicator": self._build_indicator_snapshot(tail),
            "recent_candles": tail[["open", "high", "low", "close", "volume"]].to_dict(orient="records"),
            "output_schema": {
                "signal": "BUY|SELL|HOLD",
                "confidence": "0.0~1.0",
                "size_multiplier": "0.0~1.0",
                "reason": "short string",
            },
        })

        try:
            resp = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "You are an autonomous crypto spot trading policy model. "
                                "Return only JSON."
                            ),
                        },
                        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                    ],
                },
                timeout=self.timeout,
            )
            if resp.status_code != 200:
                logger.warning(
                    "AI 자율 전략 HTTP 오류: status=%s body=%s",
                    resp.status_code,
                    (resp.text or "").strip()[:300],
                )
                return self._safe_fail(f"http_{resp.status_code}")

            content = (
                resp.json()
                .get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
                .strip()
            )
            if not content:
                return self._safe_fail("empty_response")

            parsed = json.loads(content)
            signal = str(parsed.get("signal", HOLD)).upper()
            confidence = float(parsed.get("confidence", 0.0))
            reason = str(parsed.get("reason", "")).strip() or "no_reason"
            size_multiplier = float(parsed.get("size_multiplier", 1.0))
            size_multiplier = max(0.0, min(1.0, size_multiplier))

            if signal not in (BUY, SELL, HOLD):
                signal = HOLD

            if confidence < self.min_confidence:
                self._last_signal = HOLD
                self._last_meta = {
                    "confidence": confidence,
                    "reason": f"low_conf:{confidence:.2f}<{self.min_confidence:.2f}",
                    "size_multiplier": 0.0,
                }
                return HOLD

            self._last_signal = signal
            self._last_meta = {
                "confidence": confidence,
                "reason": reason,
                "size_multiplier": size_multiplier if signal == BUY else 1.0,
            }
            return signal
        except Exception as e:
            logger.warning(f"AI 자율 전략 예외: {e}")
            return self._safe_fail(str(e))
