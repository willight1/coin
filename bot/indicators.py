"""
indicators.py — 기술 지표 모듈
==============================
규칙 기반 전략에서 사용하는 기술 지표를 pandas Series/DataFrame 기반으로 구현합니다.

모든 함수는 pandas Series 를 입력받아 pandas Series 를 반환합니다.
전략 모듈에서 쉽게 재사용할 수 있도록 순수 함수로 작성되었습니다.

지원 지표:
- RSI (Relative Strength Index)
- SMA (Simple Moving Average)
- EMA (Exponential Moving Average)
- MACD (Moving Average Convergence Divergence)
- ATR (Average True Range)
- Bollinger Bands
- 거래량 이동평균
- N봉 고가/저가 돌파 판단
"""

import pandas as pd
import numpy as np


# ============================================================
# RSI (Relative Strength Index, 상대강도지수)
# ============================================================
def calc_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """
    RSI를 계산합니다.

    RSI = 100 - (100 / (1 + RS))
    RS = 평균 상승폭 / 평균 하락폭

    Args:
        close: 종가 시리즈
        period: RSI 계산 기간 (기본 14)

    Returns:
        pd.Series: RSI 값 (0~100)
    """
    delta = close.diff()

    # 상승분과 하락분 분리
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)

    # 지수 이동평균 (Wilder 방식)
    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

    # RS 계산 (0으로 나누기 방지)
    rs = avg_gain / avg_loss.replace(0, np.nan)

    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi


# ============================================================
# SMA (Simple Moving Average, 단순 이동평균)
# ============================================================
def calc_sma(series: pd.Series, period: int = 20) -> pd.Series:
    """
    단순 이동평균을 계산합니다.

    SMA = (최근 N개 값의 합) / N

    Args:
        series: 입력 시리즈 (종가, 거래량 등)
        period: 이동평균 기간

    Returns:
        pd.Series: SMA 값
    """
    return series.rolling(window=period, min_periods=period).mean()


# ============================================================
# EMA (Exponential Moving Average, 지수 이동평균)
# ============================================================
def calc_ema(series: pd.Series, period: int = 20) -> pd.Series:
    """
    지수 이동평균을 계산합니다.

    최근 값에 더 높은 가중치를 부여합니다.

    Args:
        series: 입력 시리즈
        period: EMA 기간

    Returns:
        pd.Series: EMA 값
    """
    return series.ewm(span=period, min_periods=period, adjust=False).mean()


# ============================================================
# MACD (Moving Average Convergence Divergence)
# ============================================================
def calc_macd(close: pd.Series, fast: int = 12, slow: int = 26,
              signal: int = 9) -> tuple[pd.Series, pd.Series, pd.Series]:
    """
    MACD를 계산합니다.

    MACD Line = EMA(fast) - EMA(slow)
    Signal Line = EMA(MACD Line, signal)
    Histogram = MACD Line - Signal Line

    Args:
        close: 종가 시리즈
        fast: 빠른 EMA 기간 (기본 12)
        slow: 느린 EMA 기간 (기본 26)
        signal: 시그널 EMA 기간 (기본 9)

    Returns:
        tuple: (MACD Line, Signal Line, Histogram)
    """
    ema_fast = calc_ema(close, fast)
    ema_slow = calc_ema(close, slow)

    macd_line = ema_fast - ema_slow
    signal_line = calc_ema(macd_line, signal)
    histogram = macd_line - signal_line

    return macd_line, signal_line, histogram


# ============================================================
# ATR (Average True Range, 평균 진정 범위)
# ============================================================
def calc_atr(high: pd.Series, low: pd.Series, close: pd.Series,
             period: int = 14) -> pd.Series:
    """
    ATR을 계산합니다. 변동성 측정 지표입니다.

    True Range = max(고가-저가, |고가-전일종가|, |저가-전일종가|)
    ATR = SMA(True Range, period)

    Args:
        high: 고가 시리즈
        low: 저가 시리즈
        close: 종가 시리즈
        period: ATR 기간

    Returns:
        pd.Series: ATR 값
    """
    prev_close = close.shift(1)

    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()

    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = true_range.rolling(window=period, min_periods=period).mean()

    return atr


# ============================================================
# Bollinger Bands (볼린저 밴드)
# ============================================================
def calc_bollinger_bands(close: pd.Series, period: int = 20,
                         num_std: float = 2.0
                         ) -> tuple[pd.Series, pd.Series, pd.Series]:
    """
    볼린저 밴드를 계산합니다.

    중간선 = SMA(period)
    상단선 = 중간선 + (표준편차 * num_std)
    하단선 = 중간선 - (표준편차 * num_std)

    Args:
        close: 종가 시리즈
        period: 이동평균 기간 (기본 20)
        num_std: 표준편차 배수 (기본 2.0)

    Returns:
        tuple: (상단선, 중간선, 하단선)
    """
    middle = calc_sma(close, period)
    std = close.rolling(window=period, min_periods=period).std()

    upper = middle + (std * num_std)
    lower = middle - (std * num_std)

    return upper, middle, lower


# ============================================================
# 거래량 이동평균
# ============================================================
def calc_volume_sma(volume: pd.Series, period: int = 20) -> pd.Series:
    """
    거래량의 단순 이동평균을 계산합니다.

    Args:
        volume: 거래량 시리즈
        period: 이동평균 기간

    Returns:
        pd.Series: 거래량 SMA
    """
    return calc_sma(volume, period)


# ============================================================
# N봉 고가/저가 돌파 판단
# ============================================================
def is_high_breakout(close: pd.Series, high: pd.Series,
                     period: int = 20) -> pd.Series:
    """
    최근 N봉 고가 돌파 여부를 판단합니다.

    현재 종가가 이전 N봉의 최고가를 돌파하면 True

    Args:
        close: 종가 시리즈
        high: 고가 시리즈
        period: 돌파 판단 기간

    Returns:
        pd.Series: True/False (돌파 여부)
    """
    # 이전 봉까지의 N봉 최고가 (현재 봉 제외)
    prev_high = high.shift(1).rolling(window=period, min_periods=period).max()
    return close > prev_high


def is_low_breakdown(close: pd.Series, low: pd.Series,
                     period: int = 20) -> pd.Series:
    """
    최근 N봉 저가 하향 돌파 여부를 판단합니다.

    현재 종가가 이전 N봉의 최저가를 하향 돌파하면 True

    Args:
        close: 종가 시리즈
        low: 저가 시리즈
        period: 돌파 판단 기간

    Returns:
        pd.Series: True/False (하향 돌파 여부)
    """
    prev_low = low.shift(1).rolling(window=period, min_periods=period).min()
    return close < prev_low
