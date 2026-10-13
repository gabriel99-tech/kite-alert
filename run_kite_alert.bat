@echo off
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
python kite_alert.py --once
