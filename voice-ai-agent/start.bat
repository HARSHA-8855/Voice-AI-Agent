@echo off
echo.
echo  2Care Voice Agent - Starting all services...
echo.

echo [1/5] Confirming Redis and Postgres are active on Docker...
ping -n 3 127.0.0.1 >nul

echo [2/5] Starting FastAPI backend on port 8000...
start "FastAPI Backend" cmd /k "cd /d %~dp0backend && python -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload"
ping -n 6 127.0.0.1 >nul

echo [3/5] Starting Node.js WebSocket Gateway on port 3000...
start "WS Gateway" cmd /k "cd /d %~dp0gateway && npm run dev"
ping -n 4 127.0.0.1 >nul

echo [4/5] Starting Celery Worker...
start "Celery Worker" cmd /k "cd /d %~dp0backend && python -m celery -A scheduler.campaigns worker --loglevel=info -P solo"
ping -n 3 127.0.0.1 >nul

echo [5/5] Starting Celery Beat Scheduler...
start "Celery Beat" cmd /k "cd /d %~dp0backend && python -m celery -A scheduler.campaigns beat --loglevel=info"
ping -n 3 127.0.0.1 >nul

echo.
echo  All 5 services started!
echo  - Backend API:  http://localhost:8000
echo  - API Docs:     http://localhost:8000/docs
echo  - Health Check: http://localhost:8000/health
echo  - Metrics:      http://localhost:8000/metrics
echo  - Demo UI:      Run: cd demo ^&^& python -m http.server 8080
echo                  Then open: http://localhost:8080
echo.
echo  Serving demo on http://localhost:8080...
start "Demo UI" cmd /k "cd /d %~dp0demo && python server.py"
echo.
pause
