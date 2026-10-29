#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import datetime
import json
import socket
import sqlite3
import sys
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import config
import sync_engine

SHEET_ID = "1vgjOLxc-eh1zw42uC3pxnM86Lo2jZkz8_uwrEFhw8ss"
db_path = BASE_DIR / "data" / "ovalo_local.db"


def contar_db() -> int:
    if not db_path.exists():
        return 0
    conn = sqlite3.connect(str(db_path), timeout=15.0)
    try:
        r = conn.execute("SELECT COUNT(*) FROM habitaciones_config").fetchone()
        return int(r[0]) if r else 0
    finally:
        conn.close()


n_db = contar_db()
if n_db < 48:
    try:
        sync_engine.pull_from_sheets(full_bootstrap=False)
    except Exception:
        pass
    n_db = contar_db()

n_api = -1
try:
    req = urllib.request.Request(
        f"http://127.0.0.1:{config.APP_PORT}/api/rpc/obtenerDatosPanelDisponibilidad",
        data=b'{"args":[]}',
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    resp = json.loads(urllib.request.urlopen(req, timeout=5).read().decode("utf-8", "replace"))
    habs = (resp.get("result") or {}).get("habitaciones") or []
    n_api = len(habs)
except Exception:
    pass

panel_file = BASE_DIR / "static" / "PanelDisponibilidad.html"
has_btn = panel_file.exists() and ("btnTestTerrazas" in panel_file.read_text(encoding="utf-8", errors="replace"))

try:
    import gspread
    from google.oauth2.service_account import Credentials

    creds = Credentials.from_service_account_file(config.obtener_ruta_credenciales(), scopes=config.SCOPES)
    gc = gspread.authorize(creds)
    ws = gc.open_by_key(SHEET_ID).worksheet("Config")
    pc = socket.gethostname()
    celda = "P2" if pc.lower() == "eduardocal" else "P3"
    hora = datetime.datetime.now().strftime("%H:%M:%S")
    ws.update_acell(celda, f"GITHUB_20S_OK | PC={pc} | DB_Habs={n_db} | API_Habs={n_api} | Boton={has_btn} | {hora}")
except Exception:
    pass
