@echo off
rem Start RIBBON with the demonstration database (pyconfer-demo.db), apart from real audits.
rem The first run prepares it with real audits of synthetic batches (about 2 minutes).
rem To prepare it again from scratch, delete pyconfer-demo.db and run this file.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\windows\start.ps1" -Demo %*
pause
