# 실행 명령어 모음

## 기본 실행

### 백테스트
```bash
./venv/bin/python main.py --mode backtest
```

### 검증
```bash
./venv/bin/python main.py --mode validate
```

### 리뷰 (오늘 로그)
```bash
./venv/bin/python main.py --mode review
```

### 리뷰 (특정 날짜)
```bash
./venv/bin/python main.py --mode review --date 2026-03-25
```

## 전략별 실시간 실행 명령어

### 레짐 적응형 (추세+평균회귀)
```bash
./venv/bin/python main.py --mode trade --strategy "레짐 적응형 (추세+평균회귀)"
```

### RSI 과매도/과매수
```bash
./venv/bin/python main.py --mode trade --strategy "RSI 과매도/과매수"
```

### 이동평균 크로스
```bash
./venv/bin/python main.py --mode trade --strategy "이동평균 크로스"
```

### RSI + 이동평균 필터
```bash
./venv/bin/python main.py --mode trade --strategy "RSI + 이동평균 필터"
```

### 볼린저밴드 평균회귀
```bash
./venv/bin/python main.py --mode trade --strategy "볼린저밴드 평균회귀"
```

### 돌파 + 거래량 증가
```bash
./venv/bin/python main.py --mode trade --strategy "돌파 + 거래량 증가"
```

## 로그/상태 확인

### 실시간 로그 보기
```bash
tail -f logs/trade_$(date +%F).log
```

### 오류/경고 필터
```bash
tail -f logs/trade_$(date +%F).log | rg "ERROR|WARNING|API 에러|no_authorization_ip"
```

### 인증 확인 (BUY 없어도 API 확인 가능)
```bash
./venv/bin/python - <<'PY'
from upbit_client import UpbitClient, UpbitClientError
c = UpbitClient(dry_run=True)
try:
    c.get_accounts()
    print("AUTH_OK")
except UpbitClientError as e:
    print("AUTH_FAIL", e)
PY
```

## .env 설정 예시

### 모의/실거래 전환
```env
BOT_DRY_RUN=true   # 모의거래
BOT_DRY_RUN=false  # 실거래
```

### 멀티 코인
```env
UPBIT_MARKET=KRW-BTC
UPBIT_MARKETS=KRW-BTC,KRW-ETH,KRW-XRP
```

### 추가매수 허용 개수
```env
MAX_POSITIONS=5
```

## 수익률 분석 정확히 받는 순서 (스냅샷 기반)
1. 트레이드 실행
```bash
./venv/bin/python main.py --mode trade --strategy "레짐 적응형 (추세+평균회귀)"
```
2. 종료는 반드시 `Ctrl+C` (END 스냅샷 기록)
3. 리뷰 실행
```bash
./venv/bin/python main.py --mode review --date $(date +%F)
```
