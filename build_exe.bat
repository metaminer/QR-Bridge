@echo off
cd /d %~dp0
echo [build] installing pyinstaller (offline, from wheels\)...
C:\Python314\python.exe -m pip install --no-index --find-links=wheels pyinstaller
if errorlevel 1 (
    echo [error] pyinstaller install failed. check wheels\*.whl
    pause
    exit /b 1
)

echo [build] running pyinstaller on ui\app.py...
C:\Python314\python.exe -m PyInstaller --noconfirm --onefile --windowed --name QRBridge --paths . ui\app.py
if errorlevel 1 (
    echo [error] pyinstaller build failed.
    pause
    exit /b 1
)

echo [done] dist\QRBridge.exe is ready. It is fully standalone - copy it
echo        anywhere (including the air-gapped PC) and run it directly,
echo        no Python install or wheels\ needed on the target machine.
pause
