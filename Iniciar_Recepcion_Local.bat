@echo off
chcp 65001 > nul
cd /d "%~dp0"

:: 1. Intentar ubicar pythonw (silencioso sin ventana negra) ignorando el stub vacio de WindowsApps
set "PY_BIN="

for /f "tokens=*" %%I in ('where pythonw 2^>nul') do (
    echo %%I | findstr /I "WindowsApps" >nul || (if not defined PY_BIN set "PY_BIN=%%I")
)
if not defined PY_BIN (
    for /f "tokens=*" %%I in ('where pyw 2^>nul') do (
        echo %%I | findstr /I "WindowsApps" >nul || (if not defined PY_BIN set "PY_BIN=%%I")
    )
)
if not defined PY_BIN (
    for /f "tokens=*" %%I in ('where python 2^>nul') do (
        echo %%I | findstr /I "WindowsApps" >nul || (if not defined PY_BIN set "PY_BIN=%%I")
    )
)

:: Rutas directas en Windows en caso de que PATH no este actualizado en la sesion
if not defined PY_BIN (
    if exist "%LocalAppData%\Python\bin\pythonw.exe" set "PY_BIN=%LocalAppData%\Python\bin\pythonw.exe"
)
if not defined PY_BIN (
    if exist "%LocalAppData%\Programs\Python\Python313\pythonw.exe" set "PY_BIN=%LocalAppData%\Programs\Python\Python313\pythonw.exe"
)
if not defined PY_BIN (
    if exist "%LocalAppData%\Programs\Python\Python312\pythonw.exe" set "PY_BIN=%LocalAppData%\Programs\Python\Python312\pythonw.exe"
)
if not defined PY_BIN (
    if exist "%LocalAppData%\Programs\Python\Python311\pythonw.exe" set "PY_BIN=%LocalAppData%\Programs\Python\Python311\pythonw.exe"
)
if not defined PY_BIN (
    if exist "%LocalAppData%\Python\pythoncore-3.14-64\pythonw.exe" set "PY_BIN=%LocalAppData%\Python\pythoncore-3.14-64\pythonw.exe"
)
if not defined PY_BIN (
    if exist "C:\Python312\pythonw.exe" set "PY_BIN=C:\Python312\pythonw.exe"
)
if not defined PY_BIN (
    if exist "C:\Python311\pythonw.exe" set "PY_BIN=C:\Python311\pythonw.exe"
)
if not defined PY_BIN (
    if exist "%LocalAppData%\Programs\Python\Python312\python.exe" set "PY_BIN=%LocalAppData%\Programs\Python\Python312\python.exe"
)
if not defined PY_BIN (
    if exist "%LocalAppData%\Programs\Python\Python311\python.exe" set "PY_BIN=%LocalAppData%\Programs\Python\Python311\python.exe"
)

if not defined PY_BIN (
    echo [ERROR] No se encontro Python en esta computadora.
    pause
    exit /b 1
)

for %%D in ("%PY_BIN%") do set "PATH=%%~dpD;Lists;%%~dpDDLLs;%PATH%"
start "" "%PY_BIN%" "%~dp0lanzador_escritorio.py" %*
