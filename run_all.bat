@echo off
cd /d "%~dp0"
call venv\Scripts\activate.bat
python -m src.weather.ingestion
python -m src.air_quality.ingestion
rem python -m src.traffic.ingestion