@echo off
cd /d C:\Users\SUSHANT\Desktop\TRADING_BOT
.\.venv\Scripts\python.exe -u scripts\sweep_prod.py --skip-existing > logs\sweep_prod_full.log 2>&1
