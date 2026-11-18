# 대시보드 (Next.js)

봇이 쓰는 `../state/`·`../logs/` 파일을 서버 쪽 라우트(`app/api/*`)가 직접 읽어 보여 줍니다. 탭: 봇 전환(업비트 에이전트 코인별), 하이퍼리퀴드 실계좌·연습용·AI 연습 계좌, 에이전트 상황실.

```bash
npm install
npm run build && npx next start -H 127.0.0.1 -p 3000
```

- **반드시 `-H 127.0.0.1` 로 엽니다.** `/api/command` 가 봇에 주문 명령을 보낼 수 있습니다(JSON 본문·같은 출처만 받음).
- 다른 폴더의 기록으로 띄우려면 `DT_STATE_DIR`·`DT_LOG_DIR` 환경변수를 줍니다(화면 확인용).
- 화면 구성과 각 카드가 읽는 파일은 `../docs/HL_AGENT.md`, `../CLAUDE.md` 에 있습니다.
