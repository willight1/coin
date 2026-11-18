"""
strategies.py — 규칙 기반 전략 모듈
====================================
기본 제공 전략은 "명시적 수식 / 명시적 조건문"으로 표현됩니다.
AI 자율 전략은 별도 모듈(`ai_strategy.py`)로 제공됩니다.
전략 함수가 반환하는 신호(BUY / SELL / HOLD)를 실거래에 사용합니다.

전략 목록:
1. RSI 과매도/과매수 전략
2. 이동평균 골든크로스/데드크로스 전략
3. RSI + 이동평균 필터 전략
4. 볼린저밴드 Mean Reversion 전략
5. 돌파 + 거래량 증가 전략
6. 레짐 적응형 전략 (상승=돌파, 횡보=평균회귀, 하락=진입 회피)
7. 일봉 생존형 추세·눌림 전략

사용법:
    strategy = RSIStrategy(oversold=30, overbought=70)
    signal = strategy.generate_signal(df)
    # signal: "BUY", "SELL", "HOLD"
"""

import re
from abc import ABC, abstractmethod

import pandas as pd
import numpy as np

from .indicators import (
    calc_rsi, calc_sma, calc_ema,
    calc_bollinger_bands, calc_volume_sma,
    is_high_breakout, calc_atr,
)
from .config import _float_env, DAY_CANDLE_UNIT
from .logger import get_logger

logger = get_logger(__name__)

# 신호 상수
BUY = "BUY"
SELL = "SELL"
HOLD = "HOLD"


# ============================================================
# 기본 전략 추상 클래스
# ============================================================
class BaseStrategy(ABC):
    """
    모든 전략의 기본 클래스.
    전략을 구현하려면 이 클래스를 상속하고 generate_signal()을 구현하세요.
    """

    # 이 전략에 맞는 리스크 설정 (RiskManager 인자). 백테스터/트레이더가
    # risk_manager 를 따로 받지 않으면 이 값으로 RiskManager 를 만든다.
    risk_overrides: dict = {}

    # 이 전략이 검증된 봉 단위. 설정되어 있으면 실거래 모드는 다른 BOT_CANDLE_UNIT 에서 시작하지 않는다.
    required_candle_unit: int | None = None

    @property
    @abstractmethod
    def name(self) -> str:
        """전략 이름"""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """전략 설명"""
        pass

    @abstractmethod
    def generate_signal(self, df: pd.DataFrame) -> str:
        """
        시세 데이터를 분석하여 매매 신호를 생성합니다.

        Args:
            df: OHLCV 데이터프레임
                필수 컬럼: open, high, low, close, volume
                (시간 오름차순 정렬)

        Returns:
            str: "BUY", "SELL", "HOLD" 중 하나
        """
        pass

    def status_text(self, df: pd.DataFrame) -> str:
        """텔레그램 /status 에 덧붙일 전략 상태 한 줄 (없으면 빈 문자열)."""
        return ""

    def intraday_threshold(self, df: pd.DataFrame) -> float | None:
        """
        진행 중인 봉의 종가가 이 가격을 넘으면 매수 신호, 아래면 매도 신호가 되는 경계 가격.
        장중 경고 알림에 쓴다. 경계를 계산할 수 없는 전략은 None.
        df: 확정봉만 (진행 중인 봉 제외)
        """
        return None

    def generate_signals_series(self, df: pd.DataFrame) -> pd.Series:
        """
        전체 데이터에 대해 각 시점의 신호를 생성합니다. (백테스트용)

        기본 구현은 서브클래스에서 오버라이드하여 벡터 연산으로 최적화하세요.
        아래는 안전한 기본 구현(행별 순회)입니다.

        Args:
            df: OHLCV 데이터프레임

        Returns:
            pd.Series: 각 행에 대한 신호 ("BUY", "SELL", "HOLD")
        """
        signals = pd.Series(HOLD, index=df.index)
        for i in range(len(df)):
            if i < 1:
                continue
            sub_df = df.iloc[:i + 1].copy()
            try:
                signals.iloc[i] = self.generate_signal(sub_df)
            except Exception:
                signals.iloc[i] = HOLD
        return signals


# ============================================================
# 전략 1: RSI 과매도/과매수 전략
# ============================================================
class RSIStrategy(BaseStrategy):
    """
    RSI가 특정 수준 이하(과매도)면 매수, 특정 수준 이상(과매수)면 매도.

    진입 조건 (BUY) : RSI < oversold (기본 30)
    청산 조건 (SELL): RSI > overbought (기본 70)
    그 외: HOLD
    """

    def __init__(self, period: int = 14, oversold: float = 30.0,
                 overbought: float = 70.0):
        self.period = period
        self.oversold = oversold
        self.overbought = overbought

    @property
    def name(self) -> str:
        return "RSI 과매도/과매수"

    @property
    def description(self) -> str:
        return (f"RSI({self.period}) 기반. "
                f"RSI < {self.oversold} → 매수, RSI > {self.overbought} → 매도")

    def generate_signal(self, df: pd.DataFrame) -> str:
        if len(df) < self.period + 1:
            return HOLD

        rsi = calc_rsi(df["close"], self.period)
        current_rsi = rsi.iloc[-1]

        if pd.isna(current_rsi):
            return HOLD

        if current_rsi < self.oversold:
            return BUY
        elif current_rsi > self.overbought:
            return SELL
        return HOLD

    def generate_signals_series(self, df: pd.DataFrame) -> pd.Series:
        """벡터화된 신호 생성 (백테스트 성능 최적화)"""
        signals = pd.Series(HOLD, index=df.index)
        rsi = calc_rsi(df["close"], self.period)

        signals[rsi < self.oversold] = BUY
        signals[rsi > self.overbought] = SELL

        return signals


