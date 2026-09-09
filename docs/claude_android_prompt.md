# Claude 병행 작업용 프롬프트

아래 내용을 Claude에게 그대로 전달한다. Codex와 같은 저장소를 동시에 수정한다면 반드시
별도 Git 브랜치 또는 worktree에서 작업하게 한다.

---

`C:\Temp\DataTransfer`의 QR-Bridge 프로젝트에서 Android 검증 앱의 **프로토콜 코어와
JVM 단위 테스트**를 구현해 주세요.

## 협업 범위

Codex가 다음 영역을 병행 작업 중입니다.

- Android Gradle 앱 기본 구조
- Jetpack Compose UI와 ViewModel
- 영상 선택 및 `MediaExtractor`/`MediaCodec` 프레임 디코딩
- ML Kit 다중 QR 인식
- 전체 파이프라인 통합

따라서 아래 파일은 수정하지 마세요.

- 기존 Python 코드 전체
- `docs/` 전체
- 저장소 루트의 Gradle 설정 파일
- Android UI, ViewModel, 영상, 카메라, ML Kit 관련 코드

당신의 작업 범위는 Android에서 사용할 수 있는 **Android API 비의존 순수 Kotlin
프로토콜 라이브러리**와 테스트입니다. 별도 브랜치 `claude/android-protocol`에서
작업하고, 완료 후 변경 파일 목록과 커밋 해시를 알려 주세요. 기존 변경사항을 reset,
revert, overwrite하지 마세요.

## 먼저 읽을 파일

구현 전에 다음 파일을 읽고 실제 동작을 기준으로 삼으세요.

- `docs/android_validator_design.md`
- `docs/packet_spec.md`
- `common/qr_wire.py`
- `common/lt_wrapper.py`
- `common/hash_verify.py`
- `common/test_lt.py`
- `test_sample.py`

문서와 Python 코드가 다르면 현재 Python 코드의 실제 동작을 우선하고, 차이를 결과 보고에
기록하세요. Python 패킷 포맷이나 알고리즘을 임의로 개선하거나 QRT3로 변경하지 마세요.

## 구현 목표

패키지 이름은 `com.metaminer.qrbridge.protocol`을 사용하세요. Codex가 만든 Android
프로젝트 구조가 이미 있다면 그 안의 독립 `protocol` 모듈에 구현하세요. 아직 구조가
없다면 병합하기 쉬운 `android-validator/protocol-src/` 아래에 표준 Kotlin source-set
형식으로 작성하되, 루트 `settings.gradle(.kts)`나 앱 모듈 파일은 만들거나 수정하지
마세요.

필요한 주요 클래스와 책임은 다음과 같습니다. 이름은 Kotlin 관례에 맞게 조금 조정해도
되지만 책임을 섞지 마세요.

1. `LtPacket`
   - `seq: Long`, `seed: Long`, `data: ByteArray`, `totalK: Int`,
     `fileHash: ByteArray`, `filename: String`
   - Python의 uint32 값을 부호 없이 보존할 것

2. `Qrt2PacketParser`
   - QR scanner가 반환한 Base64 문자열 또는 ASCII bytes를 입력받음
   - Base64 해제 → XOR 화이트닝 해제 → big-endian QRT2 파싱
   - magic, 전체 길이, filename length, data length, UTF-8을 엄격히 검증
   - trailing bytes 허용 여부는 현재 Python `unpack_packet()` 동작과 동일하게 할 것
   - 파싱 오류는 앱에서 분류할 수 있는 명시적 예외/결과 타입으로 반환

3. `PythonRandomCompat`
   - CPython `random.Random`과 현재 프로젝트가 사용하는 범위에서 정확히 호환
   - MT19937 seeding
   - `getrandbits(8)`
   - `random()`
   - `sample(range(k), degree)`
   - Kotlin `Random`이나 Java `Random`으로 대체하지 말 것
   - CPython 버전에 따라 `sample()` 구현이 달라질 수 있으므로, 이 프로젝트가 실행되는
     Python에서 생성한 골든 벡터를 기준으로 현재 동작을 고정할 것

4. `RobustSolitonDistribution`
   - `common/lt_wrapper.py`와 같은 `c=0.1`, `delta=0.05`
   - 누적 확률과 degree 선택 결과가 Python 구현과 일치
   - 작은 `k`, degree 경계값을 처리

5. `LtDecoder`
   - systematic 패킷과 parity 패킷 지원
   - seed에서 degree 및 source block indices 재현
   - XOR 방정식 기반 peeling/Belief Propagation
   - 중복 `seq`는 결과를 훼손하지 않도록 무시 또는 명시적으로 보고
   - 패킷 순서가 뒤섞여도 정상 동작
   - 첫 8-byte big-endian 원본 길이 헤더를 읽고 padding 제거
   - 모든 블록이 모였다는 사실만으로 성공 처리하지 말고 SHA-256까지 검증
   - `total_k`, hash, 데이터 블록 길이가 다른 스트림 혼합을 거부

