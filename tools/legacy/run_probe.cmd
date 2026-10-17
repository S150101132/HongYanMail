@echo off
cd /d "%~dp0..\.."
python -m probe --send-self
pause