# ============================================================
# 전략 2: 이동평균 골든크로스/데드크로스 전략
# ============================================================
class MACrossStrategy(BaseStrategy):
    """
    단기 이동평균이 장기 이동평균을 상향 돌파(골든크로스)하면 매수,
    하향 돌파(데드크로스)하면 매도.

    진입 조건 (BUY) : 단기 SMA > 장기 SMA (이전 봉에서는 <=)
    청산 조건 (SELL): 단기 SMA < 장기 SMA (이전 봉에서는 >=)
    그 외: HOLD
    """

    def __init__(self, short_period: int = 5, long_period: int = 20):
        self.short_period = short_period
        self.long_period = long_period

    @property
    def name(self) -> str:
        return "이동평균 크로스"

    @property
    def description(self) -> str:
        return (f"SMA({self.short_period}) / SMA({self.long_period}) 크로스. "
                f"골든크로스 → 매수, 데드크로스 → 매도")

    def generate_signal(self, df: pd.DataFrame) -> str:
        if len(df) < self.long_period + 2:
            return HOLD

        sma_short = calc_sma(df["close"], self.short_period)
        sma_long = calc_sma(df["close"], self.long_period)

        curr_short = sma_short.iloc[-1]
        curr_long = sma_long.iloc[-1]
        prev_short = sma_short.iloc[-2]
        prev_long = sma_long.iloc[-2]

        if pd.isna(curr_short) or pd.isna(curr_long) or \
           pd.isna(prev_short) or pd.isna(prev_long):
            return HOLD

        # 골든크로스: 단기가 장기를 상향 돌파
        if prev_short <= prev_long and curr_short > curr_long:
            return BUY
        # 데드크로스: 단기가 장기를 하향 돌파
        elif prev_short >= prev_long and curr_short < curr_long:
            return SELL

        return HOLD

    def generate_signals_series(self, df: pd.DataFrame) -> pd.Series:
        """벡터화된 신호 생성"""
        signals = pd.Series(HOLD, index=df.index)

        sma_short = calc_sma(df["close"], self.short_period)
        sma_long = calc_sma(df["close"], self.long_period)

        # 이전 봉과 현재 봉의 관계 비교
        prev_short = sma_short.shift(1)
        prev_long = sma_long.shift(1)

        # 골든크로스
        golden = (prev_short <= prev_long) & (sma_short > sma_long)
        # 데드크로스
        dead = (prev_short >= prev_long) & (sma_short < sma_long)

        signals[golden] = BUY
        signals[dead] = SELL

        return signals


# ============================================================
# 전략 3: RSI + 이동평균 필터 전략
# ============================================================
class RSIWithMAFilterStrategy(BaseStrategy):
    """
    RSI 과매도/과매수 신호에 이동평균 추세 필터를 추가합니다.
    상승 추세에서만 매수, 하락 추세에서만 매도.

    진입 조건 (BUY) : RSI < oversold AND 종가 > SMA(ma_period)
    청산 조건 (SELL): RSI > overbought AND 종가 < SMA(ma_period)
    그 외: HOLD
    """

    def __init__(self, rsi_period: int = 14, oversold: float = 30.0,
                 overbought: float = 70.0, ma_period: int = 50):
        self.rsi_period = rsi_period
        self.oversold = oversold
        self.overbought = overbought
        self.ma_period = ma_period

    @property
    def name(self) -> str:
        return "RSI + 이동평균 필터"

    @property
    def description(self) -> str:
        return (f"RSI({self.rsi_period}) + SMA({self.ma_period}) 필터. "
                f"추세 방향에서만 RSI 신호 사용")

    def generate_signal(self, df: pd.DataFrame) -> str:
        min_len = max(self.rsi_period, self.ma_period) + 1
        if len(df) < min_len:
            return HOLD

        rsi = calc_rsi(df["close"], self.rsi_period)
        sma = calc_sma(df["close"], self.ma_period)

        current_rsi = rsi.iloc[-1]
        current_sma = sma.iloc[-1]
        current_close = df["close"].iloc[-1]

        if pd.isna(current_rsi) or pd.isna(current_sma):
            return HOLD

        # 상승 추세 + RSI 과매도 → 매수
        if current_rsi < self.oversold and current_close > current_sma:
            return BUY
        # 하락 추세 + RSI 과매수 → 매도
        elif current_rsi > self.overbought and current_close < current_sma:
            return SELL

        return HOLD

    def generate_signals_series(self, df: pd.DataFrame) -> pd.Series:
        """벡터화된 신호 생성"""
        signals = pd.Series(HOLD, index=df.index)

        rsi = calc_rsi(df["close"], self.rsi_period)
        sma = calc_sma(df["close"], self.ma_period)

        # 상승 추세 + 과매도 → 매수
        buy_cond = (rsi < self.oversold) & (df["close"] > sma)
        # 하락 추세 + 과매수 → 매도
        sell_cond = (rsi > self.overbought) & (df["close"] < sma)

        signals[buy_cond] = BUY
        signals[sell_cond] = SELL

        return signals


# ============================================================
# 전략 4: 볼린저밴드 Mean Reversion 전략
# ============================================================
class BollingerMeanReversionStrategy(BaseStrategy):
    """
    볼린저 밴드를 이용한 평균 회귀 전략.
    가격이 하단선 아래로 내려가면 매수(반등 기대),
    상단선 위로 올라가면 매도(조정 기대).

    진입 조건 (BUY) : 종가 < 볼린저 하단선
    청산 조건 (SELL): 종가 > 볼린저 상단선
    손절: 하단선 아래로 추가 2% 이상 하락 시 (리스크 매니저에서 처리)
    익절: 중간선(SMA) 도달 시 (SELL 신호 발생)
    """

    def __init__(self, period: int = 20, num_std: float = 2.0):
        self.period = period
        self.num_std = num_std

    @property
    def name(self) -> str:
        return "볼린저밴드 평균회귀"

    @property
    def description(self) -> str:
        return (f"BB({self.period}, {self.num_std}σ). "
                f"하단 돌파 → 매수, 상단 돌파 → 매도")

    def generate_signal(self, df: pd.DataFrame) -> str:
        if len(df) < self.period + 1:
            return HOLD

        upper, middle, lower = calc_bollinger_bands(
            df["close"], self.period, self.num_std)

        current_close = df["close"].iloc[-1]
        current_upper = upper.iloc[-1]
        current_lower = lower.iloc[-1]

        if pd.isna(current_upper) or pd.isna(current_lower):
            return HOLD

        # 하단선 아래 → 매수 (반등 기대)
        if current_close < current_lower:
            return BUY
        # 상단선 위 → 매도 (조정 기대)
        elif current_close > current_upper:
            return SELL

        return HOLD

    def generate_signals_series(self, df: pd.DataFrame) -> pd.Series:
        """벡터화된 신호 생성"""
        signals = pd.Series(HOLD, index=df.index)

        upper, middle, lower = calc_bollinger_bands(
            df["close"], self.period, self.num_std)

        signals[df["close"] < lower] = BUY
        signals[df["close"] > upper] = SELL

        return signals