6. `Sha256Verifier`
   - 표준 `MessageDigest("SHA-256")` 사용
   - 예상 raw 32-byte digest와 constant-time 방식으로 비교
   - 성공 결과에 복원 bytes와 계산된 digest를 제공

## 입력 제한과 안정성

영상에서 얻은 QR은 신뢰할 수 없는 입력입니다. 합리적인 기본 제한을 상수 또는 설정값으로
두고 테스트하세요.

- 최대 QR payload 크기
- 최대 filename byte length
- 최대 `total_k`
- 최대 block/data length
- 최대 pending equation 수

배열 크기 계산에서 정수 overflow가 발생하지 않게 하세요. filename은 데이터로만 반환하고
파일 경로로 사용하거나 파일을 쓰지 마세요. 라이브러리에는 Android Context, UI 및 파일
저장 로직을 넣지 마세요.

## 골든 벡터

Kotlin 구현 전에 현재 저장소의 Python 코드를 호출하는 생성 스크립트를 작성해 다음 fixture를
만드세요. 생성 결과는 작고 결정적인 텍스트(JSON 또는 hex) 파일로 테스트 리소스에
커밋하세요.

- QRT2 pack 전/후 bytes와 Base64 문자열
- ASCII/한글 파일명 패킷
- 여러 seed의 `getrandbits(8)` 시퀀스
- 여러 seed의 `random()` 값
- `sample(range(k), degree)` 결과: 작은 k와 큰 k, 다양한 degree
- systematic/parity 패킷의 source indices
- 작은 원본 파일의 전체 LT 패킷 집합과 SHA-256

fixture 생성용 Python 스크립트는 제품 코드와 분리하세요. 테스트가 실행될 때 Python을
요구하지 않도록 생성된 fixture를 읽어 검증해야 합니다.

특히 화이트닝 키스트림은 `random.Random(0x51515151).getrandbits(8)` 결과와 바이트 단위로
일치해야 합니다. LT parity 선택은 `random.Random(seed)`, robust Soliton degree 샘플링,
`sample(range(k), degree)` 호출 순서까지 같아야 합니다.

## 테스트 요구사항

JUnit 기반 JVM 테스트를 작성하세요. 최소한 다음을 검증해야 합니다.

- Python 골든 벡터와 난수 결과 완전 일치
- 정상 QRT2 패킷 파싱
- 잘못된 Base64, magic, 잘린 헤더/filename/data 거부
- 한글 및 빈 filename 경계 동작
- systematic-only 복원
- parity가 포함된 복원
- 패킷 누락률 0%, 10%, 30% 시나리오
- 중복 및 무작위 순서
- 서로 다른 stream/hash/total_k 혼합 거부
- 잘못된 최종 SHA-256 실패
- 비정상적으로 큰 길이/`total_k` 입력 거부

누락률 테스트는 운 좋게 성공하는 임의 테스트가 되지 않도록 seed를 고정하고, 충분한 parity
패킷을 제공해 결정적으로 통과하도록 하세요. 단순히 구현과 같은 로직으로 예상값을 다시
계산하는 테스트는 피하고 Python fixture의 고정 예상값을 사용하세요.

## 의존성과 코드 품질

- 프로토콜 모듈은 Kotlin/JDK 표준 라이브러리와 JUnit 외 런타임 의존성을 추가하지 마세요.
- QR 이미지 인식, ML Kit, Compose, CameraX는 이 모듈에 넣지 마세요.
- public API에 KDoc을 작성하고, unsigned/big-endian 변환 의도를 명시하세요.
- 실패를 삼키지 말고 호출자가 원인을 표시할 수 있는 오류 타입을 제공하세요.
- 대형 파일 전체의 불필요한 복사를 줄이되, 먼저 정확성과 Python 호환성을 증명하세요.

## 검증 및 완료 보고

가능한 모든 JVM 테스트를 실행하세요. Android/Gradle 환경 부재로 실행하지 못한 명령이
있으면 성공했다고 가정하지 말고 정확한 원인과 실행 예정 명령을 기록하세요.

완료 보고에는 다음을 포함하세요.

1. 변경 파일 목록
2. 구현한 public API 요약
3. 실행한 테스트 명령과 결과
4. Python과 완전히 호환됨을 증명한 골든 벡터 범위
5. 남은 위험 또는 Codex가 통합할 때 필요한 사항
6. 브랜치 이름과 커밋 해시

작업 범위를 벗어난 문제가 발견되면 코드를 넓게 수정하지 말고 보고만 하세요.

---

## 병합 시 Codex 확인사항

Claude 결과를 병합할 때 Codex는 다음을 확인한다.

- fixture가 실제 저장소의 Python 코드로 생성됐는지
- CPython `sample()` 분기까지 테스트됐는지
- QRT2 parser가 ML Kit의 `rawValue` 입력 형태와 연결 가능한지
- `LtDecoder`의 진행 상태를 ViewModel에서 관찰할 수 있는지
- 대형 `total_k` 입력으로 메모리 고갈이 가능한지
- SHA-256 성공 전에는 UI가 `검증 성공`으로 전환되지 않는지
