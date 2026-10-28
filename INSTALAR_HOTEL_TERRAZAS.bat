@echo off
chcp 65001 > nul
title Instalador Automatico - Hotel Terrazas de Villa
color 0A
cd /d "%~dp0"

echo ====================================================================
echo   INSTALADOR AUTOMATICO - HOTEL TERRAZAS DE VILLA
echo   Sincronizacion Directa con Google Sheets (Offline-First)
echo ====================================================================
echo.

set "DEST_DIR=C:\Recepcion_Terrazas_Local"
set "GIT_DIR=C:\Hostal_Sistema\tools\mingit"
set "GIT_EXE=%GIT_DIR%\cmd\git.exe"
set "REPO_URL=https://github.com/eduardocalaniguevara-del/app_local_recepcion.git"

:: -------------------------------------------------------------------
:: 1. VERIFICAR O INSTALAR PYTHON
:: -------------------------------------------------------------------
echo [1/6] Verificando instalacion de Python...
set "PY_CMD="
where python >nul 2>&1 && set "PY_CMD=python"
if "%PY_CMD%"=="" (
    where py >nul 2>&1 && set "PY_CMD=py"
)
if "%PY_CMD%"=="" (
    if exist "%LocalAppData%\Programs\Python\Python311\python.exe" set "PY_CMD=%LocalAppData%\Programs\Python\Python311\python.exe"
    if exist "%LocalAppData%\Programs\Python\Python312\python.exe" set "PY_CMD=%LocalAppData%\Programs\Python\Python312\python.exe"
    if exist "C:\Python311\python.exe" set "PY_CMD=C:\Python311\python.exe"
)

if "%PY_CMD%"=="" (
    echo [AVISO] Python no detectado. Descargando e instalando Python 3.11 oficial...
    powershell -NoProfile -Command "$installer = \"$env:TEMP\python_installer.exe\"; Write-Host 'Descargando instalador...'; Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe' -OutFile $installer; Write-Host 'Instalando silenciosamente (puede demorar 1-2 minutos)...'; Start-Process -FilePath $installer -ArgumentList '/quiet InstallAllUsers=0 PrependPath=1 Include_pip=1' -Wait; Remove-Item -Path $installer -Force -ErrorAction SilentlyContinue"
    
    :: Actualizar variables de entorno para esta ventana
    set "PATH=%LocalAppData%\Programs\Python\Python311;%LocalAppData%\Programs\Python\Python311\Scripts;%PATH%"
    set "PY_CMD=python"
)
echo [OK] Python listo.

:: -------------------------------------------------------------------
:: 2. VERIFICAR O DESCARGAR GIT PORTATIL (MINGIT)
:: -------------------------------------------------------------------
echo.
echo [2/6] Verificando motor de actualizacion automatica (Git)...
where git >nul 2>&1
if %ERRORLEVEL% equ 0 (
    set "GIT_EXE=git"
) else (
    if not exist "%GIT_EXE%" (
        echo [AVISO] Descargando Git portable silencioso (no requiere permisos de admin)...
        powershell -NoProfile -Command "New-Item -ItemType Directory -Force -Path '%GIT_DIR%' | Out-Null; $zip = \"%GIT_DIR%\mingit.zip\"; Invoke-WebRequest -Uri 'https://github.com/git-for-windows/git/releases/download/v2.47.1.windows.1/MinGit-2.47.1-64-bit.zip' -OutFile $zip; Expand-Archive -Path $zip -DestinationPath '%GIT_DIR%' -Force; Remove-Item $zip -Force"
    )
)
echo [OK] Motor Git listo.

:: -------------------------------------------------------------------
:: 3. DESCARGAR / ACTUALIZAR CODIGO DESDE GITHUB
:: -------------------------------------------------------------------
echo.
echo [3/6] Descargando el sistema completo desde GitHub a %DEST_DIR%...
if not exist "%DEST_DIR%" mkdir "%DEST_DIR%"

if not exist "%DEST_DIR%\.git" (
    "%GIT_EXE%" -C "%DEST_DIR%" init > nul 2>&1
    "%GIT_EXE%" -C "%DEST_DIR%" branch -M main > nul 2>&1
    "%GIT_EXE%" -C "%DEST_DIR%" remote add origin %REPO_URL% > nul 2>&1
)
"%GIT_EXE%" -C "%DEST_DIR%" fetch origin main
"%GIT_EXE%" -C "%DEST_DIR%" reset --hard origin/main
echo [OK] Codigo base sincronizado con GitHub.

:: -------------------------------------------------------------------
:: 4. CONFIGURAR PERFIL EXCLUSIVO HOTEL TERRAZAS
:: -------------------------------------------------------------------
echo.
echo [4/6] Configurando perfil del Hotel Terrazas de Villa...
if not exist "%DEST_DIR%\data" mkdir "%DEST_DIR%\data"