# ============================================================
# 전략 5: 돌파 + 거래량 증가 전략
# ============================================================
class BreakoutVolumeStrategy(BaseStrategy):
    """
    N봉 고가 돌파 + 거래량 증가를 동시에 확인하여 매수합니다.
    강한 모멘텀 돌파를 포착하는 전략입니다.

    진입 조건 (BUY) : 종가 > 최근 N봉 최고가 AND 거래량 > 거래량 SMA * vol_mult
    청산 조건 (SELL): 종가 < SMA(exit_period) — 추세 이탈 시 청산
    """

    def __init__(self, breakout_period: int = 20, vol_period: int = 20,
                 vol_mult: float = 1.5, exit_period: int = 10,
                 sell_confirm_bars: int = 3):
        self.breakout_period = breakout_period
        self.vol_period = vol_period
        self.vol_mult = vol_mult
        self.exit_period = exit_period
        self.sell_confirm_bars = max(1, sell_confirm_bars)

    @property
    def name(self) -> str:
        return "돌파 + 거래량 증가"

    @property
    def description(self) -> str:
        return (f"{self.breakout_period}봉 고가 돌파 + "
                f"거래량 > SMA({self.vol_period}) × {self.vol_mult}. "
                f"SMA({self.exit_period}) {self.sell_confirm_bars}봉 연속 하회 시 매도")

    def generate_signal(self, df: pd.DataFrame) -> str:
        min_len = max(self.breakout_period, self.vol_period, self.exit_period) + 2
        if len(df) < min_len:
            return HOLD

        # 고가 돌파 확인
        breakout = is_high_breakout(df["close"], df["high"], self.breakout_period)

        # 거래량 증가 확인
        vol_sma = calc_volume_sma(df["volume"], self.vol_period)

        # 청산 기준 이동평균
        exit_sma = calc_sma(df["close"], self.exit_period)

        current_breakout = breakout.iloc[-1]
        current_vol = df["volume"].iloc[-1]
        current_vol_sma = vol_sma.iloc[-1]
        current_close = df["close"].iloc[-1]
        current_exit_sma = exit_sma.iloc[-1]

        if pd.isna(current_vol_sma) or pd.isna(current_exit_sma):
            return HOLD

        # 돌파 + 거래량 급증 → 매수
        if current_breakout and current_vol > current_vol_sma * self.vol_mult:
            return BUY
        # 이동평균 하회가 연속 확인되면 매도 (휩쏘 완화)
        elif len(df) >= self.sell_confirm_bars:
            below_exit = df["close"] < exit_sma
            if bool(below_exit.tail(self.sell_confirm_bars).all()):
                return SELL

        return HOLD

    def generate_signals_series(self, df: pd.DataFrame) -> pd.Series:
        """벡터화된 신호 생성"""
        signals = pd.Series(HOLD, index=df.index)

        breakout = is_high_breakout(df["close"], df["high"], self.breakout_period)
        vol_sma = calc_volume_sma(df["volume"], self.vol_period)
        exit_sma = calc_sma(df["close"], self.exit_period)

        # 돌파 + 거래량 급증 → 매수
        buy_cond = breakout & (df["volume"] > vol_sma * self.vol_mult)
        # 추세 이탈이 연속 확인될 때만 매도
        below_exit = df["close"] < exit_sma
        sell_cond = (
            below_exit.rolling(window=self.sell_confirm_bars,
                               min_periods=self.sell_confirm_bars)
            .sum() == self.sell_confirm_bars
        )

        signals[buy_cond] = BUY
        signals[sell_cond] = SELL

        # 같은 봉에서 매수·매도 동시 발생 시 관망
        both = buy_cond & sell_cond
        signals[both] = HOLD

        return signals


# ============================================================
# 전략 6: 레짐 적응형 전략 (추세 + 평균회귀)
# ============================================================
class RegimeAdaptiveStrategy(BaseStrategy):
    """
    시장 상태(레짐)에 따라 전략을 전환합니다.
    - 상승장: 돌파 + 거래량 증가 추세추종
    - 횡보장: 볼린저 하단 + RSI 과매도 평균회귀
    - 하락장: 신규 매수 회피, 약세 지속 시 청산 신호만 제공
    """

    def __init__(self, trend_period: int = 50,
                 breakout_period: int = 20,
                 vol_period: int = 20,
                 vol_mult: float = 1.3,
                 exit_period: int = 12,
                 bb_period: int = 20,
                 bb_std: float = 2.0,
                 rsi_period: int = 14,
                 range_rsi_buy: float = 35.0,
                 range_rsi_sell: float = 65.0):
        self.trend_period = trend_period
        self.breakout_period = breakout_period
        self.vol_period = vol_period
        self.vol_mult = vol_mult
        self.exit_period = exit_period
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.rsi_period = rsi_period
        self.range_rsi_buy = range_rsi_buy
        self.range_rsi_sell = range_rsi_sell

    @property
    def name(self) -> str:
        return "레짐 적응형 (추세+평균회귀)"

    @property
    def description(self) -> str:
        return (
            f"상승장: {self.breakout_period}봉 돌파+거래량, "
            f"횡보장: BB({self.bb_period})+RSI({self.rsi_period}), "
            f"하락장: 신규 진입 회피"
        )

    def generate_signal(self, df: pd.DataFrame) -> str:
        min_len = max(
            self.trend_period,
            self.breakout_period + 1,
            self.vol_period,
            self.exit_period,
            self.bb_period,
            self.rsi_period + 1,
        )
        if len(df) < min_len:
            return HOLD

        close = df["close"]
        volume = df["volume"]
        high = df["high"]

        trend_sma = calc_sma(close, self.trend_period)
        trend_slope = trend_sma.diff()
        breakout = is_high_breakout(close, high, self.breakout_period)
        vol_sma = calc_volume_sma(volume, self.vol_period)
        exit_sma = calc_sma(close, self.exit_period)
        _, middle, lower = calc_bollinger_bands(close, self.bb_period, self.bb_std)
        rsi = calc_rsi(close, self.rsi_period)

        current_close = close.iloc[-1]
        current_volume = volume.iloc[-1]
        current_trend_sma = trend_sma.iloc[-1]
        current_trend_slope = trend_slope.iloc[-1]
        current_breakout = breakout.iloc[-1]
        current_vol_sma = vol_sma.iloc[-1]
        current_exit_sma = exit_sma.iloc[-1]
        current_middle = middle.iloc[-1]
        current_lower = lower.iloc[-1]
        current_rsi = rsi.iloc[-1]

        required = [
            current_trend_sma, current_trend_slope, current_vol_sma,
            current_exit_sma, current_middle, current_lower, current_rsi,
        ]
        if any(pd.isna(v) for v in required):
            return HOLD

        uptrend = (current_close > current_trend_sma) and (current_trend_slope > 0)
        downtrend = (current_close < current_trend_sma) and (current_trend_slope < 0)

        if uptrend:
            if current_breakout and current_volume > current_vol_sma * self.vol_mult:
                return BUY
            if current_close < current_exit_sma:
                return SELL
            return HOLD

        if downtrend:
            if current_close < current_exit_sma:
                return SELL
            return HOLD

        # 횡보장: 평균회귀
        if current_close < current_lower and current_rsi < self.range_rsi_buy:
            return BUY
        if current_close > current_middle or current_rsi > self.range_rsi_sell:
            return SELL
        return HOLD

    def generate_signals_series(self, df: pd.DataFrame) -> pd.Series:
        """벡터화된 신호 생성"""
        signals = pd.Series(HOLD, index=df.index)

        close = df["close"]
        volume = df["volume"]
        high = df["high"]

        trend_sma = calc_sma(close, self.trend_period)
        trend_slope = trend_sma.diff()
        breakout = is_high_breakout(close, high, self.breakout_period)
        vol_sma = calc_volume_sma(volume, self.vol_period)
        exit_sma = calc_sma(close, self.exit_period)
        _, middle, lower = calc_bollinger_bands(close, self.bb_period, self.bb_std)
        rsi = calc_rsi(close, self.rsi_period)

        uptrend = (close > trend_sma) & (trend_slope > 0)
        downtrend = (close < trend_sma) & (trend_slope < 0)
        ranging = ~(uptrend | downtrend)

        trend_buy = uptrend & breakout & (volume > vol_sma * self.vol_mult)
        trend_sell = uptrend & (close < exit_sma)
        down_sell = downtrend & (close < exit_sma)
        range_buy = ranging & (close < lower) & (rsi < self.range_rsi_buy)
        range_sell = ranging & ((close > middle) | (rsi > self.range_rsi_sell))

        signals[trend_sell | down_sell | range_sell] = SELL
        signals[trend_buy | range_buy] = BUY

        # 상승장 내 동시 충돌은 보수적으로 관망 처리
        conflict = trend_buy & trend_sell
        signals[conflict] = HOLD

        return signals


