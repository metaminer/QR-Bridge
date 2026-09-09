# QR Bridge Android Validator

촬영 영상을 PC로 옮기기 전에 스마트폰에서 QR 패킷의 복원 가능 여부를 확인하는 앱이다.

현재 이 모듈에는 Compose 화면, 영상 선택, 프레임 샘플링, ML Kit 다중 QR 인식과 프로토콜
연결 인터페이스가 들어 있다. QRT2/LT 구현은 `ProtocolSession` 인터페이스로 연결한다.

## 빌드

Android Studio에서 이 폴더를 열거나 Android SDK/JDK 17 환경에서 다음을 실행한다.

```text
gradlew.bat :app:testDebugUnitTest :app:assembleDebug
```

ML Kit의 번들 Barcode Scanning 모델을 사용하므로 설치 후 QR 인식에 네트워크가 필요 없다.

## Windows에서 빌드 후 기기에 설치

USB 디버깅을 활성화한 Android 기기를 연결하고 실행한다.

```text
build_and_install.bat
```

기기가 여러 대 연결돼 있으면 `build_and_install.bat SERIAL`처럼 adb serial을 지정한다.
배치파일은 프로토콜 테스트와 Debug APK 빌드를 수행한 뒤 기존 앱을 갱신 설치하고 실행한다.
