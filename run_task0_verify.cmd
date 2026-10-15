@echo off
rem Task 0 certificate verification without ever putting the password on a
rem command line: the password is entered in a local masked dialog.
setlocal
cd /d "%~dp0"
set PYTHONPATH=%~dp0
"%~dp0.venv\Scripts\python.exe" -u "%~dp0runtime\task0_verify.py" ^
  --pfx "D:\证件\王标-certificate.pfx" ^
  --envelope "%~dp0邮箱发送测试.eml" ^
  --report "%~dp0analysis\task0-real-decryption-result.json" ^
  --password-dialog
echo.
echo Exit code: %ERRORLEVEL%
pause
endlocal
