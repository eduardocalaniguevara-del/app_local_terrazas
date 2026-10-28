#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Script de autoreparación y desacoplamiento exclusivo para Hotel Terrazas de Villa.
Solo se ejecuta si la carpeta contiene 'terrazas' (ej. C:\\Recepcion_Terrazas_Local).
En Hotel El Óvalo no hace absolutamente nada.
"""

import json
import sqlite3
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

if "terrazas" not in str(BASE_DIR).lower():
    sys.exit(0)

DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

PERFIL_TERRAZAS = {
    "nombre_hotel": "Hotel Terrazas de Villa",
    "spreadsheet_id": "1vgjOLxc-eh1zw42uC3pxnM86Lo2jZkz8_uwrEFhw8ss",
    "col_tipo_adicional": "AZ",
    "formula_snacks_en_s2": True,
    "app_port": 8081,
}

# 1. Escribir hotel_perfil.json limpio sin BOM
perfil_path = DATA_DIR / "hotel_perfil.json"
perfil_path.write_text(json.dumps(PERFIL_TERRAZAS, indent=2, ensure_ascii=False), encoding="utf-8")

# 2. Limpiar caché del Óvalo en SQLite si existiera y sincronizar hoja de Terrazas
db_path = DATA_DIR / "ovalo_local.db"
if db_path.exists():
    try:
        conn = sqlite3.connect(str(db_path), timeout=20.0)
        conn.execute("DELETE FROM habitaciones_config")
        conn.execute("DELETE FROM estancias WHERE dirty = 0")
        conn.execute("DELETE FROM pagos WHERE synced = 1")
        conn.execute("DELETE FROM ventas_tienda WHERE synced = 1")
        conn.execute("DELETE FROM gastos WHERE synced = 1")
        conn.execute("DELETE FROM sunat_lineas_cache")
        conn.execute("DELETE FROM calzado_cache")
        conn.execute("DELETE FROM vitrina_local")
        conn.execute(
            "INSERT OR REPLACE INTO estado_sistema (clave, valor, updated_at) VALUES ('spreadsheet_id_activo', ?, '')",
            (PERFIL_TERRAZAS["spreadsheet_id"],),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass

# 3. Descargar inmediatamente las 29 habitaciones de Terrazas desde Google Sheets
try:
    if str(BASE_DIR) not in sys.path:
        sys.path.insert(0, str(BASE_DIR))
    import sync_engine
    sync_engine.SPREADSHEET_ID = PERFIL_TERRAZAS["spreadsheet_id"]
    sync_engine._reset_client()
    sync_engine.pull_from_sheets(full_bootstrap=False)
except Exception:
    pass

# 4. Apuntar el repositorio Git local al repositorio exclusivo de Terrazas
try:
    from auto_updater import _run_git
    _run_git(["remote", "set-url", "origin", "https://github.com/eduardocalaniguevara-del/app_local_terrazas.git"], timeout=10)
except Exception:
    pass
