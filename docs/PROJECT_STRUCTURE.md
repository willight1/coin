# 프로젝트 구조

```text
trade/
├── bot/                 # 핵심 트레이딩 로직(전략/리스크/클라이언트/리뷰)
├── data/                # 로컬 데이터(선택)
├── docs/                # 운영/명령어/구조 문서
├── logs/                # 실행 로그(자동 생성)
├── reports/             # 백테스트/리뷰 리포트(자동 생성)
├── dashboard.py         # Streamlit 대시보드 진입점
├── main.py              # CLI 진입점
├── README.md
├── COMMANDS.md          # 빠른 링크/요약
└── requirements.txt
```

## 원칙

- 실행 진입점은 루트(`main.py`, `dashboard.py`) 유지
- 핵심 코드는 `bot/`에만 배치
- 문서는 `docs/`에 모아서 관리
- 로그/리포트는 런타임 산출물로 취급
