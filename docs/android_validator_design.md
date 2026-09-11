# Android QR 촬영 검증 앱 설계안

## 1. 목적과 범위

송신 PC 화면의 QR 슬라이드쇼를 스마트폰으로 촬영한 뒤, PC로 영상을 옮기기 전에
스마트폰에서 해당 영상만으로 파일이 완전히 복원되는지 확인한다.

1차 버전(MVP)은 **이미 촬영된 영상 선택 → QR 패킷 수집 → LT 복원 → SHA-256 검증**에
집중한다. 앱에서 직접 촬영하면서 실시간으로 검증하는 기능은 2차 버전으로 둔다.

검증 성공 조건은 QR 인식 개수나 수신률이 아니라 다음 두 조건을 모두 만족하는 것이다.

1. LT 디코더가 모든 원본 블록을 복원한다.
2. 복원 데이터의 SHA-256이 패킷의 `file_hash`와 일치한다.

앱은 성공 시 **PC에서도 같은 프로토콜 구현으로 복원 가능한 촬영본**이라는 결과를
표시한다. 영상 끝까지 처리해도 위 조건을 만족하지 못하면 재촬영이 필요하다고 표시한다.

## 2. 사용자 흐름

1. 사용자가 `영상 선택` 버튼으로 갤러리/파일 앱에서 촬영 영상을 선택한다.
2. 앱이 영상 해상도, 길이, 프레임률을 읽고 검증을 시작한다.
3. 처리 화면에 다음 정보를 실시간 표시한다.
   - 영상 처리 위치와 경과 시간
   - 검사 프레임 수
   - 인식된 QR 수 / 유효 패킷 수 / 중복 패킷 수
   - 복원 블록 수 / `total_k`
   - 발견된 파일명과 예상 SHA-256
4. LT 복원이 끝나는 즉시 SHA-256을 계산한다. 성공하면 남은 영상 처리를 중단한다.
5. 결과 화면을 표시한다.
   - 성공: 파일명, 파일 크기, SHA-256 일치, 검증 시간
   - 불충분: 복원 블록 수, 마지막 유효 QR 시점, 재촬영 안내
   - 오류: 다른 전송의 패킷 혼합, 손상된 패킷, 지원하지 않는 포맷 등의 구체적 원인

복원 파일 저장은 기본 동작에서 제외한다. 검증 과정에서 만든 임시 데이터는 결과 확인
후 삭제한다. 이후 필요하면 `복원 파일 저장` 기능을 선택 옵션으로 추가한다.

## 3. 기술 구성

### 앱 기반

- 언어: Kotlin
- UI: Jetpack Compose, 단일 Activity
- 최소 Android 버전: API 23
- 비동기 처리: Kotlin Coroutines + `Flow`
- 영상 선택: Storage Access Framework의 `ACTION_OPEN_DOCUMENT`
- QR 인식: ML Kit Barcode Scanning의 **번들 모델**, QR 형식만 활성화
- 해시: Android/Java 표준 `MessageDigest("SHA-256")`

번들 ML Kit 모델은 앱 설치 시 함께 들어 있어 인터넷이나 Google Play 모델 다운로드를
기다리지 않고 검증할 수 있다. 이 앱은 한 화면에서 여러 QR을 연속 처리해야 하므로,
단일 코드 촬영 UI를 제공하는 Google Code Scanner 대신 사용자 영상 프레임을 직접
입력할 수 있는 Barcode Scanning API를 사용한다.

### 계층과 주요 컴포넌트

```text
Compose UI
  └─ ValidatorViewModel
      └─ ValidationCoordinator
          ├─ VideoFrameDecoder       MediaExtractor + MediaCodec
          ├─ MultiQrScanner          ML Kit, QR 전용
          ├─ Qrt2PacketParser        Base64/화이트닝/헤더 검증
          ├─ LtDecoder               Soliton + peeling/BP 복원
          ├─ PythonRandomCompat      현재 Python 송신기와 난수열 호환
          └─ Sha256Verifier          최종 무결성 검증
```

`ValidationCoordinator`는 제한된 크기의 채널을 사용해 영상 디코딩과 QR 분석 사이에
backpressure를 건다. UHD 프레임이나 QR 비트맵을 목록으로 모으지 않고, 처리 중인 소수의
프레임만 메모리에 유지한다. 화면을 벗어나거나 취소 버튼을 누르면 전체 파이프라인을
취소하고 디코더와 ML Kit 리소스를 닫는다.

