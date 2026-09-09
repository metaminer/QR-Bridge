# QR Stream Transfer

**망분리·저장매체 차단 환경에서 화면 → 카메라만으로 파일을 전송하는 도구**

---

## 배경

- 인터넷 차단, USB/외장디스크 연결 불가 환경(망분리 PC)
- 유일하게 허용된 채널: **화면 출력** + **스마트폰 카메라 촬영**
- 이 도구는 그 채널만으로 임의 파일을 전송한다

---

## 동작 원리

[송신 PC]
파일
 └─ 청크 분할 (≈1 KB/청크)
     └─ LT 인코딩 (Luby Transform, 중복 1.5×)
         └─ 청크별 QR 코드 이미지 생성
             └─ 화면에 슬라이드쇼 표시 (15 fps)

[수신 측]
스마트폰으로 화면 촬영 (동영상)
 └─ 프레임별 QR 디코드
     └─ LT 디코드 → 파일 복원
         └─ SHA-256 해시 검증
---

## 주요 스펙

| 항목 | 값 |
|------|----|
| 프레임 속도 | 15 fps |
| 실효 전송 속도 | 약 12 ~ 16 KB/s |
| 프레임 손실 허용 | 30 ~ 50% |
| QR 용량 (바이너리) | 프레임당 최대 ≈ 2.9 KB |
| 무결성 검증 | SHA-256 |

---

## 기술 스택

| 역할 | 패키지 |
|------|--------|
| LT 인코딩/디코딩 | `lt-code` |
| QR 코드 생성 | `segno` (기존 `qrcode`에서 교체 — 아래 참고) |
| 이미지 처리 | `Pillow` |
| 화면 슬라이드쇼 | `tkinter` (표준 라이브러리) |
| QR 디코드 (수신) | `pyzbar` |

설치:
```bash
pip install lt-code segno Pillow pyzbar
```

**`qrcode` → `segno` 교체 이유**: `sender/encode.py`의 `make_qr_image()`가 QR 하나를
만드는 데(이미지 변환 포함) `qrcode` 기준 약 203ms 걸렸는데, 대부분 QR 페이로드가
Base64라 세그먼트 최적화가 무의미한데도 순수 Python Reed-Solomon 연산 자체가
느린 게 원인이었다. 실측 비교:

| 방식 | QR 1장 생성(인코딩+이미지 변환) |
|------|------|
| `qrcode` | 202.9 ms |
| `segno` + numpy 래스터 | 129.9 ms |
| `segno` + 순수 bytearray 래스터(채택) | **95.0 ms** (약 2.1배 빠름) |

`cv2.QRCodeEncoder`(OpenCV 내장, 새 의존성 불필요)도 시도했지만 오히려 277~381ms로
더 느려서 채택하지 않았다. numpy 기반 래스터화도 순수 `bytearray` 직접 채우기보다
느려서(129.9ms vs 95.0ms) 새 의존성(numpy) 없이 `sender/encode.py`에 직접 구현했다.
실사용 시나리오(300KB 파일, 동시 QR 8개, 60fps) 기준 첫 루프 완료 시간은 26.6초 →
23.95초로 단축됐다(이론적 처리량 한계는 24.8초 → 18.3초로 더 크게 개선되지만
Tkinter 통합 오버헤드가 남아 있어 체감 개선폭은 그보다 작다).

---

## 디렉토리 구조

```
DataTransfer/
├── docs/
│   ├── readme.md          # 이 파일
│   ├── todo.md            # 작업 분할 계획 (이력)
│   └── packet_spec.md     # QR 프레임 바이너리 포맷 명세
├── sender/
│   ├── __init__.py
│   ├── encode.py          # 파일 → LT 인코딩 → QR 프레임 이미지 생성
│   └── display.py         # Tkinter 슬라이드쇼 + CLI 엔트리포인트
├── receiver/
│   ├── __init__.py
│   ├── decode_video.py    # 동영상 → QR 디코드 → LT 복원 + CLI 엔트리포인트
│   └── verify.py          # SHA-256 검증
└── common/
    ├── __init__.py
    ├── lt_wrapper.py      # LT 인코더/디코더 (Soliton 분포, Belief Propagation)
    ├── hash_verify.py     # SHA-256 생성/검증 유틸
    ├── qr_wire.py          # QR 페이로드 직렬화·역직렬화 + XOR 화이트닝 (송/수신 공통, 단일 소스)
    └── test_lt.py          # lt_wrapper 단위 테스트
```

실행:
```bash
python -m sender.display --file <경로> --fps 15 --redundancy 1.5
python -m receiver.decode_video --video <경로> --output <경로>
```

---

## 제약 사항

- **전송 속도**: 1 MB 파일 기준 약 1분 소요
- **조명/초점**: 촬영 환경이 나쁘면 QR 인식률 저하
- **대용량**: 수십 MB 이상은 현실적으로 비실용적
- **컬러 QR (JAB Code)**: 사전 설치 도구 필요 → 이 프로젝트에서 제외

---

## UI 실행

```bash
python -m ui.app
```

- 송신 탭: 파일을 선택해 QR 슬라이드쇼를 표시
- 수신 탭: 촬영 영상을 선택해 파일을 복원

---

## 테스트 규칙

테스트가 생성하는 임시 파일(샘플 바이너리, 복원 결과물 등)은 **시스템 임시 폴더가
아니라 프로젝트 루트의 `working/` 폴더 안에 생성하고, 그 안에서 실행**한다.

- `working/`이 없으면 테스트 코드가 직접 만든다 (`Path("working").mkdir(exist_ok=True)`).
- `test_sample.py`, `common/test_lt.py` 등 새 테스트를 추가할 때도 동일한 규칙을 따른다.
- 결과물을 지우지 않고 남겨서, 실패 시 `working/` 안의 파일을 직접 열어 원인을
  확인할 수 있게 한다(= `tempfile.TemporaryDirectory()`처럼 자동 삭제되는 임시
  디렉토리 사용 금지).
- `working/`은 순수 산출물 폴더이므로 소스 코드를 두지 않는다.