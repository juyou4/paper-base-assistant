@echo off
cd /d "%~dp0"
where py >nul 2>&1
if errorlevel 1 goto use_python
py -3 app.py --open %*
goto done
:use_python
python app.py --open %*
:done
if errorlevel 1 pause
