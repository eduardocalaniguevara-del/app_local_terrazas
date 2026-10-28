@echo off
chcp 65001 > nul
title Instalador Autonomo - Recepcion Hotel Terrazas de Villa
cd /d "%~dp0"

echo ====================================================================
echo   INSTALACION AUTOMATICA - HOTEL TERRAZAS DE VILLA
echo   Hoja vinculada: 1vgjOLxc-eh1zw42uC3pxnM86Lo2jZkz8_uwrEFhw8ss
echo ====================================================================
echo.

set "DEST_DIR=C:\Recepcion_Terrazas_Local"
set "GIT_DIR=C:\Hostal_Sistema\tools\mingit"
set "GIT_EXE=%GIT_DIR%\cmd\git.exe"

:: 1. Detectar o instalar Python
set "PY_EXE="
if exist "%LocalAppData%\Programs\Python\Python312\python.exe" set "PY_EXE=%LocalAppData%\Programs\Python\Python312\python.exe"
if exist "%LocalAppData%\Python\pythoncore-3.14-64\python.exe" set "PY_EXE=%LocalAppData%\Python\pythoncore-3.14-64\python.exe"
if exist "%LocalAppData%\Programs\Python\Python313\python.exe" set "PY_EXE=%LocalAppData%\Programs\Python\Python313\python.exe"
if exist "%LocalAppData%\Programs\Python\Python311\python.exe" set "PY_EXE=%LocalAppData%\Programs\Python\Python311\python.exe"
if exist "C:\Python312\python.exe" set "PY_EXE=C:\Python312\python.exe"
if exist "C:\Python311\python.exe" set "PY_EXE=C:\Python311\python.exe"

if defined PY_EXE goto py_detectado
for /f "tokens=*" %%I in ('where python 2^>nul') do (
    if not defined PY_EXE set "PY_EXE=%%I"
)

:py_detectado
if defined PY_EXE goto py_listo
echo [1/5] Python no detectado. Descargando e instalando Python 3.12 automaticamente...
powershell -NoProfile -Command "$u='https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe'; $p=\"$env:TEMP\python_installer.exe\"; Invoke-WebRequest -Uri $u -OutFile $p; Start-Process -FilePath $p -ArgumentList '/quiet InstallAllUsers=0 PrependPath=1 Include_test=0' -Wait; Remove-Item $p -Force -ErrorAction SilentlyContinue"
if exist "%LocalAppData%\Programs\Python\Python312\python.exe" set "PY_EXE=%LocalAppData%\Programs\Python\Python312\python.exe"

:py_listo
echo [1/5] Python listo: %PY_EXE%

:: 2. Detectar o instalar Git portable
if exist "%GIT_EXE%" goto git_listo
if exist "C:\Program Files\Git\cmd\git.exe" (
    set "GIT_EXE=C:\Program Files\Git\cmd\git.exe"
    goto git_listo
)
echo [2/5] Descargando motor de auto-actualizacion (Git portable)...
powershell -NoProfile -Command "New-Item -ItemType Directory -Force -Path '%GIT_DIR%' | Out-Null; Invoke-WebRequest -Uri 'https://github.com/git-for-windows/git/releases/download/v2.47.1.windows.1/MinGit-2.47.1-64-bit.zip' -OutFile '%GIT_DIR%\mingit.zip'; Expand-Archive -Path '%GIT_DIR%\mingit.zip' -DestinationPath '%GIT_DIR%' -Force; Remove-Item '%GIT_DIR%\mingit.zip' -Force"

:git_listo
echo [2/5] Motor Git listo: %GIT_EXE%

:: 3. Copiar archivos a C:\Recepcion_Terrazas_Local
echo [3/5] Instalando archivos en %DEST_DIR%...
if not exist "%DEST_DIR%" mkdir "%DEST_DIR%"
xcopy "%~dp0*.*" "%DEST_DIR%\" /E /Y /Q > nul

if not exist "%DEST_DIR%\data" mkdir "%DEST_DIR%\data"
powershell -NoProfile -Command "$p='%DEST_DIR%\data\hotel_perfil.json'; $cfg=@{nombre_hotel='Hotel Terrazas de Villa';spreadsheet_id='1vgjOLxc-eh1zw42uC3pxnM86Lo2jZkz8_uwrEFhw8ss';col_tipo_adicional='AZ';formula_snacks_en_s2=$true;app_port=8081}; [System.IO.File]::WriteAllText($p, ($cfg | ConvertTo-Json), [System.Text.UTF8Encoding]::new($false))"

:: 4. Conectar a GitHub (Repositorio exclusivo Hotel Terrazas)
echo [4/5] Conectando actualizador automatico con GitHub (Terrazas)...
if exist "%DEST_DIR%\.git" goto repo_listo
"%GIT_EXE%" -C "%DEST_DIR%" init > nul 2>&1
"%GIT_EXE%" -C "%DEST_DIR%" branch -M main > nul 2>&1
"%GIT_EXE%" -C "%DEST_DIR%" remote add origin https://github.com/eduardocalaniguevara-del/app_local_terrazas.git > nul 2>&1

:repo_listo
"%GIT_EXE%" -C "%DEST_DIR%" remote set-url origin https://github.com/eduardocalaniguevara-del/app_local_terrazas.git > nul 2>&1
"%GIT_EXE%" -C "%DEST_DIR%" fetch origin main > nul 2>&1
"%GIT_EXE%" -C "%DEST_DIR%" reset --hard origin/main > nul 2>&1
echo [4/5] Repositorio sincronizado al dia con GitHub (Terrazas).

:: 5. Instalar librerias y crear icono
echo [5/5] Instalando librerias y creando icono en Escritorio...
"%PY_EXE%" -m pip install --upgrade pip --quiet > nul 2>&1
"%PY_EXE%" -m pip install fastapi uvicorn pydantic gspread google-api-python-client google-auth httpx requests --quiet

powershell -NoProfile -Command "$ws = New-Object -ComObject WScript.Shell; foreach ($d in @([Environment]::GetFolderPath('Desktop'), \"$env:USERPROFILE\OneDrive\Desktop\")) { if (Test-Path $d) { $s = $ws.CreateShortcut(\"$d\Recepcion Hotel Terrazas (Local).lnk\"); $s.TargetPath = '%DEST_DIR%\Iniciar_Recepcion_Local.bat'; $s.WorkingDirectory = '%DEST_DIR%'; if (Test-Path 'C:\Program Files\Google\Chrome\Application\chrome.exe') { $s.IconLocation = 'C:\Program Files\Google\Chrome\Application\chrome.exe,0' }; $s.Save() } }"

echo.
echo ====================================================================
echo   INSTALACION COMPLETADA CON EXITO!
echo   Icono creado en tu Escritorio: "Recepcion Hotel Terrazas (Local)"
echo ====================================================================
echo.

cd /d "%DEST_DIR%"
start "" Iniciar_Recepcion_Local.bat
ping 127.0.0.1 -n 4 > nul
