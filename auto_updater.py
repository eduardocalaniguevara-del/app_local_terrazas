#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Motor de Auto-Actualización silenciosa Dual (Git + HTTPS Nativo Python) para Multi-Hotel.
Revisa cada 20 segundos si hay cambios en GitHub (origin/main):
- Funciona CON Git o SIN Git (descarga directa sin caché desde codeload.github.com).
- Respeta siempre la carpeta data/ (hotel_perfil.json, credentials.json, ovalo_local.db).
- Si cambian vistas HTML/JS o domain_logic.py, recarga en caliente en 0 ms.
- Si cambian motores base (.py), reinicia el servidor silenciosamente en segundo plano.
"""

import importlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Dict, Any, List

from config import BASE_APP_DIR, DATA_DIR, NOMBRE_HOTEL, PERFIL_HOTEL_PATH, SPREADSHEET_ID

_GIT_CANDIDATES = [
    r"C:\Hostal_Sistema\tools\mingit\cmd\git.exe",
    r"C:\Program Files\Git\cmd\git.exe",
    r"C:\Program Files (x86)\Git\cmd\git.exe",
]

_SHA_FILE = DATA_DIR / "git_head_sha.txt"
_TERRAZAS_SHEET_ID = "1vgjOLxc-eh1zw42uC3pxnM86Lo2jZkz8_uwrEFhw8ss"


def _obtener_repo_slug() -> str:
    es_terrazas = (
        "terrazas" in NOMBRE_HOTEL.lower()
        or SPREADSHEET_ID.strip() == _TERRAZAS_SHEET_ID
        or "terrazas" in str(BASE_APP_DIR).lower()
    )
    if es_terrazas:
        return "eduardocalaniguevara-del/app_local_terrazas"
    return "eduardocalaniguevara-del/app_local_recepcion"


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


def _leer_sha_guardado() -> str:
    try:
        if _SHA_FILE.exists():
            val = _SHA_FILE.read_text(encoding="utf-8").strip()
            if len(val) >= 7:
                return val
    except Exception:
        pass
    return ""


def _guardar_sha_local(sha_full: str) -> None:
    if not sha_full:
        return
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _SHA_FILE.write_text(sha_full.strip(), encoding="utf-8")
    except Exception:
        pass


def obtener_version_actual() -> str:
    sha_txt = _leer_sha_guardado()
    git_dir = BASE_APP_DIR / ".git"
    if git_dir.exists():
        try:
            r = _run_git(["rev-parse", "--short", "HEAD"], timeout=5)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()
        except Exception:
            pass
    if sha_txt:
        return sha_txt[:7]
    try:
        p1 = (BASE_APP_DIR / "domain_logic.py").stat().st_mtime
        p2 = (BASE_APP_DIR / "static" / "PanelDisponibilidad.html").stat().st_mtime
        return f"local-{int(max(p1, p2))}"
    except Exception:
        return "local-0"


def _obtener_sha_local_completo() -> str:
    git_dir = BASE_APP_DIR / ".git"
    if git_dir.exists():
        try:
            r = _run_git(["rev-parse", "HEAD"], timeout=5)
            if r.returncode == 0 and len(r.stdout.strip()) == 40:
                return r.stdout.strip()
        except Exception:
            pass
    return _leer_sha_guardado()


def _consultar_sha_remoto_https(repo_slug: str) -> str:
    """
    Consulta el SHA exacto de refs/heads/main en tiempo real (0 caché CDN, 0 rate limit, 0 git.exe).
    """
    url = f"https://github.com/{repo_slug}/info/refs?service=git-upload-pack"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "git/2.47.1",
            "Cache-Control": "no-cache, no-store",
            "Pragma": "no-cache",
        },
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    m = re.search(r"([0-9a-f]{40})\s+refs/heads/main\b", raw)
    return m.group(1) if m else ""


def _aplicar_zip_remoto_https(repo_slug: str, sha_remoto: str) -> List[str]:
    """
    Descarga el archivo ZIP exacto del commit por SHA desde codeload.github.com (inmune a caché)
    y sobrescribe únicamente los archivos modificados, protegiendo siempre data/ y bases .db.
    """
    url = f"https://codeload.github.com/{repo_slug}/zip/{sha_remoto}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 HostalUpdater/2.0"})
    with urllib.request.urlopen(req, timeout=25) as resp:
        zip_bytes = resp.read()

    cambiados: List[str] = []
    protected_prefixes = ("data/", ".git/", "__pycache__/")
    protected_exts = (".db", ".db-wal", ".db-shm", ".db-journal", ".log", ".pyc")

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            parts = info.filename.split("/", 1)
            if len(parts) < 2 or not parts[1]:
                continue
            rel_posix = parts[1]
            rel_lower = rel_posix.lower()
            if any(rel_lower.startswith(pref) for pref in protected_prefixes):
                continue
            if any(rel_lower.endswith(ext) for ext in protected_exts):
                continue

            new_data = zf.read(info.filename)
            dest = BASE_APP_DIR / Path(rel_posix)
            old_data = None
            if dest.exists():
                try:
                    old_data = dest.read_bytes()
                except Exception:
                    old_data = None

            if old_data != new_data:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(new_data)
                cambiados.append(rel_posix)

    _guardar_sha_local(sha_remoto)
    return cambiados


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
    de PanelDisponibilidad.html para que el navegador refresque la vista automáticamente.
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
        return set(json.loads(f.read_text(encoding="utf-8-sig")))
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
    Puerta de Mando Remota vía GitHub (ordenes_hoteles.json).
    """
    ord_path = BASE_APP_DIR / "ordenes_hoteles.json"
    if not ord_path.exists():
        return
    try:
        data = json.loads(ord_path.read_text(encoding="utf-8-sig"))
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
            pkgs = ord_item.get("pip_install")
            if isinstance(pkgs, list) and pkgs:
                subprocess.run(
                    [sys.executable, "-m", "pip", "install", "--quiet"] + [str(p) for p in pkgs],
                    cwd=str(BASE_APP_DIR),
                    timeout=180,
                    creationflags=creationflags,
                )

            upd_perfil = ord_item.get("actualizar_perfil")
            if isinstance(upd_perfil, dict) and upd_perfil:
                actual = {}
                if PERFIL_HOTEL_PATH.exists():
                    try:
                        actual = json.loads(PERFIL_HOTEL_PATH.read_text(encoding="utf-8-sig"))
                    except Exception:
                        actual = {}
                actual.update(upd_perfil)
                PERFIL_HOTEL_PATH.write_text(
                    json.dumps(actual, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )

            cmd_str = str(ord_item.get("comando_cmd") or "").strip()
            if cmd_str:
                subprocess.run(
                    cmd_str,
                    shell=True,
                    cwd=str(BASE_APP_DIR),
                    timeout=180,
                    creationflags=creationflags,
                )

            nuevo_git = str(ord_item.get("cambiar_git_url") or "").strip()
            if nuevo_git and (BASE_APP_DIR / ".git").exists():
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
    Comprueba si GitHub (refs/heads/main) tiene un commit más reciente que la instalación local.
    Intenta primero por Git si existe .git funcional; si no existe .git o falla cualquier paso,
    aplica actualización directa por HTTPS ZIP en Python puro (0 dependencia de git.exe).
    """
    _verificar_recarga_local_mtime()
    procesar_ordenes_remotas()

    repo_slug = _obtener_repo_slug()
    repo_url = f"https://github.com/{repo_slug}.git"

    try:
        sha_remoto = _consultar_sha_remoto_https(repo_slug)
    except Exception as exc:
        sha_remoto = ""
        err_https = str(exc)

    sha_local = _obtener_sha_local_completo()

    if sha_remoto and sha_local and sha_remoto == sha_local:
        return {"actualizado": False, "motivo": "al_dia", "version": sha_local[:7]}

    cambiados: List[str] = []
    actualizado_por_git = False
    git_dir = BASE_APP_DIR / ".git"

    if git_dir.exists():
        try:
            _run_git(["config", "--global", "--add", "safe.directory", str(BASE_APP_DIR).replace("\\", "/")], timeout=5)
            _run_git(["remote", "set-url", "origin", repo_url], timeout=5)
            f = _run_git(["fetch", "origin", "main", "--quiet"], timeout=15)
            if f.returncode == 0:
                loc = _run_git(["rev-parse", "HEAD"], timeout=5)
                rem = _run_git(["rev-parse", "origin/main"], timeout=5)
                if loc.returncode == 0 and rem.returncode == 0:
                    s_loc = loc.stdout.strip()
                    s_rem = rem.stdout.strip()
                    if s_rem and s_loc == s_rem:
                        _guardar_sha_local(s_rem)
                        return {"actualizado": False, "motivo": "al_dia", "version": s_rem[:7]}
                    if s_rem:
                        try:
                            df = _run_git(["diff", "--name-only", s_loc, s_rem], timeout=10)
                            cambiados = [x.strip() for x in df.stdout.splitlines() if x.strip()]
                        except Exception:
                            cambiados = []
                        rst = _run_git(["reset", "--hard", "origin/main"], timeout=15)
                        if rst.returncode == 0:
                            sha_local = s_loc
                            sha_remoto = s_rem
                            _guardar_sha_local(s_rem)
                            actualizado_por_git = True
        except Exception:
            actualizado_por_git = False

    if not actualizado_por_git:
        if not sha_remoto:
            return {"actualizado": False, "motivo": "sin_conexion_github", "version": obtener_version_actual()}
        try:
            cambiados = _aplicar_zip_remoto_https(repo_slug, sha_remoto)
        except Exception as exc:
            return {"actualizado": False, "motivo": "zip_error", "detalle": str(exc)}

    procesar_ordenes_remotas()
    recargar_en_memoria_sin_cortes()

    core_py = {
        "main.py",
        "sync_engine.py",
        "config.py",
        "database.py",
        "auto_updater.py",
        "lanzador_escritorio.py",
        "sunat_wsp_service.py",
    }
    if any(os.path.basename(f) in core_py for f in cambiados):
        reiniciar_proceso_completo()

    return {
        "actualizado": True,
        "metodo": "git" if actualizado_por_git else "https_zip",
        "archivos_cambiados": len(cambiados),
        "version_anterior": sha_local[:7] if sha_local else "init",
        "version_nueva": sha_remoto[:7],
    }


def reiniciar_proceso_completo() -> None:
    """
    Reinicia el servidor local por completo (nuevo proceso, código nuevo en memoria).
    Si existe reiniciar_app.ps1 en Windows, lo usa para limpiar procesos duplicados de esta carpeta;
    de lo contrario, lanza un relevo Python desacoplado.
    """
    try:
        ps1_path = BASE_APP_DIR / "reiniciar_app.ps1"
        if os.name == "nt" and ps1_path.exists():
            subprocess.Popen(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-WindowStyle",
                    "Hidden",
                    "-File",
                    str(ps1_path),
                ],
                cwd=str(BASE_APP_DIR),
                creationflags=subprocess.CREATE_NO_WINDOW,
                close_fds=True,
            )
            print("[AUTO-UPDATER] Reiniciando servidor vía reiniciar_app.ps1...")
            return

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
    """Alias compatible: aplica recarga en memoria sin tumbar el puerto."""
    recargar_en_memoria_sin_cortes()


def iniciar_hilo_auto_actualizacion(intervalo_segundos: int = 20) -> None:
    def _worker():
        time.sleep(5)
        while True:
            try:
                res = verificar_y_aplicar_actualizacion()
                if res.get("actualizado"):
                    print(
                        f"[AUTO-UPDATER] Actualizado ({res.get('metodo')}): "
                        f"{res.get('version_anterior')} -> {res.get('version_nueva')}"
                    )
            except Exception as exc:
                print(f"[AUTO-UPDATER] Aviso en ciclo: {exc}")
            time.sleep(intervalo_segundos)

    t = threading.Thread(target=_worker, name="GitAutoUpdater", daemon=True)
    t.start()
