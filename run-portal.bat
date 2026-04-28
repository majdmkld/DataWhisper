@echo off
cd /d "%~dp0"
echo Starting DataWhisper Portal...
echo Open http://localhost:5050 in your browser
start http://localhost:5050
dotnet run --urls "http://localhost:5050"
pause
