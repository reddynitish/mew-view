@echo off
REM MEW-VIEW launcher
cd /d "%~dp0"
call "%~dp0venv\Scripts\activate.bat"
python "%~dp0main.py"
