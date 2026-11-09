#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Motor de Sincronización Bidireccional Offline/Online con Google Sheets
- Bootstrap y Pull Incremental (100% lectura pasiva, respeta estancias con dirty=1)
- Cola Outbox FIFO Idempotente (0 duplicados ante cortes de red, respeta fórmulas MAP)
"""

import json
import re
import threading
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

import gspread
from google.oauth2.service_account import Credentials

from config import DATA_DIR, SCOPES, SPREADSHEET_ID, SYNC_INTERVAL_SECONDS, obtener_ruta_credenciales
from database import db_transaction, get_connection
from config import COL_TIPO_ADICIONAL, COL_TIPO_ADICIONAL_IDX, FORMULA_SNACKS_EN_S2
from domain_logic import (
    calcular_noches,
    fecha_dmy_a_ymd,
    normalizar_dni,
    normalizar_fecha_dmy,
    parse_moneda,
)

_sync_lock = threading.Lock()
_gspread_client = None
_spreadsheet = None


_sheet_tabs_cache = None


def _get_spreadsheet():
    global _gspread_client, _spreadsheet
    if _spreadsheet is not None and getattr(_spreadsheet, "id", None) == SPREADSHEET_ID:
        return _spreadsheet
    creds_path = obtener_ruta_credenciales()
    creds = Credentials.from_service_account_file(creds_path, scopes=SCOPES)
    _gspread_client = gspread.authorize(creds)
    try:
        _gspread_client.set_timeout(20)
    except Exception:
        pass
    _spreadsheet = _gspread_client.open_by_key(SPREADSHEET_ID)
    return _spreadsheet


def _get_sheet_tabs(sh):
    global _sheet_tabs_cache
    if _sheet_tabs_cache is None:
        try:
            _sheet_tabs_cache = {w.title for w in sh.worksheets()}
        except Exception:
            _sheet_tabs_cache = set()
    return _sheet_tabs_cache


def _reset_client():
    global _gspread_client, _spreadsheet, _sheet_tabs_cache
    _gspread_client = None
    _spreadsheet = None
    _sheet_tabs_cache = None


def _set_estado_sistema(conn, clave: str, valor: str) -> None:
    conn.execute(
        """
        INSERT INTO estado_sistema (clave, valor, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor, updated_at = excluded.updated_at
        """,
        (clave, str(valor), datetime.now().isoformat()),
    )


def _rgb_to_hex(rgb_dict: Dict[str, Any]) -> str:
    if not rgb_dict:
        return "#ffffff"
    r = int(round(float(rgb_dict.get("red", 1.0)) * 255))
    g = int(round(float(rgb_dict.get("green", 1.0)) * 255))
    b = int(round(float(rgb_dict.get("blue", 1.0)) * 255))
    return f"#{r:02x}{g:02x}{b:02x}".lower()


MAPA_COLORES_FUERTES = {
    "#f4cccc": "#f87171",
    "#fce5cd": "#fdba74",
    "#fb923c": "#fdba74",
    "#d9ead3": "#4ade80",
    "#fff2cc": "#facc15",
    "#c9daf8": "#60a5fa",
}


def _canon_ts_id(v: Any) -> str:
    """Normaliza timestamps '2/11/2026 9:32:00' -> '02/11/2026 09:32:00' para cruce exacto Local <-> Sheets."""
    s = str(v or "").strip()
    if not s:
        return ""
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{2,4})(?:\s+(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?)?(.*)$", s)
    if m:
        dd, mm, yy = int(m.group(1)), int(m.group(2)), m.group(3)
        if len(yy) == 2:
            yy = "20" + yy
        tail = m.group(7) or ""
        if m.group(4) is not None:
            hh = int(m.group(4))
            mi = int(m.group(5) or 0)
            ss = int(m.group(6) or 0)
            return f"{dd:02d}/{mm:02d}/{yy} {hh:02d}:{mi:02d}:{ss:02d}{tail}"
        return f"{dd:02d}/{mm:02d}/{yy}{tail}"
    return s


# ==============================================================================
# 1. PULL / BOOTSTRAP DESDE GOOGLE SHEETS (100% SOLO LECTURA)
# ==============================================================================

def pull_from_sheets(full_bootstrap: bool = False) -> Dict[str, Any]:
    """
    Lee los datos de Google Sheets y actualiza la caché local SQLite
    sin tocar ninguna estancia local que tenga cambios pendientes de subir (dirty = 1).
    """
    if not _sync_lock.acquire(blocking=False):
        return {"status": "BUSY"}

    try:
        sh = _get_spreadsheet()

        # 1. Lectura unificada por lotes (values_batch_get) — 2 llamadas en lugar de 21
        def _extraer_vr(vr_list: List[Dict[str, Any]], idx: int) -> List[List[Any]]:
            if idx < len(vr_list) and isinstance(vr_list[idx], dict):
                return vr_list[idx].get("values") or []
            return []

        tabs = _get_sheet_tabs(sh)

        ranges_step1 = [
            "'Config'!A2:N150",
            "'Panel_Disponibilidad'!D1:G1",
            "'Panel_Disponibilidad'!A3:D150",
            "'Registro_Estancias'!A:A",
            "'2.Registro_Ventas_Tienda'!A:A",
            "'Registro_Pagos'!A:A",
            "'Registro_Gastos'!A:A",
        ]
        keys_step1 = [
            "config", "disp_top", "panel", "col_a_bd", "col_t", "col_pg", "col_g"
        ]
        if "SUNAT_OCT" in tabs:
            ranges_step1.append("'SUNAT_OCT'!A2:M800")
            keys_step1.append("sunat")
        if "CALZADO_OCT" in tabs:
            ranges_step1.append("'CALZADO_OCT'!A2:V800")
            keys_step1.append("calzado")
        if "Vitrina_Tienda" in tabs:
            ranges_step1.append("'Vitrina_Tienda'!A2:M45")
            keys_step1.append("vitrina")
        if "import_Almacen" in tabs:
            ranges_step1.append("'import_Almacen'!A2:J3000")
            keys_step1.append("almacen")
        if "import_Horario" in tabs:
            ranges_step1.append("'import_Horario'!A2:L8000")
            keys_step1.append("horario")
        if "import_Horario_Audit" in tabs:
            ranges_step1.append("'import_Horario_Audit'!A2:H6000")
            keys_step1.append("horario_audit")
        if "import_Control_Pases" in tabs:
            ranges_step1.append("'import_Control_Pases'!A2:N8000")
            keys_step1.append("control_pases")

        res_step1 = sh.values_batch_get(ranges_step1)
        vrs1 = res_step1.get("valueRanges", []) if isinstance(res_step1, dict) else []

        step1_dict = {}
        for idx_k, k_name in enumerate(keys_step1):
            step1_dict[k_name] = _extraer_vr(vrs1, idx_k)

        cfg_rows = step1_dict.get("config", [])
        if cfg_rows and len(cfg_rows[0]) >= 14 and cfg_rows[0][13]:
            try:
                from auto_updater import ejecutar_salvavidas_sheets
                ejecutar_salvavidas_sheets(str(cfg_rows[0][13]))
            except Exception:
                pass
        disp_top = step1_dict.get("disp_top", [[""]]) or [[""]]
        raw_caja = disp_top[0][3] if (disp_top and len(disp_top[0]) >= 4) else "0.00"
        caja_g1 = f"{parse_moneda(raw_caja):.2f}"
        panel_rows = step1_dict.get("panel", [])
        sunat_rows = step1_dict.get("sunat", [])
        calzado_rows = step1_dict.get("calzado", [])

        col_a_bd = step1_dict.get("col_a_bd", [])
        col_t = step1_dict.get("col_t", [])
        col_pg = step1_dict.get("col_pg", [])
        col_g = step1_dict.get("col_g", [])
        vitrina_rows = step1_dict.get("vitrina", [])
        almacen_rows = step1_dict.get("almacen", [])
        almacen_rows = step1_dict.get("almacen", [])
        horario_rows = step1_dict.get("horario", [])
        horario_audit_rows = step1_dict.get("horario_audit", [])
        control_pases_rows = step1_dict.get("control_pases", [])

        total_rows_bd = max(len(col_a_bd), 1)
        last_t = len(col_t)
        last_pg = len(col_pg)
        last_g = len(col_g)

        # 2. Segunda lectura por lotes para ventanas recientes de las 4 hojas operativas
        all_bd_values = None
        recent_bd_rows = []
        tienda_rows = []
        pagos_hist_rows = []
        gastos_rows = []

        if full_bootstrap:
            ws_bd = sh.worksheet("Registro_Estancias")
            all_bd_values = ws_bd.get_all_values()
            total_rows_bd = len(all_bd_values)
            start_row_recent = 2
            recent_bd_rows = all_bd_values[1:]
        else:
            start_row_recent = max(2, total_rows_bd - 550 + 1)

        st_t = 2 if (full_bootstrap or last_t <= 5000) else max(2, last_t - 1500 + 1)
        st_pg = 2 if (full_bootstrap or last_pg <= 5000) else max(2, last_pg - 1500 + 1)
        st_g = 2 if (full_bootstrap or last_g <= 4000) else max(2, last_g - 1500 + 1)

        ranges_step2 = []
        keys_step2 = []
        if not full_bootstrap and total_rows_bd >= 2:
            ranges_step2.append(f"'Registro_Estancias'!A{start_row_recent}:BA{total_rows_bd}")
            keys_step2.append("bd")
        if last_t >= 2:
            ranges_step2.append(f"'2.Registro_Ventas_Tienda'!A{st_t}:L{last_t}")
            keys_step2.append("tienda")
        if last_pg >= 2:
            ranges_step2.append(f"'Registro_Pagos'!A{st_pg}:J{last_pg}")
            keys_step2.append("pagos")
        if last_g >= 2:
            ranges_step2.append(f"'Registro_Gastos'!A{st_g}:J{last_g}")
            keys_step2.append("gastos")

        if ranges_step2:
            res_step2 = sh.values_batch_get(ranges_step2)
            vrs2 = res_step2.get("valueRanges", []) if isinstance(res_step2, dict) else []
            for idx_k, k_name in enumerate(keys_step2):
                rows_k = _extraer_vr(vrs2, idx_k)
                if k_name == "bd":
                    recent_bd_rows = rows_k
                elif k_name == "tienda":
                    tienda_rows = [(st_t + i, r) for i, r in enumerate(rows_k)]
                elif k_name == "pagos":
                    pagos_hist_rows = [(st_pg + i, r) for i, r in enumerate(rows_k)]
                elif k_name == "gastos":
                    gastos_rows = [(st_g + i, r) for i, r in enumerate(rows_k)]

        # 5. Guardar todo en SQLite en una transacción rápida
        ahora_iso = datetime.now().isoformat()
        with db_transaction() as conn:
            prev_sh_row = conn.execute("SELECT valor FROM estado_sistema WHERE clave = 'spreadsheet_id_activo'").fetchone()
            prev_sh_id = str(prev_sh_row["valor"]).strip() if prev_sh_row else ""
            if prev_sh_id != SPREADSHEET_ID:
                conn.execute("DELETE FROM habitaciones_config")
                conn.execute("DELETE FROM estancias WHERE dirty = 0")
                conn.execute("DELETE FROM pagos WHERE synced = 1")
                conn.execute("DELETE FROM ventas_tienda WHERE synced = 1")
                conn.execute("DELETE FROM gastos WHERE synced = 1")
                conn.execute("DELETE FROM sunat_lineas_cache")
                conn.execute("DELETE FROM calzado_cache")
                conn.execute("DELETE FROM vitrina_local")
                conn.execute("DELETE FROM almacen_movimientos WHERE synced = 1")
                _set_estado_sistema(conn, "spreadsheet_id_activo", SPREADSHEET_ID)

            _set_estado_sistema(conn, "caja_sheets_g1", caja_g1)
            _set_estado_sistema(conn, "network_online", "1")
            _set_estado_sistema(conn, "last_sync_ts", datetime.now().strftime("%H:%M:%S"))
            _set_estado_sistema(conn, "last_successful_pull_iso", ahora_iso)
            _set_estado_sistema(conn, "offline_since_iso", "")
            _set_estado_sistema(conn, "last_sync_error", "")
            _set_estado_sistema(conn, "last_row_estancias", str(total_rows_bd))
            if full_bootstrap:
                _set_estado_sistema(conn, "bootstrap_full_v2", "1")

            # Catálogos de Config
            conn.execute("DELETE FROM catalogos_local")
            for idx_c, r in enumerate(cfg_rows):
                def _c(col_idx: int) -> str:
                    return str(r[col_idx]).strip() if len(r) > col_idx and r[col_idx] is not None else ""

                prod = _c(2)
                precio_p = _c(3)
                cat_g = _c(4)
                met_g = _c(5)
                pers = _c(7)
                tip_cg = _c(8)
                hot_ap = _c(13)

                if prod:
                    conn.execute("INSERT OR REPLACE INTO catalogos_local (categoria, clave, valor, orden) VALUES ('PRODUCTO_TIENDA', ?, ?, ?)", (prod, precio_p or "0", idx_c))
                if cat_g:
                    conn.execute("INSERT OR REPLACE INTO catalogos_local (categoria, clave, valor, orden) VALUES ('CAT_GASTO', ?, '', ?)", (cat_g, idx_c))
                if met_g:
                    conn.execute("INSERT OR REPLACE INTO catalogos_local (categoria, clave, valor, orden) VALUES ('MET_GASTO', ?, '', ?)", (met_g, idx_c))
                if pers:
                    conn.execute("INSERT OR REPLACE INTO catalogos_local (categoria, clave, valor, orden) VALUES ('PERSONAL', ?, '', ?)", (pers, idx_c))
                if tip_cg:
                    conn.execute("INSERT OR REPLACE INTO catalogos_local (categoria, clave, valor, orden) VALUES ('TIPO_COMP_GASTO', ?, '', ?)", (tip_cg, idx_c))
                if hot_ap:
                    conn.execute("INSERT OR REPLACE INTO catalogos_local (categoria, clave, valor, orden) VALUES ('HOTEL_APOYO', ?, '', ?)", (hot_ap, idx_c))

            # Habitaciones: lista maestra = Config!A (Lista_Habitaciones) + Config!B (Tipo).
            # Sin límite fijo de filas (Óvalo 29, Terrazas 48...). Panel_Disponibilidad aporta
            # el precio de referencia (col A) y el orden visual; los precios numéricos agrupados
            # (ej. "75-65") se heredan hacia abajo, las etiquetas de texto ("DF2", "DOBLE") no.
            precio_por_hab: Dict[str, str] = {}
            tipo_panel_por_hab: Dict[str, str] = {}
            orden_panel: List[str] = []
            ultimo_precio_num = ""
            for r in panel_rows:
                h_p = str(r[1]).strip() if len(r) > 1 and r[1] else ""
                if not h_p:
                    continue
                orden_panel.append(h_p)
                p_celda = str(r[0]).strip() if len(r) > 0 and r[0] else ""
                if p_celda and re.match(r"^\d+(\.\d+)?(\s*-\s*\d+(\.\d+)?)?$", p_celda):
                    ultimo_precio_num = p_celda
                    precio_por_hab[h_p] = p_celda
                elif p_celda:
                    precio_por_hab[h_p] = p_celda
                else:
                    precio_por_hab[h_p] = ultimo_precio_num
                tipo_panel_por_hab[h_p] = str(r[3]).strip() if len(r) > 3 and r[3] else ""

            tipo_cfg_por_hab: Dict[str, str] = {}
            orden_cfg: List[str] = []
            for r in cfg_rows:
                h_c = str(r[0]).strip() if len(r) > 0 and r[0] else ""
                if not h_c or h_c.lower().startswith("lista"):
                    continue
                if h_c not in tipo_cfg_por_hab:
                    orden_cfg.append(h_c)
                tipo_cfg_por_hab[h_c] = str(r[1]).strip() if len(r) > 1 and r[1] else ""

            habs_ordenadas: List[str] = list(orden_panel)
            for h_c in orden_cfg:
                if h_c not in habs_ordenadas:
                    habs_ordenadas.append(h_c)

            habs_validas: List[str] = []
            for idx_p, hab_num in enumerate(habs_ordenadas):
                habs_validas.append(hab_num)
                tipo_h = tipo_panel_por_hab.get(hab_num) or tipo_cfg_por_hab.get(hab_num, "")
                ultimo_precio_ref = precio_por_hab.get(hab_num, "")

                conn.execute(
                    """
                    INSERT INTO habitaciones_config (hab, orden, tipo, precio_ref)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(hab) DO UPDATE SET
                        orden = excluded.orden,
                        tipo = excluded.tipo,
                        precio_ref = excluded.precio_ref
                    """,
                    (hab_num, idx_p + 1, tipo_h, ultimo_precio_ref),
                )
            if habs_validas:
                placeholders = ",".join("?" for _ in habs_validas)
                conn.execute(f"DELETE FROM habitaciones_config WHERE hab NOT IN ({placeholders})", tuple(habs_validas))

            # Si es bootstrap completo, poblar el padrón histórico de huéspedes y empresas (~5,400 filas)
            if all_bd_values is not None and len(all_bd_values) >= 2:
                padron_h: Dict[str, Dict[str, Any]] = {}
                padron_e: Dict[str, str] = {}
                for r in all_bd_values[1:]:
                    if len(r) < 5:
                        continue
                    nac_r = str(r[8]).strip() if len(r) > 8 and r[8] else "Perú"
                    dni_r = normalizar_dni(r[4], nac_r)
                    if not dni_r:
                        continue
                    nom_r = str(r[3]).strip() if len(r) > 3 and r[3] else ""
                    sexo_r = str(r[5]).strip() if len(r) > 5 and r[5] else "M"
                    reg_r = str(r[9]).strip() if len(r) > 9 and r[9] else "Lima"
                    mot_r = str(r[10]).strip() if len(r) > 10 and r[10] else "Vacaciones, recreo u ocio"
                    hab_r = str(r[11]).strip() if len(r) > 11 and r[11] else ""
                    tar_r = parse_moneda(r[16]) if len(r) > 16 else 0.0
                    emp_r = str(r[42]).strip() if len(r) > 42 and r[42] else ""
                    cel_r = str(r[49]).strip() if len(r) > 49 and r[49] else ""
                    ruc_r = re.sub(r"\D", "", str(r[50])) if len(r) > 50 and r[50] else ""

                    prev = padron_h.get(dni_r)
                    vis_prev = prev["visitas"] if prev else 0
                    padron_h[dni_r] = {
                        "nombre": nom_r or (prev["nombre"] if prev else ""),
                        "sexo": sexo_r or (prev["sexo"] if prev else "M"),
                        "nac": nac_r or (prev["nac"] if prev else "Perú"),
                        "reg": reg_r or (prev["reg"] if prev else "Lima"),
                        "mot": mot_r or (prev["mot"] if prev else "Vacaciones, recreo u ocio"),
                        "hab_frec": hab_r or (prev["hab_frec"] if prev else ""),
                        "tarifa_frec": tar_r if tar_r > 0 else (prev["tarifa_frec"] if prev else 0.0),
                        "empresa": emp_r or (prev["empresa"] if prev else ""),
                        "celular": cel_r or (prev["celular"] if prev else ""),
                        "ruc": ruc_r or (prev["ruc"] if prev else ""),
                        "visitas": vis_prev + 1,
                    }
                    if ruc_r and emp_r:
                        padron_e[ruc_r] = emp_r

                for dni_k, pd in padron_h.items():
                    conn.execute(
                        """
                        INSERT INTO huespedes_padron (dni, nombre, sexo, nac, reg, mot, hab_frec, tarifa_frec, empresa, celular, ruc, visitas, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(dni) DO UPDATE SET
                            nombre = excluded.nombre,
                            sexo = excluded.sexo,
                            nac = excluded.nac,
                            reg = excluded.reg,
                            mot = excluded.mot,
                            hab_frec = excluded.hab_frec,
                            tarifa_frec = excluded.tarifa_frec,
                            empresa = excluded.empresa,
                            celular = excluded.celular,
                            ruc = excluded.ruc,
                            visitas = excluded.visitas,
                            updated_at = excluded.updated_at
                        """,
                        (
                            dni_k, pd["nombre"], pd["sexo"], pd["nac"], pd["reg"], pd["mot"],
                            pd["hab_frec"], pd["tarifa_frec"], pd["empresa"], pd["celular"],
                            pd["ruc"], pd["visitas"], ahora_iso,
                        ),
                    )
                for ruc_k, rs_v in padron_e.items():
                    conn.execute(
                        """
                        INSERT INTO empresas_padron (ruc, razon_social, updated_at)
                        VALUES (?, ?, ?)
                        ON CONFLICT(ruc) DO UPDATE SET razon_social = excluded.razon_social, updated_at = excluded.updated_at
                        """,
                        (ruc_k, rs_v, ahora_iso),
                    )

            # Actualizar ventana reciente de estancias (respetando dirty = 1 y normalizando timestamps)
            pend_est_ops = conn.execute(
                "SELECT 1 FROM sync_queue WHERE op_type IN ('CHECKIN', 'CONTINUAR', 'ELIMINAR_ESTANCIA_ADMIN') AND status != 'SYNCED' LIMIT 1"
            ).fetchone()
            ts_en_sheets = set()
            seen_raw_bd = set()
            for offset, r in enumerate(recent_bd_rows):
                sheet_row_num = start_row_recent + offset
                if not r or len(r) < 12:
                    continue
                hab_r = str(r[11]).strip() if len(r) > 11 and r[11] else ""
                if not hab_r:
                    continue

                def _g(idx_col: int) -> str:
                    return str(r[idx_col]).strip() if len(r) > idx_col and r[idx_col] is not None else ""

                raw_ts_a = _g(0) or f"ROW-{sheet_row_num}"
                canon_ts_a = _canon_ts_id(raw_ts_a)
                es_repetido_bd = canon_ts_a in seen_raw_bd
                seen_raw_bd.add(canon_ts_a)

                existing = None
                if not es_repetido_bd:
                    existing = conn.execute(
                        "SELECT estancia_uuid, id_timestamp_str, dirty, parent_uuid FROM estancias WHERE id_timestamp_str IN (?, ?) LIMIT 1",
                        (raw_ts_a, canon_ts_a),
                    ).fetchone()
                else:
                    id_disamb = f"{raw_ts_a} #{sheet_row_num}"
                    existing = conn.execute(
                        "SELECT estancia_uuid, id_timestamp_str, dirty, parent_uuid FROM estancias WHERE id_timestamp_str = ? OR (sheet_row = ? AND hab = ?) LIMIT 1",
                        (id_disamb, sheet_row_num, hab_r),
                    ).fetchone()

                if existing and existing["id_timestamp_str"] not in ts_en_sheets:
                    id_ts_val = existing["id_timestamp_str"]
                elif es_repetido_bd:
                    id_ts_val = f"{raw_ts_a} #{sheet_row_num}"
                else:
                    id_ts_val = canon_ts_a

                ts_en_sheets.add(id_ts_val)

                if existing and int(existing["dirty"] or 0) == 1:
                    # No sobrescribir cambios locales pendientes de subir
                    continue

                est_uuid = existing["estancia_uuid"] if existing else str(uuid.uuid4())
                parent_u = existing["parent_uuid"] if existing else ""

                f_in = normalizar_fecha_dmy(_g(1))
                f_out = normalizar_fecha_dmy(_g(2))
                ymd_in = fecha_dmy_a_ymd(f_in)
                ymd_out = fecha_dmy_a_ymd(f_out) or (ymd_in + 1 if ymd_in else 0)
                nac_val = _g(8) or "Perú"
                dni_val = normalizar_dni(_g(4), nac_val)
                noches_val = max(1, int(parse_moneda(_g(15))) or calcular_noches(f_in, f_out))

                conn.execute(
                    """
                    INSERT INTO estancias (
                        estancia_uuid, id_timestamp_str, sheet_row, parent_uuid,
                        fecha_in_dmy, fecha_out_dmy, ymd_in, ymd_out, hora_registro,
                        huesped, dni, sexo, n_acomp, dni_acomp, nac, reg, mot,
                        hab, tipo_hab, visita, noches, tarifa,
                        adic_snacks, adic_servicios, adic_comp,
                        pago_ini_efec, pago_ini_yape, pago_post_efec, pago_post_yape, pago_visa,
                        deuda_anterior, cuenta_destino, solicita_comp, tipo_comp, estado_comp,
                        nro_comp, monto_factura, estado_estancia, empresa, proyecto,
                        triple1, triple2, hotel_apoyo, observaciones, nro_op_yape, celular, ruc,
                        excedente_efec_sunat, dirty, updated_at
                    ) VALUES (
                        ?, ?, ?, ?,
                        ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?,
                        ?, ?, ?,
                        ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?,
                        ?, 0, ?
                    )
                    ON CONFLICT(id_timestamp_str) DO UPDATE SET
                        sheet_row = excluded.sheet_row,
                        fecha_in_dmy = excluded.fecha_in_dmy,
                        fecha_out_dmy = excluded.fecha_out_dmy,
                        ymd_in = excluded.ymd_in,
                        ymd_out = excluded.ymd_out,
                        hora_registro = excluded.hora_registro,
                        huesped = excluded.huesped,
                        dni = excluded.dni,
                        sexo = excluded.sexo,
                        n_acomp = excluded.n_acomp,
                        dni_acomp = excluded.dni_acomp,
                        nac = excluded.nac,
                        reg = excluded.reg,
                        mot = excluded.mot,
                        hab = excluded.hab,
                        tipo_hab = excluded.tipo_hab,
                        visita = excluded.visita,
                        noches = excluded.noches,
                        tarifa = excluded.tarifa,
                        adic_snacks = excluded.adic_snacks,
                        adic_servicios = excluded.adic_servicios,
                        adic_comp = excluded.adic_comp,
                        pago_ini_efec = excluded.pago_ini_efec,
                        pago_ini_yape = excluded.pago_ini_yape,
                        pago_post_efec = excluded.pago_post_efec,
                        pago_post_yape = excluded.pago_post_yape,
                        pago_visa = excluded.pago_visa,
                        deuda_anterior = excluded.deuda_anterior,
                        solicita_comp = excluded.solicita_comp,
                        tipo_comp = excluded.tipo_comp,
                        estado_comp = excluded.estado_comp,
                        nro_comp = excluded.nro_comp,
                        monto_factura = excluded.monto_factura,
                        estado_estancia = excluded.estado_estancia,
                        empresa = excluded.empresa,
                        proyecto = excluded.proyecto,
                        triple1 = excluded.triple1,
                        triple2 = excluded.triple2,
                        hotel_apoyo = excluded.hotel_apoyo,
                        observaciones = excluded.observaciones,
                        nro_op_yape = excluded.nro_op_yape,
                        celular = excluded.celular,
                        ruc = excluded.ruc,
                        excedente_efec_sunat = excluded.excedente_efec_sunat,
                        updated_at = excluded.updated_at
                    WHERE estancias.dirty = 0
                    """,
                    (
                        est_uuid, id_ts_val, sheet_row_num, parent_u,
                        f_in, f_out, ymd_in, ymd_out, _g(14),
                        _g(3), dni_val, _g(5) or "M", _g(6), _g(7), nac_val, _g(9) or "Lima", _g(10),
                        hab_r, _g(12), _g(13) or "Visita N° 1", noches_val, parse_moneda(_g(16)),
                        parse_moneda(_g(18)), parse_moneda(_g(19)), parse_moneda(_g(20)),
                        parse_moneda(_g(22)), parse_moneda(_g(23)), parse_moneda(_g(24)), parse_moneda(_g(25)), parse_moneda(_g(38)),
                        parse_moneda(_g(37)), _g(30), _g(31) or "No", _g(32), _g(33),
                        _g(34), parse_moneda(_g(35)), _g(36) or "En Curso", _g(42), _g(43),
                        _g(44), _g(45), _g(46), _g(47), _g(48), _g(49), re.sub(r"\D", "", _g(50)),
                        parse_moneda(_g(51)) if COL_TIPO_ADICIONAL_IDX != 51 else 0.0, ahora_iso,
                    ),
                )
                conn.execute(
                    "UPDATE estancias SET tipo_adicional = ? WHERE id_timestamp_str = ? AND dirty = 0",
                    (_g(COL_TIPO_ADICIONAL_IDX), id_ts_val),
                )

            # Limpiar en espejo estancias eliminadas en Registro_Estancias (ventana reciente + histórico por Col A)
            if not pend_est_ops:
                stale_recs = conn.execute(
                    "SELECT estancia_uuid, id_timestamp_str FROM estancias WHERE dirty = 0 AND sheet_row >= ?",
                    (start_row_recent,),
                ).fetchall()
                for st_r in stale_recs:
                    if st_r["id_timestamp_str"] not in ts_en_sheets:
                        conn.execute("DELETE FROM estancias WHERE estancia_uuid = ?", (st_r["estancia_uuid"],))

                if col_a_bd and len(col_a_bd) >= 1:
                    canon_all_bd = set()
                    canon_hist_row: Dict[str, int] = {}
                    canon_hist_dup = set()
                    for idx_ca, x in enumerate(col_a_bd[1:], start=2):
                        if x and str(x[0]).strip():
                            ck_bd = _canon_ts_id(x[0])
                            canon_all_bd.add(ck_bd)
                            if idx_ca < start_row_recent:
                                if ck_bd in canon_hist_row:
                                    canon_hist_dup.add(ck_bd)
                                else:
                                    canon_hist_row[ck_bd] = idx_ca
                    old_recs = conn.execute(
                        "SELECT estancia_uuid, id_timestamp_str, sheet_row FROM estancias WHERE dirty = 0 AND sheet_row >= 2 AND sheet_row < ?",
                        (start_row_recent,),
                    ).fetchall()
                    for old_r in old_recs:
                        ts_base = str(old_r["id_timestamp_str"] or "").split(" #")[0]
                        if not ts_base or ts_base.startswith("ROW-"):
                            continue
                        ck_old = _canon_ts_id(ts_base)
                        if ck_old not in canon_all_bd:
                            conn.execute("DELETE FROM estancias WHERE estancia_uuid = ?", (old_r["estancia_uuid"],))
                        elif ck_old in canon_hist_row and ck_old not in canon_hist_dup and int(old_r["sheet_row"] or 0) != canon_hist_row[ck_old]:
                            conn.execute("UPDATE estancias SET sheet_row = ? WHERE estancia_uuid = ?", (canon_hist_row[ck_old], old_r["estancia_uuid"]))

            # Actualizar SUNAT_OCT y CALZADO_OCT
            if sunat_rows:
                conn.execute("DELETE FROM sunat_lineas_cache")
                for s in sunat_rows:
                    def _gs(idx_s: int) -> str:
                        return str(s[idx_s]).strip() if len(s) > idx_s and s[idx_s] is not None else ""
                    nro_cp = _gs(4).upper()
                    hab_s = _gs(1).upper()
                    if not nro_cp and not hab_s:
                        continue
                    conn.execute(
                        """
                        INSERT INTO sunat_lineas_cache (
                            nro_cp, tipo, noche_dmy, hab, monto_linea, total_cp,
                            cliente, doc, fecha_emision, estado, descripcion
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            nro_cp,
                            (_gs(3) or "BOLETA").upper(),
                            normalizar_fecha_dmy(_gs(0)),
                            hab_s,
                            parse_moneda(_gs(2)),
                            parse_moneda(_gs(9)),
                            re.sub(r"^-\s*", "", _gs(5)).strip(),
                            _gs(6),
                            normalizar_fecha_dmy(_gs(7)),
                            (_gs(8) or "VIGENTE").upper(),
                            _gs(11),
                        ),
                    )

            if calzado_rows:
                conn.execute("DELETE FROM calzado_cache")
                for idx_cal, c in enumerate(calzado_rows):
                    def _gc(idx_c: int) -> str:
                        return str(c[idx_c]).strip() if len(c) > idx_c and c[idx_c] is not None else ""
                    noche_c = normalizar_fecha_dmy(_gc(1))
                    hab_c = _gc(2).upper()
                    fila_est = _gc(5).upper()
                    if not noche_c and not fila_est:
                        continue
                    clave_cal = fila_est if fila_est else f"{noche_c}|{hab_c}|{idx_cal}"
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO calzado_cache (
                            clave_calzado, fila_calzado, noche_dmy, hab, huesped, dni,
                            fila_est, monto_sunat, comprobantes, monto_aj, yape_ab,
                            efectivo_aa, excedente_efec, yape_bcp, estado_bcp, estado_conciliacion, accion
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            clave_cal,
                            idx_cal + 2,
                            noche_c,
                            hab_c,
                            _gc(3),
                            _gc(4),
                            fila_est,
                            parse_moneda(_gc(6)),
                            _gc(7),
                            parse_moneda(_gc(8)),
                            parse_moneda(_gc(11)),
                            parse_moneda(_gc(12)),
                            parse_moneda(_gc(13)),
                            parse_moneda(_gc(14)),
                            _gc(15),
                            _gc(19),
                            _gc(21),
                        ),
                    )

            # Actualizar Tienda y Pagos en espejo 1:1 con Sheets (respetando locales synced=0)
            pend_tienda_ops = conn.execute(
                "SELECT 1 FROM sync_queue WHERE op_type IN ('TIENDA', 'EDITAR_VENTA_TIENDA_ADMIN', 'ELIMINAR_VENTA_TIENDA_ADMIN') AND status != 'SYNCED' LIMIT 1"
            ).fetchone()
            if not pend_tienda_ops:
                conn.execute("DELETE FROM ventas_tienda WHERE synced = 1 AND (sheet_row >= ? OR sheet_row = 0)", (st_t,))
                if col_t is not None and st_t > 2:
                    canon_all_t = {_canon_ts_id(x[0]) for x in col_t[1:] if x and str(x[0]).strip()}
                    for old_vt in conn.execute("SELECT venta_uuid, fecha_iso FROM ventas_tienda WHERE synced = 1 AND sheet_row >= 2 AND sheet_row < ?", (st_t,)).fetchall():
                        if _canon_ts_id(old_vt["fecha_iso"]) not in canon_all_t:
                            conn.execute("DELETE FROM ventas_tienda WHERE venta_uuid = ?", (old_vt["venta_uuid"],))
            if tienda_rows:
                dirty_t_rows = {
                    int(r["sheet_row"])
                    for r in conn.execute("SELECT sheet_row FROM ventas_tienda WHERE synced = 0 AND sheet_row >= 2").fetchall()
                }
                if pend_tienda_ops:
                    conn.execute("DELETE FROM ventas_tienda WHERE synced = 1 AND sheet_row >= ?", (st_t,))
                for s_row_t, tr in tienda_rows:
                    if s_row_t in dirty_t_rows:
                        continue
                    if len(tr) < 9:
                        continue
                    prod_t = str(tr[2] or "").strip()
                    sub_t = parse_moneda(tr[8])
                    if not prod_t or sub_t <= 0:
                        continue
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO ventas_tienda (
                            venta_uuid, lote_uuid, estancia_uuid, id_estancia, fecha_iso, fecha_dmy,
                            producto, cantidad, metodo_pago, hab, personal, precio_unit, subtotal, nro_op_yape, sheet_row, synced
                        ) VALUES (?, 'SHEET', '', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                        """,
                        (
                            f"SH-T-{s_row_t}",
                            str(tr[9] or "").strip() if len(tr) > 9 else "",
                            str(tr[0] or "").strip(),
                            normalizar_fecha_dmy(tr[1]),
                            prod_t,
                            int(parse_moneda(tr[3]) or 1),
                            str(tr[4] or "").strip(),
                            str(tr[5] or "").strip(),
                            str(tr[6] or "").strip() if len(tr) > 6 else "",
                            parse_moneda(tr[7]) if len(tr) > 7 else sub_t,
                            sub_t,
                            str(tr[11] or "").strip() if len(tr) > 11 else "",
                            s_row_t,
                        ),
                    )

            pend_pago_ops = conn.execute(
                "SELECT 1 FROM sync_queue WHERE op_type IN ('PAGO', 'EDITAR_PAGO_ADMIN', 'ELIMINAR_PAGO_ADMIN', 'ELIMINAR_ESTANCIA_ADMIN') AND status != 'SYNCED' LIMIT 1"
            ).fetchone()
            if not pend_pago_ops:
                conn.execute("DELETE FROM pagos WHERE synced = 1 AND (sheet_row >= ? OR sheet_row = 0)", (st_pg,))
                if col_pg is not None and st_pg > 2:
                    ids_all_pg = {str(x[0]).strip() for x in col_pg[1:] if x and str(x[0]).strip()}
                    for old_pg in conn.execute("SELECT pago_id FROM pagos WHERE synced = 1 AND sheet_row >= 2 AND sheet_row < ?", (st_pg,)).fetchall():
                        if str(old_pg["pago_id"]).strip() not in ids_all_pg:
                            conn.execute("DELETE FROM pagos WHERE pago_id = ?", (old_pg["pago_id"],))
            if pagos_hist_rows:
                dirty_p_ids = {
                    str(r["pago_id"])
                    for r in conn.execute("SELECT pago_id FROM pagos WHERE synced = 0").fetchall()
                }
                if pend_pago_ops:
                    conn.execute("DELETE FROM pagos WHERE synced = 1 AND sheet_row >= ?", (st_pg,))
                for s_row_p, pr_h in pagos_hist_rows:
                    pid_s = str(pr_h[0] or "").strip() if len(pr_h) > 0 else ""
                    if not pid_s or len(pr_h) < 6:
                        continue
                    if pid_s in dirty_p_ids:
                        continue
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO pagos (
                            pago_id, estancia_uuid, id_estancia, fecha_iso, hab, huesped, monto,
                            medio_pago, tipo_pago, concepto, nro_operacion, sheet_row, synced
                        ) VALUES (?, '', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                        """,
                        (
                            pid_s,
                            str(pr_h[2] or "").strip() if len(pr_h) > 2 else "",
                            str(pr_h[1] or "").strip() if len(pr_h) > 1 else "",
                            str(pr_h[3] or "").strip() if len(pr_h) > 3 else "",
                            str(pr_h[4] or "").strip() if len(pr_h) > 4 else "",
                            parse_moneda(pr_h[5]) if len(pr_h) > 5 else 0.0,
                            str(pr_h[6] or "").strip() if len(pr_h) > 6 else "",
                            str(pr_h[7] or "").strip() if len(pr_h) > 7 else "",
                            str(pr_h[8] or "").strip() if len(pr_h) > 8 else "",
                            str(pr_h[9] or "").strip() if len(pr_h) > 9 else "",
                            s_row_p,
                        ),
                    )

            # Actualizar Vitrina_Tienda (A2:M45) respetando conteos locales pendientes de subir
            if vitrina_rows and len(vitrina_rows) >= 5:
                pend_vit = conn.execute(
                    "SELECT 1 FROM sync_queue WHERE op_type = 'GUARDAR_CONTEO_VITRINA' AND status != 'SYNCED' LIMIT 1"
                ).fetchone()
                def _gv(row_list: List[Any], idx_v: int) -> str:
                    return str(row_list[idx_v]).strip() if len(row_list) > idx_v and row_list[idx_v] is not None else ""

                if not pend_vit:
                    resp_e2 = _gv(vitrina_rows[0], 4) if len(vitrina_rows) > 0 else ""
                    fecha_e3 = _gv(vitrina_rows[1], 4) if len(vitrina_rows) > 1 else ""
                    row4 = vitrina_rows[2] if len(vitrina_rows) > 2 else []
                    h1_hdr = _gv(row4, 7)
                    h2_hdr = _gv(row4, 9)
                    h3_hdr = _gv(row4, 11)
                    _set_estado_sistema(conn, "vitrina_responsable_e2", resp_e2)
                    _set_estado_sistema(conn, "vitrina_fecha_e3", fecha_e3)
                    _set_estado_sistema(conn, "vitrina_h1_header", h1_hdr)
                    _set_estado_sistema(conn, "vitrina_h2_header", h2_hdr)
                    _set_estado_sistema(conn, "vitrina_h3_header", h3_hdr)

                    conn.execute("DELETE FROM vitrina_local")
                    for idx_vp, r_vp in enumerate(vitrina_rows[4:]):
                        prod_vp = _gv(r_vp, 0)
                        if not prod_vp:
                            continue
                        stock_vp = parse_moneda(_gv(r_vp, 1))
                        est_act = _gv(r_vp, 6)
                        h1_c, h1_e = _gv(r_vp, 7), _gv(r_vp, 8)
                        h2_c, h2_e = _gv(r_vp, 9), _gv(r_vp, 10)
                        h3_c, h3_e = _gv(r_vp, 11), _gv(r_vp, 12)
                        conn.execute(
                            """
                            INSERT OR REPLACE INTO vitrina_local (
                                producto, orden, stock_sheets, estado_actual,
                                h1_cant, h1_est, h2_cant, h2_est, h3_cant, h3_est, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (prod_vp, idx_vp + 1, stock_vp, est_act, h1_c, h1_e, h2_c, h2_e, h3_c, h3_e, ahora_iso),
                        )
                else:
                    # Solo actualizar stock_sheets sin pisar el conteo local pendiente
                    for idx_vp, r_vp in enumerate(vitrina_rows[4:]):
                        prod_vp = _gv(r_vp, 0)
                        if not prod_vp:
                            continue
                        stock_vp = parse_moneda(_gv(r_vp, 1))
                        conn.execute(
                            "UPDATE vitrina_local SET stock_sheets = ?, orden = ? WHERE producto = ?",
                            (stock_vp, idx_vp + 1, prod_vp),
                        )

            # Actualizar Kardex de Almacén (import_Almacen!A2:J3000) respetando movimientos locales synced=0
            if almacen_rows:
                pend_del_alm = conn.execute(
                    "SELECT 1 FROM sync_queue WHERE op_type = 'ELIMINAR_MOVIMIENTO_ALMACEN' AND status != 'SYNCED' LIMIT 1"
                ).fetchone()
                if not pend_del_alm:
                    conn.execute("DELETE FROM almacen_movimientos WHERE synced = 1")
                    for idx_am, ar in enumerate(almacen_rows):
                        s_row_am = 2 + idx_am
                        if not ar or not any(str(c or "").strip() for c in ar):
                            continue
                        prod_am = str(ar[2] or "").strip() if len(ar) > 2 else ""
                        tipo_am = str(ar[7] or "").strip() if len(ar) > 7 else ""
                        if not prod_am or not tipo_am:
                            continue
                        id_tx_am = str(ar[0] or "").strip()
                        fecha_am = normalizar_fecha_dmy(ar[1]) if len(ar) > 1 else ""
                        try:
                            cant_am = round(float(str(ar[3] or "0").replace(",", "").strip() or 0), 4)
                        except Exception:
                            cant_am = parse_moneda(ar[3]) if len(ar) > 3 else 0.0
                        costo_t_am = parse_moneda(ar[4]) if len(ar) > 4 else 0.0
                        costo_u_am = parse_moneda(ar[5]) if len(ar) > 5 else 0.0
                        met_am = str(ar[6] or "N/A").strip() if len(ar) > 6 else "N/A"
                        nota_am = str(ar[8] or "").strip() if len(ar) > 8 else ""
                        uuid_am = str(ar[9] or "").strip() if len(ar) > 9 else ""
                        if not uuid_am:
                            uuid_am = f"SH-ALM-{s_row_am}"
                        conn.execute(
                            """
                            INSERT OR IGNORE INTO almacen_movimientos (
                                mov_uuid, lote_uuid, id_transaccion, fecha_dmy, producto,
                                cantidad, costo_total, costo_unitario, metodo_pago, tipo_movimiento, nota, sheet_row, synced
                            ) VALUES (?, '', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                            """,
                            (
                                uuid_am,
                                id_tx_am,
                                fecha_am,
                                prod_am,
                                cant_am,
                                costo_t_am,
                                costo_u_am,
                                met_am,
                                tipo_am,
                                nota_am,
                                s_row_am,
                            ),
                        )

            # Actualizar Gastos en espejo 1:1 con Registro_Gastos (sin colisiones de gasto_uuid ni duplicados por fecha)
            pend_gasto_ops = conn.execute(
                "SELECT 1 FROM sync_queue WHERE op_type IN ('GASTO', 'EDITAR_GASTO_ADMIN', 'ELIMINAR_GASTO_ADMIN') AND status != 'SYNCED' LIMIT 1"
            ).fetchone()
            prev_g_by_canon = {}
            dirty_g_canon = set()
            seen_local_g = set()
            for rg_loc in conn.execute(
                "SELECT gasto_uuid, id_ts_str, personal, synced FROM gastos ORDER BY CASE WHEN gasto_uuid LIKE 'SH-G-%' THEN 1 ELSE 0 END ASC, synced ASC"
            ).fetchall():
                u_prev = str(rg_loc["gasto_uuid"] or "")
                c_k = _canon_ts_id(str(rg_loc["id_ts_str"] or "").split(" #")[0])
                if int(rg_loc["synced"] or 0) == 0:
                    dirty_g_canon.add(c_k)
                    seen_local_g.add(c_k)
                else:
                    if c_k and not c_k.startswith("SH-G-") and c_k in seen_local_g and u_prev.startswith("SH-G-"):
                        conn.execute("DELETE FROM gastos WHERE gasto_uuid = ?", (u_prev,))
                        continue
                    seen_local_g.add(c_k)
                    if c_k not in prev_g_by_canon:
                        prev_g_by_canon[c_k] = {
                            "uuid": u_prev if not u_prev.startswith("SH-G-") else str(uuid.uuid4()),
                            "personal": str(rg_loc["personal"] or "").strip(),
                        }

            if not pend_gasto_ops:
                conn.execute("DELETE FROM gastos WHERE synced = 1 AND (sheet_row >= ? OR sheet_row = 0)", (st_g,))
            else:
                conn.execute("DELETE FROM gastos WHERE synced = 1 AND gasto_uuid LIKE 'SH-G-%' AND sheet_row >= ?", (st_g,))

            if not pend_gasto_ops and col_g is not None and st_g > 2:
                canon_all_g = {_canon_ts_id(x[0]) for x in col_g[1:] if x and str(x[0]).strip()}
                for old_g in conn.execute("SELECT gasto_uuid, id_ts_str FROM gastos WHERE synced = 1 AND sheet_row >= 2 AND sheet_row < ?", (st_g,)).fetchall():
                    base_old_g = str(old_g["id_ts_str"] or "").split(" #")[0]
                    if not base_old_g.startswith("SH-G-") and _canon_ts_id(base_old_g) not in canon_all_g:
                        conn.execute("DELETE FROM gastos WHERE gasto_uuid = ?", (old_g["gasto_uuid"],))

            if gastos_rows:
                rem_synced_by_canon = {}
                for rg_rem in conn.execute("SELECT gasto_uuid, id_ts_str FROM gastos WHERE synced = 1").fetchall():
                    ck_rem = _canon_ts_id(str(rg_rem["id_ts_str"] or "").split(" #")[0])
                    rem_synced_by_canon.setdefault(ck_rem, []).append((str(rg_rem["gasto_uuid"]), str(rg_rem["id_ts_str"])))
                seen_g_canon = set()
                used_g_uuids = {
                    str(r["gasto_uuid"]) for r in conn.execute("SELECT gasto_uuid FROM gastos").fetchall()
                }
                for s_row_g, gr in gastos_rows:
                    if len(gr) < 6:
                        continue
                    desc_g_raw = str(gr[2] or "").strip()
                    monto_g_raw = parse_moneda(gr[3])
                    if not str(gr[0] or "").strip() and not desc_g_raw and monto_g_raw <= 0:
                        continue
                    raw_id_g = str(gr[0] or "").strip()
                    canon_id_g = _canon_ts_id(raw_id_g) if raw_id_g else f"SH-G-{s_row_g}"
                    if canon_id_g in dirty_g_canon:
                        continue
                    if canon_id_g in seen_g_canon:
                        id_g_final = f"{canon_id_g} #{s_row_g}"
                    else:
                        id_g_final = canon_id_g
                        for u_old_rem, ts_old_rem in rem_synced_by_canon.get(canon_id_g, []):
                            if ts_old_rem != id_g_final:
                                conn.execute("DELETE FROM gastos WHERE gasto_uuid = ? AND synced = 1", (u_old_rem,))
                                used_g_uuids.discard(u_old_rem)
                    seen_g_canon.add(canon_id_g)

                    f_g_dmy = normalizar_fecha_dmy(gr[1])
                    hora_g = ""
                    if " " in canon_id_g:
                        partes_h = canon_id_g.split(" ")[1].split(":")
                        if len(partes_h) >= 2:
                            hora_g = f"{partes_h[0].zfill(2)}:{partes_h[1].zfill(2)}"

                    prev_info = prev_g_by_canon.get(canon_id_g) or {}
                    cand_uuid = prev_info.get("uuid") or str(uuid.uuid4())
                    if cand_uuid in used_g_uuids:
                        ex_same = conn.execute("SELECT gasto_uuid FROM gastos WHERE id_ts_str = ?", (id_g_final,)).fetchone()
                        if ex_same:
                            cand_uuid = str(ex_same["gasto_uuid"])
                        else:
                            cand_uuid = str(uuid.uuid4())
                    used_g_uuids.add(cand_uuid)

                    pers_sh = str(gr[9] or "").strip() if len(gr) > 9 else ""
                    if not pers_sh and prev_info.get("personal"):
                        pers_sh = prev_info["personal"]

                    conn.execute(
                        """
                        INSERT INTO gastos (
                            gasto_uuid, id_ts_str, fecha_gasto_iso, fecha_gasto_dmy, hora_registro,
                            descripcion, monto, categoria, metodo_pago, tipo_comp, nro_comp, responsable, personal, sheet_row, synced
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                        ON CONFLICT(id_ts_str) DO UPDATE SET
                            sheet_row = excluded.sheet_row,
                            fecha_gasto_dmy = excluded.fecha_gasto_dmy,
                            descripcion = excluded.descripcion,
                            monto = excluded.monto,
                            categoria = excluded.categoria,
                            metodo_pago = excluded.metodo_pago,
                            tipo_comp = excluded.tipo_comp,
                            nro_comp = excluded.nro_comp,
                            responsable = excluded.responsable,
                            personal = CASE WHEN excluded.personal != '' THEN excluded.personal ELSE gastos.personal END,
                            synced = 1
                        WHERE gastos.synced = 1
                        """,
                        (
                            cand_uuid,
                            id_g_final,
                            id_g_final if "/" in id_g_final else f_g_dmy,
                            f_g_dmy,
                            hora_g,
                            desc_g_raw,
                            monto_g_raw,
                            str(gr[4] or "").strip(),
                            str(gr[5] or "").strip(),
                            str(gr[6] or "").strip() if len(gr) > 6 else "",
                            str(gr[7] or "").strip() if len(gr) > 7 else "",
                            str(gr[8] or "Admin").strip() if len(gr) > 8 else "Admin",
                            pers_sh,
                            s_row_g,
                        ),
                    )

            if horario_rows:
                for hr in horario_rows:
                    if len(hr) < 2 or not str(hr[0] or "").strip():
                        continue
                    emp_h = str(hr[0]).strip()
                    f_h = normalizar_fecha_dmy(hr[1])
                    loc_h = conn.execute(
                        "SELECT synced FROM horario_registros WHERE empleado = ? AND fecha_dmy = ?", (emp_h, f_h)
                    ).fetchone()
                    if loc_h and int(loc_h["synced"] or 0) == 0:
                        continue
                    def _hc(i_h: int) -> str:
                        return str(hr[i_h] or "").strip() if len(hr) > i_h else ""
                    try:
                        xn = 1 if _hc(8) in ("1", "TRUE", "true") else 0
                    except Exception:
                        xn = 0
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO horario_registros
                            (empleado, fecha_dmy, ymd, e1, s1, e2, s2, estado, extras, extra_noche, nota, ult_marca_ts, updated_at, equipo, synced)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, 1)
                        """,
                        (emp_h, f_h, fecha_dmy_a_ymd(f_h), _hm_pull(_hc(2)), _hm_pull(_hc(3)), _hm_pull(_hc(4)), _hm_pull(_hc(5)), _hc(6), _hc(7), xn, _hc(9), _hc(10), _hc(11)),
                    )
            if horario_audit_rows:
                for ar_h in horario_audit_rows:
                    if len(ar_h) < 7 or not str(ar_h[0] or "").strip():
                        continue
                    campo_h = str(ar_h[3] or "").strip()
                    inv = {"Entrada 1": "e1", "Salida 1": "s1", "Entrada 2": "e2", "Salida 2": "s2", "Estado (F)": "estado",
                           "Horas extras (G)": "extras", "Extra nocturno": "extra_noche", "Nota": "nota"}
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO horario_auditoria
                            (audit_uuid, empleado, fecha_dmy, campo, valor_anterior, valor_nuevo, editado_ts, equipo, synced)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
                        """,
                        (str(ar_h[0]).strip(), str(ar_h[1] or "").strip(), normalizar_fecha_dmy(ar_h[2]), inv.get(campo_h, campo_h),
                         _hm_pull(ar_h[4]), _hm_pull(ar_h[5]), str(ar_h[6] or "").strip(),
                         str(ar_h[7] or "") if len(ar_h) > 7 else ""),
                    )
            if control_pases_rows:
                pend_pase_ops = conn.execute(
                    "SELECT 1 FROM sync_queue WHERE op_type IN ('PASE_UPSERT', 'PASE_DELETE') AND status != 'SYNCED' LIMIT 1"
                ).fetchone()
                ids_rem_pases = set()
                for pr_c in control_pases_rows:
                    if len(pr_c) < 10 or not str(pr_c[0] or "").strip():
                        continue
                    pu_id = str(pr_c[0]).strip()
                    ids_rem_pases.add(pu_id)
                    loc_p = conn.execute("SELECT synced FROM control_pases WHERE pase_uuid = ?", (pu_id,)).fetchone()
                    if loc_p and int(loc_p["synced"] or 0) == 0:
                        continue
                    f_p = normalizar_fecha_dmy(pr_c[1])
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO control_pases (
                            pase_uuid, fecha_dmy, ymd, hora, hab, estancia_uuid, id_estancia,
                            huesped_titular, dni_visita, nombre_visita, estado, monto,
                            motivo_rechazo, equipo, created_at, synced
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                        """,
                        (
                            pu_id, f_p, fecha_dmy_a_ymd(f_p), _hm_pull(pr_c[2]),
                            str(pr_c[3] or "").strip(), str(pr_c[4] or "").strip(), str(pr_c[5] or "").strip(),
                            str(pr_c[6] or "").strip(), str(pr_c[7] or "").strip(), str(pr_c[8] or "").strip(),
                            str(pr_c[9] or "ACEPTADO").strip().upper(), parse_moneda(pr_c[10]) if len(pr_c) > 10 else 0.0,
                            str(pr_c[11] or "").strip() if len(pr_c) > 11 else "",
                            str(pr_c[12] or "").strip() if len(pr_c) > 12 else "",
                            str(pr_c[13] or "").strip() if len(pr_c) > 13 else "",
                        ),
                    )
                if not pend_pase_ops:
                    for old_cp in conn.execute("SELECT pase_uuid FROM control_pases WHERE synced = 1").fetchall():
                        if str(old_cp["pase_uuid"]) not in ids_rem_pases:
                            conn.execute("DELETE FROM control_pases WHERE pase_uuid = ?", (old_cp["pase_uuid"],))

        return {
            "status": "OK",
            "estancias_leidas": len(recent_bd_rows),
            "total_filas_bd": total_rows_bd,
            "caja_g1": caja_g1,
        }
    except Exception as e:
        _reset_client()
        ahora_iso = datetime.now().isoformat()
        err_msg = str(e)[:250]
        try:
            with db_transaction() as conn:
                prev_off = conn.execute("SELECT valor FROM estado_sistema WHERE clave = 'offline_since_iso'").fetchone()
                if not prev_off or not prev_off["valor"]:
                    _set_estado_sistema(conn, "offline_since_iso", ahora_iso)
                _set_estado_sistema(conn, "network_online", "0")
                _set_estado_sistema(conn, "last_sync_error", err_msg)
        except Exception:
            pass
        return {"status": "OFFLINE", "error": str(e)}
    finally:
        _sync_lock.release()


# ==============================================================================
# 2. PUSH DE COLA OUTBOX FIFO IDEMPOTENTE (CERO DUPLICADOS EN GOOGLE SHEETS)
# ==============================================================================

def _resolver_fila_por_col_a(ws, sheet_row_hint: int, target_id: str) -> int:
    """Ubica la fila exacta en Sheets normalizando fechas con _canon_ts_id (1 sola llamada API)."""
    col_a = ws.col_values(1)
    base_id = str(target_id or "").split(" #")[0].strip()
    if base_id.startswith("ROW-"):
        if 2 <= sheet_row_hint <= len(col_a) and not str(col_a[sheet_row_hint - 1]).strip():
            return sheet_row_hint
        return 0
    canon_t = _canon_ts_id(base_id)
    if not canon_t:
        return 0
    if 2 <= sheet_row_hint <= len(col_a) and _canon_ts_id(col_a[sheet_row_hint - 1]) == canon_t:
        return sheet_row_hint
    matches = [idx + 1 for idx in range(1, len(col_a)) if _canon_ts_id(col_a[idx]) == canon_t]
    if not matches:
        return 0
    if sheet_row_hint and sheet_row_hint >= 2:
        return min(matches, key=lambda r: abs(r - sheet_row_hint))
    return matches[-1]


def _resolver_fila_estancia_en_sheets(ws_bd, sheet_row_hint: int, id_ts_str: str) -> int:
    return _resolver_fila_por_col_a(ws_bd, sheet_row_hint, id_ts_str)


def _resolver_fila_tienda(ws_tienda, sheet_row_hint: int, fecha_iso: str, producto_prev: str = "") -> int:
    """Ubica la fila exacta en 2.Registro_Ventas_Tienda cruzando Fecha_Hora (Col A) + Producto (Col C)."""
    vals_ac = ws_tienda.get("A1:C") or []
    canon_f = _canon_ts_id(str(fecha_iso or "").split(" #")[0])
    prod_norm = str(producto_prev or "").strip().lower()

    def _match_row(idx0: int, strict_prod: bool) -> bool:
        if idx0 < 1 or idx0 >= len(vals_ac):
            return False
        r = vals_ac[idx0]
        c_a = _canon_ts_id(r[0] if len(r) > 0 else "")
        if canon_f and c_a != canon_f:
            return False
        if strict_prod and prod_norm:
            c_prod = str(r[2] if len(r) > 2 else "").strip().lower()
            if c_prod != prod_norm:
                return False
        return bool(canon_f or prod_norm)

    if 2 <= sheet_row_hint <= len(vals_ac) and _match_row(sheet_row_hint - 1, True):
        return sheet_row_hint
    matches = [i + 1 for i in range(1, len(vals_ac)) if _match_row(i, True)]
    if not matches and prod_norm and canon_f:
        if 2 <= sheet_row_hint <= len(vals_ac) and _match_row(sheet_row_hint - 1, False):
            return sheet_row_hint
        matches = [i + 1 for i in range(1, len(vals_ac)) if _match_row(i, False)]
    if not matches:
        return 0
    if sheet_row_hint and sheet_row_hint >= 2:
        return min(matches, key=lambda r: abs(r - sheet_row_hint))
    return matches[-1]


def _append_fila_idempotente(ws, id_col_a: str, row_values: List[Any], range_end_col: str, value_input_option: str = "USER_ENTERED") -> int:
    """
    Verifica si id_col_a ya existe en las últimas 35 filas de la columna A (normalizando formato de fecha).
    Si ya existe (por reintento tras micro-corte), devuelve su fila sin duplicar.
    Si no existe, escribe en last_row + 1 con USER_ENTERED.
    """
    col_a = ws.col_values(1)
    last_row = len(col_a)
    canon_id = _canon_ts_id(id_col_a)
    if canon_id:
        for i in range(last_row - 1, max(0, last_row - 35), -1):
            if _canon_ts_id(col_a[i]) == canon_id:
                return i + 1

    next_row = max(2, last_row + 1)
    # Causa raíz del bloqueo de cola: si la hoja está llena hasta su última fila de cuadrícula,
    # Sheets rechaza escribir más abajo ("exceeds grid limits"). Apps Script lo hace solo con appendRow;
    # aquí ampliamos la cuadrícula antes de escribir.
    if next_row > ws.row_count:
        ws.add_rows(max(100, next_row - ws.row_count + 50))
    ws.update(
        range_name=f"A{next_row}:{range_end_col}{next_row}",
        values=[row_values],
        value_input_option=value_input_option,
    )
    return next_row


def _hm_pull(v: Any) -> str:
    s = str(v or "").strip()
    m = re.fullmatch(r"(\d{1,2}):(\d{2})(?::\d{2})?", s)
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else s


HORARIO_CABECERA = ["Empleado", "Fecha", "Entrada 1", "Salida 1", "Entrada 2", "Salida 2", "Estado (F)", "Horas extras (G)",
                    "Extra nocturno", "Nota", "Actualizado", "Equipo"]
HORARIO_AUDIT_CABECERA = ["ID", "Empleado", "Fecha turno", "Campo", "Valor anterior", "Valor nuevo", "Editado el", "Equipo"]


def _asegurar_hoja_horario(sh, nombre: str, cabecera: List[str]):
    """Devuelve la pestaña nativa de Horario; la crea con su cabecera si aún no existe."""
    try:
        return sh.worksheet(nombre)
    except Exception:
        ws_n = sh.add_worksheet(title=nombre, rows=2000, cols=len(cabecera))
        ws_n.update(range_name="A1", values=[cabecera], value_input_option="USER_ENTERED")
        try:
            ws_n.freeze(rows=1)
        except Exception:
            pass
        return ws_n


def process_sync_queue() -> Dict[str, Any]:
    """
    Procesa en orden FIFO estricto las operaciones pendientes en sync_queue.
    """
    conn = get_connection()
    try:
        pending_items = conn.execute(
            "SELECT * FROM sync_queue WHERE status IN ('PENDING', 'ERROR') ORDER BY queue_id ASC LIMIT 25"
        ).fetchall()
    finally:
        conn.close()

    if not pending_items:
        return {"status": "IDLE", "processed": 0}

    if not _sync_lock.acquire(blocking=False):
        return {"status": "BUSY", "processed": 0}

    processed = 0
    try:
        sh = _get_spreadsheet()
        ws_bd = sh.worksheet("Registro_Estancias")
        ws_pagos = None
        ws_tienda = None
        ws_gastos = None
        ws_disp = None
        hor_cache: Dict[str, Any] = {}

        for item in pending_items:
            qid = item["queue_id"]
            op_type = item["op_type"]
            payload = json.loads(item["payload_json"])

            try:
                if op_type == "CHECKIN":
                    id_ts = payload["id_timestamp_str"]
                    fila_est = payload["fila_estancia"]
                    assigned_row = _append_fila_idempotente(ws_bd, id_ts, fila_est, "AY")

                    pagos_rows = payload.get("pagos_rows") or []
                    if pagos_rows:
                        if ws_pagos is None:
                            ws_pagos = sh.worksheet("Registro_Pagos")
                        for pr in pagos_rows:
                            _append_fila_idempotente(ws_pagos, pr[0], pr, "J")

                    with db_transaction() as c_up:
                        c_up.execute(
                            "UPDATE estancias SET sheet_row = ?, dirty = 0 WHERE estancia_uuid = ?",
                            (assigned_row, item["entity_uuid"]),
                        )
                        c_up.execute(
                            "UPDATE pagos SET synced = 1 WHERE estancia_uuid = ?",
                            (item["entity_uuid"],),
                        )

                elif op_type == "CONTINUAR":
                    old_row = _resolver_fila_estancia_en_sheets(
                        ws_bd, int(payload.get("old_sheet_row") or 0), payload.get("old_id_ts") or ""
                    )
                    if old_row >= 2:
                        ws_bd.update(range_name=f"AK{old_row}", values=[["Finalizado - Continúa"]], value_input_option="USER_ENTERED")

                    new_row = _append_fila_idempotente(ws_bd, payload["id_timestamp_str"], payload["fila_nueva"], "AY")
                    with db_transaction() as c_up:
                        if old_row >= 2:
                            c_up.execute("UPDATE estancias SET dirty = 0 WHERE estancia_uuid = ?", (payload.get("old_uuid"),))
                        c_up.execute(
                            "UPDATE estancias SET sheet_row = ?, dirty = 0 WHERE estancia_uuid = ?",
                            (new_row, payload.get("new_uuid")),
                        )

                elif op_type == "PAGO":
                    target_row = _resolver_fila_estancia_en_sheets(
                        ws_bd, int(payload.get("sheet_row") or 0), payload.get("id_timestamp_str") or ""
                    )
                    if target_row >= 2:
                        m_fact = payload.get("monto_factura")
                        m_fact_cell = round(float(m_fact), 2) if (m_fact and float(m_fact) > 0) else ""
                        ws_bd.batch_update(
                            [
                                {"range": f"T{target_row}:U{target_row}", "values": [[payload["adic_servicios"], payload["adic_comp"]]]},
                                {"range": f"Y{target_row}:Z{target_row}", "values": [[payload["pago_post_efec"], payload["pago_post_yape"]]]},
                                {"range": f"AF{target_row}:AH{target_row}", "values": [[payload["solicita_comp"], payload["tipo_comp"], payload["estado_comp"]]]},
                                {"range": f"AJ{target_row}", "values": [[m_fact_cell]]},
                                {"range": f"AL{target_row}:AM{target_row}", "values": [[payload["deuda_anterior"], payload["pago_visa"]]]},
                                {"range": f"AW{target_row}", "values": [[payload["nro_op_yape"]]]},
                                {"range": f"AE{target_row}", "values": [[payload.get("cuenta_destino") or ""]]},
                                {"range": f"{COL_TIPO_ADICIONAL}{target_row}", "values": [[payload.get("tipo_adicional") or ""]]},
                            ],
                            value_input_option="USER_ENTERED",
                        )
                    pr = payload.get("pago_row")
                    if pr:
                        if ws_pagos is None:
                            ws_pagos = sh.worksheet("Registro_Pagos")
                        _append_fila_idempotente(ws_pagos, pr[0], pr, "J")

                    with db_transaction() as c_up:
                        c_up.execute("UPDATE estancias SET dirty = 0 WHERE estancia_uuid = ?", (item["entity_uuid"],))
                        if pr:
                            c_up.execute("UPDATE pagos SET synced = 1 WHERE pago_id = ?", (pr[0],))

                elif op_type == "TIENDA":
                    if ws_tienda is None:
                        ws_tienda = sh.worksheet("2.Registro_Ventas_Tienda")
                    col_t = ws_tienda.col_values(1)
                    next_t = max(2, len(col_t) + 1)
                    v_rows = payload.get("ventas_rows") or []
                    if v_rows:
                        fin_t = next_t + len(v_rows) - 1
                        if fin_t > ws_tienda.row_count:
                            ws_tienda.add_rows(max(100, fin_t - ws_tienda.row_count + 50))
                        ws_tienda.update(
                            range_name=f"A{next_t}:L{next_t + len(v_rows) - 1}",
                            values=v_rows,
                            value_input_option="USER_ENTERED",
                        )
                    est_up = payload.get("estancia_updates")
                    if est_up and "adic_snacks" in est_up:
                        t_row = _resolver_fila_estancia_en_sheets(
                            ws_bd, int(est_up.get("sheet_row") or 0), est_up.get("id_timestamp_str") or ""
                        )
                        if t_row >= 2 and not FORMULA_SNACKS_EN_S2:
                            # Solo actualiza Col S (Adic_Snacks) cuando fue "Cargar a Habitación"
                            # (en Terrazas S2 tiene formula =MAP -> nunca se escribe)
                            ws_bd.update(f"S{t_row}", [[est_up["adic_snacks"]]], value_input_option="USER_ENTERED")
                    with db_transaction() as c_up:
                        lote_vts = c_up.execute(
                            "SELECT venta_uuid FROM ventas_tienda WHERE lote_uuid = ? ORDER BY rowid ASC",
                            (item["entity_uuid"],),
                        ).fetchall()
                        for idx_lv, lv in enumerate(lote_vts):
                            c_up.execute(
                                "UPDATE ventas_tienda SET sheet_row = ?, synced = 1 WHERE venta_uuid = ?",
                                (next_t + idx_lv, lv["venta_uuid"]),
                            )
                        if est_up:
                            c_up.execute("UPDATE estancias SET dirty = 0 WHERE estancia_uuid = ?", (est_up["estancia_uuid"],))

                elif op_type == "ELIMINAR_ESTANCIA_ADMIN":
                    t_row_e = _resolver_fila_estancia_en_sheets(
                        ws_bd, int(payload.get("sheet_row") or 0), payload.get("id_timestamp_str") or ""
                    )
                    if t_row_e >= 3:
                        ws_bd.delete_rows(t_row_e)
                    elif t_row_e == 2:
                        # Protege las fórmulas =MAP(...) alojadas en la Fila 2 (P2, R2, V2, AA2:AD2, AN2:AO2, y S2 si aplica)
                        rangos_f2 = ["A2:O2", "Q2", "T2:U2" if FORMULA_SNACKS_EN_S2 else "S2:U2", "W2:Z2", "AE2:AM2", "AP2:BA2"]
                        ws_bd.batch_clear(rangos_f2)

                    pagos_vinc = payload.get("pagos_vinculados") or []
                    if pagos_vinc:
                        if ws_pagos is None:
                            ws_pagos = sh.worksheet("Registro_Pagos")
                        filas_p_borrar = set()
                        for pv in pagos_vinc:
                            rp = _resolver_fila_por_col_a(ws_pagos, int(pv.get("sheet_row") or 0), pv.get("pago_id") or "")
                            if rp >= 2:
                                filas_p_borrar.add(rp)
                        for rp_del in sorted(filas_p_borrar, reverse=True):
                            ws_pagos.delete_rows(rp_del)

                elif op_type == "GASTO":
                    if ws_gastos is None:
                        ws_gastos = sh.worksheet("Registro_Gastos")
                    row_g = _append_fila_idempotente(ws_gastos, payload["id_ts_str"], payload["fila_gasto"], "J")
                    with db_transaction() as c_up:
                        c_up.execute("UPDATE gastos SET sheet_row = ?, synced = 1 WHERE gasto_uuid = ?", (row_g, item["entity_uuid"]))

                elif op_type == "EDITAR_GASTO_ADMIN":
                    if ws_gastos is None:
                        ws_gastos = sh.worksheet("Registro_Gastos")
                    t_row_g = _resolver_fila_por_col_a(
                        ws_gastos, int(payload.get("sheet_row") or 0), payload.get("id_ts_str") or ""
                    )
                    if t_row_g >= 2:
                        ws_gastos.update(
                            range_name=f"B{t_row_g}:J{t_row_g}",
                            values=[payload["fila_bj"]],
                            value_input_option="USER_ENTERED",
                        )
                    with db_transaction() as c_up:
                        c_up.execute(
                            "UPDATE gastos SET sheet_row = CASE WHEN ? >= 2 THEN ? ELSE sheet_row END, synced = 1 WHERE gasto_uuid = ?",
                            (t_row_g, t_row_g, item["entity_uuid"]),
                        )

                elif op_type == "ELIMINAR_GASTO_ADMIN":
                    if ws_gastos is None:
                        ws_gastos = sh.worksheet("Registro_Gastos")
                    t_row_g = _resolver_fila_por_col_a(
                        ws_gastos, int(payload.get("sheet_row") or 0), payload.get("id_ts_str") or ""
                    )
                    if t_row_g >= 2:
                        ws_gastos.delete_rows(t_row_g)

                elif op_type == "EDITAR_VENTA_TIENDA_ADMIN":
                    if ws_tienda is None:
                        ws_tienda = sh.worksheet("2.Registro_Ventas_Tienda")
                    t_row_t = _resolver_fila_tienda(
                        ws_tienda,
                        int(payload.get("sheet_row") or 0),
                        payload.get("fecha_iso") or "",
                        payload.get("producto_prev") or "",
                    )
                    if t_row_t >= 2:
                        ws_tienda.batch_update(
                            [
                                {"range": f"B{t_row_t}:I{t_row_t}", "values": [payload["fila_bi"]]},
                                {"range": f"L{t_row_t}", "values": [[payload.get("nro_op_yape") or ""]]},
                            ],
                            value_input_option="USER_ENTERED",
                        )
                    est_up_t = payload.get("estancia_updates")
                    if est_up_t and "adic_snacks" in est_up_t:
                        est_r = _resolver_fila_estancia_en_sheets(
                            ws_bd, int(est_up_t.get("sheet_row") or 0), est_up_t.get("id_timestamp_str") or ""
                        )
                        if est_r >= 2 and not FORMULA_SNACKS_EN_S2:
                            ws_bd.update(f"S{est_r}", [[est_up_t["adic_snacks"]]], value_input_option="USER_ENTERED")
                    with db_transaction() as c_up:
                        c_up.execute(
                            "UPDATE ventas_tienda SET sheet_row = CASE WHEN ? >= 2 THEN ? ELSE sheet_row END, synced = 1 WHERE venta_uuid = ?",
                            (t_row_t, t_row_t, item["entity_uuid"]),
                        )
                        if est_up_t and est_up_t.get("estancia_uuid"):
                            c_up.execute("UPDATE estancias SET dirty = 0 WHERE estancia_uuid = ?", (est_up_t["estancia_uuid"],))

                elif op_type == "ELIMINAR_VENTA_TIENDA_ADMIN":
                    if ws_tienda is None:
                        ws_tienda = sh.worksheet("2.Registro_Ventas_Tienda")
                    t_row_t = _resolver_fila_tienda(
                        ws_tienda,
                        int(payload.get("sheet_row") or 0),
                        payload.get("fecha_iso") or "",
                        payload.get("producto_prev") or "",
                    )
                    if t_row_t >= 2:
                        ws_tienda.delete_rows(t_row_t)
                    est_up_t = payload.get("estancia_updates")
                    if est_up_t and "adic_snacks" in est_up_t:
                        est_r = _resolver_fila_estancia_en_sheets(
                            ws_bd, int(est_up_t.get("sheet_row") or 0), est_up_t.get("id_timestamp_str") or ""
                        )
                        if est_r >= 2 and not FORMULA_SNACKS_EN_S2:
                            ws_bd.update(f"S{est_r}", [[est_up_t["adic_snacks"]]], value_input_option="USER_ENTERED")
                    if est_up_t and est_up_t.get("estancia_uuid"):
                        with db_transaction() as c_up:
                            c_up.execute("UPDATE estancias SET dirty = 0 WHERE estancia_uuid = ?", (est_up_t["estancia_uuid"],))

                elif op_type == "EDITAR_PAGO_ADMIN":
                    if ws_pagos is None:
                        ws_pagos = sh.worksheet("Registro_Pagos")
                    t_row_p = _resolver_fila_por_col_a(
                        ws_pagos, int(payload.get("sheet_row") or 0), payload.get("pago_id") or ""
                    )
                    if t_row_p >= 2:
                        ws_pagos.update(
                            range_name=f"B{t_row_p}:J{t_row_p}",
                            values=[payload["fila_bj"]],
                            value_input_option="USER_ENTERED",
                        )
                    est_up_p = payload.get("estancia_updates")
                    if est_up_p:
                        est_r = _resolver_fila_estancia_en_sheets(
                            ws_bd, int(est_up_p.get("sheet_row") or 0), est_up_p.get("id_timestamp_str") or ""
                        )
                        if est_r >= 2:
                            ws_bd.batch_update(
                                [
                                    {"range": f"W{est_r}:Z{est_r}", "values": [est_up_p["bloque_wz"]]},
                                    {"range": f"AM{est_r}", "values": [[est_up_p["pago_visa"]]]},
                                ],
                                value_input_option="USER_ENTERED",
                            )
                    with db_transaction() as c_up:
                        c_up.execute(
                            "UPDATE pagos SET sheet_row = CASE WHEN ? >= 2 THEN ? ELSE sheet_row END, synced = 1 WHERE pago_id = ?",
                            (t_row_p, t_row_p, item["entity_uuid"]),
                        )
                        if est_up_p and est_up_p.get("estancia_uuid"):
                            c_up.execute("UPDATE estancias SET dirty = 0 WHERE estancia_uuid = ?", (est_up_p["estancia_uuid"],))

                elif op_type == "ELIMINAR_PAGO_ADMIN":
                    if ws_pagos is None:
                        ws_pagos = sh.worksheet("Registro_Pagos")
                    t_row_p = _resolver_fila_por_col_a(
                        ws_pagos, int(payload.get("sheet_row") or 0), payload.get("pago_id") or ""
                    )
                    if t_row_p >= 2:
                        ws_pagos.delete_rows(t_row_p)
                    est_up_p = payload.get("estancia_updates")
                    if est_up_p:
                        est_r = _resolver_fila_estancia_en_sheets(
                            ws_bd, int(est_up_p.get("sheet_row") or 0), est_up_p.get("id_timestamp_str") or ""
                        )
                        if est_r >= 2:
                            ws_bd.batch_update(
                                [
                                    {"range": f"W{est_r}:Z{est_r}", "values": [est_up_p["bloque_wz"]]},
                                    {"range": f"AM{est_r}", "values": [[est_up_p["pago_visa"]]]},
                                ],
                                value_input_option="USER_ENTERED",
                            )
                        if est_up_p.get("estancia_uuid"):
                            with db_transaction() as c_up:
                                c_up.execute("UPDATE estancias SET dirty = 0 WHERE estancia_uuid = ?", (est_up_p["estancia_uuid"],))

                elif op_type == "HORARIO_UPSERT":
                    if hor_cache.get("ws") is None:
                        hor_cache["ws"] = _asegurar_hoja_horario(sh, "import_Horario", HORARIO_CABECERA)
                        vals_ab = hor_cache["ws"].get("A2:B8000") or []
                        hor_cache["idx"] = {
                            (str(r[0]).strip(), normalizar_fecha_dmy(r[1]) if len(r) > 1 else ""): i + 2
                            for i, r in enumerate(vals_ab) if r and str(r[0]).strip()
                        }
                        hor_cache["next"] = len(vals_ab) + 2
                    ws_h = hor_cache["ws"]
                    k_h = (payload["empleado"], payload["fecha_dmy"])
                    fila_h = payload["fila"]
                    r_h = hor_cache["idx"].get(k_h)
                    if r_h:
                        ws_h.update(range_name=f"A{r_h}:L{r_h}", values=[fila_h], value_input_option="RAW")
                    else:
                        r_h = hor_cache["next"]
                        if r_h > ws_h.row_count:
                            ws_h.add_rows(max(200, r_h - ws_h.row_count + 100))
                        ws_h.update(range_name=f"A{r_h}:L{r_h}", values=[fila_h], value_input_option="RAW")
                        hor_cache["idx"][k_h] = r_h
                        hor_cache["next"] = r_h + 1
                    with db_transaction() as c_up:
                        c_up.execute(
                            "UPDATE horario_registros SET synced = 1 WHERE empleado = ? AND fecha_dmy = ? AND updated_at = ?",
                            (payload["empleado"], payload["fecha_dmy"], payload["updated_at"]),
                        )

                elif op_type == "HORARIO_AUDIT":
                    if hor_cache.get("ws_aud") is None:
                        hor_cache["ws_aud"] = _asegurar_hoja_horario(sh, "import_Horario_Audit", HORARIO_AUDIT_CABECERA)
                    _append_fila_idempotente(hor_cache["ws_aud"], payload["audit_uuid"], payload["fila"], "H", value_input_option="RAW")
                    with db_transaction() as c_up:
                        c_up.execute("UPDATE horario_auditoria SET synced = 1 WHERE audit_uuid = ?", (payload["audit_uuid"],))

                elif op_type == "PASE_UPSERT":
                    from pases_logic import PASES_CABECERA
                    if hor_cache.get("ws_pases") is None:
                        hor_cache["ws_pases"] = _asegurar_hoja_horario(sh, "import_Control_Pases", PASES_CABECERA)
                    ws_p = hor_cache["ws_pases"]
                    col_a_p = ws_p.col_values(1) or []
                    row_idx_p = None
                    for idx_p, val_p in enumerate(col_a_p[1:], start=2):
                        if str(val_p).strip() == str(payload.get("pase_uuid") or "").strip():
                            row_idx_p = idx_p
                            break
                    if row_idx_p:
                        ws_p.update(range_name=f"A{row_idx_p}:N{row_idx_p}", values=[payload["fila"]], value_input_option="RAW")
                    else:
                        _append_fila_idempotente(ws_p, payload["pase_uuid"], payload["fila"], "N", value_input_option="RAW")
                    with db_transaction() as c_up:
                        c_up.execute("UPDATE control_pases SET synced = 1 WHERE pase_uuid = ?", (payload["pase_uuid"],))

                elif op_type == "PASE_DELETE":
                    from pases_logic import PASES_CABECERA
                    if hor_cache.get("ws_pases") is None:
                        hor_cache["ws_pases"] = _asegurar_hoja_horario(sh, "import_Control_Pases", PASES_CABECERA)
                    ws_p = hor_cache["ws_pases"]
                    col_a_p = ws_p.col_values(1) or []
                    for idx_p, val_p in enumerate(col_a_p[1:], start=2):
                        if str(val_p).strip() == str(payload.get("pase_uuid") or "").strip():
                            ws_p.delete_rows(idx_p)
                            break

                elif op_type == "MODIFICAR":
                    t_row = _resolver_fila_estancia_en_sheets(
                        ws_bd, int(payload.get("sheet_row") or 0), payload.get("id_timestamp_str") or ""
                    )
                    if t_row >= 2:
                        ws_bd.batch_update(
                            [
                                {"range": f"B{t_row}:M{t_row}", "values": [payload["bloque_bm"]]},
                                {"range": f"Q{t_row}", "values": [[payload["tarifa"]]]},
                                *(
                                    [{"range": f"T{t_row}:U{t_row}", "values": [payload["bloque_su"][1:]]}]
                                    if FORMULA_SNACKS_EN_S2
                                    else [{"range": f"S{t_row}:U{t_row}", "values": [payload["bloque_su"]]}]
                                ),
                                {"range": f"W{t_row}:Z{t_row}", "values": [payload["bloque_wz"]]},
                                {"range": f"AF{t_row}:AM{t_row}", "values": [payload["bloque_afam"]]},
                                {"range": f"AQ{t_row}:AY{t_row}", "values": [payload["bloque_aqay"]]},
                                *(
                                    [{"range": f"{COL_TIPO_ADICIONAL}{t_row}", "values": [[payload.get("tipo_adicional") or ""]]}]
                                    if "tipo_adicional" in payload
                                    else []
                                ),
                            ],
                            value_input_option="USER_ENTERED",
                        )
                    for paj in payload.get("pagos_ajuste") or []:
                        if ws_pagos is None:
                            ws_pagos = sh.worksheet("Registro_Pagos")
                        _append_fila_idempotente(ws_pagos, paj[0], paj, "J")
                    with db_transaction() as c_up:
                        c_up.execute("UPDATE estancias SET dirty = 0 WHERE estancia_uuid = ?", (item["entity_uuid"],))

                elif op_type == "LIBERAR":
                    t_row = _resolver_fila_estancia_en_sheets(
                        ws_bd, int(payload.get("sheet_row") or 0), payload.get("id_timestamp_str") or ""
                    )
                    if t_row >= 2:
                        ws_bd.batch_update(
                            [
                                {"range": f"C{t_row}", "values": [[payload["fecha_out_dmy"]]]},
                                {"range": f"AK{t_row}", "values": [["Finalizado"]]},
                                {"range": f"AV{t_row}", "values": [[payload["observaciones"]]]},
                            ],
                            value_input_option="USER_ENTERED",
                        )
                    with db_transaction() as c_up:
                        c_up.execute("UPDATE estancias SET dirty = 0 WHERE estancia_uuid = ?", (item["entity_uuid"],))

                elif op_type == "CANCELAR":
                    t_row = _resolver_fila_estancia_en_sheets(
                        ws_bd, int(payload.get("sheet_row") or 0), payload.get("id_timestamp_str") or ""
                    )
                    if t_row >= 2:
                        lotes = [
                            {"range": f"Q{t_row}", "values": [[0]]},
                            {"range": f"AK{t_row}", "values": [["Cancelado"]]},
                            {"range": f"AV{t_row}", "values": [[payload["observaciones"]]]},
                        ]
                        if payload.get("devolver_pago"):
                            lotes.append({"range": f"W{t_row}:Z{t_row}", "values": [[0, 0, 0, 0]]})
                            lotes.append({"range": f"AM{t_row}", "values": [[0]]})
                        ws_bd.batch_update(lotes, value_input_option="USER_ENTERED")
                    for pex in payload.get("pagos_extorno") or []:
                        if ws_pagos is None:
                            ws_pagos = sh.worksheet("Registro_Pagos")
                        _append_fila_idempotente(ws_pagos, pex[0], pex, "J")
                    with db_transaction() as c_up:
                        c_up.execute("UPDATE estancias SET dirty = 0 WHERE estancia_uuid = ?", (item["entity_uuid"],))

                elif op_type == "RESTAURAR":
                    t_row = _resolver_fila_estancia_en_sheets(
                        ws_bd, int(payload.get("sheet_row") or 0), payload.get("id_timestamp_str") or ""
                    )
                    if t_row >= 2:
                        ws_bd.batch_update(
                            [
                                {"range": f"AK{t_row}", "values": [["En Curso"]]},
                                {"range": f"AV{t_row}", "values": [[payload["observaciones"]]]},
                            ],
                            value_input_option="USER_ENTERED",
                        )
                    with db_transaction() as c_up:
                        c_up.execute("UPDATE estancias SET dirty = 0 WHERE estancia_uuid = ?", (item["entity_uuid"],))

                elif op_type == "FACT_META":
                    t_row = _resolver_fila_estancia_en_sheets(
                        ws_bd, int(payload.get("sheet_row") or 0), payload.get("id_timestamp_str") or ""
                    )
                    if t_row >= 2:
                        lotes_fact = [
                            {"range": f"U{t_row}", "values": [[payload["adic_comp"]]]},
                            {"range": f"AF{t_row}:AJ{t_row}", "values": [payload["bloque_afaj"]]},
                            {"range": f"AQ{t_row}", "values": [[payload["empresa"]]]},
                            {"range": f"AX{t_row}:AY{t_row}", "values": [[payload["celular"], payload["ruc"]]]},
                        ]
                        if payload.get("dni_actualizado") and payload.get("dni"):
                            dni_str = str(payload["dni"]).strip()
                            dni_cell = f"'{dni_str}" if dni_str.startswith("0") else dni_str
                            lotes_fact.append({"range": f"E{t_row}", "values": [[dni_cell]]})
                        ws_bd.batch_update(lotes_fact, value_input_option="USER_ENTERED")
                    with db_transaction() as c_up:
                        c_up.execute("UPDATE estancias SET dirty = 0 WHERE estancia_uuid = ?", (item["entity_uuid"],))

                elif op_type == "COLOR_HAB":
                    pass  # Obsoleto: los colores ya no se envían a Sheets.

                elif op_type == "GUARDAR_CONTEO_VITRINA":
                    ws_vit = sh.worksheet("Vitrina_Tienda")
                    responsable_v = str(payload.get("responsable") or "").strip()
                    fecha_hora_v = str(payload.get("fecha_hora_dmy") or "").strip()
                    hdr_conteo = f"Conteo:\n{responsable_v}\n{fecha_hora_v}"
                    hdr_estado = f"Estado:\n{responsable_v}\n{fecha_hora_v}"
                    mapa_items = payload.get("items_map") or {}

                    actual_a_i = ws_vit.get("A4:I45") or []
                    h4_actual = ""
                    if len(actual_a_i) >= 1 and len(actual_a_i[0]) >= 8:
                        h4_actual = str(actual_a_i[0][7] or "").strip()

                    # Idempotencia: si H4 ya tiene exactamente este encabezado, no volver a insertar columnas
                    if h4_actual != hdr_conteo.strip():
                        filas_prod = actual_a_i[2:] if len(actual_a_i) >= 3 else []
                        num_filas_v = len(filas_prod)
                        if num_filas_v > 0:
                            valores_hist = []
                            valores_aviso = []
                            valores_limpiar_d = []
                            for r_p in filas_prod:
                                nom_p = str(r_p[0] or "").strip() if len(r_p) > 0 else ""
                                item_p = mapa_items.get(nom_p)
                                if nom_p and item_p and str(item_p.get("cant", "")).strip() != "":
                                    c_val = item_p["cant"]
                                    try:
                                        c_num = float(c_val)
                                        c_cell = int(c_num) if c_num.is_integer() else c_num
                                    except Exception:
                                        c_cell = c_val
                                    valores_hist.append([c_cell])
                                    valores_aviso.append([str(item_p.get("estado") or "")])
                                else:
                                    valores_hist.append([""])
                                    valores_aviso.append([""])
                                valores_limpiar_d.append([""])

                            # 1. Insertar 2 columnas antes de la columna H (índice 8, 1-based)
                            ws_vit.insert_cols([[], []], col=8)

                            # 2. Actualizar E2, E3, D6:D, G6:G, H4:I4 y H6:I en un solo lote
                            last_row_v = 5 + num_filas_v
                            valores_hi = [
                                [valores_hist[i][0], valores_aviso[i][0]]
                                for i in range(num_filas_v)
                            ]
                            ws_vit.batch_update(
                                [
                                    {"range": "E2", "values": [[responsable_v]]},
                                    {"range": "E3", "values": [[fecha_hora_v]]},
                                    {"range": f"D6:D{last_row_v}", "values": valores_limpiar_d},
                                    {"range": f"G6:G{last_row_v}", "values": valores_aviso},
                                    {"range": "H4:I4", "values": [[hdr_conteo, hdr_estado]]},
                                    {"range": f"H6:I{last_row_v}", "values": valores_hi},
                                ],
                                value_input_option="USER_ENTERED",
                            )
                            try:
                                ws_vit.format(
                                    "H4:I4",
                                    {
                                        "backgroundColor": {"red": 0.0, "green": 0.0, "blue": 0.0},
                                        "textFormat": {
                                            "bold": True,
                                            "foregroundColor": {"red": 1.0, "green": 1.0, "blue": 1.0},
                                        },
                                        "wrapStrategy": "WRAP",
                                    },
                                )
                            except Exception:
                                pass

                            try:
                                max_cols_v = 107
                                if ws_vit.col_count > max_cols_v:
                                    ws_vit.delete_columns(max_cols_v + 1, ws_vit.col_count)
                            except Exception:
                                pass

                elif op_type == "MOVIMIENTO_ALMACEN":
                    ws_alm = sh.worksheet("import_Almacen")
                    movs_list = payload.get("movimientos") or []
                    if movs_list:
                        col_j = ws_alm.col_values(10)
                        col_a = ws_alm.col_values(1)
                        existentes_j = {str(x).strip(): idx_r + 1 for idx_r, x in enumerate(col_j) if str(x).strip()}
                        next_row_alm = max(len(col_a), len(col_j), 1) + 1
                        filas_nuevas = []
                        uuids_nuevos = []
                        actualizaciones_sync = []

                        for m_it in movs_list:
                            m_u = str(m_it.get("mov_uuid") or "").strip()
                            fila_m = m_it.get("fila") or []
                            if not m_u or not fila_m:
                                continue
                            if m_u in existentes_j:
                                actualizaciones_sync.append((existentes_j[m_u], m_u))
                            else:
                                filas_nuevas.append(fila_m)
                                uuids_nuevos.append(m_u)

                        if filas_nuevas:
                            end_row_alm = next_row_alm + len(filas_nuevas) - 1
                            if ws_alm.row_count < end_row_alm + 20 or ws_alm.col_count < 10:
                                ws_alm.resize(
                                    rows=max(ws_alm.row_count, end_row_alm + 100),
                                    cols=max(ws_alm.col_count, 10),
                                )
                            ws_alm.update(
                                values=filas_nuevas,
                                range_name=f"A{next_row_alm}:J{end_row_alm}",
                                value_input_option="USER_ENTERED",
                            )
                            for offset_m, m_u in enumerate(uuids_nuevos):
                                actualizaciones_sync.append((next_row_alm + offset_m, m_u))

                        # Leer stock recalculado de Vitrina_Tienda!A6:B45 para transición atómica sin salto
                        nuevos_stocks_vit = []
                        try:
                            ws_vit_ref = sh.worksheet("Vitrina_Tienda")
                            ab_vals = ws_vit_ref.get("A6:B45") or []
                            for r_ab in ab_vals:
                                p_ab = str(r_ab[0] or "").strip() if len(r_ab) > 0 else ""
                                if p_ab:
                                    s_ab = parse_moneda(r_ab[1]) if len(r_ab) > 1 else 0.0
                                    nuevos_stocks_vit.append((s_ab, p_ab))
                        except Exception:
                            pass

                        with db_transaction() as c_alm:
                            for r_num_m, m_u in actualizaciones_sync:
                                c_alm.execute(
                                    "UPDATE almacen_movimientos SET sheet_row = ?, synced = 1 WHERE mov_uuid = ?",
                                    (r_num_m, m_u),
                                )
                            for s_ab, p_ab in nuevos_stocks_vit:
                                c_alm.execute(
                                    "UPDATE vitrina_local SET stock_sheets = ? WHERE producto = ?",
                                    (s_ab, p_ab),
                                )

                elif op_type == "ELIMINAR_MOVIMIENTO_ALMACEN":
                    ws_alm = sh.worksheet("import_Almacen")
                    m_u_del = str(payload.get("mov_uuid") or "").strip()
                    s_row_hint = int(payload.get("sheet_row") or 0)
                    id_tx_del = str(payload.get("id_transaccion") or "").strip()
                    prod_del = str(payload.get("producto") or "").strip()
                    tipo_del = str(payload.get("tipo_movimiento") or "").strip()

                    all_alm = ws_alm.get_all_values()
                    fila_borrar = 0
                    for idx_r, r_a in enumerate(all_alm[1:], start=2):
                        u_cell = str(r_a[9] or "").strip() if len(r_a) > 9 else ""
                        if m_u_del and u_cell == m_u_del:
                            fila_borrar = idx_r
                            break
                    if not fila_borrar and s_row_hint >= 2 and s_row_hint <= len(all_alm):
                        r_cand = all_alm[s_row_hint - 1]
                        if (
                            len(r_cand) > 7
                            and str(r_cand[2] or "").strip() == prod_del
                            and str(r_cand[7] or "").strip() == tipo_del
                            and str(r_cand[0] or "").strip() == id_tx_del
                        ):
                            fila_borrar = s_row_hint
                    if fila_borrar >= 2:
                        ws_alm.delete_rows(fila_borrar)
                        try:
                            ws_vit_ref = sh.worksheet("Vitrina_Tienda")
                            ab_vals = ws_vit_ref.get("A6:B45") or []
                            with db_transaction() as c_vref:
                                for r_ab in ab_vals:
                                    p_ab = str(r_ab[0] or "").strip() if len(r_ab) > 0 else ""
                                    if p_ab:
                                        s_ab = parse_moneda(r_ab[1]) if len(r_ab) > 1 else 0.0
                                        c_vref.execute(
                                            "UPDATE vitrina_local SET stock_sheets = ? WHERE producto = ?",
                                            (s_ab, p_ab),
                                        )
                        except Exception:
                            pass

                # Marcar operación como SYNCED
                with db_transaction() as c_done:
                    c_done.execute(
                        "UPDATE sync_queue SET status = 'SYNCED', synced_at = ?, last_error = '' WHERE queue_id = ?",
                        (datetime.now().isoformat(), qid),
                    )
                    _set_estado_sistema(c_done, "network_online", "1")
                    _set_estado_sistema(c_done, "last_sync_ts", datetime.now().strftime("%H:%M:%S"))
                    _set_estado_sistema(c_done, "offline_since_iso", "")
                    _set_estado_sistema(c_done, "last_sync_error", "")
                processed += 1

            except Exception as op_err:
                err_txt = str(op_err)[:300]
                err_low = err_txt.lower()
                es_error_red = any(
                    k in err_low
                    for k in ("timeout", "timed out", "connection", "socket", "502", "503", "500", "429", "quota", "reset", "eof", "unreachable")
                )
                intentos_prev = int(item["attempts"] or 0) + 1

                # Autocuración: si NO es error de red y falla >= 5 veces seguidas, respaldar y destrabar tomando Sheets como base
                if not es_error_red and intentos_prev >= 5:
                    try:
                        DATA_DIR.mkdir(parents=True, exist_ok=True)
                        with open(DATA_DIR / "autocuracion_backup.jsonl", "a", encoding="utf-8") as fb:
                            fb.write(
                                json.dumps(
                                    {
                                        "ts": datetime.now().isoformat(),
                                        "queue_id": qid,
                                        "op_type": op_type,
                                        "entity_uuid": item["entity_uuid"],
                                        "error": err_txt,
                                        "payload": payload,
                                    },
                                    ensure_ascii=False,
                                )
                                + "\n"
                            )
                    except Exception:
                        pass

                    with db_transaction() as c_heal:
                        c_heal.execute(
                            "UPDATE sync_queue SET status = 'AUTOCURADO', attempts = ?, last_error = ? WHERE queue_id = ?",
                            (intentos_prev, f"[AUTOCURADO] {err_txt}", qid),
                        )
                        if item["entity_uuid"]:
                            c_heal.execute(
                                "UPDATE estancias SET dirty = 0 WHERE estancia_uuid = ?",
                                (item["entity_uuid"],),
                            )
                    continue

                with db_transaction() as c_err:
                    c_err.execute(
                        "UPDATE sync_queue SET status = 'ERROR', attempts = ?, last_error = ? WHERE queue_id = ?",
                        (intentos_prev, err_txt, qid),
                    )
                    prev_off = c_err.execute("SELECT valor FROM estado_sistema WHERE clave = 'offline_since_iso'").fetchone()
                    if not prev_off or not prev_off["valor"]:
                        _set_estado_sistema(c_err, "offline_since_iso", datetime.now().isoformat())
                    _set_estado_sistema(c_err, "network_online", "0")
                    _set_estado_sistema(c_err, "last_sync_error", err_txt[:250])
                _reset_client()
                break

        # Si se procesaron elementos que afectan la caja, refrescar G1 de Panel_Disponibilidad
        if processed > 0:
            try:
                if ws_disp is None:
                    ws_disp = sh.worksheet("Panel_Disponibilidad")
                val_g1 = ws_disp.acell("G1").value
                with db_transaction() as c_g1:
                    _set_estado_sistema(c_g1, "caja_sheets_g1", f"{parse_moneda(val_g1):.2f}")
            except Exception:
                pass

        return {"status": "OK", "processed": processed}
    except Exception as e:
        _reset_client()
        ahora_iso = datetime.now().isoformat()
        try:
            with db_transaction() as conn:
                prev_off = conn.execute("SELECT valor FROM estado_sistema WHERE clave = 'offline_since_iso'").fetchone()
                if not prev_off or not prev_off["valor"]:
                    _set_estado_sistema(conn, "offline_since_iso", ahora_iso)
                _set_estado_sistema(conn, "network_online", "0")
                _set_estado_sistema(conn, "last_sync_error", str(e)[:250])
        except Exception:
            pass
        return {"status": "OFFLINE", "processed": processed, "error": str(e)}
    finally:
        _sync_lock.release()
