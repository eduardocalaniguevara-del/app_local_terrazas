@echo off
cd /d "%~dp0"

set "PY_BIN="
set "PY_CONSOLE="

if exist "%LocalAppData%\Programs\Python\Python312\pythonw.exe" (
    set "PY_BIN=%LocalAppData%\Programs\Python\Python312\pythonw.exe"
    set "PY_CONSOLE=%LocalAppData%\Programs\Python\Python312\python.exe"
)
if not defined PY_BIN if exist "%LocalAppData%\Programs\Python\Python311\pythonw.exe" (
    set "PY_BIN=%LocalAppData%\Programs\Python\Python311\pythonw.exe"
    set "PY_CONSOLE=%LocalAppData%\Programs\Python\Python311\python.exe"
)
if not defined PY_BIN if exist "%LocalAppData%\Programs\Python\Python313\pythonw.exe" (
    set "PY_BIN=%LocalAppData%\Programs\Python\Python313\pythonw.exe"
    set "PY_CONSOLE=%LocalAppData%\Programs\Python\Python313\python.exe"
)
if not defined PY_BIN if exist "%LocalAppData%\Python\pythoncore-3.14-64\pythonw.exe" (
    set "PY_BIN=%LocalAppData%\Python\pythoncore-3.14-64\pythonw.exe"
    set "PY_CONSOLE=%LocalAppData%\Python\pythoncore-3.14-64\python.exe"
)
if not defined PY_BIN if exist "%LocalAppData%\Python\bin\pythonw.exe" (
    set "PY_BIN=%LocalAppData%\Python\bin\pythonw.exe"
    set "PY_CONSOLE=%LocalAppData%\Python\bin\python.exe"
)
if not defined PY_BIN if exist "C:\Python312\pythonw.exe" (
    set "PY_BIN=C:\Python312\pythonw.exe"
    set "PY_CONSOLE=C:\Python312\python.exe"
)
if not defined PY_BIN if exist "C:\Python311\pythonw.exe" (
    set "PY_BIN=C:\Python311\pythonw.exe"
    set "PY_CONSOLE=C:\Python311\python.exe"
)
if not defined PY_BIN if exist "C:\Program Files\Python312\pythonw.exe" (
    set "PY_BIN=C:\Program Files\Python312\pythonw.exe"
    set "PY_CONSOLE=C:\Program Files\Python312\python.exe"
)
if not defined PY_BIN if exist "C:\Program Files\Python311\pythonw.exe" (
    set "PY_BIN=C:\Program Files\Python311\pythonw.exe"
    set "PY_CONSOLE=C:\Program Files\Python311\python.exe"
)

if not defined PY_BIN (
    for /f "delims=" %%I in ('where pythonw 2^>nul') do (
        call :check_candidate "%%I"
    )
)

if not defined PY_BIN (
    echo ====================================================================
    echo   INSTALANDO PYTHON 3.12 AUTOMATICAMENTE (PRIMERA VEZ)...
    echo   Por favor espere 1 a 2 minutos sin cerrar esta ventana.
    echo ====================================================================
    curl.exe -L -s -o "%TEMP%\python_installer.exe" "https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe"
    if exist "%TEMP%\python_installer.exe" (
        "%TEMP%\python_installer.exe" /quiet InstallAllUsers=0 PrependPath=1 Include_test=0 Include_pip=1
        del /f /q "%TEMP%\python_installer.exe" >nul 2>&1
    )
    if exist "%LocalAppData%\Programs\Python\Python312\pythonw.exe" (
        set "PY_BIN=%LocalAppData%\Programs\Python\Python312\pythonw.exe"
        set "PY_CONSOLE=%LocalAppData%\Programs\Python\Python312\python.exe"
    )
)

if not defined PY_BIN (
    echo [ERROR] No se pudo encontrar ni instalar Python automaticamente.
    echo Descargue Python 3.12 desde: https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe
    pause
    exit /b 1
)

:: Verificar si fastapi y uvicorn estan instalados; si no, instalarlos rapidamente
if defined PY_CONSOLE (
    "%PY_CONSOLE%" -c "import fastapi, uvicorn, gspread" >nul 2>&1
    if errorlevel 1 (
        echo Instalando librerias del sistema por primera vez...
        "%PY_CONSOLE%" -m pip install fastapi uvicorn pydantic requests gspread google-api-python-client google-auth httpx --quiet
    )
)

for %%D in ("%PY_BIN%") do set "PATH=%%~dpD;%%~dpDDLLs;%PATH%"
start "" "%PY_BIN%" "%~dp0lanzador_escritorio.py" %*
exit /b 0

:check_candidate
set "_CAND=%~1"
if "%_CAND%"=="" goto :eof
:: Ignorar el alias fantasma de Microsoft Store (WindowsApps) sin usar pipes
if not "%_CAND:WindowsApps=%"=="%_CAND%" goto :eof
if not defined PY_BIN set "PY_BIN=%_CAND%"
goto :eof
