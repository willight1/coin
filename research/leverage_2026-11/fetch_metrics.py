# 바이낸스 과거 지표(미결제약정·롱숏 비율) → bn_{SYM}_metrics.pkl. 받는 코드는 봇과 같은 함수(bot/hl_signals.metrics_daily).
# oi_sweep.py 가 이 파일을 읽는다. 봇·재선정은 state/hl_metrics_{코인}.pkl 캐시와 fetch_bn 의 bn_{SYM}_x.pkl 을 쓴다.
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from bot.hl_signals import metrics_daily  # noqa: E402

D = os.path.dirname(os.path.abspath(__file__))
if __name__ == "__main__":
    for s in sys.argv[1:] or ("BTCUSDT", "ETHUSDT"):
        df = metrics_daily(s[:-4], path=f"{D}/bn_{s}_metrics.pkl")
        print(s, len(df), df.index[0].date(), "~", df.index[-1].date())