# ============================================================
# 전략 7: 일봉 생존형 추세·눌림 전략
# ============================================================
class DailySurvivalTrendStrategy(BaseStrategy):
    """
    장기 추세(EMA200) 위에서만 매수하고, 횡보/저변동 구간은 회피합니다.
    진입은 눌림 회복(EMA20 재돌파) + RSI 필터 또는 돌파+거래량 증가로 수행합니다.

    의도:
    - 추세장에서 수익 구간을 길게 가져가기
    - 횡보장/저변동 구간의 불필요한 진입 줄이기
    - 단순한 규칙으로 일봉 운용 가능하도록 구성
    """

    def __init__(
        self,
        trend_ema_period: int = 120,
        signal_ema_period: int = 9,
        exit_ema_period: int = 21,
        breakout_period: int = 8,
        vol_period: int = 10,
        vol_mult: float = 1.0,
        rsi_period: int = 14,
        rsi_buy_min: float = 35.0,
        rsi_buy_max: float = 70.0,
        rsi_sell: float = 66.0,
        atr_period: int = 14,
        min_atr_pct: float = 0.001,
        sell_confirm_bars: int = 1,
    ):
        self.trend_ema_period = trend_ema_period
        self.signal_ema_period = signal_ema_period
        self.exit_ema_period = exit_ema_period
        self.breakout_period = breakout_period
        self.vol_period = vol_period
        self.vol_mult = vol_mult
        self.rsi_period = rsi_period
        self.rsi_buy_min = rsi_buy_min
        self.rsi_buy_max = rsi_buy_max
        self.rsi_sell = rsi_sell
        self.atr_period = atr_period
        self.min_atr_pct = min_atr_pct
        self.sell_confirm_bars = max(1, sell_confirm_bars)

    @property
    def name(self) -> str:
        return "1분 생존형 추세·눌림"

    @property
    def description(self) -> str:
        return (
            f"EMA({self.trend_ema_period}) 추세 필터 + EMA({self.signal_ema_period}) "
            f"눌림 회복/돌파 진입. ATR%<{self.min_atr_pct * 100:.2f} 구간 회피, "
            f"EMA({self.exit_ema_period}) {self.sell_confirm_bars}봉 하회 또는 "
            f"RSI>{self.rsi_sell} 과열 후 약화 시 매도"
        )

    def generate_signal(self, df: pd.DataFrame) -> str:
        min_len = max(
            self.trend_ema_period + 2,
            self.signal_ema_period + 2,
            self.exit_ema_period + self.sell_confirm_bars + 1,
            self.breakout_period + 2,
            self.vol_period + 1,
            self.rsi_period + 2,
            self.atr_period + 2,
        )
        if len(df) < min_len:
            return HOLD

        close = df["close"]
        high = df["high"]
        low = df["low"]
        volume = df["volume"]

        ema_trend = calc_ema(close, self.trend_ema_period)
        ema_signal = calc_ema(close, self.signal_ema_period)
        ema_exit = calc_ema(close, self.exit_ema_period)
        rsi = calc_rsi(close, self.rsi_period)
        atr = calc_atr(high, low, close, self.atr_period)
        atr_pct = atr / close
        breakout = is_high_breakout(close, high, self.breakout_period)
        vol_sma = calc_volume_sma(volume, self.vol_period)

        curr_close = close.iloc[-1]
        prev_close = close.iloc[-2]
        curr_trend = ema_trend.iloc[-1]
        prev_trend = ema_trend.iloc[-2]
        curr_signal = ema_signal.iloc[-1]
        prev_signal = ema_signal.iloc[-2]
        curr_rsi = rsi.iloc[-1]
        curr_atr_pct = atr_pct.iloc[-1]
        curr_breakout = breakout.iloc[-1]
        curr_volume = volume.iloc[-1]
        curr_vol_sma = vol_sma.iloc[-1]

        required = [
            curr_trend, prev_trend, curr_signal, prev_signal,
            curr_rsi, curr_atr_pct, curr_vol_sma,
        ]
        if any(pd.isna(v) for v in required):
            return HOLD

        uptrend = (
            curr_close > (curr_trend * 0.997)
            and curr_trend >= (prev_trend * 0.9995)
        )
        low_vol_range = curr_atr_pct < self.min_atr_pct
        if low_vol_range:
            return HOLD

        # 매도: 추세 약화 또는 과열 후 하락 전환
        if len(df) >= self.sell_confirm_bars:
            below_exit = close < ema_exit
            if bool(below_exit.tail(self.sell_confirm_bars).all()) and curr_rsi < 48:
                return SELL
        if curr_rsi > self.rsi_sell and curr_close < curr_signal:
            return SELL
        # 매도 보강: 단기 추세선 하향 이탈 + 모멘텀 약화
        if curr_close < curr_signal and curr_rsi < 50:
            return SELL

        if not uptrend:
            return HOLD

        # 매수 1) 눌림 후 EMA20 회복 + RSI 건전 구간
        pullback_reclaim = (
            (prev_close <= prev_signal) and
            (curr_close > curr_signal) and
            (self.rsi_buy_min <= curr_rsi <= self.rsi_buy_max)
        )
        # 매수 1-보강) 상승 추세 내 단기 과매도 눌림
        bull_dip_buy = (
            curr_close < (curr_signal * 0.997)
            and curr_close > (curr_trend * 0.992)
            and 32.0 <= curr_rsi <= 58.0
        )
        # 매수 2) 고점 돌파 + 거래량 동반
        breakout_entry = curr_breakout and (
            (curr_volume > curr_vol_sma * self.vol_mult) or (curr_rsi >= 50.0)
        )
        # 1분봉 특화: 신호선 상향 재돌파 + RSI 중립 이상
        micro_reclaim = (
            (prev_close <= prev_signal)
            and (curr_close > curr_signal)
            and (curr_rsi >= 48.0)
        )

        if pullback_reclaim or bull_dip_buy or breakout_entry or micro_reclaim:
            return BUY
        return HOLD

    def generate_signals_series(self, df: pd.DataFrame) -> pd.Series:
        """벡터화된 신호 생성"""
        signals = pd.Series(HOLD, index=df.index)

        close = df["close"]
        high = df["high"]
        low = df["low"]
        volume = df["volume"]

        ema_trend = calc_ema(close, self.trend_ema_period)
        ema_signal = calc_ema(close, self.signal_ema_period)
        ema_exit = calc_ema(close, self.exit_ema_period)
        rsi = calc_rsi(close, self.rsi_period)
        atr = calc_atr(high, low, close, self.atr_period)
        atr_pct = atr / close
        breakout = is_high_breakout(close, high, self.breakout_period)
        vol_sma = calc_volume_sma(volume, self.vol_period)

        uptrend = (
            (close > (ema_trend * 0.997)) &
            (ema_trend >= (ema_trend.shift(1) * 0.9995))
        )
        low_vol_range = atr_pct < self.min_atr_pct

        pullback_reclaim = (
            (close.shift(1) <= ema_signal.shift(1)) &
            (close > ema_signal) &
            (rsi >= self.rsi_buy_min) &
            (rsi <= self.rsi_buy_max)
        )
        bull_dip_buy = (
            (close < (ema_signal * 0.997)) &
            (close > (ema_trend * 0.992)) &
            (rsi >= 32.0) &
            (rsi <= 58.0)
        )
        breakout_entry = breakout & (
            (volume > vol_sma * self.vol_mult) | (rsi >= 50.0)
        )
        micro_reclaim = (
            (close.shift(1) <= ema_signal.shift(1)) &
            (close > ema_signal) &
            (rsi >= 48.0)
        )
        buy_cond = uptrend & ~low_vol_range & (
            pullback_reclaim | bull_dip_buy | breakout_entry | micro_reclaim
        )

        below_exit = close < ema_exit
        exit_trend = (
            below_exit.rolling(window=self.sell_confirm_bars,
                               min_periods=self.sell_confirm_bars)
            .sum() == self.sell_confirm_bars
        ) & (rsi < 48.0)
        exit_overheat = (rsi > self.rsi_sell) & (close < ema_signal)
        exit_momentum_weak = (close < ema_signal) & (rsi < 50.0)
        sell_cond = exit_trend | exit_overheat | exit_momentum_weak

        signals[sell_cond] = SELL
        signals[buy_cond] = BUY
        signals[buy_cond & sell_cond] = HOLD
        return signals