## 4. 영상 및 QR 처리 방식

### 저장 영상 처리

`MediaExtractor`로 영상 트랙을 선택하고 `MediaCodec`으로 프레임을 시간 순서대로
디코딩한다. 임의 시점의 프레임을 매번 새로 찾는 방식은 긴 UHD 영상에서 비용이 크므로
사용하지 않는다.

초기 프레임 샘플링 목표는 송신 FPS 이상이다. 예를 들어 송신이 15 fps이고 촬영이
30 fps이면 우선 모든 촬영 프레임을 순차 처리하되, 처리 속도가 재생 속도를 따라가지
못할 때는 타임스탬프 기준으로 중복 가능성이 높은 프레임을 건너뛴다. 이미 받은 `seq`는
LT 디코더에 다시 넣지 않고 중복 통계만 증가시킨다.

### 동시 1~8개 QR

ML Kit 결과에서 한 프레임의 모든 QR을 수집한다. 각 QR은 독립 패킷이며 화면상의 위치나
홀짝 순서에 의존하지 않는다. 따라서 일부 QR만 인식돼도 정상 패킷은 모두 LT 디코더에
추가한다.

초기 구현은 전체 프레임 다중 QR 탐지를 사용한다. 실제 FHD/UHD 촬영 표본에서 5~8개 QR
인식률이 부족하면 다음 최적화를 순서대로 적용한다.

1. 모니터 화면 사각형을 검출하거나 사용자가 한 번 영역을 지정한다.
2. 송신 레이아웃(1~4개 1행, 5~8개 2행)에 맞춰 관심 영역을 나눈다.
3. 각 영역에 여백을 포함해 QR 탐지를 수행하고 동일 payload를 합친다.

QR 개수를 사용자가 미리 지정하지 않아도 동작하게 하되, 자동 검출이 불안정한 영상에는
`1~8개 레이아웃` 수동 선택을 고급 옵션으로 제공한다.

## 5. QRT2 및 LT 호환성

Android 앱은 현재 PC 송신기가 만드는 QRT2를 그대로 읽어야 한다.

```text
Base64 decode
  → 고정 키스트림 XOR 화이트닝 해제
  → big-endian QRT2 헤더 파싱
  → 패킷/스트림 일관성 검사
  → LT 디코더에 추가
```

QRT2 필드는 `seq`, `seed`, `total_k`, raw 32-byte `file_hash`, UTF-8 파일명,
`data`이며 자세한 바이트 배열은 `docs/packet_spec.md`를 단일 명세로 사용한다.

현재 프로토콜에는 언어 이식 시 주의할 점이 있다. 화이트닝과 LT parity 블록 선택이
Python `random.Random`의 결과에 의존한다. Kotlin의 일반 난수 생성기를 사용하면 같은
`seed`에서도 다른 블록을 선택해 복원이 실패한다. 따라서 MVP에는 다음을 구현한다.

- MT19937 상태 전이 및 Python `getrandbits(8)` 호환
- Python `random()` 호환 부동소수점 생성
- Python `sample(range(k), degree)`와 같은 선택 결과
- robust Soliton 분포(`c=0.1`, `delta=0.05`)와 동일한 누적 확률 계산
- systematic 패킷(`seq < total_k`) 및 parity 패킷 처리
- peeling/Belief Propagation 복원과 8-byte 원본 길이 헤더 제거

이 부분은 추측으로 이식하지 않고 Python 코드에서 생성한 골든 벡터로 바이트 단위 검증한다.
장기적으로는 Python 구현 세부사항에 의존하지 않는 고정 PRNG와 명확한 정수 연산 규칙을
QRT3에 정의하는 것이 안전하다. QRT3 도입 시 QRT2 읽기 호환은 유지한다.

### 스트림 일관성과 입력 제한

첫 유효 패킷으로 활성 스트림의 `file_hash`, `total_k`, 파일명, 청크 크기를 결정하고,
일치하지 않는 패킷은 다른 전송의 패킷으로 분리하거나 거부한다. 악성 또는 손상된 영상이
비정상적으로 큰 메모리를 요구하지 않도록 다음 값에 상한을 둔다.

- QR payload 및 `data_len`
- `total_k`와 예상 복원 파일 크기
- 파일명 UTF-8 길이
- 대기 중인 LT 방정식 수

파일명은 화면 표시용으로 정규화하며 경로 구분자를 파일 경로로 해석하지 않는다.

## 6. 상태와 화면

