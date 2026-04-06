# 업비트 AI 자율 자동매매 시스템

AI가 최근 캔들/지표를 직접 해석해 매수·매도·관망을 판단하고, 리스크 규칙으로 안전장치를 거는 업비트 현물 트레이딩 프로젝트입니다.

## 핵심 원칙
- 실주문 판단은 `AI 자율 매매` 전략의 `BUY/SELL/HOLD` 신호를 기본으로 사용
- 손절/트레일링/순이익 기준 등 리스크 규칙은 항상 우선
- 규칙 기반 전략도 병행 사용 가능

## 프로젝트 구조
```text
trade/
├── bot/
│   ├── backtester.py
│   ├── config.py
│   ├── indicators.py
│   ├── llm_gate.py
│   ├── logger.py
│   ├── review_agent.py
│   ├── risk_manager.py
│   ├── strategies.py
│   ├── trader.py
│   └── upbit_client.py
├── data/
├── docs/
│   ├── COMMANDS.md
│   └── PROJECT_STRUCTURE.md
├── logs/
├── reports/
├── main.py
├── dashboard.py
├── .env.example
├── COMMANDS.md
└── requirements.txt
```

## 빠른 시작
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

`.env`에서 최소 항목 설정:
- `UPBIT_ACCESS_KEY`, `UPBIT_SECRET_KEY`
- `BOT_DRY_RUN=true` (처음엔 반드시 모의거래)
- `UPBIT_MARKET` / `UPBIT_MARKETS`

## 실행 모드
```bash
./venv/bin/python main.py --mode backtest
./venv/bin/python main.py --mode validate
./venv/bin/python main.py --mode trade --strategy "AI 자율 매매"
./venv/bin/python main.py --mode trade --strategy "1분 생존형 추세·눌림"
./venv/bin/python main.py --mode review --date 2026-03-28
./venv/bin/python -m streamlit run dashboard.py
```

상세 명령어는 [docs/COMMANDS.md](/Users/juno/Downloads/trade/docs/COMMANDS.md) 참고.

## 현재 전략 목록
- AI 자율 매매
- 1분 생존형 추세·눌림
- 레짐 적응형 (추세+평균회귀)
- RSI 과매도/과매수
- 이동평균 크로스
- RSI + 이동평균 필터
- 볼린저밴드 평균회귀
- 돌파 + 거래량 증가

## 운영 메모
- 주문 루프는 빠르게 돌더라도 신호 계산은 확정봉 기준으로 처리됨
- 매수는 최소주문금액(5,000 KRW) 미만이면 사전 차단됨
- 리뷰 리포트는 `reports/daily_review_*.md`로 저장됨

## 선택 기능
- GPT 리뷰 제안: `OPENAI_API_KEY`, `OPENAI_MODEL`
- AI 자율 매매: `AI_TRADER_ENABLED=true` (기본)
- LLM 매수/매도 게이트: `LLM_GATE_ENABLED=true` (규칙 전략 운용 시)

## 주의
- 자동매매는 원금 손실 위험이 있습니다.
- 실거래 전 `DRY_RUN`으로 충분히 검증하세요.
