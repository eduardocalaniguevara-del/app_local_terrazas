# -*- coding: utf-8 -*-
"""
Lanzador Autónomo de Escritorio — Recepción Hotel Terrazas de Villa
- Crea automáticamente el acceso directo nativo en el Escritorio apuntando directo a pythonw.exe
  (evita bloqueos de Windows SmartScreen sobre archivos .bat y problemas de asociación de .py).
- Si se ejecuta desde consola (python.exe), lanza el demonio en pythonw.exe desacoplado para que
  cerrar la ventana negra nunca apague el servidor.
"""

import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import webbrowser

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from config import APP_HOST, APP_PORT, NOMBRE_HOTEL

HOST = APP_HOST
PORT = APP_PORT
URL = f"http://{HOST}:{PORT}"

BROWSER_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def asegurar_icono_escritorio_nativo() -> None:
    """Crea el acceso directo en el Escritorio apuntando directo a pythonw.exe (0 bloqueos .bat)."""
    if os.name != "nt":
        return
    try:
        py_exe = Path(sys.executable).resolve()
        pyw_exe = py_exe.with_name("pythonw.exe")
        if not pyw_exe.exists():
            pyw_exe = py_exe
        script_path = Path(__file__).resolve()
        ps_cmd = f'''
$ws = New-Object -ComObject WScript.Shell
$dirs = @([Environment]::GetFolderPath('Desktop'), "$env:USERPROFILE\\Desktop", "$env:USERPROFILE\\OneDrive\\Desktop") | Select-Object -Unique
foreach ($d in $dirs) {{
    if ($d -and (Test-Path $d)) {{
        foreach ($name in @("Recepcion Hotel Terrazas (Local).lnk", "Recepcion Hotel Terrazas.lnk")) {{
            $lnk = Join-Path $d $name
            $s = $ws.CreateShortcut($lnk)
            $s.TargetPath = "{pyw_exe}"
            $s.Arguments = '"{script_path}"'
            $s.WorkingDirectory = "{APP_DIR}"
            $s.Description = "Abrir {NOMBRE_HOTEL}"
            if (Test-Path "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe") {{
                $s.IconLocation = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe,0"
            }} elseif (Test-Path "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe") {{
                $s.IconLocation = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe,0"
            }}
            $s.Save()
        }}
    }}
}}
'''
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_cmd],
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=8,
        )
    except Exception:
        pass


def servidor_activo() -> bool:
    try:
        with socket.create_connection((HOST, PORT), timeout=0.5):
            return True
    except OSError:
        return False


def abrir_navegador_app() -> None:
    if "--no-browser" in sys.argv:
        return
    for browser_exe in BROWSER_PATHS:
        if os.path.exists(browser_exe):
            try:
                subprocess.Popen(
                    [browser_exe, f"--app={URL}"],
                    cwd=str(APP_DIR),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    close_fds=True,
                )
                return
            except Exception:
                pass
    try:
        os.startfile(URL)
    except Exception:
        webbrowser.open(URL)


def _esperar_y_abrir_navegador() -> None:
    for _ in range(40):
        if servidor_activo():
            break
        time.sleep(0.2)
    abrir_navegador_app()


def main() -> None:
    threading.Thread(target=asegurar_icono_escritorio_nativo, daemon=True).start()

    if servidor_activo():
        abrir_navegador_app()
        return

    # Si se ejecutó con python.exe (consola) y no tiene --daemon, relanzar en pythonw.exe desacoplado
    # para que al cerrar la ventana negra el servidor siga vivo en segundo plano.
    exe_name = Path(sys.executable).name.lower()
    if os.name == "nt" and exe_name == "python.exe" and "--daemon" not in sys.argv:
        pyw = Path(sys.executable).with_name("pythonw.exe")
        if pyw.exists():
            subprocess.Popen(
                [str(pyw), str(Path(__file__).resolve()), "--daemon"] + sys.argv[1:],
                cwd=str(APP_DIR),
                creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS,
                close_fds=True,
            )
            _esperar_y_abrir_navegador()
            return

    log_path = APP_DIR / "server_local.log"
    log_file = open(log_path, "a", encoding="utf-8", buffering=1)
    sys.stdout = log_file
    sys.stderr = log_file

    threading.Thread(target=_esperar_y_abrir_navegador, daemon=True).start()

    import uvicorn
    from main import app

    uvicorn.run(app, host=HOST, port=PORT, log_config=None)


if __name__ == "__main__":
    main()
