# 명령어 안내

실행 명령어 문서는 아래로 이동했습니다.

- [docs/COMMANDS.md](/Users/juno/Downloads/trade/docs/COMMANDS.md)

자주 쓰는 실행:

```bash
./venv/bin/python main.py --mode backtest
./venv/bin/python main.py --mode validate
./venv/bin/python main.py --mode trade                           # 기본: 일봉 SMA50 추세
./venv/bin/python main.py --mode trade --strategy "AI 자율 매매"   # 미검증
./venv/bin/python -m streamlit run dashboard.py
./venv/bin/python test_backtest_parity.py   # 백테스트 == 실거래 패리티 점검
```
