@echo off
chcp 65001 >nul
cd /d "%~dp0"
py -3.11 -c "import piexif, PIL, imageio_ffmpeg" 2>nul
if errorlevel 1 py -3.11 -m pip install -r requirements.txt
py -3.11 gui.py
if errorlevel 1 pause
