@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "PROJECT_DIR=%~dp0"
set "PACKAGE_ID=com.metaminer.qrbridge.validator"
set "MAIN_ACTIVITY=%PACKAGE_ID%/.MainActivity"
set "OUTPUT_APK=%PROJECT_DIR%app\build\outputs\apk\debug\app-debug.apk"

:: Optional first argument: adb device serial.
set "DEVICE_SERIAL=%~1"

:: Prefer configured SDK paths, then use the SDK installed on this PC.
if defined ANDROID_SDK_ROOT (
    set "SDK_DIR=%ANDROID_SDK_ROOT%"
) else if defined ANDROID_HOME (
    set "SDK_DIR=%ANDROID_HOME%"
) else (
    set "SDK_DIR=C:\AndroidSDK"
)
set "ADB_EXE=%SDK_DIR%\platform-tools\adb.exe"
set "ANDROID_HOME=%SDK_DIR%"
set "ANDROID_SDK_ROOT=%SDK_DIR%"
set "GRADLE_USER_HOME=%PROJECT_DIR%..\working\gradle-home"

:: AGP requires JDK 17 or newer. Prefer this PC's known JDK 17 installation.
if exist "C:\Program Files\Java\jdk-17\bin\java.exe" (
    set "JAVA_HOME=C:\Program Files\Java\jdk-17"
) else if not defined JAVA_HOME (
    echo ERROR: JDK 17 was not found. Set JAVA_HOME to a JDK 17 or newer installation.
    exit /b 1
)

if not exist "%PROJECT_DIR%gradlew.bat" (
    echo ERROR: Gradle Wrapper not found: %PROJECT_DIR%gradlew.bat
    exit /b 1
)
if not exist "%ADB_EXE%" (
    echo ERROR: adb not found: %ADB_EXE%
    exit /b 1
)
if not exist "%SDK_DIR%\platforms\android-35\android.jar" (
    echo ERROR: Android SDK platform 35 is not installed under %SDK_DIR%.
    exit /b 1
)

echo === QR Bridge Android Validator build ===
echo Project: %PROJECT_DIR%
echo SDK:     %SDK_DIR%
echo JDK:     %JAVA_HOME%
echo.

cd /d "%PROJECT_DIR%"

:: --no-daemon prevents a Java Gradle daemon from remaining after the build.
call "%PROJECT_DIR%gradlew.bat" --no-daemon :protocol:test :app:assembleDebug
if errorlevel 1 (
    echo.
    echo Build or test FAILED. Skipping device installation.
    exit /b 1
)

if not exist "%OUTPUT_APK%" (
    echo ERROR: Build succeeded but APK was not found: %OUTPUT_APK%
    exit /b 1
)

echo.
echo Build OK: %OUTPUT_APK%
echo.
echo === Checking connected Android device ===
"%ADB_EXE%" start-server >nul
if errorlevel 1 (
    echo ERROR: Could not start the adb server.
    exit /b 1
)

if defined DEVICE_SERIAL (
    "%ADB_EXE%" -s "%DEVICE_SERIAL%" get-state 1>nul 2>nul
    if errorlevel 1 (
        echo ERROR: Device "%DEVICE_SERIAL%" is not connected or authorized.
        "%ADB_EXE%" devices -l
        exit /b 1
    )
) else (
    set /a DEVICE_COUNT=0
    for /f "skip=1 tokens=1,2" %%A in ('"%ADB_EXE%" devices') do (
        if "%%B"=="device" (
            set /a DEVICE_COUNT+=1
            set "DEVICE_SERIAL=%%A"
        )
    )
    if !DEVICE_COUNT! EQU 0 (
        echo ERROR: No authorized Android device found.
        echo Enable USB debugging, connect the phone, and accept the authorization prompt.
        "%ADB_EXE%" devices -l
        exit /b 1
    )
    if !DEVICE_COUNT! GTR 1 (
        echo ERROR: More than one Android device is connected.
        echo Run this file with the target serial: build_and_install.bat SERIAL
        "%ADB_EXE%" devices -l
        exit /b 1
    )
)

echo Device: %DEVICE_SERIAL%
echo.
echo === Installing debug APK ===
"%ADB_EXE%" -s "%DEVICE_SERIAL%" install -r "%OUTPUT_APK%"
if errorlevel 1 (
    echo.
    echo Install FAILED. Check USB debugging authorization and available device storage.
    exit /b 1
)

echo.
echo === Launching QR Bridge Validator ===
"%ADB_EXE%" -s "%DEVICE_SERIAL%" shell am force-stop "%PACKAGE_ID%"
"%ADB_EXE%" -s "%DEVICE_SERIAL%" shell am start -n "%MAIN_ACTIVITY%"
if errorlevel 1 (
    echo WARNING: APK installed, but the app could not be launched automatically.
    exit /b 1
)

echo.
echo Done. Built, tested, installed, and launched on %DEVICE_SERIAL%.
endlocal
