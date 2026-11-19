@echo off
cd /d "%~dp0"
python -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1
if not errorlevel 1 goto use_python
py -3 -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1
if errorlevel 1 goto no_python
py -3 app.py --open %*
goto done
:use_python
python app.py --open %*
goto done
:no_python
echo Python 3.10+ is required.
pause
exit /b 1
:done
if errorlevel 1 pause
