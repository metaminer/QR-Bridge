**UI 구조 설계 (내가 직접 확정)**

```
ui/
├── __init__.py
├── app.py          ← 탭 메인 프레임 (ttk.Notebook)
├── sender_tab.py   ← 송신 탭 (파일 선택·설정·슬라이드쇼 제어)
├── receiver_tab.py ← 수신 탭 (동영상 입력·디코딩 진행·해시 결과)
└── widgets.py      ← 공통 위젯 (파일 선택 행, 로그 패널, 진행바)
```

---

**분담표**

| 담당 | 작업 | 이유 |
|------|------|------|
| **클로드 (나)** | `ui/app.py` · `ui/sender_tab.py` 구현, `test_sample.py` 통합 테스트 | 기존 `SenderApp` · `encode.py` 흐름을 가장 잘 파악하고 있음 |
| **@코덱스** | `ui/receiver_tab.py` · `ui/widgets.py` 구현, `sender/display.py` 스트리밍 리팩토링 | 수신 디코딩 로직(`decode_video.py`)과 스레드 분리가 핵심 — 코드 생성 강점 활용. 현재 `SenderApp`이 전 프레임을 메모리에 올리는 구조는 반드시 on-the-fly 방식으로 수정 필요 |
| **@로컬LLM** | `ui/__init__.py` 생성, `docs/readme.md` UI 실행 안내 섹션 추가, 기존 소스 파일 docstring 보완 | 입출력 형식이 명확한 단순 반복 작업만 배정 |