# 실행 명령어 정리

## 0) 환경 준비
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

## 1) 기본 실행
```bash
./venv/bin/python main.py --mode backtest
./venv/bin/python main.py --mode validate
./venv/bin/python main.py --mode trade
./venv/bin/python main.py --mode trade --strategy "AI 자율 매매"
./venv/bin/python main.py --mode review
./venv/bin/python main.py --mode review --date 2026-03-28
./venv/bin/python -m streamlit run dashboard.py
```

## 2) 전략별 트레이드 실행
```bash
./venv/bin/python main.py --mode trade --strategy "AI 자율 매매"
./venv/bin/python main.py --mode trade --strategy "1분 생존형 추세·눌림"
./venv/bin/python main.py --mode trade --strategy "레짐 적응형 (추세+평균회귀)"
./venv/bin/python main.py --mode trade --strategy "RSI 과매도/과매수"
./venv/bin/python main.py --mode trade --strategy "이동평균 크로스"
./venv/bin/python main.py --mode trade --strategy "RSI + 이동평균 필터"
./venv/bin/python main.py --mode trade --strategy "볼린저밴드 평균회귀"
./venv/bin/python main.py --mode trade --strategy "돌파 + 거래량 증가"
```

## 3) 로그/상태 확인
```bash
tail -f logs/trade_$(date +%F).log
tail -f logs/trade_$(date +%F).log | rg "ERROR|WARNING|API 에러|no_authorization_ip"
```

### API 인증 확인 (주문 없음)
```bash
./venv/bin/python - <<'PY'
from bot.upbit_client import UpbitClient, UpbitClientError
c = UpbitClient(dry_run=True)
try:
    c.get_accounts()
    print("AUTH_OK")
except UpbitClientError as e:
    print("AUTH_FAIL", e)
PY
```

### 공인 IP 확인
```bash
curl -4 https://api.ipify.org
curl -6 https://api64.ipify.org
```

## 4) .env 핵심 설정

### 모의/실거래
```env
BOT_DRY_RUN=true
BOT_DRY_RUN=false
```

### 실행/로그
```env
BOT_INTERVAL_SECONDS=3
BOT_LOG_SIGNAL_CHANGE_ONLY=false
```

### 멀티마켓
```env
UPBIT_MARKET=KRW-BTC
UPBIT_MARKETS=KRW-BTC,KRW-ETH,KRW-XRP
```

### 리스크/청산
```env
BUY_RATIO_PCT=0.30
MIN_KRW_RESERVE=5000
STOP_LOSS_PCT=0.05
TAKE_PROFIT_PCT=0.10
TRAILING_STOP_PCT=0.05
MIN_NET_PROFIT_PCT=0.00
MAX_CHASE_PCT=0.003
MAX_POSITIONS=0
COOLDOWN_SECONDS=30
MAX_CONSECUTIVE_BUY_SIGNALS=0
```

### GPT 리뷰(선택)
```env
OPENAI_API_KEY=your_key
OPENAI_MODEL=gpt-4o-mini
OPENAI_TIMEOUT_SECONDS=20
```

### LLM 매수 게이트(선택)
```env
LLM_GATE_ENABLED=true
LLM_GATE_MODEL=gpt-4o-mini
LLM_GATE_TIMEOUT_SECONDS=8
LLM_GATE_MIN_CONFIDENCE=0.55
LLM_GATE_FAIL_OPEN=true
LLM_GATE_MAX_INPUT_CANDLES=120
```

### AI 자율 매매(선택)
```env
AI_TRADER_ENABLED=true
AI_TRADER_MODEL=gpt-4o-mini
AI_TRADER_TIMEOUT_SECONDS=10
AI_TRADER_MAX_INPUT_CANDLES=160
AI_TRADER_MIN_CONFIDENCE=0.55
AI_TRADER_FAIL_SIGNAL=HOLD
```

## 5) 권장 순서
1. `./venv/bin/python main.py --mode backtest`
2. `./venv/bin/python main.py --mode validate`
3. `./venv/bin/python main.py --mode trade --strategy "1분 생존형 추세·눌림"` (`BOT_DRY_RUN=true`)
4. 종료는 `Ctrl+C` (END 스냅샷 기록)
5. `./venv/bin/python main.py --mode review --date $(date +%F)`
