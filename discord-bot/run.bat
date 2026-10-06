@echo off
REM Convenience launcher for Windows.
REM Creates a .env from the example on first run, then starts the bot.

cd /d "%~dp0"

if not exist ".env" (
    copy ".env.example" ".env" >nul
    echo Created .env - paste your bot token into it now.
    echo Opening .env...
    notepad ".env"
    echo.
    echo Press any key once you have saved the token.
    pause >nul
)

if not exist "data" mkdir "data"

echo Starting bot...
python bot.py

echo.
echo Bot stopped. Errors above if there are any.
pause