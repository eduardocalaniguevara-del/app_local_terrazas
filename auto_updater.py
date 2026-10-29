#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Motor de Auto-Actualización silenciosa vía Git para los 3 Hoteles.
Revisa periódicamente si hay cambios en GitHub (origin/main), descarga el código
nuevo sin tocar la carpeta data/ (ni hotel_perfil.json ni ovalo_local.db) y
recarga los módulos e interfaces en memoria sin tumbar el servidor ni abrir ventanas CMD.
"""

import importlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Dict, Any

from config import BASE_APP_DIR, DATA_DIR, NOMBRE_HOTEL, PERFIL_HOTEL_PATH, SPREADSHEET_ID

_GIT_CANDIDATES = [
    r"C:\Hostal_Sistema\tools\mingit\cmd\git.exe",
    r"C:\Program Files\Git\cmd\git.exe",
    r"C:\Program Files (x86)\Git\cmd\git.exe",
]


def obtener_git_exe() -> str:
    en_path = shutil.which("git")
    if en_path:
        return en_path
    for p in _GIT_CANDIDATES:
        if os.path.exists(p):
            return p
    return "git"


def _run_git(args: list, timeout: int = 15) -> subprocess.CompletedProcess:
    git_bin = obtener_git_exe()
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    return subprocess.run(
        [git_bin] + args,
        cwd=str(BASE_APP_DIR),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=creationflags,
    )


def obtener_version_actual() -> str:
    git_dir = BASE_APP_DIR / ".git"
    if not git_dir.exists():
        try:
            p1 = (BASE_APP_DIR / "domain_logic.py").stat().st_mtime
            p2 = (BASE_APP_DIR / "static" / "PanelDisponibilidad.html").stat().st_mtime
            return f"local-{int(max(p1, p2))}"
        except Exception:
            return "local-0"
    try:
        r = _run_git(["rev-parse", "--short", "HEAD"], timeout=5)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except Exception:
        pass
    return "git-unknown"


_LAST_DL_MTIME: float = 0.0


def _verificar_recarga_local_mtime() -> bool:
    global _LAST_DL_MTIME
    try:
        dl_path = BASE_APP_DIR / "domain_logic.py"
        if not dl_path.exists():
            return False
        mt = dl_path.stat().st_mtime
        if _LAST_DL_MTIME == 0.0:
            _LAST_DL_MTIME = mt
            return False
        if mt > _LAST_DL_MTIME:
            _LAST_DL_MTIME = mt
            recargar_en_memoria_sin_cortes()
            return True
    except Exception:
        pass
    return False


def recargar_en_memoria_sin_cortes() -> None:
    """
    Recarga domain_logic directamente en memoria y actualiza el timestamp
    de PanelDisponibilidad.html para que el navegador refresque la vista sin
    matar el servidor ni interrumpir el hilo de sincronización de sync_engine.
    """
    global _LAST_DL_MTIME
    try:
        if "domain_logic" in sys.modules:
            importlib.reload(sys.modules["domain_logic"])
        dl_path = BASE_APP_DIR / "domain_logic.py"
        if dl_path.exists():
            _LAST_DL_MTIME = dl_path.stat().st_mtime
    except Exception as exc:
        print(f"[AUTO-UPDATER] Aviso recargando domain_logic en memoria: {exc}")

    try:
        panel_html = BASE_APP_DIR / "static" / "PanelDisponibilidad.html"
        if panel_html.exists():
            now_ts = time.time()
            os.utime(panel_html, (now_ts, now_ts))
    except Exception:
        pass


def _cargar_ordenes_ejecutadas() -> set:
    f = DATA_DIR / "ordenes_ejecutadas.json"
    if not f.exists():
        return set()
    try:
        return set(json.loads(f.read_text(encoding="utf-8")))
    except Exception:
        return set()


def _guardar_orden_ejecutada(orden_id: str) -> None:
    ejecutadas = _cargar_ordenes_ejecutadas()
    ejecutadas.add(orden_id)
    try:
        (DATA_DIR / "ordenes_ejecutadas.json").write_text(
            json.dumps(sorted(ejecutadas), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass


def _es_para_este_hotel(target: str) -> bool:
    t = str(target or "todos").strip().lower()
    if t in ("", "todos", "all", "*"):
        return True
    if t in NOMBRE_HOTEL.lower() or t in SPREADSHEET_ID.lower() or t in str(BASE_APP_DIR).lower():
        return True
    return False


def procesar_ordenes_remotas() -> None:
    """
    Puerta de Mando Remota vía GitHub (ordenes_hoteles.json):
    Permite instalar librerías pip, ejecutar comandos de mantenimiento,
    actualizar hotel_perfil.json o desacoplar un hotel a un nuevo repositorio Git.
    """
    ord_path = BASE_APP_DIR / "ordenes_hoteles.json"
    if not ord_path.exists():
        return
    try:
        data = json.loads(ord_path.read_text(encoding="utf-8"))
        ordenes = data.get("ordenes", []) if isinstance(data, dict) else []
    except Exception:
        return

    ejecutadas = _cargar_ordenes_ejecutadas()
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

    for ord_item in ordenes:
        if not isinstance(ord_item, dict):
            continue
        oid = str(ord_item.get("id") or "").strip()
        if not oid or oid in ejecutadas:
            continue
        if not _es_para_este_hotel(ord_item.get("target", "todos")):
            continue

        try:
            # 1. Instalar librerías de Python si se solicita
            pkgs = ord_item.get("pip_install")
            if isinstance(pkgs, list) and pkgs:
                subprocess.run(
                    [sys.executable, "-m", "pip", "install", "--quiet"] + [str(p) for p in pkgs],
                    cwd=str(BASE_APP_DIR),
                    timeout=180,
                    creationflags=creationflags,
                )

            # 2. Actualizar parámetros en data/hotel_perfil.json
            upd_perfil = ord_item.get("actualizar_perfil")
            if isinstance(upd_perfil, dict) and upd_perfil:
                actual = {}
                if PERFIL_HOTEL_PATH.exists():
                    try:
                        actual = json.loads(PERFIL_HOTEL_PATH.read_text(encoding="utf-8"))
                    except Exception:
                        actual = {}
                actual.update(upd_perfil)
                PERFIL_HOTEL_PATH.write_text(
                    json.dumps(actual, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )

            # 3. Ejecutar comando CMD/PowerShell si se solicita
            cmd_str = str(ord_item.get("comando_cmd") or "").strip()
            if cmd_str:
                subprocess.run(
                    cmd_str,
                    shell=True,
                    cwd=str(BASE_APP_DIR),
                    timeout=180,
                    creationflags=creationflags,
                )

            # 4. Desacoplar o cambiar URL de repositorio Git si se solicita
            nuevo_git = str(ord_item.get("cambiar_git_url") or "").strip()
            if nuevo_git:
                _run_git(["remote", "set-url", "origin", nuevo_git], timeout=10)
                _run_git(["fetch", "origin", "main", "--quiet"], timeout=20)
                _run_git(["reset", "--hard", "origin/main"], timeout=15)
                recargar_en_memoria_sin_cortes()

            _guardar_orden_ejecutada(oid)
            print(f"[AUTO-UPDATER] Orden remota ejecutada con éxito: {oid}")
        except Exception as exc:
            print(f"[AUTO-UPDATER] Error ejecutando orden remota {oid}: {exc}")


def ejecutar_salvavidas_sheets(celda_cmd: str) -> None:
    """
    Salvavidas de emergencia desde Google Sheets (Config!N2):
    Formato: CMD:<ID_UNICO>|<ACCION>=<VALOR>
    Ejemplo:
      CMD:RESCATE_01|GIT_URL=https://github.com/usuario/nuevo_repo.git
      CMD:RESCATE_02|PIP=openpyxl
      CMD:RESCATE_03|CMD=ipconfig /flushdns
    Funciona incluso si Git fallara por completo en la otra PC.
    """
    raw = str(celda_cmd or "").strip()
    if not raw.upper().startswith("CMD:"):
        return
    partes = raw[4:].split("|", 1)
    if len(partes) != 2:
        return
    oid = f"SHEETS_{partes[0].strip()}"
    instruccion = partes[1].strip()
    if not oid or oid in _cargar_ordenes_ejecutadas():
        return

    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        if instruccion.upper().startswith("GIT_URL="):
            nueva_url = instruccion.split("=", 1)[1].strip()
            if not (BASE_APP_DIR / ".git").exists():
                _run_git(["init"], timeout=10)
                _run_git(["branch", "-M", "main"], timeout=10)
                _run_git(["remote", "add", "origin", nueva_url], timeout=10)
            else:
                _run_git(["remote", "set-url", "origin", nueva_url], timeout=10)
            _run_git(["fetch", "origin", "main", "--quiet"], timeout=20)
            _run_git(["reset", "--hard", "origin/main"], timeout=15)
            recargar_en_memoria_sin_cortes()
        elif instruccion.upper().startswith("PIP="):
            pkgs = instruccion.split("=", 1)[1].strip().split()
            if pkgs:
                subprocess.run(
                    [sys.executable, "-m", "pip", "install", "--quiet"] + pkgs,
                    cwd=str(BASE_APP_DIR),
                    timeout=180,
                    creationflags=creationflags,
                )
        elif instruccion.upper().startswith("CMD="):
            cmd_s = instruccion.split("=", 1)[1].strip()
            if cmd_s:
                subprocess.run(
                    cmd_s,
                    shell=True,
                    cwd=str(BASE_APP_DIR),
                    timeout=180,
                    creationflags=creationflags,
                )
        _guardar_orden_ejecutada(oid)
        print(f"[SALVAVIDAS SHEETS] Orden ejecutada: {oid}")
    except Exception as exc:
        print(f"[SALVAVIDAS SHEETS] Error en {oid}: {exc}")


def verificar_y_aplicar_actualizacion() -> Dict[str, Any]:
    """
    Comprueba si origin/main tiene un commit más reciente que HEAD.
    Si hay cambios, ejecuta git reset --hard origin/main (preservando data/)
    y recarga en memoria sin desconectar el servidor.
    """
    _verificar_recarga_local_mtime()
    procesar_ordenes_remotas()
    git_dir = BASE_APP_DIR / ".git"
    if not git_dir.exists():
        return {"actualizado": False, "motivo": "sin_repo_git", "version": obtener_version_actual()}

    try:
        f = _run_git(["fetch", "origin", "main", "--quiet"], timeout=15)
        if f.returncode != 0:
            return {"actualizado": False, "motivo": "fetch_error", "detalle": f.stderr.strip()}

        loc = _run_git(["rev-parse", "HEAD"], timeout=5)
        rem = _run_git(["rev-parse", "origin/main"], timeout=5)
        if loc.returncode != 0 or rem.returncode != 0:
            return {"actualizado": False, "motivo": "rev_parse_error"}

        sha_local = loc.stdout.strip()
        sha_remoto = rem.stdout.strip()
        if not sha_remoto or sha_local == sha_remoto:
            return {"actualizado": False, "motivo": "al_dia", "version": sha_local[:7]}

        rst = _run_git(["reset", "--hard", "origin/main"], timeout=15)
        if rst.returncode != 0:
            return {"actualizado": False, "motivo": "reset_error", "detalle": rst.stderr.strip()}

        cambiados = []
        try:
            df = _run_git(["diff", "--name-only", sha_local, sha_remoto], timeout=10)
            cambiados = [x.strip() for x in df.stdout.splitlines() if x.strip()]
        except Exception:
            pass

        procesar_ordenes_remotas()
        recargar_en_memoria_sin_cortes()

        # Solo domain_logic y los HTML se recargan en caliente. Si cambió cualquier otro .py
        # (sync_engine, config, main, database...) el proceso sigue con el código viejo en memoria:
        # hay que reiniciarlo para que el cambio tenga efecto.
        if any(f.endswith(".py") and f != "domain_logic.py" for f in cambiados):
            reiniciar_proceso_completo()

        return {
            "actualizado": True,
            "version_anterior": sha_local[:7],
            "version_nueva": sha_remoto[:7],
        }
    except Exception as exc:
        return {"actualizado": False, "motivo": "excepcion", "detalle": str(exc)}


def reiniciar_proceso_completo() -> None:
    """
    Reinicia el servidor local por completo (nuevo proceso, código nuevo en memoria).
    Lanza un relevo desacoplado que espera a que este proceso libere el puerto y
    vuelve a encender lanzador_escritorio.py sin abrir ventanas nuevas; luego termina este proceso.
    """
    try:
        py = sys.executable
        if py.lower().endswith("python.exe"):
            pyw = py[:-10] + "pythonw.exe"
            if os.path.exists(pyw):
                py = pyw
        lanzador = str(BASE_APP_DIR / "lanzador_escritorio.py")
        relevo = (
            "import subprocess,sys,time; time.sleep(4); "
            "subprocess.Popen([sys.argv[1], sys.argv[2], '--no-browser'], cwd=sys.argv[3], "
            "creationflags=0x00000008|0x08000000, close_fds=True)"
        )
        subprocess.Popen(
            [py, "-c", relevo, py, lanzador, str(BASE_APP_DIR)],
            cwd=str(BASE_APP_DIR),
            creationflags=(0x00000008 | 0x08000000) if os.name == "nt" else 0,
            close_fds=True,
        )
        print("[AUTO-UPDATER] Reiniciando servidor para aplicar la actualización...")
        time.sleep(0.5)
        os._exit(0)
    except Exception as exc:
        print(f"[AUTO-UPDATER] No se pudo reiniciar el proceso: {exc}")


def reiniciar_servidor_en_caliente() -> None:
    """Alias compatible: aplica recarga en memoria sin tumbar el puerto 8080."""
    recargar_en_memoria_sin_cortes()


def iniciar_hilo_auto_actualizacion(intervalo_segundos: int = 30) -> None:
    def _worker():
        while True:
            time.sleep(intervalo_segundos)
            res = verificar_y_aplicar_actualizacion()
            if res.get("actualizado"):
                print(
                    f"[AUTO-UPDATER] Actualizado en caliente ({res.get('version_anterior')} -> "
                    f"{res.get('version_nueva')}) sin desconexión."
                )

    t = threading.Thread(target=_worker, name="GitAutoUpdater", daemon=True)
    t.start()
