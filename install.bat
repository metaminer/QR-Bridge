@echo off
cd /d %~dp0
echo [install] offline packages...
C:\Python314\python.exe -m pip install --no-index --find-links=wheels zxing-cpp Pillow lt-code pyzbar
if errorlevel 1 (
    echo [error] core package install failed. check wheels\*.whl
    pause
    exit /b 1
)

echo [install] optional: opencv-python (needed by receiver\decode_video.py)...
C:\Python314\python.exe -m pip install --no-index --find-links=wheels opencv-python
if errorlevel 1 (
    echo [warn] opencv-python wheel not found in wheels\ - skipped. Install it separately before running the receiver.
)

echo [done]
pause