```kotlin
sealed interface ValidationState {
    data object Idle
    data class ReadingMetadata(...)
    data class Scanning(...)
    data class Recovering(...)
    data class Verifying(...)
    data class Success(...)
    data class Insufficient(...)
    data class Error(...)
}
```

화면은 `영상 선택`, `검증 진행`, `결과` 세 영역으로 단순하게 구성한다. 진행률은 영상
타임스탬프 기준 진행률과 LT 블록 복원률을 함께 표시한다. 패킷 수만으로는 성공 여부를
판단할 수 없으므로 `유효 패킷 / 목표 패킷`은 보조 정보로 표시한다.

`Insufficient` 결과에는 단순 실패 문구 대신 다음 촬영 판단 정보를 제공한다.

- 복원률과 부족 블록 수
- 영상에서 마지막 QR이 검출된 시각
- QR이 거의 검출되지 않은 경우: 초점, 흔들림, 노출, 화면 점유율 점검 안내
- 일부만 부족한 경우: 같은 슬라이드쇼를 더 오래 촬영하라는 안내

## 7. 테스트 계획

### JVM 단위 테스트

- QRT2 헤더/파일명/data 파싱과 잘린 패킷 거부
- 화이트닝 전후 Python 골든 벡터 일치
- Python `getrandbits`, `random`, `sample` 시퀀스 일치
- robust Soliton degree 및 선택 인덱스 일치
- LT 복원: 누락률 0%, 10%, 30%, 중복과 순서 뒤섞임
- SHA-256 성공/실패
- 서로 다른 파일의 패킷 혼합 거부

### Android 계측 테스트

- 송신 QR 1, 2, 4, 8개 표본 영상
- FHD 60 fps와 UHD 30 fps 표본
- 초반/중간 프레임 누락, 중복 프레임, 짧게 끊긴 영상
- 흔들림, 원근 왜곡, 밝기 변화가 있는 영상
- 큰 영상에서 메모리 사용량이 일정 범위로 유지되는지 확인

골든 데이터와 테스트 영상은 프로젝트 규칙에 맞춰 `working/`에서 생성하되, 저장소에는
용량이 작은 고정 fixture만 포함한다.

## 8. 구현 순서

1. `android-validator/` Gradle 프로젝트와 Compose 기본 화면 구성
2. QRT2 파서, Python 호환 난수기, LT 디코더 및 골든 벡터 테스트
3. 영상 선택과 `MediaExtractor`/`MediaCodec` 순차 프레임 공급기
4. ML Kit 다중 QR 인식과 중복 제거
5. 전체 검증 파이프라인, 진행/취소/결과 화면 연결
6. 실제 1~10 QR 영상으로 인식률 및 메모리 측정, ROI 최적화

**실시간 촬영 검증은 범위에서 제외한다.** 한 번 구현했다가 제거했다 — 기기별로 Preview,
VideoCapture, ImageAnalysis 동시 조합의 지원 해상도가 제각각이고, 특히 폰 기본 카메라 앱이
내주는 UHD 60fps 최고 비트레이트를 앱 내 녹화로는 재현하기 어려워 실제 촬영 조건과
검증 조건이 어긋났다. 사용자는 기본 카메라 앱으로 촬영하고, 이 앱은 그 영상 파일을
검증하는 역할만 맡는다. 그 덕에 앱은 카메라 권한도, CameraX 의존성도 필요 없다.

## 9. 완료 기준

- 비행기 모드에서도 설치된 앱으로 검증할 수 있다.
- 현재 Python 송신기의 QRT2 영상을 Android에서 복원하고 SHA-256 일치를 표시한다.
- 한 프레임에 1~8개 QR이 있어도 모두 수집한다.
- 성공 시 영상 끝까지 기다리지 않고 종료한다.
- 실패 시 재촬영 여부를 사용자가 명확히 판단할 수 있다.
- UHD 장시간 영상에서도 전체 프레임/QR 이미지를 메모리에 쌓지 않는다.

## 참고 자료

- [ML Kit Android Barcode Scanning](https://developers.google.com/ml-kit/vision/barcode-scanning/android)
- [Android CameraX architecture](https://developer.android.com/media/camera/camerax/architecture)
- [Android CameraX image analysis](https://developer.android.com/media/camera/camerax/analyze)
- [Android MediaExtractor API](https://developer.android.com/reference/android/media/MediaExtractor)
- [Android MediaCodec API](https://developer.android.com/reference/android/media/MediaCodec)
