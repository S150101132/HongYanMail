@echo off
rem Install Hongyan on machines whose policy forbids programs from writing %TEMP%.
rem The installer unpacks itself into TEMP, so point TEMP at a folder beside this
rem script for the duration of the install. The program goes to a Hongyan folder beside
rem this script by default (still changeable in the wizard): on locked-down machines the
rem user profile and drive roots may both be read-only.
setlocal
set "HY_TEMP=%~dp0setup-temp"
if not exist "%HY_TEMP%" mkdir "%HY_TEMP%"
set "TEMP=%HY_TEMP%"
set "TMP=%HY_TEMP%"
set "SETUP="
for %%f in ("%~dp0Hongyan-Setup-*.exe") do set "SETUP=%%~ff"
if not defined SETUP (
  echo Hongyan-Setup-*.exe was not found next to this script.
  pause
  exit /b 1
)
start "" /wait "%SETUP%" /DIR="%~dp0Hongyan-Installed" %*
rmdir /s /q "%HY_TEMP%" 2>nul
endlocal