# ============================================================
# 전략 레지스트리 — 사용 가능한 전략 목록
# ============================================================
class SmaTrendStrategy(BaseStrategy):
    """
    일봉 종가가 SMA 위면 보유, 아래면 현금. 하락장 회피용 저빈도 추세 필터.
    측정 근거는 CLAUDE.md '일봉 추세 필터' 참고 (BOT_CANDLE_UNIT=1440 전제).
    """

    required_candle_unit = DAY_CANDLE_UNIT

    # 추세 신호만으로 사고팔게 부가 규칙을 끈다. 트레일링/손절/순이익 게이트가
    # 켜져 있으면 추세 중간에 털려서 측정한 결과와 다른 전략이 된다.
    risk_overrides = {
        # 전역 BUY_RATIO_PCT(15분봉용 0.30)와 분리. 1.0 = 측정 기준(MDD 56%), 0.5 = MDD 40%
        "buy_ratio_pct": _float_env("TREND_BUY_RATIO_PCT", 1.0),
        "stop_loss_pct": 0,
        "trailing_stop_pct": 0,
        "take_profit_pct": 0,
        "min_net_profit_pct": -1e9,
        "cooldown_seconds": 0,
        "max_consecutive_buy_signals": 0,
        "atr_reduce_mult": 1e9,
        "atr_block_mult": 1e9,
    }

    # ponytail: 트레이더는 업비트 제한으로 200봉만 받으므로 period < 200 이어야
    # 신호가 난다. SMA200 을 쓰려면 트레이더 캔들 조회에 페이지네이션이 필요하다.
    def __init__(self, period: int = 50):
        self.period = period

    @property
    def name(self) -> str:
        return f"일봉 SMA{self.period} 추세"

    @property
    def description(self) -> str:
        return f"종가 > SMA{self.period} 이면 보유, 아래로 내려가면 전량 매도"

    def generate_signal(self, df: pd.DataFrame) -> str:
        return self.generate_signals_series(df).iloc[-1]

    def intraday_threshold(self, df: pd.DataFrame) -> float | None:
        # 오늘 종가 c 가 SMA_n(최근 n-1개 확정 종가 + c) 위  <=>  c > 최근 n-1개 종가 평균
        if len(df) < self.period - 1:
            return None
        return float(df["close"].tail(self.period - 1).mean())

    def status_text(self, df: pd.DataFrame) -> str:
        sma = df["close"].rolling(self.period).mean().iloc[-1]
        close = df["close"].iloc[-1]
        if pd.isna(sma) or sma <= 0:
            return ""
        side = "위 → 보유" if close > sma else "아래 → 현금"
        return (f"SMA{self.period} {sma:,.0f} / 확정 종가 {close:,.0f} "
                f"({(close / sma - 1) * 100:+.2f}%, {side})")

    def generate_signals_series(self, df: pd.DataFrame) -> pd.Series:
        sma = df["close"].rolling(self.period).mean()
        signals = pd.Series(HOLD, index=df.index)
        signals[df["close"] > sma] = BUY
        signals[df["close"] < sma] = SELL
        return signals


