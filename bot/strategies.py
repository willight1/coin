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

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import pandas as pd
import numpy as np

from .indicators import (
    calc_rsi, calc_sma, calc_ema, calc_macd,
    calc_bollinger_bands, calc_volume_sma,
    is_high_breakout, calc_atr,
)
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
