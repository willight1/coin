# 실행 명령어 정리

## 0) 환경 준비
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

## 1) 기본 실행

### 백테스트
```bash
./venv/bin/python main.py --mode backtest
```

### 검증
```bash
./venv/bin/python main.py --mode validate
```

### 트레이드 (전략 미지정: 기본 전략 사용)
```bash
./venv/bin/python main.py --mode trade
```

### 리뷰 (오늘 로그)
```bash
./venv/bin/python main.py --mode review
```

### 리뷰 (특정 날짜)
```bash
./venv/bin/python main.py --mode review --date 2026-03-26
```

## 2) 전략별 트레이드 실행

```bash
./venv/bin/python main.py --mode trade --strategy "레짐 적응형 (추세+평균회귀)"
./venv/bin/python main.py --mode trade --strategy "1분 생존형 추세·눌림"
./venv/bin/python main.py --mode trade --strategy "RSI 과매도/과매수"
./venv/bin/python main.py --mode trade --strategy "이동평균 크로스"
./venv/bin/python main.py --mode trade --strategy "RSI + 이동평균 필터"
./venv/bin/python main.py --mode trade --strategy "볼린저밴드 평균회귀"
./venv/bin/python main.py --mode trade --strategy "돌파 + 거래량 증가"
```

## 3) 로그/상태 확인

### 오늘 로그 실시간
```bash
tail -f logs/trade_$(date +%F).log
```

### 오류/경고만 보기
```bash
tail -f logs/trade_$(date +%F).log | rg "ERROR|WARNING|API 에러|no_authorization_ip"
```

### API 인증 확인 (주문 없이)
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

## 4) .env 핵심 설정 예시

### 모의/실거래
```env
BOT_DRY_RUN=true
BOT_DRY_RUN=false
```

### 멀티코인
```env
UPBIT_MARKET=KRW-BTC
UPBIT_MARKETS=KRW-BTC,KRW-ETH,KRW-XRP
```

### 리스크/진입 제어
```env
BUY_RATIO_PCT=0.30
MIN_KRW_RESERVE=5000
STOP_LOSS_PCT=0.03
TRAILING_STOP_PCT=0.02
MIN_NET_PROFIT_PCT=0.00
MAX_CHASE_PCT=0.004
MAX_POSITIONS=0
COOLDOWN_SECONDS=120
MAX_CONSECUTIVE_BUY_SIGNALS=5
```

### GPT 리뷰 에이전트(선택)
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

## 5) 권장 실행 순서
1. `./venv/bin/python main.py --mode backtest`
2. `./venv/bin/python main.py --mode validate`
3. `./venv/bin/python main.py --mode trade --strategy "돌파 + 거래량 증가"` (먼저 `BOT_DRY_RUN=true`)
4. 종료는 `Ctrl+C` (END 스냅샷 기록)
5. `./venv/bin/python main.py --mode review --date $(date +%F)`