def get_all_strategies() -> list[BaseStrategy]:
    """
    모든 기본 전략 인스턴스를 반환합니다.

    Returns:
        list[BaseStrategy]: 전략 리스트
    """
    return [
        DailySurvivalTrendStrategy(),
        RegimeAdaptiveStrategy(
            trend_period=50,
            breakout_period=16,
            vol_period=20,
            vol_mult=1.2,
            exit_period=12,
            bb_period=20,
            bb_std=1.6,
            rsi_period=14,
            range_rsi_buy=45.0,
            range_rsi_sell=62.0,
        ),
        RSIStrategy(),
        MACrossStrategy(),
        RSIWithMAFilterStrategy(),
        BollingerMeanReversionStrategy(),
        BreakoutVolumeStrategy(
            breakout_period=14,
            vol_period=14,
            vol_mult=1.2,
            exit_period=10,
            sell_confirm_bars=3,
        ),
        SmaTrendStrategy(50),
    ]


def get_strategy_by_name(name: str) -> BaseStrategy | None:
    """
    이름으로 전략을 검색합니다.

    Args:
        name: 전략 이름

    Returns:
        BaseStrategy 또는 None
    """
    if name == "AI 자율 매매":
        from .ai_strategy import AIAutonomousStrategy
        return AIAutonomousStrategy()

    for strategy in get_all_strategies():
        if strategy.name == name:
            return strategy
    return None