powershell -NoProfile -Command "$perfil = @{ nombre_hotel = 'Hotel Terrazas de Villa'; spreadsheet_id = '1vgjOLxc-eh1zw42uC3pxnM86Lo2jZkz8_uwrEFhw8ss'; col_tipo_adicional = 'AZ'; formula_snacks_en_s2 = $true; app_port = 8080 }; $perfil | ConvertTo-Json | Set-Content -Path '%DEST_DIR%\data\hotel_perfil.json' -Encoding UTF8"
echo [OK] Perfil configurado para Terrazas.

:: -------------------------------------------------------------------
:: 5. DESEMPAQUETAR CREDENCIALES DE GOOGLE SHEETS
:: -------------------------------------------------------------------
echo.
echo [5/6] Instalando credenciales de enlace con Google Sheets...
powershell -NoProfile -Command "$b64 = 'ewogICJ0eXBlIjogInNlcnZpY2VfYWNjb3VudCIsCiAgInByb2plY3RfaWQiOiAib3ZhbG8tbWFyaWF0ZWd1aS1ib3QiLAogICJwcml2YXRlX2tleV9pZCI6ICI0YjJkYWFiMjM5OTBjNTk4ZDAyMjViMjFlMjMwZGNjMWFlNTM1OTRkIiwKICAicHJpdmF0ZV9rZXkiOiAiLS0tLS1CRUdJTiBQUklWQVRFIEtFWS0tLS0tXG5NSUlFdmdJQkFEQU5CZ2txaGtpRzl3MEJBUUVGQUFTQ0JLZ3dnZ1NrQWdFQUFvSUJBUUM1dG1WRG9pblhINlVRXG5GRWVTSmVIbXRCajBTM0U0aGlrSWlpeis2bGpIbGFzcVR5MkVBZU92akwxZjdCcUFnT3pSWnA1aC93K0dHVHBRXG5NZ2h1dEpZYnlPM25UUlVIR0lQa3EvOHdzMmF2SXFaenUxRzlmTkJnVXZZelZCUDdiajcrT1FaRWVsTm9xTitOXG45QWVwMnJpcHNPbVNoQnZiOTRCQUpESXRPOFNBbjduZmpEZU1reTI2cGkrWWNLUlEwU3FVMk4yNjZNM09jbVRpXG5HY0ZmamdLYkJnZHZPZ1dWNS92NmFjMEVuUWF1Ly9hSWxLOHVSSjgxRnU5Y0ZUNE9zQURRckpId1pPUTNxRDZyXG4zdlk3T0FBVGlZTEZyTEVxK05oYkJ3alVRekJibG9iV012V2JRZ1A5K21QVFBoa0pkTDZLc2ZoVFhOV1JTZ2pZXG52ekhxaGNmZEFnTUJBQUVDZ2dFQUxaMDRPQ2wvZjJBOWFicWhiYXN5VmprTUhBemZyVk4raHFOeHM5Y1pvVVFJXG51NmFnOWxpRWRSenJjc0VLTFYvOVN0MmFyMVUzbWVkZVpFNDF0Z05URDFZRjRSY1V3QjVzWHVGRUY5RW91b0dYXG5RcnZTc3VrU1FXWi9KRkpWYWg5RkI2bzMrTi9XMXRFTHY5RWRoTUc5TTZqSlpmeGUzMk5tTTlyd0hCNGZBTHhQXG5BVXZZQ3Zxbk8zSFFpOTBFUW1TcjZJelN2Ym9MZUN1UVhxNzlvZSsvMW90MzZnM0NtWmdvNERqalFpcFNGTXYxXG5jSmhPZDdkdmwvVWh6Tk82SHp2bWtNeTJPc0dQWkJOWFBQK3VWblp5d2Zrd3hnZlArR1UyRXh0S2I4emp0Sm1lXG5DWW4zVzI5R0hVZmUrQ0EvbW5DSGRVeWlyT29IYVlEbFRQUDVXRFc5SFFLQmdRRHhRRVBYVXBVRG4wbDNUV2NHXG55dUhoZUJqVWZ5TzJnQnl2T25vR0g3K0kxRnd1Nm13MnRUQnU3Rmd2dHRFTnlvd3RPK3I1RFA2bnVQMTVRQVlrXG52MkVTNmpQaDB5T1lwK1RqdityZGk4NFg3b082NTU1NEcrY1U2b0tydGpDNlh6MHB5UGgvMFRseEh3WDZSc1ZVXG56VFZDcytTMVNjVW1Kb0dOZ1g3TmJ6czhhd0tCZ1FERkVPcU9KSnQ2dUhrVGZVaXYzcEVGTDBBU09lV01FUkxiXG50VWVHYjBHN2lGMC9MeHJOaVVoczJCaUdYcjJqb2FRRXBKRHFzT0piajB5b3F2RzkzV1A5SkFPT3ZibS9IM0M3XG5KaDA3MEY2TVU1YlBQajg4MjBTK0s1M3RsWXp4TnY4UmxqQ2lJMDVodUZNOXpFb1Q4dlpRSDNLbzErNHA2Zm1nXG5nb2xtc0hTZTF3S0JnUUNENzVKcWNwWmQrY0hTeGZqZkpSc3RGR0gzN0dRYWphdWlSUWV3eTdJUzRaaGNiQVdzXG5VYURnT003dG9UeHBIMmxUMzUzY2dEZlFnQUhYU3FWUmVYeGNQTXNNNkV4aVV3ZUJhbGJHdkl1RVdRT2Z0c1hhXG5oOS96ZDJEcENXVlN0MzBhbWZjVTU5WlRRQlVqejRwaG15eFNnMUZEdDhsaTJOSW5FamZIbjlvMGF3S0JnRDhRXG5pOCtUWFlKbmVLL0kvekx4Q2YzYndpaFRDT2ZMS3ZyT3g2WDVBdE10NzVGaDFMVUVBdjU2SlZjVENLTk4vanhTXG5JZUpKV3hjc0E3OVNmV3hpWmpaRGlUUHIrWDUxWkZFd0FGajg5TlQ1aHlDcGJmbGV2bUFSZTBmbC9TWFFrTTRpXG4vMHRUK3lpQW9YcjhYcFNGdzFlZXlNWEozSGpqWExTR01jeUFHY0lmQW9HQkFPYU9JVzRuTVdtbWxVQkk0RjVXXG5BZHptaWdOVmZnQmhDajJlZkR3TUpzUHRkZWFxblpVdTc3ZVpML2JsZkxVZnVHZzlTWXpaOWk0dllIZmdpN24zXG5IVnFhTjFBNjN1Y2h3UW1uTlZhdFhOWW1HemY2aDR1ZU9mMXFqakxtaGpJSFNvZWJ0SHZWdTY1bFZ3VWtqTWNQXG5haVFndGhxWDNrMThrK3JOZ0NhV0NiSTBcbi0tLS0tRU5EIFBSSVZBVEUgS0VZLS0tLS1cbiIsCiAgImNsaWVudF9lbWFpbCI6ICJhbnRpZ3Jhdml0eS1zaGVldHNAb3ZhbG8tbWFyaWF0ZWd1aS1ib3QuaWFtLmdzZXJ2aWNlYWNjb3VudC5jb20iLAogICJjbGllbnRfaWQiOiAiMTAyNjczNjcwNDMyMjAzNjMyODQ5IiwKICAiYXV0aF91cmkiOiAiaHR0cHM6Ly9hY2NvdW50cy5nb29nbGUuY29tL28vb2F1dGgyL2F1dGgiLAogICJ0b2tlbl91cmkiOiAiaHR0cHM6Ly9vYXV0aDIuZ29vZ2xlYXBpcy5jb20vdG9rZW4iLAogICJhdXRoX3Byb3ZpZGVyX3g1MDlfY2VydF91cmwiOiAiaHR0cHM6Ly93d3cuZ29vZ2xlYXBpcy5jb20vb2F1dGgyL3YxL2NlcnRzIiwKICAiY2xpZW50X3g1MDlfY2VydF91cmwiOiAiaHR0cHM6Ly93d3cuZ29vZ2xlYXBpcy5jb20vcm9ib3QvdjEvbWV0YWRhdGEveDUwOS9hbnRpZ3Jhdml0eS1zaGVldHMlNDBvdmFsby1tYXJpYXRlZ3VpLWJvdC5pYW0uZ3NlcnZpY2VhY2NvdW50LmNvbSIsCiAgInVuaXZlcnNlX2RvbWFpbiI6ICJnb29nbGVhcGlzLmNvbSIKfQo='; [System.IO.File]::WriteAllBytes('%DEST_DIR%\data\credentials.json', [System.Convert]::FromBase64String($b64))"
echo [OK] Credenciales instaladas de forma segura.

:: -------------------------------------------------------------------
:: 6. INSTALAR LIBRERIAS PYTHON Y CREAR ACCESO DIRECTO
:: -------------------------------------------------------------------
echo.
echo [6/6] Verificando dependencias de Python y creando acceso directo...
%PY_CMD% -m pip install fastapi uvicorn pydantic requests gspread google-api-python-client google-auth --quiet

:: Crear acceso directo en el Escritorio
powershell -NoProfile -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut([Environment]::GetFolderPath('Desktop') + '\Recepcion Hotel Terrazas.lnk'); $s.TargetPath = '%DEST_DIR%\Iniciar_Recepcion_Local.bat'; $s.WorkingDirectory = '%DEST_DIR%'; $s.Save()"
echo [OK] Acceso directo 'Recepcion Hotel Terrazas' creado en el Escritorio.

echo.
echo ====================================================================
echo   INSTALACION COMPLETADA CON EXITO
echo   Iniciando el sistema por primera vez...
echo ====================================================================
echo.

cd /d "%DEST_DIR%"
call Iniciar_Recepcion_Local.bat
