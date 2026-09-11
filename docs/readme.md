# QR Stream Transfer

**망분리·저장매체 차단 환경에서 화면 → 카메라만으로 파일을 전송하는 도구**

---

## 배경

- 인터넷 차단, USB/외장디스크 연결 불가 환경(망분리 PC)
- 유일하게 허용된 채널: **화면 출력** + **스마트폰 카메라 촬영**
- 이 도구는 그 채널만으로 임의 파일을 전송한다

---

## 동작 원리
```
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
```
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
| LT 인코딩/디코딩 | 자체 구현 (`common/lt_wrapper.py`) |
| QR 코드 생성/디코드 | `zxing-cpp` (네이티브 C++ 바인딩 — 아래 참고. 생성: `qrcode`→`segno`→`zxing-cpp`, 디코드: `pyzbar`→`zxing-cpp` 순으로 교체됨) |
| 이미지 처리 | `Pillow` |
| 화면 슬라이드쇼 | `tkinter` (표준 라이브러리) |

설치:
```bash
pip install zxing-cpp Pillow
```

**QR 인코딩 라이브러리 교체 이력**: `sender/encode.py`의 `make_qr_image()`가 QR 하나를
만드는 데(인코딩+이미지 변환) `qrcode` 기준 약 203ms 걸렸는데, 대부분 QR 페이로드가
Base64라 세그먼트 최적화가 무의미한데도 순수 Python Reed-Solomon 연산 자체가
느린 게 원인이었다. 순수 Python 대안들을 실측한 뒤, 네이티브 C++ 확장까지 조사해서
최종적으로 `zxing-cpp`로 교체했다:

| 방식 | QR 1장 생성(인코딩+이미지 변환) | 비고 |
|------|------|------|
| `qrcode` (최초) | 202.9 ms | 순수 Python |
| `cv2.QRCodeEncoder` | 277~381 ms | OpenCV 내장, 새 의존성 불필요하지만 오히려 더 느려 기각 |
| `segno` + numpy 래스터 | 129.9 ms | 순수 Python |
| `segno` + 순수 bytearray 래스터 | 95.0 ms | 순수 Python, 한때 채택(약 2.1배) |
| `qrcodegen` | 295.9 ms | 순수 Python, 기각 |
| **`zxing-cpp`(네이티브 C++, 현재 채택)** | **4.84 ms** | **qrcode 대비 약 42배, segno 대비 약 20배** |

`zxing-cpp`는 Windows용 사전빌드 wheel(`abi3`, Python 3.12+ 전 버전 호환, 추가 pip
의존성 없음, DLL 별도 설치 불필요)이 있어 오프라인 `wheels/` 배포에 적합하다.
실제 QR 이미지 생성 → `pyzbar` 디코드 왕복으로 정확성도 검증했다(그 뒤 수신측
디코더도 아래처럼 `zxing-cpp`로 교체됨).

실사용 시나리오(300KB 파일, 동시 QR 8개, 60fps) 기준 첫 루프 완료 시간:

| 단계 | 첫 루프 시간 |
|------|------|
| `qrcode` (최초) | ~30초 |
| `segno` | 23.95초 |
| `zxing-cpp`(현재) | **3.46초** |

3MB 파일(4610프레임)도 35.8초에 처리되어(QR 1장당 평균 7.77ms, 300KB 테스트와
선형 비례) 메가바이트급 전송도 실용적인 범위에 들어왔다.

**수신측 디코더 교체 (`pyzbar` → `zxing-cpp`)**: `zxing-cpp`가 디코드(`read_barcodes`)도
지원해서, `receiver/decode_video.py`의 QR 디코딩도 `pyzbar`에서 `zxing-cpp`로
교체했다. 압축 영상 프레임(정상/블러/회전/다운스케일 열화 조건)으로 인식률을
비교한 결과 모든 조건에서 `pyzbar`와 동일하거나 더 나았다(다운스케일 열화 조건에서
152장 중 147 vs **152**로 `zxing-cpp`가 5장 더 인식). 디코딩 속도는 동일 912회
호출 기준 24.7초 → **5.97초**(약 4.1배). 실제 mp4 왕복 테스트(`working/test_video.py`)
기준 디코딩 소요시간도 1.1~1.8초 → **0.3~0.4초**로 줄었다. 이제 `pyzbar`는 프로젝트
어디에서도 쓰이지 않는다.

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

## 실행파일(.exe)로 패키징

Python이 설치되지 않은 PC(또는 망분리 PC)에서도 바로 실행할 수 있도록
`ui/app.py`를 `PyInstaller`로 단일 실행파일에 묶을 수 있다.

```bash
build_exe.bat
```

- `wheels/`에 있는 `pyinstaller`(+의존성)를 오프라인 설치한 뒤
  `dist/QRBridge.exe`(단일 파일, 콘솔 창 없음)를 만든다.
- 결과물은 **완전히 독립적**이다 — Python 설치, `wheels/`, 프로젝트 폴더
  전부 없이 `QRBridge.exe` 파일 하나만 복사해서 실행하면 된다(실제로 별도
  폴더에 복사해서 실행까지 확인함). 망분리 PC에는 이 exe 하나만 전달하면
  된다.
- 용량은 약 75MB(`opencv-python`이 큼). 콘솔 없이(`--windowed`) 빌드되므로
  콘솔 로그를 봐야 하면 `build_exe.bat`의 `--windowed`를 `--console`로
  바꿔서 다시 빌드한다.
- `build/`, `dist/`, `*.spec`은 재생성 가능한 산출물이라 git에는 커밋하지
  않는다(`.gitignore` 참고).

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