# ============================================================
# BTC 자율 에이전트 전략 라이브러리 (docs/BTC_DAYTRADE.md 2장)
# 실행기(dt_live)·점수판·백테스트(simulate_library)가 같은 entry_exit/bar_action 을 쓴다.
# ============================================================
class LibraryStrategy(BaseStrategy):
    """
    에이전트가 고르는 전략. 파라미터는 RANGES 범위로 잘린다.
    kind="bar": 봉 마감 때 entry_exit 마지막 값으로 판단, 다음 봉 시가(=마감 직후 시장가) 체결.
    kind="intraday": 장중 가격이 기준가를 통과하면 체결 (vb_daily, vwap_revert).
    """

    ID = ""
    UNIT = 60                      # 판단 봉(분). 1440 = 일봉
    KIND = "bar"
    RANGES: dict = {}              # 이름 -> (최소, 최대, 기본)
    RULE = ""
    HAS_MAX_HOLD = True            # sma_trend_daily 만 False

    def __init__(self, **params):
        self.params, self.clamped = clamp_params(self.RANGES, params)

    @property
    def name(self) -> str:
        return self.ID

    @property
    def description(self) -> str:
        return self.RULE

    @property
    def max_hold_h(self) -> float | None:
        return self.params.get("max_hold") if self.HAS_MAX_HOLD else None

    def entry_exit(self, df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
        """확정봉 df -> (진입 신호, 청산 신호) bool 시리즈."""
        raise NotImplementedError

    def holds(self, df: pd.DataFrame) -> bool:
        """보유 중 전략을 이것으로 바꿀 때 포지션을 넘겨받을 상태인가 (청산 조건이 아님)."""
        return False

    def generate_signals_series(self, df: pd.DataFrame) -> pd.Series:
        e, x = self.entry_exit(df)
        s = pd.Series(HOLD, index=df.index)
        s[x] = SELL
        s[e] = BUY
        return s

    def generate_signal(self, df: pd.DataFrame) -> str:
        return self.generate_signals_series(df).iloc[-1]


def clamp_params(ranges: dict, params: dict | None) -> tuple[dict, list[str]]:
    """범위 밖·모르는 값은 잘라내고 무엇을 잘랐는지 돌려준다. 숫자가 아니면 기본값."""
    out, notes = {}, []
    params = params or {}
    for k, (lo, hi, default) in ranges.items():
        v = params.get(k, default)
        try:
            v = float(v)
        except (TypeError, ValueError):
            notes.append(f"{k}={params.get(k)!r}→기본 {default}")
            v = default
        c = min(max(v, lo), hi)
        if c != v:
            notes.append(f"{k}={v:g}→{c:g}")
        out[k] = int(c) if isinstance(default, int) else c
    notes += [f"{k} 무시" for k in params if k not in ranges]
    return out, notes


class RsiMomoStrategy(LibraryStrategy):
    RULE = "RSI(14)가 entry 를 상향 돌파하면 매수, RSI < exit 또는 max_hold(시간) 넘으면 매도"

    def entry_exit(self, df):
        rsi = calc_rsi(df["close"], 14)
        e = (rsi.shift(1) < self.params["entry"]) & (rsi >= self.params["entry"])
        return e.fillna(False), (rsi < self.params["exit"]).fillna(False)

    def holds(self, df):
        rsi = calc_rsi(df["close"], 14).iloc[-1]
        return bool(rsi >= self.params["exit"])


class RsiMomo1h(RsiMomoStrategy):
    ID, UNIT = "rsi_momo_1h", 60
    RANGES = {"entry": (65, 75, 70), "exit": (40, 55, 50), "max_hold": (24, 96, 48)}


class RsiMomo4h(RsiMomoStrategy):
    ID, UNIT = "rsi_momo_4h", 240
    RANGES = {"entry": (65, 75, 70), "exit": (40, 55, 50), "max_hold": (48, 96, 96)}


class BreakoutVol1h(LibraryStrategy):
    ID, UNIT = "breakout_vol_1h", 60
    RANGES = {"N": (12, 48, 24), "vol_mult": (1.5, 3.0, 2.0), "M": (6, 24, 12), "max_hold": (24, 96, 48)}
    RULE = "종가 > 직전 N봉 고가 최댓값 + 거래대금 > 직전 N봉 평균 × vol_mult 이면 매수, 종가 < 직전 M봉 저가 최솟값 또는 max_hold 면 매도"

    def entry_exit(self, df):
        n, m = self.params["N"], self.params["M"]
        val = df["value"] if "value" in df else df["close"] * df["volume"]
        e = (df["close"] > df["high"].rolling(n).max().shift(1)) & \
            (val > val.rolling(n).mean().shift(1) * self.params["vol_mult"])
        x = df["close"] < df["low"].rolling(m).min().shift(1)
        return e.fillna(False), x.fillna(False)

    def holds(self, df):
        return not bool(self.entry_exit(df)[1].iloc[-1])


class SmaTrendDaily(LibraryStrategy):
    ID, UNIT, HAS_MAX_HOLD = "sma_trend_daily", DAY_CANDLE_UNIT, False
    RANGES = {"period": (20, 50, 50)}
    RULE = "일봉 종가 > SMA(period) 면 다음 09:00 매수, 아래면 다음 09:00 매도. 최대 보유시간·손절 없음(킬 드로다운만)"

    def entry_exit(self, df):
        sma = df["close"].rolling(self.params["period"]).mean()
        return (df["close"] > sma).fillna(False), (df["close"] < sma).fillna(False)

    def holds(self, df):
        return bool(self.entry_exit(df)[0].iloc[-1])


class VbDaily(LibraryStrategy):
    ID, UNIT, KIND = "vb_daily", DAY_CANDLE_UNIT, "intraday"
    RANGES = {"k": (0.4, 0.7, 0.5), "ma_n": (3, 10, 5)}
    RULE = "09:00 시가 + k × 전일 변동폭을 장중 돌파하면 매수 (시가 > 직전 ma_n일 종가 평균일 때만), 다음 날 09:00 매도"

    def level(self, daily: pd.DataFrame) -> float | None:
        """daily: 오늘(진행 중) 봉을 마지막 행으로 포함. 오늘 매수 기준가, 필터에 걸리면 None."""
        if len(daily) < self.params["ma_n"] + 2:
            return None
        today, prev = daily.iloc[-1], daily.iloc[-2]
        ma = daily["close"].iloc[-1 - self.params["ma_n"]:-1].mean()
        if today["open"] <= ma:
            return None
        return float(today["open"] + self.params["k"] * (prev["high"] - prev["low"]))

    def entry_exit(self, df):  # 점수판용 근사: 그날 고가가 기준가를 넘었는가
        rng = (df["high"] - df["low"]).shift(1)
        lvl = df["open"] + self.params["k"] * rng
        ok = df["open"] > df["close"].rolling(self.params["ma_n"]).mean().shift(1)
        return (ok & (df["high"] >= lvl)).fillna(False), pd.Series(True, index=df.index)


class VwapRevert(LibraryStrategy):
    ID, UNIT, KIND = "vwap_revert", 15, "intraday"
    RANGES = {"dev": (0.5, 2.0, 1.0)}
    RULE = "세션(09:00) VWAP × (1 − dev%) 에 post_only 지정가 매수, VWAP 에 지정가 매도, 09:00 강제 청산. 세션 첫 15분 거래 안 함"

    def entry_exit(self, df):
        f = pd.Series(False, index=df.index)
        return f, f


class Discretionary(LibraryStrategy):
    ID, KIND = "discretionary", "manual"
    RANGES = {"max_hold": (1, 96, 96)}
    RULE = "에이전트가 근거를 적고 buy_now 로 직접 매수. 예산의 DT_DISCRETIONARY_MAX_PCT 이하. 청산은 sell_now·손절·max_hold"

    def entry_exit(self, df):
        f = pd.Series(False, index=df.index)
        return f, f


LIBRARY: dict[str, type[LibraryStrategy]] = {
    c.ID: c for c in (RsiMomo1h, RsiMomo4h, BreakoutVol1h, SmaTrendDaily, VbDaily, VwapRevert, Discretionary)
}


def make_library_strategy(sid: str, params: dict | None = None) -> LibraryStrategy | None:
    cls = LIBRARY.get(sid)
    return cls(**(params or {})) if cls else None


def bar_action(holding: bool, entry: bool, exit_: bool, held_h: float,
               max_hold_h: float | None) -> str | None:
    """봉 마감 시 판단. 실행기와 점수판이 같이 쓴다."""
    if not holding:
        return BUY if entry else None
    if exit_ or (max_hold_h is not None and held_h >= max_hold_h):
        return SELL
    return None


def simulate_library(strat: LibraryStrategy, df: pd.DataFrame, cost: float = 0.001) -> dict:
    """
    주문 없는 계산. df: 확정봉(시간 오름차순, candle_date_time_kst 포함).
    bar: 신호봉 다음 봉 시가 체결. vb_daily: 기준가(시가가 넘으면 시가) 매수 → 다음 봉 시가 매도.
    vwap_revert: 15분봉 근사 (직전 봉까지 VWAP 으로 지정가, 저가가 통과하면 체결, 지정가 비용 0.05%).
    Returns: {"equity": Series(봉 시가 기준), "trades": [(진입시각, 청산시각, 수익률)]}
    """
    ts = pd.to_datetime(df["candle_date_time_kst"]).reset_index(drop=True)
    o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    n = len(df)
    eq = np.ones(n)
    trades = []
    cash, pos, entry_px, entry_t = 1.0, 0.0, 0.0, None

    if strat.ID == "vwap_revert":
        maker = 0.0005
        dev = strat.params["dev"] / 100
        sess = (ts - pd.Timedelta(hours=9)).dt.date
        tp = (df["high"] + df["low"] + df["close"]).to_numpy(float) / 3
        vol = df["volume"].to_numpy(float)
        pv = v = 0.0
        for i in range(n):
            if i and sess[i] != sess[i - 1]:
                if pos:
                    cash = pos * o[i] * (1 - cost); trades.append((entry_t, ts[i], cash / entry_px - 1)); pos = 0.0
                pv = v = 0.0
            vwap = pv / v if v > 0 else None
            first = i == 0 or sess[i] != sess[i - 1]
            if vwap and not first:
                if not pos and l[i] < vwap * (1 - dev):
                    px = min(o[i], vwap * (1 - dev))
                    entry_px, entry_t = cash, ts[i]
                    pos = cash * (1 - maker) / px; cash = 0.0
                if pos and h[i] > vwap and ts[i] != entry_t:
                    cash = pos * max(o[i], vwap) * (1 - maker)
                    trades.append((entry_t, ts[i], cash / entry_px - 1)); pos = 0.0
            pv += tp[i] * vol[i]; v += vol[i]
            eq[i] = cash + pos * c[i]
        return {"equity": pd.Series(eq, index=ts), "trades": trades}

    if strat.ID == "vb_daily":
        e, _ = strat.entry_exit(df)
        rng = np.r_[np.nan, (h - l)[:-1]]
        for i in range(n):
            if e.iloc[i] and i + 1 < n:
                px = max(o[i], o[i] + strat.params["k"] * rng[i])
                r = (o[i + 1] * (1 - cost)) / (px * (1 + cost)) - 1
                cash *= 1 + r
                trades.append((ts[i], ts[i + 1], r))
            eq[i] = cash
        return {"equity": pd.Series(eq, index=ts), "trades": trades}

    e, x = strat.entry_exit(df)
    e, x = e.to_numpy(bool), x.to_numpy(bool)
    for i in range(n):
        # i 봉 시가에 직전 봉(i-1) 신호 체결
        if i:
            held = (ts[i] - entry_t).total_seconds() / 3600 if pos else 0.0
            act = bar_action(pos > 0, e[i - 1], x[i - 1], held, strat.max_hold_h)
            if act == BUY:
                entry_px, entry_t = cash, ts[i]
                pos = cash * (1 - cost) / o[i]; cash = 0.0
            elif act == SELL:
                cash = pos * o[i] * (1 - cost)
                trades.append((entry_t, ts[i], cash / entry_px - 1)); pos = 0.0
        eq[i] = cash + pos * c[i]
    return {"equity": pd.Series(eq, index=ts), "trades": trades}


def window_stats(sim: dict, since: pd.Timestamp) -> dict:
    """sim 의 since 이후 성과: 수익%, 거래 수(청산 기준), 승률%, MDD%."""
    eq = sim["equity"][sim["equity"].index >= since]
    if eq.empty:
        return {"ret": 0.0, "trades": 0, "win": None, "mdd": 0.0}
    base = sim["equity"][sim["equity"].index < since]
    start = base.iloc[-1] if len(base) else eq.iloc[0]
    tr = [t for t in sim["trades"] if t[1] >= since]
    mdd = float(((eq / eq.cummax()) - 1).min() * 100)
    return {"ret": round(float(eq.iloc[-1] / start - 1) * 100, 2), "trades": len(tr),
            "win": round(100 * sum(t[2] > 0 for t in tr) / len(tr), 1) if tr else None,
            "mdd": round(mdd, 2)}


# 사람에게 보여 줄 한글 이름 (텔레그램·대시보드). 코드·LLM 입력은 영문 ID 를 그대로 쓴다
KO_NAME = {
    "rsi_momo_1h": "상승세 올라타기(1시간)", "rsi_momo_4h": "상승세 올라타기(4시간)",
    "breakout_vol_1h": "거래 몰릴 때 최고가 돌파(1시간)", "sma_trend_daily": "평균선 위면 들고 있기(하루)",
    "vb_daily": "아침에 크게 오르면 사기(하루)", "vwap_revert": "평균보다 쌀 때 사기(15분)",
    "discretionary": "AI 직접 판단", "none": "현금 대기", "keep": "전략 유지", "hold": "그냥 들고 있기",
}
KO_PARAM = {"entry": "살 때 힘 기준", "exit": "팔 때 힘 기준", "max_hold": "최대 보유 시간", "N": "최고가 볼 기간(봉)",
            "vol_mult": "거래량 몇 배", "M": "최저가 볼 기간(봉)", "period": "평균 낼 일수", "k": "전날 출렁임 몇 배",
            "ma_n": "평균 낼 일수", "dev": "평균보다 몇 % 쌀 때"}
# AI 가 쓴 문장 속 어려운 말 → 쉬운 말 (dashboard-web/lib/ko.ts 의 EASY_WORDS 와 같게)
EASY_WORDS = [
    (r"\bMACD(\s?hist)?\b", "오르내림 힘"), (r"\bdonchian\d*_pos\b", "최근 범위 속 위치(0=바닥, 1=꼭대기)"),
    (r"(호가\s?)?\btop\d+\s?imbalance\b", "사자·팔자 주문 차이"), (r"\bimbalance\b", "주문 차이"),
    (r"(체결\s?)?\bbuy_ratio\b", "사는 쪽 비율"), (r"과매도", "너무 많이 떨어진"), (r"과매수", "너무 많이 오른"),
    (r"현물\s?롱\s?전용", "오를 때만 돈을 버는 방식"), (r"세션\s?VWAP", "오늘 평균 거래가"), (r"세션\s?", "오늘 "),
    (r"\+DI|\bpdi\b", "상승 힘"), (r"-DI|\bmdi\b", "하락 힘"), (r"\bADX\b", "추세 세기"), (r"\bRSI\b", "과열 지수"),
    (r"\b[ES]MA\b", "평균선"), (r"\bATR\b", "평소 출렁임폭"), (r"\bVWAP\b", "오늘 평균 거래가"),
    (r"z\s?점수|z-?score", "평소보다 튄 정도"), (r"밴드폭\s?백분위", "출렁임 크기 순위"), (r"볼린저\s?밴드?", "가격 범위 띠"),
    (r"역배열", "내림 순서"), (r"정배열", "오름 순서"), (r"휩쏘", "속임 신호"), (r"모멘텀", "오르는 힘"),
    (r"음봉", "내린 봉"), (r"양봉", "오른 봉"), (r"지지선", "버티던 가격"), (r"저항선", "막히던 가격"),
    (r"레짐|국면", "시장 흐름"), (r"플랜", "전략"), (r"\bescalate\b", "다시 판단"), (r"\bkeep\b", "유지"),
    (r"\bconfidence\b", "확신도"), (r"\bbuy_now\b", "바로 매수"), (r"\bsell_now\b", "바로 매도"),
    (r"\bfollow_plan\b", "전략대로"), (r"\b1d\b", "하루봉"), (r"(?-i:\b(\d+)h\b)", r"\1시간봉"), (r"(?-i:\b(\d+)m\b)", r"\1분봉"),
]
_EASY_RE = [(re.compile(a, re.I), b) for a, b in EASY_WORDS]


def ko(sid: str | None) -> str:
    return KO_NAME.get(sid or "", sid or "-")


def ko_params(params: dict | None) -> str:
    return ", ".join(f"{KO_PARAM.get(k, k)} {v:g}" for k, v in (params or {}).items()) or "기본값"


def ko_sub(text: str) -> str:
    """문장 속 전략 ID·지표 이름을 쉬운 말로 (AI 가 쓴 이유를 사람에게 보여 줄 때)."""
    for sid in sorted(KO_NAME, key=len, reverse=True):
        if len(sid) > 4:
            text = text.replace(sid, KO_NAME[sid])
    for rx, word in _EASY_RE:
        text = rx.sub(word, text)
    return text
