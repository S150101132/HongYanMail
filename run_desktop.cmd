@echo off
cd /d "%~dp0"
rem Sangfor endpoint protection on this PC lets only the installed Python write under D:\work
rem (the task folder of the task center); the copy in .venv is refused. So run the installed
rem interpreter the venv was made from, with the venv's packages.
set "BASE="
for /f "tokens=1,* delims==" %%a in ('findstr /b /c:"home" "%~dp0.venv\pyvenv.cfg"') do set "BASE=%%b"
if defined BASE set "BASE=%BASE:~1%"
if defined BASE if exist "%BASE%\pythonw.exe" (
  set "PYTHONPATH=%~dp0.venv\Lib\site-packages"
  "%BASE%\pythonw.exe" -m coremail_desktop %*
  goto :eof
)
"%~dp0.venv\Scripts\pythonw.exe" -m coremail_desktop %*
