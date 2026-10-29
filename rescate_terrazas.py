#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Script de Rescate y Sincronización Autónoma para Hotel Terrazas de Villa.
Descarga la última versión desde GitHub por HTTPS puro (sin requerir Git),
actualiza a 48 habitaciones, reporta telemetría y reinicia el servidor en caliente.
"""

import datetime
import io
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

REPO_SLUG = "eduardocalaniguevara-del/app_local_terrazas"
SHEET_ID = "1vgjOLxc-eh1zw42uC3pxnM86Lo2jZkz8_uwrEFhw8ss"
ORDEN_ID = "SHEETS_AUTO_SYNC_TERRAZAS_V4"


def detectar_puerto_activo() -> int:
    # 1. Revisar puerto configurado en hotel_perfil.json si existe
    perfil_path = DATA_DIR / "hotel_perfil.json"
    puerto_cfg = None
    if perfil_path.exists():
        try:
            d = json.loads(perfil_path.read_text(encoding="utf-8-sig"))
            if isinstance(d, dict) and d.get("app_port"):
                puerto_cfg = int(d["app_port"])
        except Exception:
            pass

    # 2. Probar qué puerto está respondiendo en vivo en esta PC
    candidatos = []
    if puerto_cfg in (8080, 8081):
        candidatos.append(puerto_cfg)
    for p in (8081, 8080):
        if p not in candidatos:
            candidatos.append(p)

    for p in candidatos:
        try:
            raw = urllib.request.urlopen(f"http://127.0.0.1:{p}/api/sync/status", timeout=1.5).read().decode("utf-8", "replace")
            st = json.loads(raw)
            if "terrazas" in str(st.get("nombreHotel", "")).lower():
                return p
        except Exception:
            pass

    for p in candidatos:
        try:
            with socket.create_connection(("127.0.0.1", p), timeout=0.5):
                return p
        except OSError:
            pass

    return puerto_cfg or 8081


def obtener_sha_remoto() -> str:
    url = f"https://github.com/{REPO_SLUG}/info/refs?service=git-upload-pack"
    req = urllib.request.Request(url, headers={"User-Agent": "git/2.47.1", "Cache-Control": "no-cache"})
    raw = urllib.request.urlopen(req, timeout=12).read().decode("utf-8", errors="replace")
    m = re.search(r"([0-9a-f]{40})\s+refs/heads/main\b", raw)
    return m.group(1) if m else ""


def descargar_y_extraer(sha: str) -> int:
    url = f"https://codeload.github.com/{REPO_SLUG}/zip/{sha}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 RescateTerrazas/1.0"})
    zip_bytes = urllib.request.urlopen(req, timeout=30).read()

    protected_prefixes = ("data/", ".git/", "__pycache__/")
    protected_exts = (".db", ".db-wal", ".db-shm", ".db-journal", ".log", ".pyc")
    actualizados = 0

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
            dest = BASE_DIR / Path(rel_posix)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(zf.read(info.filename))
            actualizados += 1

    (DATA_DIR / "git_head_sha.txt").write_text(sha, encoding="utf-8")
    panel_html = BASE_DIR / "static" / "PanelDisponibilidad.html"
    if panel_html.exists():
        now_ts = time.time()
        os.utime(panel_html, (now_ts, now_ts))
    return actualizados


def registrar_orden_ejecutada() -> None:
    f = DATA_DIR / "ordenes_ejecutadas.json"
    ejecutadas = set()
    if f.exists():
        try:
            ejecutadas = set(json.loads(f.read_text(encoding="utf-8-sig")))
        except Exception:
            ejecutadas = set()
    ejecutadas.add(ORDEN_ID)
    f.write_text(json.dumps(sorted(ejecutadas), indent=2, ensure_ascii=False), encoding="utf-8")


def main() -> None:
    puerto = detectar_puerto_activo()
    perfil = {
        "nombre_hotel": "Hotel Terrazas de Villa",
        "spreadsheet_id": SHEET_ID,
        "col_tipo_adicional": "AZ",
        "formula_snacks_en_s2": True,
        "app_port": puerto,
    }
    (DATA_DIR / "hotel_perfil.json").write_text(
        json.dumps(perfil, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    sha = obtener_sha_remoto()
    if sha:
        descargar_y_extraer(sha)

    registrar_orden_ejecutada()

    # Sincronizar las 48 habitaciones con el nuevo sync_engine (sin borrar antes por si hay cuota 429)
    db_path = DATA_DIR / "ovalo_local.db"

    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        subprocess.run(
            [sys.executable, "-c", "import sync_engine; sync_engine.pull_from_sheets(full_bootstrap=False)"],
            cwd=str(BASE_DIR),
            timeout=45,
            creationflags=creationflags,
        )
    except Exception:
        pass

    n_habs = 0
    if db_path.exists():
        try:
            conn = sqlite3.connect(str(db_path), timeout=10.0)
            row = conn.execute("SELECT COUNT(*) FROM habitaciones_config").fetchone()
            n_habs = int(row[0]) if row else 0
            conn.close()
        except Exception:
            pass

    # Reportar confirmación en Config!P2 (PC desarrollo) o Config!P3 (Laptop Hotel Terrazas)
    try:
        if str(BASE_DIR) not in sys.path:
            sys.path.insert(0, str(BASE_DIR))
        import config
        import gspread
        from google.oauth2.service_account import Credentials

        creds = Credentials.from_service_account_file(config.obtener_ruta_credenciales(), scopes=config.SCOPES)
        gc = gspread.authorize(creds)
        ws = gc.open_by_key(SHEET_ID).worksheet("Config")
        hostname = socket.gethostname()
        celda = "P2" if hostname.lower() == "eduardocal" else "P3"
        hora = datetime.datetime.now().strftime("%H:%M:%S")
        msg = f"OK | PC={hostname} | Port={puerto} | Habs={n_habs} | SHA={sha[:7] if sha else 'local'} | {hora}"
        ws.update_acell(celda, msg)
    except Exception:
        pass

    # Reiniciar el proceso servidor en segundo plano para cargar todos los módulos nuevos
    ps1_path = BASE_DIR / "reiniciar_app.ps1"
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
            cwd=str(BASE_DIR),
            creationflags=subprocess.CREATE_NO_WINDOW,
            close_fds=True,
        )


if __name__ == "__main__":
    main()
