@echo off
rem Uninstall Hongyan on machines whose policy forbids programs from writing %TEMP%:
rem the uninstaller copies itself into TEMP before it runs. Installed beside unins000.exe.
rem The uninstaller also deletes this script, so the last line uses "(goto) 2>nul":
rem it ends the batch context (cmd stops reading this file) while the rest of the
rem already-parsed line still runs; %...% values were expanded when it was parsed.
set "HY_APPDIR=%~dp0"
set "HY_TEMP=%~dp0uninstall-temp"
if not exist "%HY_TEMP%" mkdir "%HY_TEMP%"
cd /d "%~dp0.."
rem unins000.exe hands over to a copy in TEMP and returns at once; that copy cannot
rem delete itself, so a detached cmd removes the leftovers a few seconds later.
(goto) 2>nul & set "TEMP=%HY_TEMP%" & set "TMP=%HY_TEMP%" & start "" /wait "%HY_APPDIR%unins000.exe" %* & start "" /min cmd /c "ping -n 16 127.0.0.1 >nul & rmdir /s /q "%HY_TEMP%" & rmdir "%HY_APPDIR%.""
