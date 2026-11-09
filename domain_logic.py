#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Motor de Reglas de Negocio y Operaciones en 0 ms (SQLite Local)
Replica con exactitud matemática las reglas de Código.js y protege las 9 columnas
con fórmulas MAP(...) de Google Sheets: P(16), R(18), V(22), AA(27), AB(28), AC(29), AD(30), AN(40), AO(41).
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
import unicodedata
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import importlib as _il
import sys as _sys
import threading as _threading

try:
    if "config" in _sys.modules:
        _il.reload(_sys.modules["config"])
    if "database" in _sys.modules:
        _il.reload(_sys.modules["database"])
        _sys.modules["database"].init_db()
except Exception:
    pass

from config import (
    COL_TIPO_ADICIONAL,
    COL_TIPO_ADICIONAL_IDX,
    FORMULA_SNACKS_EN_S2,
    NOMBRE_HOTEL,
    SPREADSHEET_ID,
)
from database import db_transaction, get_connection, init_db

try:
    init_db()
except Exception:
    pass

try:
    from config import BASE_APP_DIR as _BAD
    if "main" in _sys.modules:
        setattr(_sys.modules["main"], "NOMBRE_HOTEL", NOMBRE_HOTEL)
    if "sync_engine" in _sys.modules:
        _se_mod = _sys.modules["sync_engine"]
        _old_sh_id = getattr(_se_mod, "SPREADSHEET_ID", "")
        setattr(_se_mod, "SPREADSHEET_ID", SPREADSHEET_ID)
        setattr(_se_mod, "COL_TIPO_ADICIONAL", COL_TIPO_ADICIONAL)
        setattr(_se_mod, "COL_TIPO_ADICIONAL_IDX", COL_TIPO_ADICIONAL_IDX)
        setattr(_se_mod, "FORMULA_SNACKS_EN_S2", FORMULA_SNACKS_EN_S2)
        try:
            _il.reload(_se_mod)
        except Exception:
            pass
        if hasattr(_se_mod, "_reset_client") and _old_sh_id != SPREADSHEET_ID:
            _se_mod._reset_client()
        if hasattr(_se_mod, "pull_from_sheets"):
            _threading.Thread(target=_se_mod.pull_from_sheets, kwargs={"full_bootstrap": False}, daemon=True).start()
    if "terrazas" in str(_BAD).lower() and "auto_updater" in _sys.modules:
        _au_mod = _sys.modules["auto_updater"]
        if hasattr(_au_mod, "_run_git"):
            _r_rem = _au_mod._run_git(["remote", "get-url", "origin"], timeout=5)
            if _r_rem.returncode == 0 and "app_local_terrazas" not in _r_rem.stdout:
                _au_mod._run_git(
                    ["remote", "set-url", "origin", "https://github.com/eduardocalaniguevara-del/app_local_terrazas.git"],
                    timeout=5,
                )
except Exception:
    pass


# ==============================================================================
# UTILIDADES DE FECHA, MONEDA Y NORMALIZACIÓN
# ==============================================================================

def hoy_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def hoy_dmy() -> str:
    return datetime.now().strftime("%d/%m/%Y")


def hora_actual_hm() -> str:
    return datetime.now().strftime("%H:%M")


def normalizar_fecha_dmy(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, datetime):
        return val.strftime("%d/%m/%Y")
    s = str(val).strip()
    if not s or s == "-":
        return ""
    # Si viene con hora (ej. "24/10/2026 14:30:00" o "2026-10-24T...")
    if "T" in s:
        s = s.split("T")[0]
    elif " " in s:
        s = s.split(" ")[0]

    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        p = s.split("-")
        return f"{p[2]}/{p[1]}/{p[0]}"

    m = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})$", s)
    if m:
        d = m.group(1).zfill(2)
        mes = m.group(2).zfill(2)
        y = m.group(3)
        if len(y) == 2:
            y = "20" + y
        return f"{d}/{mes}/{y}"
    return s


def dmy_a_iso(dmy_str: str) -> str:
    s = normalizar_fecha_dmy(dmy_str)
    p = s.split("/")
    if len(p) == 3:
        return f"{p[2]}-{p[1].zfill(2)}-{p[0].zfill(2)}"
    return s


def fecha_dmy_a_ymd(dmy_str: str) -> int:
    s = normalizar_fecha_dmy(dmy_str)
    p = s.split("/")
    if len(p) != 3:
        return 0
    try:
        return int(p[2]) * 10000 + int(p[1]) * 100 + int(p[0])
    except ValueError:
        return 0


def calcular_noches(fecha_in_dmy: str, fecha_out_dmy: str) -> int:
    try:
        d1 = datetime.strptime(normalizar_fecha_dmy(fecha_in_dmy), "%d/%m/%Y")
        d2 = datetime.strptime(normalizar_fecha_dmy(fecha_out_dmy), "%d/%m/%Y")
        diff = (d2 - d1).days
        return max(1, diff)
    except Exception:
        return 1


def parse_moneda(val: Any) -> float:
    if val is None or val == "":
        return 0.0
    if isinstance(val, (int, float)):
        return round(float(val), 2)
    s = str(val).replace("S/", "").replace("s/", "").replace(",", "").strip()
    if not s or s == "-":
        return 0.0
    try:
        return round(float(s), 2)
    except ValueError:
        return 0.0


def normalizar_dni(dni: Any, nac: str = "Perú") -> str:
    s = str(dni or "").strip()
    if not s:
        return ""
    solo_num = re.sub(r"\D", "", s)
    if solo_num and len(solo_num) == 7 and ("per" in (nac or "Perú").lower()):
        return solo_num.zfill(8)
    return s


def normalizar_texto_comparacion(txt: Any) -> str:
    s = str(txt or "").strip().lower()
    if not s or s == "-":
        return ""
    s = "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )
    return re.sub(r"\s+", " ", s).strip()


def son_mismo_huesped(dni1: str, nom1: str, dni2: str, nom2: str) -> bool:
    d1 = normalizar_dni(dni1)
    d2 = normalizar_dni(dni2)
    if d1 and d2 and d1 == d2:
        return True
    n1 = normalizar_texto_comparacion(nom1)
    n2 = normalizar_texto_comparacion(nom2)
    if n1 and n2:
        if n1 == n2 or n1 in n2 or n2 in n1:
            return True
        t1 = [t for t in n1.split(" ") if len(t) > 2]
        t2 = [t for t in n2.split(" ") if len(t) > 2]
        if len(t1) >= 2 and len(t2) >= 2 and len(set(t1).intersection(t2)) >= 2:
            return True
    return False


def extraer_detalle_liberacion(obs: str) -> Dict[str, str]:
    s = str(obs or "")
    m = re.search(r"\[Liberada\s+(\d{1,2}:\d{2})\s*-\s*([^\]]+)\]", s, re.IGNORECASE)
    if m:
        return {"horaSalida": m.group(1).strip(), "motivo": m.group(2).strip()}
    m2 = re.search(r"\[Cancelada\s+(\d{1,2}:\d{2})\s*-\s*([^\]]+)\]", s, re.IGNORECASE)
    if m2:
        return {"horaSalida": m2.group(1).strip(), "motivo": m2.group(2).strip()}
    return {"horaSalida": "-", "motivo": ""}


def construir_detalle_adicional(
    adic_snacks: float,
    adic_serv: float,
    tienda_pagada: float = 0.0,
    tienda_items: Optional[List[Dict[str, Any]]] = None,
    saldo_estancia: float = 0.0,
    tipo_adicional: str = "",
) -> str:
    partes = []
    if adic_snacks > 0:
        if saldo_estancia <= 0:
            partes.append(f"Tienda pagada (cuenta): S/ {adic_snacks:.2f}")
        else:
            partes.append(f"Tienda cargada a cuenta: S/ {adic_snacks:.2f}")
    if tienda_pagada > 0:
        partes.append(f"Tienda pagada: S/ {tienda_pagada:.2f}")
    if adic_serv > 0:
        _tipo_txt = f" ({tipo_adicional})" if tipo_adicional else ""
        partes.append(f"Servicios{_tipo_txt}: S/ {adic_serv:.2f}")
    if tienda_items:
        resumen_items = ", ".join(
            f"{it.get('cant', 1)}x {it.get('prod', '')} (S/ {float(it.get('subTotal', 0)):.2f})"
            for it in tienda_items[:6]
        )
        if resumen_items:
            partes.append(f"[{resumen_items}]")
    return " + ".join(partes)


# ==============================================================================
# RESOLUCIÓN DE FACTURACIÓN (CALZADO_OCT + SUNAT_OCT + REGISTRO_ESTANCIAS)
# ==============================================================================

def cargar_indices_facturacion_memoria(conn) -> Dict[str, Any]:
    sunat_por_cp: Dict[str, List[Dict[str, Any]]] = {}
    sunat_por_noche_hab: Dict[str, List[Dict[str, Any]]] = {}
    calzado_por_fila: Dict[str, Dict[str, Any]] = {}
    calzado_por_noche_hab: Dict[str, Dict[str, Any]] = {}

    for r in conn.execute("SELECT * FROM sunat_lineas_cache").fetchall():
        it = {
            "nro": r["nro_cp"],
            "tipo": r["tipo"],
            "noche": r["noche_dmy"],
            "hab": r["hab"],
            "montoLinea": float(r["monto_linea"] or 0),
            "totalCp": float(r["total_cp"] or 0),
            "cliente": r["cliente"],
            "doc": r["doc"],
            "fechaEmision": r["fecha_emision"],
            "estado": r["estado"],
            "descripcion": r["descripcion"],
        }
        if it["nro"]:
            sunat_por_cp.setdefault(it["nro"], []).append(it)
        if it["noche"] and it["hab"] and it["estado"] == "VIGENTE":
            k = f"{it['noche']}|{it['hab']}"
            sunat_por_noche_hab.setdefault(k, []).append(it)

    for c in conn.execute("SELECT * FROM calzado_cache").fetchall():
        item_cal = {
            "filaCalzado": int(c["fila_calzado"] or 0),
            "noche": c["noche_dmy"],
            "hab": c["hab"],
            "huesped": c["huesped"],
            "dni": c["dni"],
            "filaEst": c["fila_est"],
            "montoSunat": float(c["monto_sunat"] or 0),
            "comprobantes": c["comprobantes"],
            "montoAJ": float(c["monto_aj"] or 0),
            "yapeAB": float(c["yape_ab"] or 0),
            "efectivoAA": float(c["efectivo_aa"] or 0),
            "excedenteEfec": float(c["excedente_efec"] or 0),
            "yapeBcp": float(c["yape_bcp"] or 0),
            "estadoBcp": c["estado_bcp"],
            "estadoConciliacion": c["estado_conciliacion"],
            "accion": c["accion"],
        }
        if item_cal["filaEst"]:
            calzado_por_fila[item_cal["filaEst"]] = item_cal
        if item_cal["noche"] and item_cal["hab"] and item_cal["hab"] != "GRUPAL":
            calzado_por_noche_hab[f"{item_cal['noche']}|{item_cal['hab']}"] = item_cal

    return {
        "sunatPorCp": sunat_por_cp,
        "sunatPorNocheHab": sunat_por_noche_hab,
        "calzadoPorFila": calzado_por_fila,
        "calzadoPorNocheHab": calzado_por_noche_hab,
    }


def resolver_facturacion_estancia(stay: Dict[str, Any], idx_fact: Dict[str, Any]) -> Dict[str, Any]:
    hab = str(stay.get("hab") or "").strip().upper()
    check_in = str(stay.get("checkIn") or "")
    fila_num = int(stay.get("fila") or 0)
    fila_key = f"E{fila_num}" if fila_num > 0 else ""

    calzado = (
        idx_fact["calzadoPorFila"].get(fila_key)
        if fila_key and fila_key in idx_fact["calzadoPorFila"]
        else idx_fact["calzadoPorNocheHab"].get(f"{check_in}|{hab}")
    )

    comprobantes_detalle: List[Dict[str, Any]] = []
    vistos = set()

    def agregar_item_sunat(it: Dict[str, Any]):
        if not it:
            return
        k = f"{it.get('nro', '')}|{it.get('noche', '')}|{it.get('montoLinea', 0)}"
        if k in vistos:
            return
        vistos.add(k)
        comprobantes_detalle.append(it)

    if calzado and calzado.get("comprobantes"):
        for raw_tok in str(calzado["comprobantes"]).split(","):
            tok = re.sub(r"\(.*?\)", "", raw_tok).strip().upper()
            if tok and tok in idx_fact["sunatPorCp"]:
                for x in idx_fact["sunatPorCp"][tok]:
                    agregar_item_sunat(x)

    if check_in and hab and f"{check_in}|{hab}" in idx_fact["sunatPorNocheHab"]:
        for x in idx_fact["sunatPorNocheHab"][f"{check_in}|{hab}"]:
            agregar_item_sunat(x)

    nro_comp_bd = str(stay.get("nroCompBD") or "").strip()
    if nro_comp_bd:
        nro_lim = nro_comp_bd.upper()
        if nro_lim in idx_fact["sunatPorCp"]:
            for x in idx_fact["sunatPorCp"][nro_lim]:
                agregar_item_sunat(x)

    suma_lineas_sunat = round(
        sum(float(c.get("montoLinea") or 0) for c in comprobantes_detalle if c.get("estado") == "VIGENTE"),
        2,
    )

    pago_yape = float(stay.get("pagoYape") or 0)
    monto_fact_bd = float(stay.get("montoFactBD") or 0)
    adic_comp = float(stay.get("adicComp") or 0)
    tarifa_noche = float(stay.get("tarifa") or 0)
    noches = max(1, int(stay.get("noches") or 1))
    subtotal_aloj = float(stay.get("subtotalAloj") or (noches * tarifa_noche))
    snacks = float(stay.get("snacks") or 0)
    servicios = float(stay.get("servicios") or 0)
    estado_comp_bd = str(stay.get("estadoCompBD") or "").strip()
    estado_comp_lower = estado_comp_bd.lower()
    solicita_lower = str(stay.get("solicitaComp") or "").strip().lower()
    tipo_comp_bd = str(stay.get("tipoComp") or "").strip()
    tipo_comp_lower = tipo_comp_bd.lower()
    ruc = str(stay.get("ruc") or "").strip()
    empresa = str(stay.get("empresa") or "").strip()

    tiene_cp_factura = bool(
        (calzado and calzado.get("comprobantes") and re.search(r"(?:E0\d+|F\d+)", str(calzado["comprobantes"]), re.I))
        or any("FACT" in str(c.get("tipo", "")).upper() or re.match(r"^(?:E0|F)", str(c.get("nro", "")), re.I) for c in comprobantes_detalle)
        or re.match(r"^(?:E0|F)", nro_comp_bd, re.I)
    )
    es_factura = bool(
        tiene_cp_factura
        or "fact" in tipo_comp_lower
        or ruc != ""
        or empresa != ""
        or (adic_comp > 0 and "bol" not in tipo_comp_lower)
    )
    tipo_comp_resuelto = "Factura" if es_factura else "Boleta"

    estado_fact = ""
    monto_facturado = 0.0

    tiene_cp_en_calzado = bool(
        calzado
        and (
            float(calzado.get("montoSunat") or 0) > 0
            or (calzado.get("comprobantes") and re.search(r"(?:EB\d+|E0\d+|B\d+|F\d+)", str(calzado["comprobantes"]), re.I))
        )
    )

    if estado_comp_lower.startswith("emitido") and nro_comp_bd:
        # Si ya fue emitido en local (o registrado con número en BD), prioridad inmediata
        estado_fact = "Emitido"
        monto_facturado = monto_fact_bd if monto_fact_bd > 0 else (suma_lineas_sunat or pago_yape or subtotal_aloj)
        if not comprobantes_detalle:
            comprobantes_detalle.append({
                "nro": nro_comp_bd,
                "tipo": tipo_comp_resuelto.upper(),
                "noche": check_in,
                "hab": hab,
                "montoLinea": monto_facturado,
                "totalCp": monto_facturado,
                "cliente": empresa or stay.get("huesped") or "",
                "doc": ruc or stay.get("dni") or "",
                "fechaEmision": check_in,
                "estado": "VIGENTE",
                "descripcion": "Emitido desde Recepción",
            })
    elif tiene_cp_en_calzado or len(comprobantes_detalle) > 0:
        estado_fact = "Emitido"
        monto_facturado = (
            float(calzado["montoSunat"])
            if calzado and float(calzado.get("montoSunat") or 0) > 0
            else (suma_lineas_sunat if suma_lineas_sunat > 0 else (float(calzado["montoAJ"]) if calzado and float(calzado.get("montoAJ") or 0) > 0 else monto_fact_bd))
        )
        if not comprobantes_detalle and calzado and calzado.get("comprobantes"):
            comprobantes_detalle.append({
                "nro": calzado["comprobantes"],
                "tipo": "FACTURA" if "E001" in str(calzado["comprobantes"]) else "BOLETA",
                "noche": calzado.get("noche") or check_in,
                "hab": hab,
                "montoLinea": monto_facturado,
                "totalCp": monto_facturado,
                "cliente": calzado.get("huesped") or stay.get("huesped") or "",
                "doc": calzado.get("dni") or stay.get("dni") or "",
                "fechaEmision": calzado.get("noche") or check_in,
                "estado": "VIGENTE",
                "descripcion": "Comprobante registrado en CALZADO_OCT",
            })
    elif calzado and (
        calzado.get("estadoConciliacion") == "YAPE_SIN_COMPROBANTE"
        or float(calzado.get("yapeBcp") or 0) > 0
        or float(calzado.get("yapeAB") or 0) > 0
        or float(calzado.get("montoAJ") or 0) > 0
    ):
        if "emitido" in estado_comp_lower:
            estado_fact = "Emitido"
            monto_facturado = monto_fact_bd or float(calzado.get("montoAJ") or 0) or float(calzado.get("yapeBcp") or pago_yape)
        else:
            estado_fact = "Pendiente"
            monto_facturado = float(calzado.get("montoAJ") or 0) or monto_fact_bd or 0.0
    else:
        if "emitido" in estado_comp_lower or (nro_comp_bd != "" and estado_comp_lower != "pendiente"):
            estado_fact = "Emitido"
            monto_facturado = monto_fact_bd if monto_fact_bd > 0 else (pago_yape if pago_yape > 0 else (subtotal_aloj + adic_comp + snacks + servicios))
            if not comprobantes_detalle:
                comprobantes_detalle.append({
                    "nro": nro_comp_bd or "Registrado en Recepción",
                    "tipo": tipo_comp_resuelto.upper(),
                    "noche": check_in,
                    "hab": hab,
                    "montoLinea": monto_facturado,
                    "totalCp": monto_facturado,
                    "cliente": empresa or stay.get("huesped") or "",
                    "doc": ruc or stay.get("dni") or "",
                    "fechaEmision": check_in,
                    "estado": "EMITIDO (RECEPCIÓN)",
                    "descripcion": "Anotado en Registro_Estancias (Columna AJ)",
                })
        elif (
            es_factura
            or pago_yape > 0
            or estado_comp_lower == "pendiente"
            or solicita_lower in ("sí", "si")
            or (tipo_comp_lower not in ("", "ninguno", "no"))
            or monto_fact_bd > 0
            or adic_comp > 0
        ):
            estado_fact = "Pendiente"
            monto_facturado = monto_fact_bd if monto_fact_bd > 0 else 0.0

    monto_acordado_total = round(subtotal_aloj + adic_comp + snacks + servicios, 2)
    if monto_fact_bd > 0:
        monto_por_facturar = monto_fact_bd
    elif calzado:
        monto_por_facturar = (
            float(calzado.get("montoAJ") or 0)
            or float(calzado.get("yapeBcp") or 0)
            or float(calzado.get("yapeAB") or 0)
            or (pago_yape if pago_yape > 0 else monto_acordado_total)
        )
    else:
        monto_por_facturar = monto_acordado_total if es_factura else (pago_yape if pago_yape > 0 else monto_acordado_total)

    comprobantes_str = (
        nro_comp_bd
        or (calzado.get("comprobantes") if calzado and calzado.get("comprobantes") else "")
        or (comprobantes_detalle[0]["nro"] if comprobantes_detalle else "")
    )

    return {
        "filaNum": fila_num,
        "idEstancia": stay.get("id") or "",
        "estanciaUuid": stay.get("estanciaUuid") or "",
        "estado": estado_fact,
        "esFactura": es_factura,
        "tipoComp": tipo_comp_resuelto,
        "montoFacturado": round(float(monto_facturado or 0), 2),
        "montoSolicitadoFact": round(monto_fact_bd, 2),
        "montoPorFacturar": round(float(monto_por_facturar or 0), 2),
        "adicComprobante": round(adic_comp, 2),
        "tarifaNoche": round(tarifa_noche, 2),
        "noches": noches,
        "subtotalAloj": round(subtotal_aloj, 2),
        "adicSnacks": round(snacks, 2),
        "adicServicios": round(servicios, 2),
        "pagadoTotal": round(float(stay.get("pagado") or 0), 2),
        "pagoEfec": round(float(stay.get("pagoEfec") or 0), 2),
        "yapeEstancia": round(pago_yape, 2),
        "codigoYape": stay.get("codigoMontoHora") or "",
        "dni": stay.get("dni") or "",
        "ruc": ruc,
        "empresa": empresa,
        "celular": stay.get("celular") or "",
        "comprobantesStr": comprobantes_str,
        "nroCompBD": nro_comp_bd,
        "comprobantesDetalle": comprobantes_detalle,
        "enCalzado": bool(calzado),
        "filaCalzado": int(calzado.get("filaCalzado") or 0) if calzado else 0,
        "filaEst": fila_key or (calzado.get("filaEst") if calzado else ""),
        "nocheCalzado": calzado.get("noche") if calzado else check_in,
        "montoSunatCalzado": float(calzado.get("montoSunat") or 0) if calzado else 0.0,
        "comprobantesCalzado": calzado.get("comprobantes") if calzado else "",
        "montoAJCalzado": float(calzado.get("montoAJ") or 0) if calzado else 0.0,
        "yapeABCalzado": float(calzado.get("yapeAB") or 0) if calzado else 0.0,
        "estadoConciliacion": calzado.get("estadoConciliacion") if calzado else "",
        "accionCalzado": calzado.get("accion") if calzado else "",
        "yapeBcp": float(calzado.get("yapeBcp") or 0) if calzado else 0.0,
        "estadoBcp": calzado.get("estadoBcp") if calzado else "",
    }


# ==============================================================================
# CONSULTA DEL PANEL DE DISPONIBILIDAD EN 0 MS (SQLITE LOCAL)
# ==============================================================================

def obtener_caja_efectivo_actual(conn) -> str:
    row = conn.execute("SELECT valor FROM estado_sistema WHERE clave = 'caja_sheets_g1'").fetchone()
    base_caja = parse_moneda(row["valor"]) if row else 0.0
    delta_row = conn.execute(
        "SELECT COALESCE(SUM(delta_caja), 0) AS delta FROM sync_queue WHERE status IN ('PENDING', 'PROCESSING', 'ERROR')"
    ).fetchone()
    delta = float(delta_row["delta"] or 0.0) if delta_row else 0.0
    return f"{(base_caja + delta):.2f}"


def obtener_estado_red_cola(conn=None) -> Dict[str, Any]:
    close_after = False
    if conn is None:
        conn = get_connection()
        close_after = True
    try:
        pend = conn.execute(
            "SELECT COUNT(*) AS c FROM sync_queue WHERE status IN ('PENDING', 'PROCESSING', 'ERROR')"
        ).fetchone()["c"]
        online_row = conn.execute("SELECT valor FROM estado_sistema WHERE clave = 'network_online'").fetchone()
        last_sync_row = conn.execute("SELECT valor FROM estado_sistema WHERE clave = 'last_sync_ts'").fetchone()
        last_sync_iso_row = conn.execute("SELECT valor FROM estado_sistema WHERE clave = 'last_successful_pull_iso'").fetchone()
        offline_since_row = conn.execute("SELECT valor FROM estado_sistema WHERE clave = 'offline_since_iso'").fetchone()
        last_err_sys_row = conn.execute("SELECT valor FROM estado_sistema WHERE clave = 'last_sync_error'").fetchone()
        err_row = conn.execute(
            "SELECT last_error FROM sync_queue WHERE status = 'ERROR' AND last_error != '' ORDER BY queue_id ASC LIMIT 1"
        ).fetchone()

        online = (online_row["valor"] != "0") if online_row else True
        last_sync_ts = last_sync_row["valor"] if last_sync_row else ""
        last_sync_iso = last_sync_iso_row["valor"] if last_sync_iso_row else ""
        offline_since_iso = offline_since_row["valor"] if offline_since_row else ""
        last_error = (last_err_sys_row["valor"] if last_err_sys_row and last_err_sys_row["valor"] else "") or (err_row["last_error"] if err_row else "")

        minutos_offline = 0
        if not online and offline_since_iso:
            try:
                dt_off = datetime.fromisoformat(offline_since_iso)
                minutos_offline = max(0, int((datetime.now() - dt_off).total_seconds() / 60))
            except Exception:
                minutos_offline = 0

        minutos_desde_sync = -1
        if last_sync_iso:
            try:
                dt_s = datetime.fromisoformat(last_sync_iso)
                minutos_desde_sync = max(0, int((datetime.now() - dt_s).total_seconds() / 60))
            except Exception:
                minutos_desde_sync = -1

        return {
            "online": online,
            "pendingCount": int(pend or 0),
            "lastSync": last_sync_ts,
            "lastSyncISO": last_sync_iso,
            "offlineSinceISO": offline_since_iso,
            "minutosOffline": minutos_offline,
            "minutosDesdeSync": minutos_desde_sync,
            "lastError": last_error,
            "nombreHotel": NOMBRE_HOTEL,
        }
    finally:
        if close_after:
            conn.close()


_ULTIMA_FECHA_PANEL_ISO: Optional[str] = None


def es_empresa_corporativa(emp: Any) -> bool:
    """True solo si es una empresa corporativa real (ignora TRANSITO / PARTICULAR de uso diario)."""
    s = str(emp or "").strip().upper()
    if not s or s in ("-", "—", "NINGUNA", "NO"):
        return False
    for pref in ("TRANSITO", "TRÁNSITO", "PARTICULAR"):
        if s.startswith(pref):
            return False
    return True


def obtener_datos_panel_local(fecha_override: Optional[str] = None) -> Dict[str, Any]:
    global _ULTIMA_FECHA_PANEL_ISO
    conn = get_connection()
    try:
        prev_sh_row = conn.execute("SELECT valor FROM estado_sistema WHERE clave = 'spreadsheet_id_activo'").fetchone()
        prev_sh_id = str(prev_sh_row["valor"]).strip() if prev_sh_row else ""
        tiene_hab_ajena = False
        if "1vgjOLxc" in SPREADSHEET_ID:
            r_aj = conn.execute("SELECT 1 FROM habitaciones_config WHERE hab IN ('101','102','103','104','105') LIMIT 1").fetchone()
            if r_aj:
                tiene_hab_ajena = True
        if (prev_sh_id and prev_sh_id != SPREADSHEET_ID) or tiene_hab_ajena:
            conn.execute("DELETE FROM habitaciones_config")
            conn.execute("DELETE FROM estancias WHERE dirty = 0")
            conn.execute("DELETE FROM pagos WHERE synced = 1")
            conn.execute("DELETE FROM ventas_tienda WHERE synced = 1")
            conn.execute("DELETE FROM gastos WHERE synced = 1")
            conn.execute("DELETE FROM sunat_lineas_cache")
            conn.execute("DELETE FROM calzado_cache")
            conn.execute("DELETE FROM vitrina_local")
            conn.execute("DELETE FROM almacen_movimientos WHERE synced = 1")
            conn.execute(
                "INSERT OR REPLACE INTO estado_sistema (clave, valor, updated_at) VALUES ('spreadsheet_id_activo', ?, '')",
                (SPREADSHEET_ID,),
            )
            conn.commit()
            try:
                if "sync_engine" in _sys.modules:
                    _se_m = _sys.modules["sync_engine"]
                    if hasattr(_se_m, "_reset_client"):
                        _se_m._reset_client()
                    if hasattr(_se_m, "pull_from_sheets"):
                        _threading.Thread(target=_se_m.pull_from_sheets, kwargs={"full_bootstrap": False}, daemon=True).start()
            except Exception:
                pass

        fecha_hoy_iso_val = hoy_iso()
        fecha_hoy_dmy_val = hoy_dmy()

        if fecha_override:
            fecha_vis = normalizar_fecha_dmy(fecha_override) or fecha_hoy_dmy_val
            _ULTIMA_FECHA_PANEL_ISO = dmy_a_iso(fecha_vis)
        elif _ULTIMA_FECHA_PANEL_ISO:
            fecha_vis = normalizar_fecha_dmy(_ULTIMA_FECHA_PANEL_ISO) or fecha_hoy_dmy_val
        else:
            fecha_vis = fecha_hoy_dmy_val
            _ULTIMA_FECHA_PANEL_ISO = fecha_hoy_iso_val

        ymd_vis = fecha_dmy_a_ymd(fecha_vis)
        try:
            dt_vis = datetime.strptime(fecha_vis, "%d/%m/%Y")
            fecha_ayer_dmy = (dt_vis - timedelta(days=1)).strftime("%d/%m/%Y")
        except Exception:
            fecha_ayer_dmy = ""
        ymd_ayer = fecha_dmy_a_ymd(fecha_ayer_dmy) if fecha_ayer_dmy else 0
        mapa_ayer_por_hab: Dict[str, Dict[str, Any]] = {}
        total_caja = obtener_caja_efectivo_actual(conn)
        idx_fact = cargar_indices_facturacion_memoria(conn)

        # 1. Leer ventas de tienda recientes para cruce de ítems
        mapa_tienda_hab: Dict[str, List[Dict[str, Any]]] = {}
        for tv in conn.execute("SELECT * FROM ventas_tienda ORDER BY rowid DESC LIMIT 400").fetchall():
            h_tv = str(tv["hab"] or "").strip()
            if not h_tv:
                continue
            met_tv = str(tv["metodo_pago"] or "").strip()
            es_cargo = met_tv in ("Cargar a Habitación", "Cargar a la Cuenta")
            f_v_dmy = normalizar_fecha_dmy(tv["fecha_dmy"])
            mapa_tienda_hab.setdefault(h_tv, []).append({
                "prod": tv["producto"],
                "cant": int(tv["cantidad"] or 1),
                "subTotal": float(tv["subtotal"] or 0),
                "metodo": met_tv,
                "pagado": not es_cargo,
                "fechaVenta": f_v_dmy,
                "ymdVenta": fecha_dmy_a_ymd(f_v_dmy),
                "idEstancia": str(tv["id_estancia"] or ""),
            })

        # 2. Leer estancias recientes (ordenadas de la más nueva a la más antigua)
        rows_est = conn.execute(
            """
            SELECT * FROM estancias
            ORDER BY CASE WHEN sheet_row > 0 THEN sheet_row ELSE 9999999 END DESC, rowid DESC
            LIMIT 650
            """
        ).fetchall()

        mapa_celular_dni: Dict[str, str] = {}
        for r in rows_est:
            d = normalizar_dni(r["dni"], r["nac"])
            c = str(r["celular"] or "").strip()
            if d and c and d not in mapa_celular_dni:
                mapa_celular_dni[d] = c

        mapa_emp_por_ruc: Dict[str, str] = {}
        for ep in conn.execute("SELECT ruc, razon_social FROM empresas_padron").fetchall():
            r_ep = str(ep["ruc"] or "").strip()
            n_ep = str(ep["razon_social"] or "").strip()
            if r_ep and n_ep:
                mapa_emp_por_ruc[r_ep] = n_ep

        estancias_cache: List[Dict[str, Any]] = []
        mapa_bd_por_fecha: Dict[str, Dict[str, Any]] = {}
        mapa_historial_dia: Dict[str, List[Dict[str, Any]]] = {}

        for r in rows_est:
            hab = str(r["hab"] or "").strip()
            if not hab:
                continue
            hotel_apoyo = str(r["hotel_apoyo"] or "").strip()
            f_in = normalizar_fecha_dmy(r["fecha_in_dmy"])
            f_out = normalizar_fecha_dmy(r["fecha_out_dmy"])
            ymd_in = int(r["ymd_in"] or fecha_dmy_a_ymd(f_in))
            ymd_out = int(r["ymd_out"] or fecha_dmy_a_ymd(f_out) or (ymd_in + 1 if ymd_in else 0))

            noches_calc = max(1, int(r["noches"] or calcular_noches(f_in, f_out)))
            tarifa = float(r["tarifa"] or 0)
            subtotal_aloj = round(noches_calc * tarifa, 2)
            snacks = float(r["adic_snacks"] or 0)
            servicios = float(r["adic_servicios"] or 0)
            adic_comp = float(r["adic_comp"] or 0)
            pago_efec = round(float(r["pago_ini_efec"] or 0) + float(r["pago_post_efec"] or 0), 2)
            pago_yape = round(float(r["pago_ini_yape"] or 0) + float(r["pago_post_yape"] or 0), 2)
            pago_visa = round(float(r["pago_visa"] or 0), 2)
            pagado_total = round(pago_efec + pago_yape + pago_visa, 2)
            deuda_ant = round(float(r["deuda_anterior"] or 0), 2)
            saldo = round(subtotal_aloj + snacks + servicios + adic_comp + deuda_ant - pagado_total, 2)

            estado_est = str(r["estado_estancia"] or "En Curso").strip()
            est_lower = estado_est.lower()
            obs_fila = str(r["observaciones"] or "").strip()
            es_continuacion_cerrada = "contin" in est_lower
            es_cancelada = "cancel" in est_lower or "[cancelada" in obs_fila.lower()
            es_finalizada_simple = (
                not es_continuacion_cerrada
                and not es_cancelada
                and (est_lower == "finalizado" or "finaliz" in est_lower or "[liberada" in obs_fila.lower())
            )
            es_inactiva = es_cancelada or es_finalizada_simple

            # Tienda asociada a esta estancia
            tienda_pagada = 0.0
            tienda_items: List[Dict[str, Any]] = []
            for it_t in mapa_tienda_hab.get(hab, []):
                coincide = False
                if it_t["idEstancia"] and it_t["idEstancia"] in (r["id_timestamp_str"], r["estancia_uuid"], f_in):
                    coincide = True
                elif not it_t["idEstancia"] and ymd_in > 0 and it_t["ymdVenta"] > 0:
                    coincide = (ymd_in <= it_t["ymdVenta"] <= ymd_out)
                if coincide:
                    if it_t["pagado"]:
                        tienda_pagada += it_t["subTotal"]
                    tienda_items.append({
                        "prod": it_t["prod"],
                        "cant": it_t["cant"],
                        "subTotal": round(it_t["subTotal"], 2),
                        "metodo": it_t["metodo"],
                        "pagado": it_t["pagado"],
                        "fecha": it_t["fechaVenta"],
                    })
            tienda_pagada = round(tienda_pagada, 2)

            dni_norm = normalizar_dni(r["dni"], r["nac"])
            cel_val = str(r["celular"] or "").strip() or mapa_celular_dni.get(dni_norm, "")
            det_lib = extraer_detalle_liberacion(obs_fila)

            info_fila: Dict[str, Any] = {
                "estanciaUuid": r["estancia_uuid"],
                "parentUuid": r["parent_uuid"] or "",
                "fila": int(r["sheet_row"] or 0),
                "filaNum": int(r["sheet_row"] or 0),
                "id": r["id_timestamp_str"],
                "hab": hab,
                "hotelApoyo": hotel_apoyo,
                "checkIn": f_in or "-",
                "checkOut": f_out or "-",
                "ymdIn": ymd_in,
                "ymdOut": ymd_out,
                "hora": r["hora_registro"] or "-",
                "horaSalida": det_lib["horaSalida"],
                "motivo": det_lib["motivo"],
                "huesped": r["huesped"] or "-",
                "dni": dni_norm,
                "ruc": str(r["ruc"] or "").strip(),
                "empresa": str(r["empresa"] or "").strip() or mapa_emp_por_ruc.get(str(r["ruc"] or "").strip(), ""),
                "proyecto": str(r["proyecto"] or "").strip(),
                "nAcomp": str(r["n_acomp"] or "").strip(),
                "triple1": str(r["triple1"] or "").strip(),
                "tipoAdicional": str(r["tipo_adicional"] or "").strip(),
                "cuentaDestino": str(r["cuenta_destino"] or "").strip(),
                "celular": cel_val,
                "noches": noches_calc,
                "tarifa": tarifa,
                "subtotalAloj": subtotal_aloj,
                "snacks": snacks,
                "servicios": servicios,
                "tiendaPagada": tienda_pagada,
                "tiendaItems": tienda_items,
                "adicComp": adic_comp,
                "pagoEfec": pago_efec,
                "pagoYape": pago_yape,
                "pagoVisa": pago_visa,
                "pagado": pagado_total,
                "deudaAnt": deuda_ant,
                "saldo": saldo,
                "estadoEstancia": estado_est,
                "continuoEstancia": es_continuacion_cerrada or (deuda_ant != 0),
                "esContinuacionCerrada": es_continuacion_cerrada,
                "esInactiva": es_inactiva,
                "esCancelada": es_cancelada,
                "observaciones": obs_fila,
                "codigoMontoHora": str(r["nro_op_yape"] or "").strip(),
                "solicitaComp": str(r["solicita_comp"] or "").strip(),
                "tipoComp": str(r["tipo_comp"] or "").strip(),
                "estadoCompBD": str(r["estado_comp"] or "").strip(),
                "nroCompBD": str(r["nro_comp"] or "").strip(),
                "montoFactBD": float(r["monto_factura"] or 0),
            }

            info_fila["facturacion"] = resolver_facturacion_estancia(info_fila, idx_fact)

            if not hotel_apoyo and len(estancias_cache) < 350:
                estancias_cache.append(info_fila)

            # Detectar si la habitación estuvo ocupada el día inmediatamente anterior (para botón 🔁 junto a Check-in)
            activa_ayer = (not hotel_apoyo) and (not es_inactiva) and (not es_continuacion_cerrada) and (
                (ymd_ayer > 0 and ymd_in > 0 and ymd_in <= ymd_ayer < ymd_out)
                or (fecha_ayer_dmy and f_in == fecha_ayer_dmy)
            )
            if activa_ayer and hab not in mapa_ayer_por_hab:
                mapa_ayer_por_hab[hab] = info_fila

            # REGLA EXACTA DE NOCHE DE HOSPEDAJE (Corrige de raíz falsos positivos de check-out del día anterior):
            # Una estancia corresponde a la fecha visualizada SOLO si:
            # 1) (ymd_in <= ymd_vis < ymd_out)  -> Noche dentro del rango de hospedaje
            # 2) (f_in == fecha_vis)            -> Ingreso ese mismo día (incluye por horas del mismo día)
            activa_en_fecha = (not hotel_apoyo) and (
                (ymd_vis > 0 and ymd_in > 0 and ymd_in <= ymd_vis < ymd_out)
                or (f_in == fecha_vis)
            )

            if not activa_en_fecha:
                continue

            if not es_inactiva:
                # Si es "En Curso" tiene prioridad sobre "Finalizado - Continúa" en la misma fecha;
                # pero si estamos viendo una noche pasada (ej. Ayer) donde la fila quedó como "Finalizado - Continúa",
                # sí se muestra como la estancia ocupada de esa noche si no hay otra fila "En Curso" de esa noche.
                existente = mapa_bd_por_fecha.get(hab)
                if not existente:
                    mapa_bd_por_fecha[hab] = info_fila
                elif existente.get("esContinuacionCerrada") and not es_continuacion_cerrada:
                    mapa_bd_por_fecha[hab] = info_fila
            elif es_finalizada_simple and f_in == fecha_vis:
                # Solo entran al historial 👤 estancias que ingresaron en la fecha visualizada y fueron liberadas
                # (nunca canceladas ni continuaciones)
                mapa_historial_dia.setdefault(hab, []).append({
                    "estanciaUuid": info_fila["estanciaUuid"],
                    "parentUuid": info_fila["parentUuid"],
                    "fila": info_fila["fila"],
                    "id": info_fila["id"],
                    "hab": hab,
                    "huesped": info_fila["huesped"],
                    "dni": info_fila["dni"],
                    "celular": info_fila["celular"],
                    "checkIn": info_fila["checkIn"],
                    "checkOut": info_fila["checkOut"],
                    "hora": info_fila["hora"],
                    "horaSalida": info_fila["horaSalida"],
                    "motivo": info_fila["motivo"],
                    "tarifa": info_fila["tarifa"],
                    "pagado": info_fila["pagado"],
                    "pagoDetalle": {
                        "efectivo": info_fila["pagoEfec"],
                        "yape": info_fila["pagoYape"],
                        "visa": info_fila["pagoVisa"],
                    },
                    "saldo": info_fila["saldo"],
                    "estadoEstancia": info_fila["estadoEstancia"],
                    "facturacion": info_fila["facturacion"],
                })

        # 3. Construir las 29 habitaciones desde habitaciones_config
        colores_panel = _cargar_colores_panel(conn)
        habs_cfg = conn.execute("SELECT * FROM habitaciones_config ORDER BY orden ASC, hab ASC").fetchall()
        habitaciones: List[Dict[str, Any]] = []
        plantilla_habs: List[Dict[str, Any]] = []

        for hc in habs_cfg:
            hab_num = str(hc["hab"]).strip()
            precio_ref = str(hc["precio_ref"] or "").strip()
            tipo = str(hc["tipo"] or "").strip()
            _cg = colores_panel["globales"].get(hab_num) or colores_panel["porFecha"].get(fecha_vis, {}).get(hab_num) or {}
            color_pers = str(_cg.get("color") or "")
            nota_color = str(_cg.get("nota") or "")

            plantilla_habs.append({
                "hab": hab_num,
                "precioRef": precio_ref,
                "tipo": tipo,
                "colorPersonalizado": color_pers,
                "notaColor": nota_color,
            })

            st = mapa_bd_por_fecha.get(hab_num)
            raw_hist = mapa_historial_dia.get(hab_num, [])

            # FILTRO ESTRICTO ANTI-FALSO-POSITIVO 👤 (Excluye mismo huésped, mismo DNI zfill(8) y duplicados entre sí)
            hist_filtrado: List[Dict[str, Any]] = []
            for ant in raw_hist:
                if st and son_mismo_huesped(st.get("dni", ""), st.get("huesped", ""), ant.get("dni", ""), ant.get("huesped", "")):
                    continue
                if st and st.get("parentUuid") and st.get("parentUuid") == ant.get("estanciaUuid"):
                    continue
                if any(son_mismo_huesped(prev.get("dni", ""), prev.get("huesped", ""), ant.get("dni", ""), ant.get("huesped", "")) for prev in hist_filtrado):
                    continue
                hist_filtrado.append(ant)

            if not st:
                st_ayer = mapa_ayer_por_hab.get(hab_num)
                habitaciones.append({
                    "colorPersonalizado": color_pers,
                    "notaColor": nota_color,
                    "precioRef": precio_ref,
                    "hab": hab_num,
                    "tipo": tipo,
                    "estado": "libre",
                    "ocupadaAyer": bool(st_ayer),
                    "huespedAyer": st_ayer["huesped"] if st_ayer else "",
                    "dniAyer": str(st_ayer["dni"] or "") if st_ayer else "",
                    "checkInAyer": str(st_ayer["checkIn"] or "") if st_ayer else "",
                    "filaAyer": int(st_ayer["fila"] or 0) if st_ayer else 0,
                    "idAyer": str(st_ayer["id"] or "") if st_ayer else "",
                    "estanciaUuidAyer": str(st_ayer["estanciaUuid"] or "") if st_ayer else "",
                    "deudaAnt": 0,
                    "continuoEstancia": False,
                    "esVueltoContinua": False,
                    "checkIn": "-",
                    "hora": "-",
                    "huesped": "-",
                    "dni": "",
                    "celular": "",
                    "tarifaPactada": 0,
                    "pagado": 0,
                    "pagoDetalle": None,
                    "empresa": "",
                    "proyecto": "",
                    "nAcomp": "",
                    "triple1": "",
                    "adicional": 0,
                    "adicSnacksCargado": 0,
                    "adicServicios": 0,
                    "tiendaPagada": 0,
                    "tiendaItems": [],
                    "adicDetalle": "",
                    "saldo": 0,
                    "filaNum": 0,
                    "estanciaUuid": "",
                    "facturacion": None,
                    "historialDia": hist_filtrado,
                })
            else:
                saldo_real_est = float(st["saldo"] or 0)
                snacks_carg = float(st["snacks"] or 0)
                serv_carg = float(st["servicios"] or 0)
                adic_comp_est = float(st["adicComp"] or 0)
                pagado_est = float(st["pagado"] or 0)
                emp_est = str(st.get("empresa") or "").strip()
                if es_empresa_corporativa(emp_est) and saldo_real_est > 0:
                    tot_adic_est = snacks_carg + serv_carg + adic_comp_est
                    adic_pend = max(0.0, round(tot_adic_est - pagado_est, 2))
                    saldo = min(saldo_real_est, adic_pend)
                else:
                    saldo = saldo_real_est

                deuda_ant = float(st["deudaAnt"] or 0)
                continuo = bool(st["continuoEstancia"])
                es_vuelto = (saldo < 0) and (continuo or deuda_ant < 0)
                if saldo > 0 and (continuo or deuda_ant > 0):
                    estado_clave = "ocupado_deuda_continua"
                elif saldo > 0:
                    estado_clave = "ocupado_debe"
                else:
                    estado_clave = "ocupado_aldia"

                t_pag = float(st["tiendaPagada"] or 0)
                t_its = st["tiendaItems"] or []
                total_adic = round(snacks_carg + serv_carg + t_pag, 2)

                habitaciones.append({
                    "colorPersonalizado": color_pers,
                    "notaColor": nota_color,
                    "precioRef": precio_ref,
                    "hab": hab_num,
                    "tipo": tipo,
                    "estado": estado_clave,
                    "deudaAnt": deuda_ant,
                    "continuoEstancia": continuo,
                    "esVueltoContinua": es_vuelto,
                    "checkIn": st["checkIn"],
                    "checkOut": st["checkOut"],
                    "hora": st["hora"],
                    "huesped": st["huesped"],
                    "dni": st["dni"],
                    "celular": st["celular"],
                    "tarifaPactada": float(st["tarifa"] or 0),
                    "pagado": float(st["pagado"] or 0),
                    "pagoDetalle": {
                        "efectivo": float(st["pagoEfec"] or 0),
                        "yape": float(st["pagoYape"] or 0),
                        "visa": float(st["pagoVisa"] or 0),
                        "codigoMontoHora": st["codigoMontoHora"] or "",
                    },
                    "adicional": total_adic,
                    "adicSnacksCargado": snacks_carg,
                    "adicServicios": serv_carg,
                    "adicComp": float(st["adicComp"] or 0),
                    "tiendaPagada": t_pag,
                    "tiendaItems": t_its,
                    "empresa": st.get("empresa") or "",
                    "proyecto": st.get("proyecto") or "",
                    "nAcomp": st.get("nAcomp") or "",
                    "triple1": st.get("triple1") or "",
                    "tipoAdicional": st.get("tipoAdicional") or "",
                    "adicDetalle": construir_detalle_adicional(snacks_carg, serv_carg, t_pag, t_its, saldo, st.get("tipoAdicional") or ""),
                    "saldo": saldo,
                    "filaNum": int(st["fila"] or 0),
                    "id": st["id"],
                    "idEstancia": st["id"],
                    "estanciaUuid": st["estanciaUuid"],
                    "facturacion": st["facturacion"],
                    "historialDia": hist_filtrado,
                })

        sync_info = obtener_estado_red_cola(conn)
        vis_hoy_cnt = 0
        vis_rech_cnt = 0
        try:
            import pases_logic as _pl
            f_corte_v = _pl.fecha_corte_mediodia_actual() if fecha_vis == datetime.now().strftime("%d/%m/%Y") else fecha_vis
            ymd_v = fecha_dmy_a_ymd(f_corte_v)
            r_vp = conn.execute(
                "SELECT SUM(CASE WHEN estado = 'ACEPTADO' THEN 1 ELSE 0 END) AS a, "
                "SUM(CASE WHEN estado = 'RECHAZADO' THEN 1 ELSE 0 END) AS r "
                "FROM control_pases WHERE ymd = ?",
                (ymd_v,),
            ).fetchone()
            if r_vp:
                vis_hoy_cnt = int(r_vp["a"] or 0)
                vis_rech_cnt = int(r_vp["r"] or 0)
        except Exception:
            pass
        return {
            "nombreHotel": NOMBRE_HOTEL,
            "coloresGlobales": colores_panel["globales"],
            "coloresPorFecha": colores_panel["porFecha"],
            "fechaHoyISO": fecha_hoy_iso_val,
            "fechaVisualizada": fecha_vis,
            "totalCaja": total_caja,
            "visitasHoyCount": vis_hoy_cnt,
            "visitasRechazadasHoy": vis_rech_cnt,
            "habitaciones": habitaciones,
            "plantillaHabs": plantilla_habs,
            "estanciasCache": estancias_cache,
            "syncStatus": sync_info,
        }
    finally:
        conn.close()


# ==============================================================================
# CONSTRUCTOR DE FILA DE 51 COLUMNAS PARA GOOGLE SHEETS (PROTEGE FÓRMULAS MAP)
# Columnas MAP vacías (""): 16(P), 18(R), 22(V), 27(AA), 28(AB), 29(AC), 30(AD), 40(AN), 41(AO)
# ==============================================================================

def construir_fila_sheets_estancia(r: Dict[str, Any]) -> List[Any]:
    m_fact = r.get("monto_factura")
    m_fact_cell = round(float(m_fact), 2) if (m_fact is not None and m_fact != "" and float(m_fact or 0) > 0) else ""
    return [
        r["id_timestamp_str"],                  # 1  (A) ID Timestamp
        r["fecha_in_dmy"],                      # 2  (B) Fecha_CheckIn
        r["fecha_out_dmy"],                     # 3  (C) Fecha_CheckOut
        r["huesped"],                           # 4  (D) Nombres_Huesped
        r["dni"],                               # 5  (E) DNI_Huesped
        r.get("sexo") or "M",                   # 6  (F) Sexo
        r.get("n_acomp") or "",                 # 7  (G) Nombres_Acompañante (DOBLE)
        r.get("dni_acomp") or "",               # 8  (H) DNI_Acompañante (DOBLE)
        r.get("nac") or "Perú",                 # 9  (I) Nacionalidad
        r.get("reg") or "Lima",                 # 10 (J) Region
        r.get("mot") or "Vacaciones, recreo u ocio", # 11 (K) Motivo de viaje
        r["hab"],                               # 12 (L) N habitacion
        r.get("tipo_hab") or "",                # 13 (M) Tipo_Habitacion
        r.get("visita") or "Visita N° 1",       # 14 (N) Cliente_Recurrente
        r.get("hora_registro") or "",           # 15 (O) Hora_Registro
        "",                                     # 16 (P) [FÓRMULA MAP: Noches_Reales]
        round(float(r.get("tarifa") or 0), 2),  # 17 (Q) Tarifa_Noche
        "",                                     # 18 (R) [FÓRMULA MAP: Subtotal_Alojamiento]
        round(float(r.get("adic_snacks") or 0), 2) if float(r.get("adic_snacks") or 0) > 0 else "", # 19 (S) Adic_Snacks
        round(float(r.get("adic_servicios") or 0), 2), # 20 (T) Adic_Servicios
        round(float(r.get("adic_comp") or 0), 2),      # 21 (U) Adic_Comprobante
        "",                                     # 22 (V) [FÓRMULA MAP: Total_Cuenta]
        round(float(r.get("pago_ini_efec") or 0), 2),  # 23 (W) Pago_Inicial_Efectivo
        round(float(r.get("pago_ini_yape") or 0), 2),  # 24 (X) Pago_Inicial_Electronico
        round(float(r.get("pago_post_efec") or 0), 2), # 25 (Y) P_Posteriores_Efectivo
        round(float(r.get("pago_post_yape") or 0), 2), # 26 (Z) Pagos_Posteriores_YAPE
        "",                                     # 27 (AA) [FÓRMULA MAP: Total_Pago_Efectivo]
        "",                                     # 28 (AB) [FÓRMULA MAP: Total_Pago_Yape]
        "",                                     # 29 (AC) [FÓRMULA MAP: Total_Pagado]
        "",                                     # 30 (AD) [FÓRMULA MAP: Saldo_Final_Estancia]
        r.get("cuenta_destino") or "",          # 31 (AE) Cuenta_Destino
        r.get("solicita_comp") or "No",         # 32 (AF) Solicita_Comprobante
        r.get("tipo_comp") or "",               # 33 (AG) Tipo_Comprobante
        r.get("estado_comp") or "",             # 34 (AH) Estado_Comprobante
        r.get("nro_comp") or "",                # 35 (AI) Nro_Comprobante
        m_fact_cell,                            # 36 (AJ) monto factura
        r.get("estado_estancia") or "En Curso", # 37 (AK) Estado_Estancia
        round(float(r.get("deuda_anterior") or 0), 2), # 38 (AL) Cargo_Deuda_Anterior
        round(float(r.get("pago_visa") or 0), 2),      # 39 (AM) Pagos VISA
        "",                                     # 40 (AN) [FÓRMULA MAP: Estado Pago]
        "",                                     # 41 (AO) [FÓRMULA MAP: Cliente fiel]
        "",                                     # 42 (AP) facturacion total
        r.get("empresa") or "",                 # 43 (AQ) EMPRESA
        r.get("proyecto") or "",                # 44 (AR) PROYECTO
        r.get("triple1") or "",                 # 45 (AS) Nombres_Acompañante (TRIPLE)
        r.get("triple2") or "",                 # 46 (AT) DNI_Acompañante (TRIPLE)
        r.get("hotel_apoyo") or "",             # 47 (AU) Hotel_Apoyo
        r.get("observaciones") or "",           # 48 (AV) observaciones
        r.get("nro_op_yape") or "",             # 49 (AW) N° de operacion
        r.get("celular") or "",                 # 50 (AX) CELULAR
        r.get("ruc") or "",                     # 51 (AY) RUC
    ]


def _encolar_operacion(conn, op_type: str, entity_uuid: str, payload: Dict[str, Any], delta_caja: float = 0.0) -> str:
    op_uuid = str(uuid.uuid4())
    payload = dict(payload or {})
    _det_mov = payload.pop("_mov_detalle", None)
    try:
        _movimiento_desde_operacion(conn, op_type, entity_uuid, payload, delta_caja, _det_mov)
    except Exception:
        pass  # el historial nunca debe bloquear una operación de caja
    conn.execute(
        """
        INSERT INTO sync_queue (op_uuid, op_type, entity_uuid, payload_json, delta_caja, status, created_at)
        VALUES (?, ?, ?, ?, ?, 'PENDING', ?)
        """,
        (op_uuid, op_type, entity_uuid, json.dumps(payload, ensure_ascii=False), round(delta_caja, 2), datetime.now().isoformat()),
    )
    return op_uuid


# ==============================================================================
# OPERACIONES DE ESCRITURA EN 0 MS (CHECK-IN, PAGOS, TIENDA, GASTOS, ESTADOS)
# ==============================================================================

def guardar_checkin_local(datos: Dict[str, Any]) -> Dict[str, Any]:
    nac = str(datos.get("nac") or "Perú").strip()
    dni = normalizar_dni(datos.get("dni"), nac)
    nombre = str(datos.get("nombre") or "").strip().upper()
    sexo = str(datos.get("sexo") or "M").strip()
    reg = str(datos.get("reg") or "Lima").strip()
    mot = str(datos.get("mot") or "Vacaciones, recreo u ocio").strip()
    hab = str(datos.get("hab") or "").strip()
    tarifa = parse_moneda(datos.get("tarifa"))
    p_efec_base = parse_moneda(datos.get("pEfec"))
    p_elec_base = parse_moneda(datos.get("pElec"))
    p_visa_base = parse_moneda(datos.get("pVisa"))
    serv_monto = parse_moneda(datos.get("servMonto"))
    serv_metodo = str(datos.get("servMetodo") or "Cargar a Habitación").strip()

    p_efec = round(p_efec_base + (serv_monto if serv_monto > 0 and serv_metodo == "Efectivo" else 0.0), 2)
    p_elec = round(p_elec_base + (serv_monto if serv_monto > 0 and serv_metodo == "Yape" else 0.0), 2)
    p_visa = round(p_visa_base + (serv_monto if serv_monto > 0 and serv_metodo == "VISA" else 0.0), 2)

    nro_op_yape = str(datos.get("nroOpYape") or "").strip()
    n_acomp = str(datos.get("nAcomp") or "").strip()
    dni_acomp = str(datos.get("dniAcomp") or "").strip()
    triple1 = str(datos.get("triple1") or "").strip()
    triple2 = str(datos.get("triple2") or "").strip()
    celular = str(datos.get("celular") or "").strip()
    ruc = re.sub(r"\D", "", str(datos.get("ruc") or ""))
    empresa = str(datos.get("empresa") or "").strip().upper()
    m_fact = parse_moneda(datos.get("mFact")) if datos.get("mFact") not in (None, "") else 0.0
    adic_comp = parse_moneda(datos.get("adicComp"))
    tipo_c = str(datos.get("tipoC") or "").strip()
    if (not tipo_c or tipo_c == "Ninguno") and (ruc or empresa):
        tipo_c = "Factura"
    elif (not tipo_c or tipo_c == "Ninguno") and p_elec > 0:
        tipo_c = "Boleta"
    hotel_tercerizado = str(datos.get("hotelTercerizado") or "").strip()

    if not dni or not nombre or not hab:
        raise ValueError("Faltan campos obligatorios (DNI, Nombre o Habitación).")
    if p_elec > 0 and not nro_op_yape:
        raise ValueError("El N° de Operación Yape es obligatorio cuando hay pago con Yape.")

    fecha_in_dmy = normalizar_fecha_dmy(datos.get("fechaIn")) or hoy_dmy()
    fecha_out_dmy = normalizar_fecha_dmy(datos.get("fechaOut")) or (datetime.now() + timedelta(days=1)).strftime("%d/%m/%Y")
    ymd_in = fecha_dmy_a_ymd(fecha_in_dmy)
    ymd_out = fecha_dmy_a_ymd(fecha_out_dmy) or (ymd_in + 1)
    noches = calcular_noches(fecha_in_dmy, fecha_out_dmy)

    ahora = datetime.now()
    id_ts_str = ahora.strftime("%d/%m/%Y %H:%M:%S")
    hora_reg = ahora.strftime("%H:%M")
    base_ms = int(time.time() * 1000)

    with db_transaction() as conn:
        # Protección anti-doble-clic (45 segundos)
        dup = conn.execute(
            """
            SELECT estancia_uuid, sheet_row, visita, updated_at FROM estancias
            WHERE dni = ? AND hab = ? AND fecha_in_dmy = ? AND estado_estancia = 'En Curso'
            ORDER BY rowid DESC LIMIT 1
            """,
            (dni, hab, fecha_in_dmy),
        ).fetchone()
        if dup and dup["updated_at"]:
            try:
                dt_ant = datetime.fromisoformat(dup["updated_at"])
                if abs((ahora - dt_ant).total_seconds()) < 45:
                    return {
                        "ok": True,
                        "fila": int(dup["sheet_row"] or 0),
                        "estanciaUuid": dup["estancia_uuid"],
                        "hab": hab,
                        "nombre": nombre,
                        "visita": dup["visita"],
                        "duplicadoEvitado": True,
                    }
            except Exception:
                pass

        tipo_hab = str(datos.get("tipoHab") or "").strip()
        if not tipo_hab:
            hc = conn.execute("SELECT tipo FROM habitaciones_config WHERE hab = ?", (hab,)).fetchone()
            tipo_hab = hc["tipo"] if hc else ""

        pad = conn.execute("SELECT visitas FROM huespedes_padron WHERE dni = ?", (dni,)).fetchone()
        visitas_total = (int(pad["visitas"]) + 1) if pad else 1
        visita_str = str(datos.get("visita") or "").strip() or f"Visita N° {visitas_total}"

        solicita = "Sí" if (tipo_c and tipo_c != "Ninguno") else "No"
        estado_c = "Pendiente" if (tipo_c and tipo_c != "Ninguno") else ""
        estancia_uuid = str(uuid.uuid4())

        rec = {
            "estancia_uuid": estancia_uuid,
            "id_timestamp_str": id_ts_str,
            "sheet_row": 0,
            "parent_uuid": "",
            "fecha_in_dmy": fecha_in_dmy,
            "fecha_out_dmy": fecha_out_dmy,
            "ymd_in": ymd_in,
            "ymd_out": ymd_out,
            "hora_registro": hora_reg,
            "huesped": nombre,
            "dni": dni,
            "sexo": sexo,
            "n_acomp": n_acomp,
            "dni_acomp": dni_acomp,
            "nac": nac,
            "reg": reg,
            "mot": mot,
            "hab": hab,
            "tipo_hab": tipo_hab,
            "visita": visita_str,
            "noches": noches,
            "tarifa": tarifa,
            "adic_snacks": 0.0,
            "adic_servicios": serv_monto,
            "adic_comp": adic_comp,
            "pago_ini_efec": p_efec,
            "pago_ini_yape": p_elec,
            "pago_post_efec": 0.0,
            "pago_post_yape": 0.0,
            "pago_visa": p_visa,
            "deuda_anterior": 0.0,
            "cuenta_destino": "",
            "solicita_comp": solicita,
            "tipo_comp": tipo_c,
            "estado_comp": estado_c,
            "nro_comp": "",
            "monto_factura": m_fact,
            "estado_estancia": "En Curso",
            "empresa": empresa,
            "proyecto": "",
            "triple1": triple1,
            "triple2": triple2,
            "hotel_apoyo": hotel_tercerizado,
            "observaciones": "",
            "nro_op_yape": nro_op_yape if p_elec > 0 else "",
            "celular": celular,
            "ruc": ruc,
        }

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
                dirty, updated_at
            ) VALUES (
                :estancia_uuid, :id_timestamp_str, 0, '',
                :fecha_in_dmy, :fecha_out_dmy, :ymd_in, :ymd_out, :hora_registro,
                :huesped, :dni, :sexo, :n_acomp, :dni_acomp, :nac, :reg, :mot,
                :hab, :tipo_hab, :visita, :noches, :tarifa,
                :adic_snacks, :adic_servicios, :adic_comp,
                :pago_ini_efec, :pago_ini_yape, :pago_post_efec, :pago_post_yape, :pago_visa,
                :deuda_anterior, :cuenta_destino, :solicita_comp, :tipo_comp, :estado_comp,
                :nro_comp, :monto_factura, :estado_estancia, :empresa, :proyecto,
                :triple1, :triple2, :hotel_apoyo, :observaciones, :nro_op_yape, :celular, :ruc,
                1, :updated_at
            )
            """,
            {**rec, "updated_at": ahora.isoformat()},
        )

        # Actualizar padrón local de huéspedes y empresas
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
                empresa = CASE WHEN excluded.empresa != '' THEN excluded.empresa ELSE huespedes_padron.empresa END,
                celular = CASE WHEN excluded.celular != '' THEN excluded.celular ELSE huespedes_padron.celular END,
                ruc = CASE WHEN excluded.ruc != '' THEN excluded.ruc ELSE huespedes_padron.ruc END,
                visitas = huespedes_padron.visitas + 1,
                updated_at = excluded.updated_at
            """,
            (dni, nombre, sexo, nac, reg, mot, hab, tarifa, empresa, celular, ruc, visitas_total, ahora.isoformat()),
        )
        if ruc and empresa:
            conn.execute(
                """
                INSERT INTO empresas_padron (ruc, razon_social, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(ruc) DO UPDATE SET razon_social = excluded.razon_social, updated_at = excluded.updated_at
                """,
                (ruc, empresa, ahora.isoformat()),
            )

        # Registrar pagos iniciales en tabla pagos
        pagos_sheets = []
        if p_efec_base > 0:
            pid = f"{base_ms}-1"
            p_row = [pid, id_ts_str, id_ts_str, hab, nombre, p_efec_base, "Efectivo", "Inicial", "Pago Inicial Estancia (Efectivo)", ""]
            pagos_sheets.append(p_row)
            conn.execute(
                "INSERT INTO pagos (pago_id, estancia_uuid, id_estancia, fecha_iso, hab, huesped, monto, medio_pago, tipo_pago, concepto, nro_operacion, synced) VALUES (?,?,?,?,?,?,?,?,?,?,?,0)",
                (pid, estancia_uuid, id_ts_str, id_ts_str, hab, nombre, p_efec_base, "Efectivo", "Inicial", "Pago Inicial Estancia (Efectivo)", ""),
            )
        if p_elec_base > 0:
            pid = f"{base_ms + 1}-2"
            p_row = [pid, id_ts_str, id_ts_str, hab, nombre, p_elec_base, "Yape", "Inicial", "Pago Inicial Estancia (Yape)", nro_op_yape]
            pagos_sheets.append(p_row)
            conn.execute(
                "INSERT INTO pagos (pago_id, estancia_uuid, id_estancia, fecha_iso, hab, huesped, monto, medio_pago, tipo_pago, concepto, nro_operacion, synced) VALUES (?,?,?,?,?,?,?,?,?,?,?,0)",
                (pid, estancia_uuid, id_ts_str, id_ts_str, hab, nombre, p_elec_base, "Yape", "Inicial", "Pago Inicial Estancia (Yape)", nro_op_yape),
            )
        if p_visa_base > 0:
            pid = f"{base_ms + 2}-3"
            p_row = [pid, id_ts_str, id_ts_str, hab, nombre, p_visa_base, "VISA", "Inicial", "Pago Inicial Estancia (VISA)", ""]
            pagos_sheets.append(p_row)
            conn.execute(
                "INSERT INTO pagos (pago_id, estancia_uuid, id_estancia, fecha_iso, hab, huesped, monto, medio_pago, tipo_pago, concepto, nro_operacion, synced) VALUES (?,?,?,?,?,?,?,?,?,?,?,0)",
                (pid, estancia_uuid, id_ts_str, id_ts_str, hab, nombre, p_visa_base, "VISA", "Inicial", "Pago Inicial Estancia (VISA)", ""),
            )
        if serv_monto > 0:
            try:
                conn.execute(
                    "INSERT INTO servicios_detalle (serv_uuid, estancia_uuid, id_estancia, fecha_dmy, hora, hab, tipo_servicio, metodo_pago, monto, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (str(uuid.uuid4()), estancia_uuid, id_ts_str, f_in_dmy, hora_reg[:5], hab, "Servicio en Check-In", serv_metodo, serv_monto, ahora.isoformat()),
                )
            except Exception:
                pass
        if serv_monto > 0 and serv_metodo != "Cargar a Habitación":
            pid = f"{base_ms + 3}-4"
            op_s = nro_op_yape if serv_metodo == "Yape" else ""
            p_row = [pid, id_ts_str, id_ts_str, hab, nombre, serv_monto, serv_metodo, "Inicial", "Pago Inicial Estancia (Servicio Adicional)", op_s]
            pagos_sheets.append(p_row)
            conn.execute(
                "INSERT INTO pagos (pago_id, estancia_uuid, id_estancia, fecha_iso, hab, huesped, monto, medio_pago, tipo_pago, concepto, nro_operacion, synced) VALUES (?,?,?,?,?,?,?,?,?,?,?,0)",
                (pid, estancia_uuid, id_ts_str, id_ts_str, hab, nombre, serv_monto, serv_metodo, "Inicial", "Pago Inicial Estancia (Servicio Adicional)", op_s),
            )

        fila_sheets = construir_fila_sheets_estancia(rec)
        _encolar_operacion(
            conn,
            "CHECKIN",
            estancia_uuid,
            {
                "id_timestamp_str": id_ts_str,
                "fila_estancia": fila_sheets,
                "pagos_rows": pagos_sheets,
            },
            delta_caja=p_efec,
        )

    return {
        "ok": True,
        "fila": 0,
        "estanciaUuid": estancia_uuid,
        "hab": hab,
        "nombre": nombre,
        "visita": visita_str,
    }


def _buscar_estancia_local(conn, fila: int = 0, id_busq: str = "", estancia_uuid: str = "", hab: str = "") -> sqlite3.Row:
    if estancia_uuid:
        r = conn.execute("SELECT * FROM estancias WHERE estancia_uuid = ?", (estancia_uuid,)).fetchone()
        if r:
            return r
    if fila and int(fila) >= 2:
        r = conn.execute("SELECT * FROM estancias WHERE sheet_row = ?", (int(fila),)).fetchone()
        if r and (not hab or str(r["hab"]).strip() == str(hab).strip()):
            return r
    if id_busq:
        r = conn.execute(
            "SELECT * FROM estancias WHERE id_timestamp_str = ? OR estancia_uuid = ?",
            (str(id_busq), str(id_busq)),
        ).fetchone()
        if r:
            return r
    if hab:
        # Causa raíz del error "cancela otra estancia": las estancias creadas offline aún no tienen
        # fila de Sheets (sheet_row = 0) y con "ORDER BY sheet_row DESC" quedaban DETRÁS de las antiguas.
        # Prioridad: pendientes locales (sheet_row = 0) > fecha de ingreso más reciente > fila más alta.
        orden_reciente = "CASE WHEN sheet_row = 0 THEN 1 ELSE 0 END DESC, ymd_in DESC, sheet_row DESC, rowid DESC"
        r = conn.execute(
            f"SELECT * FROM estancias WHERE hab = ? AND estado_estancia = 'En Curso' ORDER BY {orden_reciente} LIMIT 1",
            (str(hab).strip(),),
        ).fetchone()
        if r:
            return r
        r2 = conn.execute(
            f"SELECT * FROM estancias WHERE hab = ? ORDER BY {orden_reciente} LIMIT 1",
            (str(hab).strip(),),
        ).fetchone()
        if r2:
            return r2
    raise ValueError("No se encontró la estancia solicitada.")


def guardar_pago_posterior_local(datos: Dict[str, Any]) -> Dict[str, Any]:
    monto = parse_moneda(datos.get("monto"))
    conc = str(datos.get("concepto") or "Pago de Saldo").strip()
    met = str(datos.get("metodo") or "").strip()
    nro_op_yape = str(datos.get("nroOpYape") or "").strip()
    est_c = str(datos.get("estadoComp") or "").strip()
    mon_c = datos.get("montoComp")
    tipo_adic_nuevo = str(datos.get("tipoAdicional") or "").strip()
    cuenta_destino_nueva = str(datos.get("cuentaDestino") or "").strip()
    nro_op_banco = str(datos.get("nroOpBanco") or "").strip()
    METODOS_BANCO = ("VISA", "BCP", "Interbank", "BBVA", "Transferencia")

    with db_transaction() as conn:
        st = _buscar_estancia_local(
            conn,
            fila=int(datos.get("fila") or 0),
            id_busq=str(datos.get("id") or datos.get("idEstancia") or ""),
            estancia_uuid=str(datos.get("estanciaUuid") or ""),
            hab=str(datos.get("hab") or ""),
        )
        est_uuid = st["estancia_uuid"]
        hab = st["hab"]
        huesped = st["huesped"]

        adic_serv = float(st["adic_servicios"] or 0)
        adic_comp = float(st["adic_comp"] or 0)
        deuda_ant = float(st["deuda_anterior"] or 0)
        p_post_efec = float(st["pago_post_efec"] or 0)
        p_post_yape = float(st["pago_post_yape"] or 0)
        p_visa = float(st["pago_visa"] or 0)
        nro_op_actual = str(st["nro_op_yape"] or "").strip()
        solicita_comp = str(st["solicita_comp"] or "No")
        tipo_comp = str(st["tipo_comp"] or "")
        estado_comp = str(st["estado_comp"] or "")
        monto_fact = float(st["monto_factura"] or 0)
        tipo_adic_actual = str(st["tipo_adicional"] or "").strip()
        cuenta_destino_actual = str(st["cuenta_destino"] or "").strip()

        if est_c:
            estado_comp = est_c
        if mon_c not in (None, ""):
            monto_fact = parse_moneda(mon_c)

        delta_caja = 0.0
        pago_row_sheets = None

        if monto > 0:
            if conc != "Pago de Saldo":
                if conc == "Servicio Adicional":
                    adic_serv = round(adic_serv + monto, 2)
                    if tipo_adic_nuevo:
                        _tags = [t.strip() for t in tipo_adic_actual.split(",") if t.strip()]
                        for _t in [t.strip() for t in tipo_adic_nuevo.split(",") if t.strip()]:
                            if _t.lower() not in [x.lower() for x in _tags]:
                                _tags.append(_t)
                        tipo_adic_actual = ", ".join(_tags)
                    try:
                        conn.execute(
                            "INSERT INTO servicios_detalle (serv_uuid, estancia_uuid, id_estancia, fecha_dmy, hora, hab, tipo_servicio, metodo_pago, monto, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (
                                str(uuid.uuid4()),
                                est_uuid,
                                st["id_timestamp_str"],
                                st["fecha_in_dmy"],
                                datetime.now().strftime("%H:%M"),
                                hab,
                                (tipo_adic_nuevo or "Servicio").strip(),
                                met,
                                round(float(monto), 2),
                                datetime.now().isoformat(),
                            ),
                        )
                    except Exception:
                        pass
                elif "Servicio por Comprob" in conc:
                    adic_comp = round(adic_comp + monto, 2)
                elif conc == "Carga Deuda Anterior":
                    deuda_ant = round(deuda_ant + monto, 2)

            if met not in ("Cargar a la Cuenta", "Cargar a Habitación"):
                factor = -1.0 if conc == "Vuelto a cliente" else 1.0
                delta_monto = round(monto * factor, 2)
                if met == "Efectivo":
                    p_post_efec = round(p_post_efec + delta_monto, 2)
                    delta_caja = delta_monto
                elif met == "Yape":
                    p_post_yape = round(p_post_yape + delta_monto, 2)
                elif met in METODOS_BANCO:
                    p_visa = round(p_visa + delta_monto, 2)
                    if cuenta_destino_nueva:
                        cuenta_destino_actual = cuenta_destino_nueva

                if met == "Yape" and nro_op_yape:
                    nro_op_actual = f"{nro_op_actual} / {nro_op_yape}" if nro_op_actual else nro_op_yape

                if met == "Yape" and not est_c and not estado_comp:
                    tiene_ruc_emp = bool(str(st["ruc"] or "").strip() or str(st["empresa"] or "").strip())
                    solicita_comp = "Sí"
                    tipo_comp = "Factura" if tiene_ruc_emp else (tipo_comp or "Boleta")
                    estado_comp = "Pendiente"

                ahora_str = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
                pid = f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:4]}"
                tipo_p = "Extorno/Vuelto" if factor < 0 else "Posterior"
                pago_row_sheets = [
                    pid,
                    ahora_str,
                    st["id_timestamp_str"],
                    hab,
                    huesped,
                    delta_monto,
                    met,
                    tipo_p,
                    conc,
                    nro_op_yape if met == "Yape" else (nro_op_banco if met in METODOS_BANCO else ""),
                ]
                conn.execute(
                    """
                    INSERT INTO pagos (pago_id, estancia_uuid, id_estancia, fecha_iso, hab, huesped, monto, medio_pago, tipo_pago, concepto, nro_operacion, synced)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                    """,
                    (pid, est_uuid, st["id_timestamp_str"], ahora_str, hab, huesped, delta_monto, met, tipo_p, conc, nro_op_yape if met == "Yape" else (nro_op_banco if met in METODOS_BANCO else "")),
                )

        conn.execute(
            """
            UPDATE estancias SET
                adic_servicios = ?, adic_comp = ?, deuda_anterior = ?,
                pago_post_efec = ?, pago_post_yape = ?, pago_visa = ?,
                nro_op_yape = ?, solicita_comp = ?, tipo_comp = ?,
                estado_comp = ?, monto_factura = ?, tipo_adicional = ?, cuenta_destino = ?,
                dirty = 1, updated_at = ?
            WHERE estancia_uuid = ?
            """,
            (
                adic_serv, adic_comp, deuda_ant,
                p_post_efec, p_post_yape, p_visa,
                nro_op_actual, solicita_comp, tipo_comp,
                estado_comp, monto_fact, tipo_adic_actual, cuenta_destino_actual,
                datetime.now().isoformat(), est_uuid,
            ),
        )

        _encolar_operacion(
            conn,
            "PAGO",
            est_uuid,
            {
                "estancia_uuid": est_uuid,
                "id_timestamp_str": st["id_timestamp_str"],
                "sheet_row": int(st["sheet_row"] or 0),
                "hab": hab,
                "adic_servicios": adic_serv,
                "adic_comp": adic_comp,
                "deuda_anterior": deuda_ant,
                "pago_post_efec": p_post_efec,
                "pago_post_yape": p_post_yape,
                "pago_visa": p_visa,
                "nro_op_yape": nro_op_actual,
                "solicita_comp": solicita_comp,
                "tipo_comp": tipo_comp,
                "estado_comp": estado_comp,
                "monto_factura": monto_fact,
                "tipo_adicional": tipo_adic_actual,
                "cuenta_destino": cuenta_destino_actual,
                "pago_row": pago_row_sheets,
            },
            delta_caja=delta_caja,
        )

    return {
        "ok": True,
        "hab": hab,
        "nombre": huesped,
        "detalle": f"Pago registrado ({met}: S/ {monto:.2f})" if monto > 0 else "Comprobante actualizado",
    }


def guardar_venta_tienda_local(datos: Dict[str, Any]) -> Dict[str, Any]:
    items = datos.get("items") or []
    met = str(datos.get("metodo") or "").strip()
    hab = str(datos.get("hab") or "").strip()
    f_in_iso = str(datos.get("fechaCheckIn") or "").strip()
    pers = str(datos.get("personal") or "").strip()
    nro_op_yape = str(datos.get("nroOpYape") or "").strip()

    if not items:
        raise ValueError("Selecciona al menos un producto y su cantidad.")
    if not met:
        raise ValueError("Selecciona el método de pago.")
    if met == "Yape" and not nro_op_yape:
        raise ValueError("Ingresa el N° de Operación Yape.")
    if met == "Cargar a la Cuenta" and not pers:
        raise ValueError("Debes indicar a quién le pertenece el consumo personal.")

    ahora = datetime.now()
    ahora_ts = ahora.strftime("%d/%m/%Y %H:%M:%S")
    fecha_log = ahora.strftime("%d/%m/%Y")
    lote_uuid = str(uuid.uuid4())

    with db_transaction() as conn:
        precios_map = {
            r["clave"]: parse_moneda(r["valor"])
            for r in conn.execute("SELECT clave, valor FROM catalogos_local WHERE categoria = 'PRODUCTO_TIENDA'").fetchall()
        }

        total_venta = 0.0
        items_limpios = []
        for it in items:
            prod = str(it.get("prod") or "").strip()
            cant = int(it.get("cant") or 0)
            if prod and cant > 0:
                p_unit = precios_map.get(prod, parse_moneda(it.get("precio")))
                sub = round(cant * p_unit, 2)
                total_venta = round(total_venta + sub, 2)
                items_limpios.append({"prod": prod, "cant": cant, "precioUnit": p_unit, "subTotal": sub})

        if not items_limpios:
            raise ValueError("No hay productos válidos en la venta.")

        st = None
        if hab:
            fila_dir = int(datos.get("filaEstancia") or 0)
            orden_reciente = "CASE WHEN sheet_row = 0 THEN 1 ELSE 0 END DESC, ymd_in DESC, sheet_row DESC, rowid DESC"
            if fila_dir >= 2:
                r_dir = conn.execute(
                    "SELECT * FROM estancias WHERE sheet_row = ? AND hab = ? AND estado_estancia != 'Cancelado'",
                    (fila_dir, hab),
                ).fetchone()
                if r_dir:
                    st = r_dir
            f_busq_dmy = normalizar_fecha_dmy(f_in_iso) if f_in_iso else ""
            if not st and f_busq_dmy:
                st = conn.execute(
                    f"SELECT * FROM estancias WHERE hab = ? AND fecha_in_dmy = ? AND estado_estancia != 'Cancelado' ORDER BY {orden_reciente} LIMIT 1",
                    (hab, f_busq_dmy),
                ).fetchone()
            if not st:
                st = conn.execute(
                    f"SELECT * FROM estancias WHERE hab = ? AND estado_estancia = 'En Curso' ORDER BY {orden_reciente} LIMIT 1",
                    (hab,),
                ).fetchone()

        est_uuid = st["estancia_uuid"] if st else ""
        id_est_str = st["id_timestamp_str"] if st else ""
        est_updates = None
        es_cargo = met in ("Cargar a la Cuenta", "Cargar a Habitación")

        # REGLA CONTABLE DE TIENDA (idéntica a Código.js y a la fórmula de Caja G1 en Sheets):
        # - Si es "Efectivo", "Yape" o "VISA" (pagado al instante): SOLO se registra en 2.Registro_Ventas_Tienda
        #   (NO se toca Adic_Snacks ni Pagos_Posteriores en Registro_Estancias, evitando duplicar Adicional y Caja G1).
        # - Si es "Cargar a Habitación" / "Cargar a la Cuenta": SÍ suma a Adic_Snacks (Col S) en Registro_Estancias
        #   para que suba el Saldo pendiente de la habitación; cuando luego paguen ese saldo por Pagos Posteriores,
        #   recién entrará en Pagos_Posteriores sin duplicar el Adicional.
        if st and es_cargo:
            snacks_new = round(float(st["adic_snacks"] or 0) + total_venta, 2)
            conn.execute(
                """
                UPDATE estancias SET
                    adic_snacks = ?, dirty = 1, updated_at = ?
                WHERE estancia_uuid = ?
                """,
                (snacks_new, ahora.isoformat(), est_uuid),
            )
            est_updates = {
                "estancia_uuid": est_uuid,
                "id_timestamp_str": id_est_str,
                "sheet_row": int(st["sheet_row"] or 0),
                "adic_snacks": snacks_new,
            }

        ventas_rows_sheets = []
        for it in items_limpios:
            v_uuid = str(uuid.uuid4())
            conn.execute(
                """
                INSERT INTO ventas_tienda (
                    venta_uuid, lote_uuid, estancia_uuid, id_estancia, fecha_iso, fecha_dmy,
                    producto, cantidad, metodo_pago, hab, personal, precio_unit, subtotal, nro_op_yape, synced
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    v_uuid, lote_uuid, est_uuid, id_est_str, ahora_ts, fecha_log,
                    it["prod"], it["cant"], met, hab, pers, it["precioUnit"], it["subTotal"],
                    nro_op_yape if met == "Yape" else "",
                ),
            )
            ventas_rows_sheets.append([
                ahora_ts,
                fecha_log,
                it["prod"],
                it["cant"],
                met,
                hab,
                pers,
                it["precioUnit"],
                it["subTotal"],
                id_est_str,
                "",
                nro_op_yape if met == "Yape" else "",
            ])

        delta_caja = total_venta if met == "Efectivo" else 0.0
        _encolar_operacion(
            conn,
            "TIENDA",
            lote_uuid,
            {
                "lote_uuid": lote_uuid,
                "ventas_rows": ventas_rows_sheets,
                "estancia_updates": est_updates,
            },
            delta_caja=delta_caja,
        )

    return {
        "ok": True,
        "total": total_venta,
        "itemsCount": len(items_limpios),
        "hab": hab,
        "vinculado": bool(st) if hab else True,
    }


def guardar_gasto_local(datos: Dict[str, Any]) -> Dict[str, Any]:
    desc = str(datos.get("descripcion") or "").strip()
    monto = parse_moneda(datos.get("monto"))
    cat = str(datos.get("categoria") or "").strip()
    met = str(datos.get("metodo") or "").strip()
    tip = str(datos.get("tipoComp") or "").strip()
    nro = str(datos.get("nroComp") or "").strip()
    resp = str(datos.get("responsable") or "Admin").strip() or "Admin"
    personal_g = str(datos.get("personal") or "").strip()
    fecha_raw = str(datos.get("fecha") or hoy_iso()).strip()

    if not fecha_raw:
        raise ValueError("Falta la fecha del gasto.")
    if not desc:
        raise ValueError("Falta la descripción del gasto.")
    if monto <= 0:
        raise ValueError("El monto del gasto debe ser mayor a 0.")
    if not cat:
        raise ValueError("Selecciona la categoría del gasto.")
    if not met:
        raise ValueError("Selecciona el método de pago del gasto.")
    if cat.lower().startswith("adelanto") and not personal_g:
        raise ValueError("Selecciona a qué personal corresponde el adelanto de sueldo.")

    fecha_dmy = normalizar_fecha_dmy(fecha_raw) or hoy_dmy()
    fecha_iso = dmy_a_iso(fecha_dmy)
    ahora = datetime.now()
    id_ts_str = ahora.strftime("%d/%m/%Y %H:%M:%S")
    hora_reg = ahora.strftime("%H:%M")
    gasto_uuid = str(uuid.uuid4())

    with db_transaction() as conn:
        # id_ts_str es único: si dos gastos caen en el mismo segundo, el siguiente avanza 1 s
        while conn.execute("SELECT 1 FROM gastos WHERE id_ts_str = ?", (id_ts_str,)).fetchone():
            ahora = ahora + timedelta(seconds=1)
            id_ts_str = ahora.strftime("%d/%m/%Y %H:%M:%S")
            hora_reg = ahora.strftime("%H:%M")
        conn.execute(
            """
            INSERT INTO gastos (
                gasto_uuid, id_ts_str, fecha_gasto_iso, fecha_gasto_dmy, hora_registro,
                descripcion, monto, categoria, metodo_pago, tipo_comp, nro_comp, responsable, personal, synced
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (gasto_uuid, id_ts_str, fecha_iso, fecha_dmy, hora_reg, desc, monto, cat, met, tip, nro, resp, personal_g),
        )

        delta_caja = -monto if met == "Efectivo" else 0.0
        _encolar_operacion(
            conn,
            "GASTO",
            gasto_uuid,
            {
                "gasto_uuid": gasto_uuid,
                "id_ts_str": id_ts_str,
                "fila_gasto": [id_ts_str, fecha_dmy, desc, monto, cat, met, tip, nro, resp, personal_g],
            },
            delta_caja=delta_caja,
        )

    return {
        "ok": True,
        "gastoUuid": gasto_uuid,
        "descripcion": desc,
        "monto": monto,
        "metodo": met,
        "afectaCaja": met == "Efectivo",
    }


def continuar_estancia_local(datos: Dict[str, Any]) -> Dict[str, Any]:
    with db_transaction() as conn:
        ahora = datetime.now()
        f_in_nueva = normalizar_fecha_dmy(datos.get("fechaInNueva")) or hoy_dmy()
        f_out_nueva = normalizar_fecha_dmy(datos.get("fechaOutNueva")) or (ahora + timedelta(days=1)).strftime("%d/%m/%Y")
        ymd_in = fecha_dmy_a_ymd(f_in_nueva) or int(ahora.strftime("%Y%m%d"))
        ymd_out = fecha_dmy_a_ymd(f_out_nueva) or (ymd_in + 1)

        try:
            dt_in_nueva = datetime.strptime(f_in_nueva, "%d/%m/%Y")
        except Exception:
            dt_in_nueva = ahora
        ymd_ayer_enlace = int((dt_in_nueva - timedelta(days=1)).strftime("%Y%m%d"))
        f_in_ant_dmy = normalizar_fecha_dmy(datos.get("fechaInAnterior"))
        ymd_in_ant_enlace = fecha_dmy_a_ymd(f_in_ant_dmy) if f_in_ant_dmy else ymd_ayer_enlace

        hab_ant = str(datos.get("habAnterior") or "").strip()
        hab_busq = hab_ant or str(datos.get("habNueva") or "").strip()
        est_uuid_busq = str(datos.get("estanciaUuid") or "").strip()
        id_ant_busq = str(datos.get("idAnterior") or "").strip()
        fila_ant_busq = int(datos.get("filaAnterior") or 0)

        old = None
        orden_reciente = "CASE WHEN sheet_row = 0 THEN 1 ELSE 0 END DESC, ymd_in DESC, sheet_row DESC, rowid DESC"

        # 1. Enlace primario por Check-In anterior + Habitación de origen (y UUID si corresponde a esa hab y fecha)
        if est_uuid_busq:
            r_u = conn.execute("SELECT * FROM estancias WHERE estancia_uuid = ?", (est_uuid_busq,)).fetchone()
            if r_u and (not hab_ant or str(r_u["hab"]).strip() == hab_ant) and int(r_u["ymd_in"] or 0) < ymd_in:
                old = r_u

        if not old and hab_busq:
            r_chk = conn.execute(
                f"""
                SELECT * FROM estancias
                WHERE hab = ?
                  AND estado_estancia = 'En Curso'
                  AND (ymd_in = ? OR ymd_in = ? OR (ymd_in < ? AND ymd_out = ?))
                ORDER BY {orden_reciente}
                LIMIT 1
                """,
                (hab_busq, ymd_in_ant_enlace, ymd_ayer_enlace, ymd_in, ymd_in),
            ).fetchone()
            if r_chk:
                old = r_chk

        if not old:
            old = _buscar_estancia_local(
                conn,
                fila=fila_ant_busq,
                id_busq=id_ant_busq,
                estancia_uuid=est_uuid_busq,
                hab=hab_busq,
            )

        if hab_ant and str(old["hab"]).strip() != hab_ant:
            raise ValueError(
                f"Error de enlace en Continuar Estancia: se solicitó la Hab. {hab_ant}, pero el registro apunta a Hab. {old['hab']} ({old['huesped']})."
            )

        est_ant_low = str(old["estado_estancia"] or "").strip().lower()
        if "contin" in est_ant_low or "cancel" in est_ant_low:
            raise ValueError(f"La estancia de Hab. {old['hab']} ({old['huesped']}) ya fue continuada o anulada anteriormente.")

        ymd_in_old = int(old["ymd_in"] or 0)
        ymd_out_old = int(old["ymd_out"] or 0)
        if ymd_in_old >= ymd_in:
            raise ValueError(
                f"La estancia de Hab. {old['hab']} ({old['huesped']}) ya tiene Check-In en la fecha {old['fecha_in_dmy']} y no puede volverse a continuar en el mismo día."
            )
        if ymd_in_old > 0 and ymd_in_old < ymd_ayer_enlace and ymd_out_old < ymd_in:
            raise ValueError(
                f"Por seguridad, Continuar Estancia solo permite enlazar con el Check-In de 1 día hacia atrás (la estancia de Hab. {old['hab']} ingresó el {old['fecha_in_dmy']})."
            )

        noches_ant = max(1, int(old["noches"] or 1))
        sub_aloj_ant = round(noches_ant * float(old["tarifa"] or 0), 2)
        cargos_ant = (
            sub_aloj_ant
            + float(old["adic_snacks"] or 0)
            + float(old["adic_servicios"] or 0)
            + float(old["adic_comp"] or 0)
            + float(old["deuda_anterior"] or 0)
        )
        pagos_ant = (
            float(old["pago_ini_efec"] or 0)
            + float(old["pago_ini_yape"] or 0)
            + float(old["pago_post_efec"] or 0)
            + float(old["pago_post_yape"] or 0)
            + float(old["pago_visa"] or 0)
        )
        deuda_arrastre = round(cargos_ant - pagos_ant, 2)

        id_ts_str = ahora.strftime("%d/%m/%Y %H:%M:%S")
        hora_reg = ahora.strftime("%H:%M")
        noches_nuevas = calcular_noches(f_in_nueva, f_out_nueva)

        hab_nueva = str(datos.get("habNueva") or old["hab"]).strip()
        tarifa_nueva = parse_moneda(datos.get("tarifaNueva")) if datos.get("tarifaNueva") not in (None, "") else float(old["tarifa"] or 0)
        tipo_hab_nueva = old["tipo_hab"]
        if hab_nueva != old["hab"]:
            hc = conn.execute("SELECT tipo FROM habitaciones_config WHERE hab = ?", (hab_nueva,)).fetchone()
            if hc:
                tipo_hab_nueva = hc["tipo"]

        # 1. Cerrar estancia anterior como "Finalizado - Continúa"
        conn.execute(
            "UPDATE estancias SET estado_estancia = 'Finalizado - Continúa', dirty = 1, updated_at = ? WHERE estancia_uuid = ?",
            (ahora.isoformat(), old["estancia_uuid"]),
        )

        # 2. Crear nueva estancia enlazada con parent_uuid
        new_uuid = str(uuid.uuid4())
        rec = {
            "estancia_uuid": new_uuid,
            "id_timestamp_str": id_ts_str,
            "sheet_row": 0,
            "parent_uuid": old["estancia_uuid"],
            "fecha_in_dmy": f_in_nueva,
            "fecha_out_dmy": f_out_nueva,
            "ymd_in": ymd_in,
            "ymd_out": ymd_out,
            "hora_registro": hora_reg,
            "huesped": old["huesped"],
            "dni": old["dni"],
            "sexo": old["sexo"],
            "n_acomp": old["n_acomp"],
            "dni_acomp": old["dni_acomp"],
            "nac": old["nac"],
            "reg": old["reg"],
            "mot": old["mot"],
            "hab": hab_nueva,
            "tipo_hab": tipo_hab_nueva,
            "visita": old["visita"],
            "noches": noches_nuevas,
            "tarifa": tarifa_nueva,
            "adic_snacks": 0.0,
            "adic_servicios": 0.0,
            "adic_comp": 0.0,
            "pago_ini_efec": 0.0,
            "pago_ini_yape": 0.0,
            "pago_post_efec": 0.0,
            "pago_post_yape": 0.0,
            "pago_visa": 0.0,
            "deuda_anterior": deuda_arrastre,
            "cuenta_destino": "",
            "solicita_comp": "Sí" if old["ruc"] or old["empresa"] else "No",
            "tipo_comp": "Factura" if old["ruc"] or old["empresa"] else "",
            "estado_comp": "Pendiente" if old["ruc"] or old["empresa"] else "",
            "nro_comp": "",
            "monto_factura": 0.0,
            "estado_estancia": "En Curso",
            "empresa": old["empresa"],
            "proyecto": old["proyecto"],
            "triple1": old["triple1"],
            "triple2": old["triple2"],
            "hotel_apoyo": old["hotel_apoyo"],
            "observaciones": "",
            "nro_op_yape": "",
            "celular": old["celular"],
            "ruc": old["ruc"],
        }

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
                dirty, updated_at
            ) VALUES (
                :estancia_uuid, :id_timestamp_str, 0, :parent_uuid,
                :fecha_in_dmy, :fecha_out_dmy, :ymd_in, :ymd_out, :hora_registro,
                :huesped, :dni, :sexo, :n_acomp, :dni_acomp, :nac, :reg, :mot,
                :hab, :tipo_hab, :visita, :noches, :tarifa,
                :adic_snacks, :adic_servicios, :adic_comp,
                :pago_ini_efec, :pago_ini_yape, :pago_post_efec, :pago_post_yape, :pago_visa,
                :deuda_anterior, :cuenta_destino, :solicita_comp, :tipo_comp, :estado_comp,
                :nro_comp, :monto_factura, :estado_estancia, :empresa, :proyecto,
                :triple1, :triple2, :hotel_apoyo, :observaciones, :nro_op_yape, :celular, :ruc,
                1, :updated_at
            )
            """,
            {**rec, "updated_at": ahora.isoformat()},
        )

        fila_sheets = construir_fila_sheets_estancia(rec)
        _encolar_operacion(
            conn,
            "CONTINUAR",
            new_uuid,
            {
                "old_uuid": old["estancia_uuid"],
                "old_id_ts": old["id_timestamp_str"],
                "old_sheet_row": int(old["sheet_row"] or 0),
                "new_uuid": new_uuid,
                "id_timestamp_str": id_ts_str,
                "fila_nueva": fila_sheets,
            },
            delta_caja=0.0,
        )

    return {
        "ok": True,
        "fila": 0,
        "estanciaUuid": new_uuid,
        "hab": hab_nueva,
        "nombre": old["huesped"],
        "deudaArrastre": deuda_arrastre,
    }


def obtener_estancia_para_modificar_local(hab_buscada: str, fila_o_id: Any = None) -> Dict[str, Any]:
    conn = get_connection()
    try:
        fila_num = 0
        id_str = ""
        if fila_o_id is not None and str(fila_o_id).strip() != "":
            s_val = str(fila_o_id).strip()
            if s_val.isdigit() and int(s_val) < 100000:
                fila_num = int(s_val)
            else:
                id_str = s_val
        r = _buscar_estancia_local(conn, fila=fila_num, id_busq=id_str, estancia_uuid=id_str, hab=hab_buscada)
        ruc_val = str(r["ruc"] or "").strip()
        emp_val = str(r["empresa"] or "").strip()
        reg_val = str(r["reg"] or "").strip()
        cel_val = str(r["celular"] or "").strip()
        dni_val = str(r["dni"] or "").strip()
        ruc_sugerido = ""
        emp_sugerida = ""
        if dni_val:
            hp = conn.execute("SELECT reg, ruc, empresa, celular FROM huespedes_padron WHERE dni = ?", (dni_val,)).fetchone()
            if hp:
                if not reg_val and (hp["reg"] or "").strip():
                    reg_val = str(hp["reg"]).strip()
                if not cel_val and (hp["celular"] or "").strip():
                    cel_val = str(hp["celular"]).strip()
                if (hp["ruc"] or "").strip():
                    ruc_sugerido = str(hp["ruc"]).strip()
                if (hp["empresa"] or "").strip():
                    emp_sugerida = str(hp["empresa"]).strip()
            if not ruc_sugerido or not emp_sugerida:
                est_prev = conn.execute(
                    "SELECT ruc, empresa, reg, celular FROM estancias WHERE dni = ? AND (trim(ruc) != '' OR trim(empresa) != '') ORDER BY ymd_in DESC, sheet_row DESC, rowid DESC LIMIT 1",
                    (dni_val,),
                ).fetchone()
                if est_prev:
                    if not ruc_sugerido and (est_prev["ruc"] or "").strip():
                        ruc_sugerido = str(est_prev["ruc"]).strip()
                    if not emp_sugerida and (est_prev["empresa"] or "").strip():
                        emp_sugerida = str(est_prev["empresa"]).strip()
                    if not reg_val and (est_prev["reg"] or "").strip():
                        reg_val = str(est_prev["reg"]).strip()
        if ruc_val and not emp_val:
            ep = conn.execute("SELECT razon_social FROM empresas_padron WHERE ruc = ?", (ruc_val,)).fetchone()
            if ep and (ep["razon_social"] or "").strip():
                emp_val = str(ep["razon_social"]).strip()
        elif emp_val and not ruc_val:
            ep = conn.execute("SELECT ruc FROM empresas_padron WHERE UPPER(TRIM(razon_social)) = ? LIMIT 1", (emp_val.upper(),)).fetchone()
            if ep and (ep["ruc"] or "").strip():
                ruc_val = str(ep["ruc"]).strip()
        if ruc_sugerido and not emp_sugerida:
            ep = conn.execute("SELECT razon_social FROM empresas_padron WHERE ruc = ?", (ruc_sugerido,)).fetchone()
            if ep and (ep["razon_social"] or "").strip():
                emp_sugerida = str(ep["razon_social"]).strip()
        return {
            "ok": True,
            "estanciaUuid": r["estancia_uuid"],
            "fila": int(r["sheet_row"] or 0),
            "id": r["id_timestamp_str"],
            "fechaIn": dmy_a_iso(r["fecha_in_dmy"]),
            "fechaOut": dmy_a_iso(r["fecha_out_dmy"]),
            "nombre": r["huesped"],
            "dni": r["dni"],
            "sexo": r["sexo"] or "M",
            "nAcomp": r["n_acomp"],
            "dniAcomp": r["dni_acomp"],
            "nac": r["nac"] or "Perú",
            "reg": reg_val or "Lima",
            "mot": r["mot"],
            "hab": r["hab"],
            "tipoHab": r["tipo_hab"],
            "tarifa": float(r["tarifa"] or 0),
            "snacks": float(r["adic_snacks"] or 0),
            "servicios": float(r["adic_servicios"] or 0),
            "adicComp": float(r["adic_comp"] or 0),
            "pEfecIni": float(r["pago_ini_efec"] or 0),
            "pElecIni": float(r["pago_ini_yape"] or 0),
            "pEfecPost": float(r["pago_post_efec"] or 0),
            "pYapePost": float(r["pago_post_yape"] or 0),
            "pVisa": float(r["pago_visa"] or 0),
            "deudaAnt": float(r["deuda_anterior"] or 0),
            "estadoEstancia": r["estado_estancia"] or "En Curso",
            "tipoComp": r["tipo_comp"],
            "estadoComp": r["estado_comp"],
            "nroComp": r["nro_comp"],
            "montoFact": float(r["monto_factura"]) if float(r["monto_factura"] or 0) > 0 else "",
            "empresa": emp_val,
            "proyecto": r["proyecto"],
            "triple1": r["triple1"],
            "triple2": r["triple2"],
            "hotelApoyo": r["hotel_apoyo"],
            "observaciones": r["observaciones"],
            "nroOpYape": r["nro_op_yape"],
            "celular": cel_val,
            "ruc": ruc_val,
            "rucSugerido": ruc_sugerido,
            "empresaSugerida": emp_sugerida,
        }
    finally:
        conn.close()


def modificar_estancia_local(datos: Dict[str, Any]) -> Dict[str, Any]:
    with db_transaction() as conn:
        actual = _buscar_estancia_local(
            conn,
            fila=int(datos.get("fila") or 0),
            id_busq=str(datos.get("id") or ""),
            estancia_uuid=str(datos.get("estanciaUuid") or ""),
            hab=str(datos.get("hab") or ""),
        )

        hab_nueva = str(datos.get("hab") or actual["hab"]).strip()
        tipo_hab = actual["tipo_hab"]
        if hab_nueva != actual["hab"]:
            hc = conn.execute("SELECT tipo FROM habitaciones_config WHERE hab = ?", (hab_nueva,)).fetchone()
            if not hc:
                raise ValueError(f"La habitación {hab_nueva} no existe en Config.")
            tipo_hab = hc["tipo"]

        f_in_dmy = normalizar_fecha_dmy(datos.get("fechaIn")) if datos.get("fechaIn") else actual["fecha_in_dmy"]
        f_out_dmy = normalizar_fecha_dmy(datos.get("fechaOut")) if datos.get("fechaOut") else actual["fecha_out_dmy"]
        ymd_in = fecha_dmy_a_ymd(f_in_dmy)
        ymd_out = fecha_dmy_a_ymd(f_out_dmy)
        if ymd_in > 0 and ymd_out > 0 and ymd_out <= ymd_in:
            raise ValueError("El Check-Out debe ser posterior al Check-In.")
        noches = calcular_noches(f_in_dmy, f_out_dmy)

        def num_val(k: str, prev: float) -> float:
            v = datos.get(k)
            return prev if v in (None, "") else parse_moneda(v)

        def txt_val(k: str, prev: str) -> str:
            v = datos.get(k)
            return prev if v is None else str(v).strip()

        nombre = txt_val("nombre", actual["huesped"]).upper()
        nac = txt_val("nac", actual["nac"]) or "Perú"
        dni = normalizar_dni(txt_val("dni", actual["dni"]), nac)
        sexo = txt_val("sexo", actual["sexo"]) or "M"
        n_acomp = txt_val("nAcomp", actual["n_acomp"])
        dni_acomp = txt_val("dniAcomp", actual["dni_acomp"])
        reg = txt_val("reg", actual["reg"])
        mot = txt_val("mot", actual["mot"])
        tarifa = num_val("tarifa", float(actual["tarifa"] or 0))
        snacks = num_val("snacks", float(actual["adic_snacks"] or 0))
        servicios = num_val("servicios", float(actual["adic_servicios"] or 0))
        adic_comp = num_val("adicComp", float(actual["adic_comp"] or 0))
        tipo_adic_prev = actual["tipo_adicional"] if "tipo_adicional" in actual.keys() else ""
        tipo_adic = txt_val("tipoAdicional", tipo_adic_prev)

        p_efec_ini_prev = float(actual["pago_ini_efec"] or 0)
        p_elec_ini_prev = float(actual["pago_ini_yape"] or 0)
        p_efec_post_prev = float(actual["pago_post_efec"] or 0)
        p_yape_post_prev = float(actual["pago_post_yape"] or 0)
        p_visa_prev = float(actual["pago_visa"] or 0)

        p_efec_ini = num_val("pEfecIni", p_efec_ini_prev)
        p_elec_ini = num_val("pElecIni", p_elec_ini_prev)
        p_efec_post = num_val("pEfecPost", p_efec_post_prev)
        p_yape_post = num_val("pYapePost", p_yape_post_prev)
        p_visa = num_val("pVisa", p_visa_prev)
        deuda_ant = num_val("deudaAnt", float(actual["deuda_anterior"] or 0))

        diff_efec = round((p_efec_ini + p_efec_post) - (p_efec_ini_prev + p_efec_post_prev), 2)
        diff_yape = round((p_elec_ini + p_yape_post) - (p_elec_ini_prev + p_yape_post_prev), 2)
        diff_visa = round(p_visa - p_visa_prev, 2)

        tipo_c = txt_val("tipoComp", actual["tipo_comp"])
        solicita = "Sí" if tipo_c else (actual["solicita_comp"] or "No")
        estado_c = txt_val("estadoComp", actual["estado_comp"])
        nro_comp = txt_val("nroComp", actual["nro_comp"])
        monto_fact = num_val("montoFact", float(actual["monto_factura"] or 0)) if datos.get("montoFact") not in (None, "") else float(actual["monto_factura"] or 0)
        estado_est = txt_val("estadoEstancia", actual["estado_estancia"]) or "En Curso"
        empresa = txt_val("empresa", actual["empresa"]).upper()
        proyecto = txt_val("proyecto", actual["proyecto"])
        triple1 = txt_val("triple1", actual["triple1"])
        triple2 = txt_val("triple2", actual["triple2"])
        hotel_apoyo = txt_val("hotelApoyo", actual["hotel_apoyo"])
        observaciones = txt_val("observaciones", actual["observaciones"])
        nro_op_yape = txt_val("nroOpYape", actual["nro_op_yape"])
        celular = txt_val("celular", actual["celular"])
        ruc = re.sub(r"\D", "", txt_val("ruc", actual["ruc"]))
        ruc_prev_clean = re.sub(r"\D", "", str(actual["ruc"] or ""))
        emp_prev_clean = str(actual["empresa"] or "").strip().upper()
        if ruc and (not empresa or (ruc != ruc_prev_clean and empresa == emp_prev_clean)):
            ep_row = conn.execute("SELECT razon_social FROM empresas_padron WHERE ruc = ?", (ruc,)).fetchone()
            if ep_row and (ep_row["razon_social"] or "").strip():
                empresa = str(ep_row["razon_social"]).strip().upper()
            elif ruc != ruc_prev_clean and empresa == emp_prev_clean:
                empresa = ""
        elif empresa and empresa != emp_prev_clean and (not ruc or ruc == ruc_prev_clean):
            ep_row = conn.execute("SELECT ruc FROM empresas_padron WHERE UPPER(TRIM(razon_social)) = ? LIMIT 1", (empresa,)).fetchone()
            if ep_row and (ep_row["ruc"] or "").strip():
                ruc = re.sub(r"\D", "", str(ep_row["ruc"]))

        ahora_str = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        pagos_ajuste = []
        if abs(diff_efec) >= 0.01:
            pid = f"{int(time.time()*1000)}-ajE"
            conc_efec = (
                "Registro de pago anulado por corrección o duplicación (Efectivo)"
                if diff_efec < 0
                else "Pago registrado por corrección de estancia (Efectivo)"
            )
            pagos_ajuste.append([pid, ahora_str, actual["id_timestamp_str"], hab_nueva, nombre, diff_efec, "Efectivo", "Ajuste Corregido", conc_efec, ""])
        if abs(diff_yape) >= 0.01:
            pid = f"{int(time.time()*1000)}-ajY"
            conc_yape = (
                "Registro de pago anulado por corrección o duplicación (Yape)"
                if diff_yape < 0
                else "Pago registrado por corrección de estancia (Yape)"
            )
            pagos_ajuste.append([pid, ahora_str, actual["id_timestamp_str"], hab_nueva, nombre, diff_yape, "Yape", "Ajuste Corregido", conc_yape, nro_op_yape])
        if abs(diff_visa) >= 0.01:
            pid = f"{int(time.time()*1000)}-ajV"
            conc_visa = (
                "Registro de pago anulado por corrección o duplicación (VISA)"
                if diff_visa < 0
                else "Pago registrado por corrección de estancia (VISA)"
            )
            pagos_ajuste.append([pid, ahora_str, actual["id_timestamp_str"], hab_nueva, nombre, diff_visa, "VISA", "Ajuste Corregido", conc_visa, ""])

        for paj_r in pagos_ajuste:
            conn.execute(
                """
                INSERT OR IGNORE INTO pagos (
                    pago_id, estancia_uuid, id_estancia, fecha_iso, hab, huesped, monto,
                    medio_pago, tipo_pago, concepto, nro_operacion, sheet_row, synced
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0)
                """,
                (
                    paj_r[0],
                    actual["estancia_uuid"],
                    paj_r[2],
                    paj_r[1],
                    paj_r[3],
                    paj_r[4],
                    paj_r[5],
                    paj_r[6],
                    paj_r[7],
                    paj_r[8],
                    paj_r[9],
                ),
            )

        conn.execute(
            """
            UPDATE estancias SET
                fecha_in_dmy = ?, fecha_out_dmy = ?, ymd_in = ?, ymd_out = ?, noches = ?,
                huesped = ?, dni = ?, sexo = ?, n_acomp = ?, dni_acomp = ?,
                nac = ?, reg = ?, mot = ?, hab = ?, tipo_hab = ?, tarifa = ?,
                adic_snacks = ?, adic_servicios = ?, adic_comp = ?, tipo_adicional = ?,
                pago_ini_efec = ?, pago_ini_yape = ?, pago_post_efec = ?, pago_post_yape = ?, pago_visa = ?,
                solicita_comp = ?, tipo_comp = ?, estado_comp = ?, nro_comp = ?, monto_factura = ?,
                estado_estancia = ?, deuda_anterior = ?,
                empresa = ?, proyecto = ?, triple1 = ?, triple2 = ?, hotel_apoyo = ?,
                observaciones = ?, nro_op_yape = ?, celular = ?, ruc = ?,
                dirty = 1, updated_at = ?
            WHERE estancia_uuid = ?
            """,
            (
                f_in_dmy, f_out_dmy, ymd_in, ymd_out, noches,
                nombre, dni, sexo, n_acomp, dni_acomp,
                nac, reg, mot, hab_nueva, tipo_hab, tarifa,
                snacks, servicios, adic_comp, tipo_adic,
                p_efec_ini, p_elec_ini, p_efec_post, p_yape_post, p_visa,
                solicita, tipo_c, estado_c, nro_comp, monto_fact,
                estado_est, deuda_ant,
                empresa, proyecto, triple1, triple2, hotel_apoyo,
                observaciones, nro_op_yape, celular, ruc,
                datetime.now().isoformat(), actual["estancia_uuid"],
            ),
        )

        if dni:
            conn.execute(
                """
                INSERT INTO huespedes_padron (dni, nombre, sexo, nac, reg, mot, hab_frec, tarifa_frec, empresa, celular, ruc, visitas, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(dni) DO UPDATE SET
                    nombre = CASE WHEN excluded.nombre != '' THEN excluded.nombre ELSE huespedes_padron.nombre END,
                    sexo = CASE WHEN excluded.sexo != '' THEN excluded.sexo ELSE huespedes_padron.sexo END,
                    nac = CASE WHEN excluded.nac != '' THEN excluded.nac ELSE huespedes_padron.nac END,
                    reg = CASE WHEN excluded.reg != '' THEN excluded.reg ELSE huespedes_padron.reg END,
                    mot = CASE WHEN excluded.mot != '' THEN excluded.mot ELSE huespedes_padron.mot END,
                    hab_frec = CASE WHEN excluded.hab_frec != '' THEN excluded.hab_frec ELSE huespedes_padron.hab_frec END,
                    tarifa_frec = CASE WHEN excluded.tarifa_frec > 0 THEN excluded.tarifa_frec ELSE huespedes_padron.tarifa_frec END,
                    empresa = CASE WHEN excluded.empresa != '' THEN excluded.empresa ELSE huespedes_padron.empresa END,
                    celular = CASE WHEN excluded.celular != '' THEN excluded.celular ELSE huespedes_padron.celular END,
                    ruc = CASE WHEN excluded.ruc != '' THEN excluded.ruc ELSE huespedes_padron.ruc END,
                    updated_at = excluded.updated_at
                """,
                (dni, nombre, sexo, nac, reg, mot, hab_nueva, tarifa, empresa, celular, ruc, datetime.now().isoformat()),
            )
        if ruc and empresa:
            conn.execute(
                """
                INSERT INTO empresas_padron (ruc, razon_social, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(ruc) DO UPDATE SET razon_social = excluded.razon_social, updated_at = excluded.updated_at
                """,
                (ruc, empresa, datetime.now().isoformat()),
            )

        _cambios_mod = _diff_campos([
            ("Huésped", actual["huesped"], nombre), ("DNI", actual["dni"], dni),
            ("Habitación", actual["hab"], hab_nueva), ("Check-In", actual["fecha_in_dmy"], f_in_dmy),
            ("Check-Out", actual["fecha_out_dmy"], f_out_dmy), ("Tarifa", actual["tarifa"], tarifa),
            ("Servicios", actual["adic_servicios"], servicios), ("Adic. Comprobante", actual["adic_comp"], adic_comp),
            ("Pago Efectivo inicial", actual["pago_ini_efec"], p_efec_ini), ("Pago Yape inicial", actual["pago_ini_yape"], p_elec_ini),
            ("Pago Efectivo posterior", actual["pago_post_efec"], p_efec_post), ("Pago Yape posterior", actual["pago_post_yape"], p_yape_post),
            ("Pago VISA", actual["pago_visa"], p_visa), ("Estado comprobante", actual["estado_comp"], estado_c),
            ("Monto factura", actual["monto_factura"], monto_fact), ("Estado estancia", actual["estado_estancia"], estado_est),
            ("Deuda anterior", actual["deuda_anterior"], deuda_ant), ("Celular", actual["celular"], celular),
            ("RUC", actual["ruc"], ruc), ("Empresa", actual["empresa"], empresa),
            ("Observaciones", actual["observaciones"], observaciones),
        ])
        _encolar_operacion(
            conn,
            "MODIFICAR",
            actual["estancia_uuid"],
            {
                "_mov_detalle": _cambios_mod,
                "estancia_uuid": actual["estancia_uuid"],
                "id_timestamp_str": actual["id_timestamp_str"],
                "sheet_row": int(actual["sheet_row"] or 0),
                "bloque_bm": [f_in_dmy, f_out_dmy, nombre, dni, sexo, n_acomp, dni_acomp, nac, reg, mot, hab_nueva, tipo_hab],
                "tarifa": tarifa,
                "bloque_su": [snacks, servicios, adic_comp],
                "tipo_adicional": tipo_adic,
                "bloque_wz": [p_efec_ini, p_elec_ini, p_efec_post, p_yape_post],
                "bloque_afam": [solicita, tipo_c, estado_c, nro_comp, monto_fact if monto_fact > 0 else "", estado_est, deuda_ant, p_visa],
                "bloque_aqay": [empresa, proyecto, triple1, triple2, hotel_apoyo, observaciones, nro_op_yape, celular, ruc],
                "pagos_ajuste": pagos_ajuste,
            },
            delta_caja=diff_efec,
        )

    return {
        "ok": True,
        "fila": int(actual["sheet_row"] or 0),
        "estanciaUuid": actual["estancia_uuid"],
        "hab": hab_nueva,
        "nombre": nombre,
    }


def liberar_habitacion_local(datos: Dict[str, Any]) -> Dict[str, Any]:
    with db_transaction() as conn:
        actual = _buscar_estancia_local(
            conn,
            fila=int(datos.get("fila") or 0),
            id_busq=str(datos.get("id") or ""),
            estancia_uuid=str(datos.get("estanciaUuid") or ""),
            hab=str(datos.get("hab") or ""),
        )
        hora_act = hora_actual_hm()
        hoy_str = hoy_dmy()
        f_out_actual = normalizar_fecha_dmy(actual["fecha_out_dmy"])
        nueva_f_out = hoy_str if fecha_dmy_a_ymd(f_out_actual) > fecha_dmy_a_ymd(hoy_str) else f_out_actual
        ymd_out_nuevo = fecha_dmy_a_ymd(nueva_f_out)

        obs_prev = str(actual["observaciones"] or "").strip()
        motivo_txt = str(datos.get("motivo") or "Por horas").strip()
        nota_lib = f"[Liberada {hora_act} - {motivo_txt}]"
        obs_nueva = f"{obs_prev} | {nota_lib}" if obs_prev else nota_lib

        conn.execute(
            """
            UPDATE estancias SET
                estado_estancia = 'Finalizado',
                fecha_out_dmy = ?, ymd_out = ?,
                observaciones = ?, dirty = 1, updated_at = ?
            WHERE estancia_uuid = ?
            """,
            (nueva_f_out, ymd_out_nuevo, obs_nueva, datetime.now().isoformat(), actual["estancia_uuid"]),
        )

        _encolar_operacion(
            conn,
            "LIBERAR",
            actual["estancia_uuid"],
            {
                "estancia_uuid": actual["estancia_uuid"],
                "id_timestamp_str": actual["id_timestamp_str"],
                "sheet_row": int(actual["sheet_row"] or 0),
                "fecha_out_dmy": nueva_f_out,
                "observaciones": obs_nueva,
            },
            delta_caja=0.0,
        )

    return {
        "ok": True,
        "hab": actual["hab"],
        "fila": int(actual["sheet_row"] or 0),
        "huesped": actual["huesped"],
        "horaLiberada": hora_act,
    }


def cancelar_estancia_local(datos: Dict[str, Any]) -> Dict[str, Any]:
    devolver_pago = bool(datos.get("devolverPago"))
    with db_transaction() as conn:
        actual = _buscar_estancia_local(
            conn,
            fila=int(datos.get("fila") or 0),
            id_busq=str(datos.get("id") or ""),
            estancia_uuid=str(datos.get("estanciaUuid") or ""),
            hab=str(datos.get("hab") or ""),
        )
        hora_act = hora_actual_hm()
        hab = actual["hab"]
        huesped = actual["huesped"]

        tot_efec = round(float(actual["pago_ini_efec"] or 0) + float(actual["pago_post_efec"] or 0), 2)
        tot_yape = round(float(actual["pago_ini_yape"] or 0) + float(actual["pago_post_yape"] or 0), 2)
        tot_visa = round(float(actual["pago_visa"] or 0), 2)

        obs_prev = str(actual["observaciones"] or "").strip()
        motivo_txt = str(datos.get("motivo") or "Cancelación de reserva/estancia").strip()
        nota_can = f"[Cancelada {hora_act} - {motivo_txt}{' - Pagos devueltos' if devolver_pago else ''}]"
        obs_nueva = f"{obs_prev} | {nota_can}" if obs_prev else nota_can

        pagos_extorno = []
        ahora_str = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        delta_caja = 0.0

        if devolver_pago:
            delta_caja = -tot_efec
            if tot_efec > 0:
                pagos_extorno.append([f"{int(time.time()*1000)}-exE", ahora_str, actual["id_timestamp_str"], hab, huesped, -tot_efec, "Efectivo", "Extorno", "Cancelación estancia (Devolución Efectivo)", ""])
            if tot_yape > 0:
                pagos_extorno.append([f"{int(time.time()*1000)}-exY", ahora_str, actual["id_timestamp_str"], hab, huesped, -tot_yape, "Yape", "Extorno", "Cancelación estancia (Devolución Yape)", ""])
            if tot_visa > 0:
                pagos_extorno.append([f"{int(time.time()*1000)}-exV", ahora_str, actual["id_timestamp_str"], hab, huesped, -tot_visa, "VISA", "Extorno", "Cancelación estancia (Devolución VISA)", ""])

            conn.execute(
                """
                UPDATE estancias SET
                    estado_estancia = 'Cancelado', tarifa = 0,
                    pago_ini_efec = 0, pago_ini_yape = 0, pago_post_efec = 0, pago_post_yape = 0, pago_visa = 0,
                    observaciones = ?, dirty = 1, updated_at = ?
                WHERE estancia_uuid = ?
                """,
                (obs_nueva, datetime.now().isoformat(), actual["estancia_uuid"]),
            )
        else:
            conn.execute(
                """
                UPDATE estancias SET
                    estado_estancia = 'Cancelado', tarifa = 0,
                    observaciones = ?, dirty = 1, updated_at = ?
                WHERE estancia_uuid = ?
                """,
                (obs_nueva, datetime.now().isoformat(), actual["estancia_uuid"]),
            )

        _encolar_operacion(
            conn,
            "CANCELAR",
            actual["estancia_uuid"],
            {
                "estancia_uuid": actual["estancia_uuid"],
                "id_timestamp_str": actual["id_timestamp_str"],
                "sheet_row": int(actual["sheet_row"] or 0),
                "devolver_pago": devolver_pago,
                "observaciones": obs_nueva,
                "pagos_extorno": pagos_extorno,
            },
            delta_caja=delta_caja,
        )

    return {
        "ok": True,
        "hab": hab,
        "fila": int(actual["sheet_row"] or 0),
        "huesped": huesped,
    }


def restaurar_estancia_local(datos: Dict[str, Any]) -> Dict[str, Any]:
    with db_transaction() as conn:
        actual = _buscar_estancia_local(
            conn,
            fila=int(datos.get("fila") or 0),
            id_busq=str(datos.get("id") or ""),
            estancia_uuid=str(datos.get("estanciaUuid") or ""),
            hab=str(datos.get("hab") or ""),
        )
        hab = actual["hab"]
        hoy_str = hoy_dmy()
        otra = conn.execute(
            """
            SELECT huesped FROM estancias
            WHERE hab = ? AND estancia_uuid != ? AND fecha_in_dmy = ? AND estado_estancia = 'En Curso'
            LIMIT 1
            """,
            (hab, actual["estancia_uuid"], hoy_str),
        ).fetchone()
        if otra:
            raise ValueError(f"No se puede restaurar: la habitación {hab} ya tiene otra estancia activa hoy ({otra['huesped']}).")

        hora_act = hora_actual_hm()
        obs_prev = str(actual["observaciones"] or "").strip()
        nota_res = f"[Restaurada a En Curso {hora_act}]"
        obs_nueva = f"{obs_prev} | {nota_res}" if obs_prev else nota_res

        conn.execute(
            """
            UPDATE estancias SET
                estado_estancia = 'En Curso', observaciones = ?, dirty = 1, updated_at = ?
            WHERE estancia_uuid = ?
            """,
            (obs_nueva, datetime.now().isoformat(), actual["estancia_uuid"]),
        )

        _encolar_operacion(
            conn,
            "RESTAURAR",
            actual["estancia_uuid"],
            {
                "estancia_uuid": actual["estancia_uuid"],
                "id_timestamp_str": actual["id_timestamp_str"],
                "sheet_row": int(actual["sheet_row"] or 0),
                "observaciones": obs_nueva,
            },
            delta_caja=0.0,
        )

    return {
        "ok": True,
        "hab": hab,
        "fila": int(actual["sheet_row"] or 0),
        "huesped": actual["huesped"],
    }


def actualizar_comprobante_desde_panel_local(datos: Dict[str, Any]) -> Dict[str, Any]:
    hab_busq = str(datos.get("hab") or "").strip()
    nuevo_estado = str(datos.get("estadoComp") or "Emitido").strip()
    nuevo_tipo = str(datos.get("tipoComp") or "").strip()
    nuevo_nro = str(datos.get("nroComp") or "").strip()
    raw_monto = datos.get("montoFact") if datos.get("montoFact") not in (None, "") else datos.get("montoFactura")
    nuevo_monto = parse_moneda(raw_monto) if raw_monto not in (None, "") else None
    nuevo_adic = parse_moneda(datos.get("adicComp")) if datos.get("adicComp") not in (None, "") else None
    nuevo_celular = str(datos.get("celular") or "").strip() if datos.get("celular") is not None else None
    ruc_entrada = re.sub(r"\D", "", str(datos.get("ruc") or ""))
    emp_entrada = str(datos.get("empresa") or "").strip().upper()
    dni_raw_in = str(datos.get("dni") or "").strip() if datos.get("dni") is not None else None

    with db_transaction() as conn:
        st = _buscar_estancia_local(
            conn,
            fila=int(datos.get("fila") or 0),
            id_busq=str(datos.get("id") or ""),
            estancia_uuid=str(datos.get("estanciaUuid") or ""),
            hab=hab_busq,
        )

        dni_prev = str(st["dni"] or "").strip()
        dni_final = dni_prev
        dni_actualizado = False
        if dni_raw_in is not None and dni_raw_in not in ("", "-"):
            dni_norm_in = normalizar_dni(dni_raw_in, str(st["nac"] or "Perú"))
            solo_d = re.sub(r"\D", "", dni_norm_in)
            if len(solo_d) == 7:
                dni_norm_in = solo_d.zfill(8)
            elif len(solo_d) == 8 and "/" not in dni_raw_in and "," not in dni_raw_in:
                dni_norm_in = solo_d
            if dni_norm_in and dni_norm_in != dni_prev:
                dni_final = dni_norm_in
                dni_actualizado = True

        ruc_prev_st = re.sub(r"\D", "", str(st["ruc"] or "").strip())
        emp_prev_st = str(st["empresa"] or "").strip().upper()
        ruc_final = ruc_entrada if ruc_entrada else ruc_prev_st
        emp_final = emp_entrada if emp_entrada else ("" if (ruc_entrada and ruc_entrada != ruc_prev_st) else emp_prev_st)
        if ruc_final and (not emp_final or (ruc_final != ruc_prev_st and emp_final == emp_prev_st)):
            ep_r = conn.execute("SELECT razon_social FROM empresas_padron WHERE ruc = ?", (ruc_final,)).fetchone()
            if ep_r and (ep_r["razon_social"] or "").strip():
                emp_final = str(ep_r["razon_social"]).strip().upper()

        if nuevo_tipo == "Factura":
            if not ruc_final or len(ruc_final) != 11 or not re.match(r"^(10|15|17|20)", ruc_final):
                raise ValueError("RUC inválido: para Factura debe tener 11 dígitos y empezar con 10 o 20.")
        elif nuevo_tipo == "Boleta" and datos.get("limpiarRuc") is True:
            ruc_final = ""
            emp_final = ""

        tipo_final = nuevo_tipo or st["tipo_comp"] or ("Factura" if ruc_final else "Boleta")
        monto_final = nuevo_monto if nuevo_monto is not None else float(st["monto_factura"] or 0)
        adic_final = nuevo_adic if nuevo_adic is not None else float(st["adic_comp"] or 0)
        cel_final = nuevo_celular if (nuevo_celular is not None and nuevo_celular != "") else st["celular"]
        nro_final = nuevo_nro if datos.get("nroComp") is not None else st["nro_comp"]

        if nro_final and not datos.get("forzarNroComp"):
            otra_hab = conn.execute(
                """
                SELECT hab, huesped, sheet_row FROM estancias
                WHERE UPPER(TRIM(nro_comp)) = UPPER(TRIM(?))
                  AND TRIM(hab) != TRIM(?)
                  AND TRIM(dni) != TRIM(?)
                LIMIT 1
                """,
                (nro_final, str(st["hab"] or ""), dni_final),
            ).fetchone()
            if otra_hab:
                raise ValueError(
                    f"El comprobante {nro_final} ya pertenece a la Hab. {otra_hab['hab']} "
                    f"({otra_hab['huesped']}, fila #{otra_hab['sheet_row']}). "
                    f"Verifica que no estés confundiendo de habitación."
                )

        conn.execute(
            """
            UPDATE estancias SET
                dni = ?,
                solicita_comp = 'Sí',
                tipo_comp = ?,
                estado_comp = ?,
                nro_comp = ?,
                monto_factura = ?,
                adic_comp = ?,
                celular = ?,
                ruc = ?,
                empresa = ?,
                dirty = 1,
                updated_at = ?
            WHERE estancia_uuid = ?
            """,
            (
                dni_final, tipo_final, nuevo_estado, nro_final, monto_final, adic_final,
                cel_final, ruc_final, emp_final, datetime.now().isoformat(), st["estancia_uuid"],
            ),
        )

        if ruc_final and emp_final:
            conn.execute(
                """
                INSERT INTO empresas_padron (ruc, razon_social, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(ruc) DO UPDATE SET razon_social = excluded.razon_social, updated_at = excluded.updated_at
                """,
                (ruc_final, emp_final, datetime.now().isoformat()),
            )

        _encolar_operacion(
            conn,
            "FACT_META",
            st["estancia_uuid"],
            {
                "estancia_uuid": st["estancia_uuid"],
                "id_timestamp_str": st["id_timestamp_str"],
                "sheet_row": int(st["sheet_row"] or 0),
                "bloque_afaj": ["Sí", tipo_final, nuevo_estado, nro_final, monto_final if monto_final > 0 else ""],
                "adic_comp": adic_final,
                "celular": cel_final,
                "ruc": ruc_final,
                "empresa": emp_final,
                "dni": dni_final,
                "dni_actualizado": dni_actualizado,
            },
            delta_caja=0.0,
        )

    return {
        "ok": True,
        "fila": int(st["sheet_row"] or 0),
        "estanciaUuid": st["estancia_uuid"],
        "hab": st["hab"],
        "dni": dni_final,
        "estadoComp": nuevo_estado,
        "tipoComp": tipo_final,
        "nroComp": nro_final,
        "montoFact": monto_final,
        "adicComp": adic_final,
        "celular": cel_final,
        "ruc": ruc_final,
        "empresa": emp_final,
    }


COLORES_VALIDOS = {"#f87171", "#fdba74", "#60a5fa"}
COLOR_GLOBAL_ROJO = "#f87171"
COLORES_SHARED_FILE = Path(r"C:\Hostal_Sistema\config\colores_panel.json")


def _cargar_colores_panel(conn) -> Dict[str, Any]:
    """Devuelve {'globales': {hab: {color, nota}}, 'porFecha': {fecha: {hab: {color, nota}}}}.
    Si existe C:\\Hostal_Sistema\\config\\colores_panel.json (sincronizado entre Laptop 1 y Laptop 2
    vía Syncthing sin tocar Google Sheets), sincroniza con él."""
    if COLORES_SHARED_FILE.exists():
        try:
            data = json.loads(COLORES_SHARED_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict) and ("globales" in data or "porFecha" in data):
                return {"globales": data.get("globales") or {}, "porFecha": data.get("porFecha") or {}}
        except Exception:
            pass
    globales: Dict[str, Any] = {}
    por_fecha: Dict[str, Dict[str, Any]] = {}
    for r in conn.execute("SELECT hab, fecha_dmy, color_hex, nota FROM habitaciones_colores").fetchall():
        item = {"color": r["color_hex"], "nota": r["nota"] or ""}
        if r["fecha_dmy"] == "*":
            globales[r["hab"]] = item
        else:
            por_fecha.setdefault(r["fecha_dmy"], {})[r["hab"]] = item
    return {"globales": globales, "porFecha": por_fecha}


def guardar_color_habitacion_local(hab: str, color_hex: str, fecha_ref: Optional[str] = None, nota: str = "") -> Dict[str, Any]:
    """Colores del panel SOLO LOCAL / ENTRE LAPTOPS (no se envían a Sheets).
    Rojo = global hasta desmarcar; Naranja/Celeste = solo ese día y esa habitación.
    Blanco/vacío = limpia ambos."""
    hab_clean = str(hab or "").strip()
    c_clean = str(color_hex or "").strip().lower()
    nota_clean = str(nota or "").strip()[:80]
    if not hab_clean:
        raise ValueError("Habitación requerida.")
    fecha_dmy = normalizar_fecha_dmy(fecha_ref) if fecha_ref else ""
    fecha_dmy = fecha_dmy or hoy_dmy()
    ahora = datetime.now().isoformat()

    if c_clean in ("", "default", "#ffffff", "#fff"):
        c_clean = ""
    elif c_clean not in COLORES_VALIDOS:
        raise ValueError("Color no disponible.")

    with db_transaction() as conn:
        estado_col = _cargar_colores_panel(conn)
        estado_col["globales"].pop(hab_clean, None)
        if fecha_dmy in estado_col["porFecha"]:
            estado_col["porFecha"][fecha_dmy].pop(hab_clean, None)

        conn.execute("DELETE FROM habitaciones_colores WHERE hab = ? AND (fecha_dmy = '*' OR fecha_dmy = ?)", (hab_clean, fecha_dmy))
        if c_clean:
            clave_fecha = "*" if c_clean == COLOR_GLOBAL_ROJO else fecha_dmy
            conn.execute(
                "INSERT OR REPLACE INTO habitaciones_colores (hab, fecha_dmy, color_hex, nota, updated_at) VALUES (?, ?, ?, ?, ?)",
                (hab_clean, clave_fecha, c_clean, nota_clean, ahora),
            )
            item = {"color": c_clean, "nota": nota_clean}
            if c_clean == COLOR_GLOBAL_ROJO:
                estado_col["globales"][hab_clean] = item
            else:
                estado_col["porFecha"].setdefault(fecha_dmy, {})[hab_clean] = item

        try:
            _nombre_col = {"#f87171": "🔴 Inutilizada (todas las fechas)", "#fdba74": "🟠 Horas Extras", "#60a5fa": "🔵 Celeste"}.get(c_clean, "⚪ Automático / Normal")
            _registrar_movimiento(
                conn, "Color / Nota de Habitación", hab=hab_clean,
                resumen=f"Hab. {hab_clean} → {_nombre_col}" + (f" · Nota: {nota_clean}" if nota_clean else "") + f" ({fecha_dmy})",
            )
        except Exception:
            pass
        try:
            COLORES_SHARED_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp_file = COLORES_SHARED_FILE.with_suffix(".json.tmp")
            tmp_file.write_text(json.dumps(estado_col, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp_file.replace(COLORES_SHARED_FILE)
        except Exception:
            pass

    return {"ok": True, "hab": hab_clean, "color": c_clean or "#ffffff", "fecha": fecha_dmy}

# ==============================================================================
# CONSULTAS AUXILIARES PARA MODALES INDEPENDIENTES (CHECK-IN, PAGOS, TIENDA, GASTOS)
# ==============================================================================

def obtener_datos_iniciales_checkin_local() -> Dict[str, Any]:
    panel = obtener_datos_panel_local(_ULTIMA_FECHA_PANEL_ISO)
    conn = get_connection()
    try:
        f_vis_dmy = str(panel.get("fechaVisualizada") or hoy_dmy()).strip()
        f_hoy = dmy_a_iso(f_vis_dmy) or hoy_iso()
        try:
            dt_ref = datetime.strptime(f_hoy, "%Y-%m-%d")
            f_man = (dt_ref + timedelta(days=1)).strftime("%Y-%m-%d")
        except Exception:
            f_man = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

        hoteles_apoyo = [
            r["clave"]
            for r in conn.execute("SELECT clave FROM catalogos_local WHERE categoria = 'HOTEL_APOYO' ORDER BY orden").fetchall()
        ]

        habs_modal = []
        for h in panel["habitaciones"]:
            habs_modal.append({
                "hab": h["hab"],
                "tipo": h["tipo"],
                "precioSug": h["precioRef"],
                "estado": "Libre" if h["estado"] == "libre" else "Ocupado",
                "libre": h["estado"] == "libre",
                "huespedActual": h["huesped"] if h["huesped"] != "-" else "",
                "checkInISO": dmy_a_iso(h["checkIn"]) if h["checkIn"] != "-" else "",
            })

        emp_por_ruc = {}
        ruc_por_emp = {}
        for ep in conn.execute("SELECT ruc, razon_social FROM empresas_padron").fetchall():
            r_c = re.sub(r"\D", "", str(ep["ruc"] or "").strip())
            e_c = str(ep["razon_social"] or "").strip().upper()
            if r_c and e_c:
                emp_por_ruc[r_c] = e_c
                ruc_por_emp[e_c] = r_c

        huespedes_dict = {}
        for h in conn.execute("SELECT * FROM huespedes_padron").fetchall():
            d_key = str(h["dni"] or "").strip()
            if not d_key:
                continue
            r_val = re.sub(r"\D", "", str(h["ruc"] or "").strip())
            e_val = str(h["empresa"] or "").strip().upper()
            if r_val and not e_val and r_val in emp_por_ruc:
                e_val = emp_por_ruc[r_val]
            elif e_val and not r_val and e_val in ruc_por_emp:
                r_val = ruc_por_emp[e_val]
            huespedes_dict[d_key] = {
                "nombre": h["nombre"],
                "sexo": h["sexo"] or "M",
                "nac": h["nac"] or "Perú",
                "reg": h["reg"] or "",
                "mot": h["mot"] or "Vacaciones, recreo u ocio",
                "tarifa": h["tarifa_frec"] or "",
                "hab": h["hab_frec"] or "",
                "celular": h["celular"] or "",
                "ruc": r_val,
                "empresa": e_val,
                "visitas": int(h["visitas"] or 1),
            }

        # Enriquecer con historial de estancias para garantizar que ningún cliente recurrente pierda su RUC, Empresa o Procedencia
        for er in conn.execute(
            """
            SELECT dni, huesped, sexo, nac, reg, mot, hab, tarifa, celular, ruc, empresa
            FROM estancias
            WHERE trim(dni) != ''
            ORDER BY ymd_in ASC, sheet_row ASC, rowid ASC
            """
        ).fetchall():
            d_key = str(er["dni"] or "").strip()
            if not d_key:
                continue
            r_est = re.sub(r"\D", "", str(er["ruc"] or "").strip())
            e_est = str(er["empresa"] or "").strip().upper()
            if r_est and e_est:
                emp_por_ruc[r_est] = e_est
                ruc_por_emp[e_est] = r_est
            if r_est and not e_est and r_est in emp_por_ruc:
                e_est = emp_por_ruc[r_est]
            elif e_est and not r_est and e_est in ruc_por_emp:
                r_est = ruc_por_emp[e_est]

            item = huespedes_dict.get(d_key)
            if not item:
                huespedes_dict[d_key] = {
                    "nombre": str(er["huesped"] or "").strip().upper(),
                    "sexo": str(er["sexo"] or "M").strip() or "M",
                    "nac": str(er["nac"] or "Perú").strip() or "Perú",
                    "reg": str(er["reg"] or "").strip(),
                    "mot": str(er["mot"] or "Vacaciones, recreo u ocio").strip() or "Vacaciones, recreo u ocio",
                    "tarifa": float(er["tarifa"] or 0) if float(er["tarifa"] or 0) > 0 else "",
                    "hab": str(er["hab"] or "").strip(),
                    "celular": str(er["celular"] or "").strip(),
                    "ruc": r_est,
                    "empresa": e_est,
                    "visitas": 1,
                }
            else:
                if (er["huesped"] or "").strip():
                    item["nombre"] = str(er["huesped"]).strip().upper()
                if (er["reg"] or "").strip():
                    item["reg"] = str(er["reg"]).strip()
                if (er["celular"] or "").strip():
                    item["celular"] = str(er["celular"]).strip()
                if (er["hab"] or "").strip():
                    item["hab"] = str(er["hab"]).strip()
                if float(er["tarifa"] or 0) > 0:
                    item["tarifa"] = float(er["tarifa"])
                if r_est:
                    item["ruc"] = r_est
                if e_est:
                    item["empresa"] = e_est
                if item.get("ruc") and not item.get("empresa") and item["ruc"] in emp_por_ruc:
                    item["empresa"] = emp_por_ruc[item["ruc"]]
                elif item.get("empresa") and not item.get("ruc") and item["empresa"] in ruc_por_emp:
                    item["ruc"] = ruc_por_emp[item["empresa"]]

        # Blindaje estricto Continuar Estancia (Enlace por Fecha de Check-In = Ayer):
        # 1) La fecha destino de continuación es hoy (o la fecha del panel si es futura); por tanto,
        #    el Check-In de la estancia a continuar DEBE ser 1 día antes (ymd_ayer_ref) o multinoche que sale hoy.
        # 2) JAMÁS incluir estancias cuyo Check-In ya sea hoy (ymd_in_st >= ymd_hoy_ref), ni dejar que
        #    una estancia registrada hoy oculte la estancia del día anterior cuando el huésped cambia de habitación.
        try:
            dt_hoy_real = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            dt_base_cont = max(dt_ref, dt_hoy_real)
            ymd_hoy_ref = int(dt_base_cont.strftime("%Y%m%d"))
            ymd_ayer_ref = int((dt_base_cont - timedelta(days=1)).strftime("%Y%m%d"))
        except Exception:
            ymd_hoy_ref = int(datetime.now().strftime("%Y%m%d"))
            ymd_ayer_ref = int((datetime.now() - timedelta(days=1)).strftime("%Y%m%d"))

        estancias_activas = []
        habs_ya_evaluadas = set()
        for st in panel["estanciasCache"]:
            if st.get("esCancelada"):
                continue
            hab_key = str(st.get("hab") or "").strip()
            if not hab_key or hab_key in habs_ya_evaluadas:
                continue
            ymd_in_st = fecha_dmy_a_ymd(st.get("checkIn", ""))
            ymd_out_st = fecha_dmy_a_ymd(st.get("checkOut", ""))
            # Ignorar estancias cuyo Check-In ya es hoy (o posterior): no son candidatas de ayer ni deben ocultar la estancia de ayer
            if ymd_in_st >= ymd_hoy_ref:
                continue
            # Bloquear la habitación en su estancia más reciente previa a hoy (aunque esté Finalizada o Continúa, jamás retroceder a días más antiguos)
            habs_ya_evaluadas.add(hab_key)
            if st.get("esInactiva") or st.get("esContinuacionCerrada"):
                continue
            if ymd_in_st > 0 and ymd_in_st < ymd_ayer_ref and ymd_out_st < ymd_hoy_ref:
                continue
            if True:
                sub_aloj = float(st.get("subtotalAloj") or st.get("tarifa") or 0)
                t_cuenta = round(
                    sub_aloj
                    + float(st.get("snacks") or 0)
                    + float(st.get("servicios") or 0)
                    + float(st.get("adicComp") or 0)
                    + float(st.get("deudaAnt") or 0),
                    2,
                )
                estancias_activas.append({
                    "fila": st["fila"],
                    "id": st["id"],
                    "estanciaUuid": st["estanciaUuid"],
                    "hab": hab_key,
                    "nombre": st["huesped"],
                    "dni": st["dni"],
                    "fechaIn": st["checkIn"],
                    "fechaOut": st["checkOut"],
                    "tarifa": st["tarifa"],
                    "totalCuenta": t_cuenta,
                    "totalPagado": float(st.get("pagado") or 0),
                    "saldo": st["saldo"],
                    "estado": st["estadoEstancia"],
                })

        empresas_cat: Dict[str, Dict[str, Any]] = {}
        for er in conn.execute(
            "SELECT empresa, ruc, proyecto FROM estancias WHERE empresa != '' ORDER BY rowid DESC LIMIT 3000"
        ).fetchall():
            nom = str(er["empresa"]).strip().upper()
            if not nom:
                continue
            ent = empresas_cat.setdefault(nom, {"ruc": "", "proyectos": []})
            if not ent["ruc"] and str(er["ruc"] or "").strip():
                ent["ruc"] = str(er["ruc"]).strip()
            pr_n = str(er["proyecto"] or "").strip()
            if pr_n and pr_n not in ent["proyectos"]:
                ent["proyectos"].append(pr_n)
        for ep in conn.execute("SELECT ruc, razon_social FROM empresas_padron").fetchall():
            nom = str(ep["razon_social"] or "").strip().upper()
            if nom:
                ent = empresas_cat.setdefault(nom, {"ruc": "", "proyectos": []})
                if not ent["ruc"]:
                    ent["ruc"] = str(ep["ruc"] or "").strip()

        return {
            "fechaHoy": f_hoy,
            "fechaManana": f_man,
            "empresasCatalogo": empresas_cat,
            "habitaciones": habs_modal,
            "hotelesTercerizados": hoteles_apoyo,
            "huespedes": huespedes_dict,
            "estanciasActivas": estancias_activas,
        }
    finally:
        conn.close()


def obtener_datos_pagos_posteriores_local() -> Dict[str, Any]:
    panel = obtener_datos_panel_local(_ULTIMA_FECHA_PANEL_ISO)
    res = []
    habs_vistas: Dict[str, bool] = {}
    for st in panel["estanciasCache"][:150]:
        if st.get("esCancelada") or st.get("esInactiva"):
            if float(st.get("saldo") or 0) <= 0:
                continue
        hab = str(st.get("hab") or "").strip()
        if not hab:
            continue
        saldo = float(st.get("saldo") or 0)
        if habs_vistas.get(hab) and saldo <= 0:
            continue
        habs_vistas[hab] = True

        tarifa = float(st.get("tarifa") or 0)
        subtotal_aloj = float(st.get("subtotalAloj") or tarifa)
        snacks = float(st.get("snacks") or 0)
        servicios = float(st.get("servicios") or 0)
        comp_extra = float(st.get("adicComp") or 0)
        deuda_ant = float(st.get("deudaAnt") or 0)
        pagado = float(st.get("pagado") or 0)
        total_cuenta = round(subtotal_aloj + snacks + servicios + comp_extra, 2)

        res.append({
            "fila": st["fila"],
            "id": st["id"],
            "estanciaUuid": st["estanciaUuid"],
            "hab": hab,
            "nombre": st["huesped"],
            "dni": st["dni"],
            "fechaIn": st["checkIn"],
            "fechaOut": st["checkOut"],
            "hora": st.get("hora") or "",
            "tarifa": tarifa,
            "snacks": snacks,
            "servicios": servicios,
            "compExtra": comp_extra,
            "totalCuenta": total_cuenta,
            "totalPagado": pagado,
            "pagado": pagado,
            "saldo": saldo,
            "deudaAnt": deuda_ant,
            "estado": st["estadoEstancia"],
            "estadoComp": st["estadoCompBD"],
            "montoFact": st["montoFactBD"],
        })
    return {"estanciasActivas": res}


def obtener_datos_tienda_local() -> Dict[str, Any]:
    panel = obtener_datos_panel_local(_ULTIMA_FECHA_PANEL_ISO)
    conn = get_connection()
    try:
        prods = [
            {"nombre": r["clave"], "precio": parse_moneda(r["valor"])}
            for r in conn.execute("SELECT clave, valor FROM catalogos_local WHERE categoria = 'PRODUCTO_TIENDA' ORDER BY orden").fetchall()
        ]
        personal = [
            r["clave"]
            for r in conn.execute("SELECT clave FROM catalogos_local WHERE categoria = 'PERSONAL' ORDER BY orden").fetchall()
        ]
        habs = [
            {
                "hab": h["hab"],
                "tipo": h["tipo"],
                "libre": h["estado"] == "libre",
                "huespedActual": h["huesped"] if h["huesped"] != "-" else "",
                "checkInISO": dmy_a_iso(h["checkIn"]) if h["checkIn"] != "-" else "",
            }
            for h in panel["habitaciones"]
        ]
        return {
            "fechaHoy": hoy_iso(),
            "habitaciones": habs,
            "productosTienda": prods,
            "listaPersonal": personal,
        }
    finally:
        conn.close()


def obtener_datos_gastos_local() -> Dict[str, Any]:
    conn = get_connection()
    try:
        cats = [r["clave"] for r in conn.execute("SELECT clave FROM catalogos_local WHERE categoria = 'CAT_GASTO' ORDER BY orden").fetchall()]
        mets = [r["clave"] for r in conn.execute("SELECT clave FROM catalogos_local WHERE categoria = 'MET_GASTO' ORDER BY orden").fetchall()]
        pers = [r["clave"] for r in conn.execute("SELECT clave FROM catalogos_local WHERE categoria = 'PERSONAL' ORDER BY orden").fetchall()]
        tips = [r["clave"] for r in conn.execute("SELECT clave FROM catalogos_local WHERE categoria = 'TIPO_COMP_GASTO' ORDER BY orden").fetchall()]

        hoy_str = hoy_dmy()
        gastos_hoy = []
        tot_hoy = 0.0
        tot_efec_hoy = 0.0
        for g in conn.execute("SELECT * FROM gastos WHERE fecha_gasto_dmy = ? ORDER BY rowid DESC", (hoy_str,)).fetchall():
            m = float(g["monto"] or 0)
            tot_hoy = round(tot_hoy + m, 2)
            if g["metodo_pago"] == "Efectivo":
                tot_efec_hoy = round(tot_efec_hoy + m, 2)
            gastos_hoy.append({
                "hora": g["hora_registro"],
                "descripcion": g["descripcion"],
                "monto": m,
                "categoria": g["categoria"],
                "metodo": g["metodo_pago"],
                "tipoComp": g["tipo_comp"],
                "nroComp": g["nro_comp"],
                "responsable": g["responsable"],
                "personal": g["personal"],
            })

        # Personal con las MISMAS etiquetas que Tienda ("Cargar a la Cuenta"), mostrando el nombre real entre paréntesis
        etiquetas_pers: Dict[str, str] = {}
        for pc in conn.execute("SELECT empleado, rol, categoria_tienda FROM personal_config WHERE activo = 1 ORDER BY orden").fetchall():
            if pc["categoria_tienda"]:
                etiquetas_pers[pc["categoria_tienda"]] = pc["empleado"]
        personal_opciones = []
        for p in (pers or list(etiquetas_pers.keys())):
            real = etiquetas_pers.get(p, "")
            personal_opciones.append({"valor": p, "etiqueta": (f"{p} ({real})" if real and real != p else p)})

        return {
            "personalOpciones": personal_opciones,
            "fechaHoy": hoy_iso(),
            "categorias": cats or ["Limpieza y Suministros", "Servicios Básicos", "Mantenimiento", "Personal / Adelantos", "Otros"],
            "metodos": mets or ["Efectivo", "Yape", "Transferencia BCP"],
            "personal": pers or ["Admin", "Recepción"],
            "tiposComp": tips or ["Boleta", "Factura", "Recibo / Ticket", "Sin comprobante"],
            "gastosHoy": gastos_hoy,
            "totalHoy": tot_hoy,
            "totalEfectivoHoy": tot_efec_hoy,
        }
    finally:
        conn.close()


# ==============================================================================
# HISTORIAL DE MOVIMIENTOS (menú 🕒 tipo "reloj de Sheets")
# Cada laptop escribe SU PROPIO archivo .jsonl en C:\Hostal_Sistema\config\movimientos
# (Syncthing lo replica sin conflictos) y la lectura une ambos equipos.
# ==============================================================================

import socket as _socket

MOVIMIENTOS_DIR = Path(r"C:\Hostal_Sistema\config\movimientos")


def _nombre_equipo() -> str:
    host = _socket.gethostname()
    mapa = {"eduardocal": "Laptop 1 (Admin)", "laptop-japigjt2": "Laptop 2 (Recepción)"}
    return mapa.get(host.lower(), host)


def _num_cmp(v: Any) -> Optional[float]:
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


def _diff_campos(pares: List[Tuple[str, Any, Any]]) -> List[Dict[str, str]]:
    """Devuelve solo los campos que cambiaron: [{campo, antes, despues}]"""
    cambios: List[Dict[str, str]] = []
    for campo, antes, despues in pares:
        na, nd = _num_cmp(antes), _num_cmp(despues)
        if na is not None and nd is not None and not isinstance(antes, str) and not isinstance(despues, str):
            if abs(na - nd) < 0.005:
                continue
            cambios.append({"campo": campo, "antes": f"{na:g}", "despues": f"{nd:g}"})
            continue
        sa, sd = str(antes if antes is not None else "").strip(), str(despues if despues is not None else "").strip()
        if sa == sd:
            continue
        cambios.append({"campo": campo, "antes": sa or "—", "despues": sd or "—"})
    return cambios


def _registrar_movimiento(conn, accion: str, hab: str = "", huesped: str = "", resumen: str = "",
                          detalle: Optional[List[Dict[str, str]]] = None, monto: float = 0.0) -> None:
    ahora = datetime.now()
    mov = {
        "mov_id": f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:4]}",
        "fecha_dmy": ahora.strftime("%d/%m/%Y"),
        "hora": ahora.strftime("%H:%M:%S"),
        "ts_iso": ahora.isoformat(),
        "accion": accion,
        "hab": str(hab or "").strip(),
        "huesped": str(huesped or "").strip(),
        "equipo": _nombre_equipo(),
        "resumen": resumen,
        "detalle": detalle or [],
        "monto": round(float(monto or 0), 2),
    }
    conn.execute(
        """
        INSERT OR IGNORE INTO historial_movimientos
            (mov_id, fecha_dmy, hora, ts_iso, accion, hab, huesped, equipo, resumen, detalle_json, monto)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (mov["mov_id"], mov["fecha_dmy"], mov["hora"], mov["ts_iso"], mov["accion"], mov["hab"],
         mov["huesped"], mov["equipo"], mov["resumen"], json.dumps(mov["detalle"], ensure_ascii=False), mov["monto"]),
    )
    try:
        MOVIMIENTOS_DIR.mkdir(parents=True, exist_ok=True)
        with open(MOVIMIENTOS_DIR / f"mov_{_socket.gethostname().lower()}.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(mov, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _movimiento_desde_operacion(conn, op_type: str, entity_uuid: str, payload: Dict[str, Any],
                                delta_caja: float, detalle: Optional[List[Dict[str, str]]]) -> None:
    est = None
    if op_type in ("CHECKIN", "CONTINUAR", "PAGO", "MODIFICAR", "LIBERAR", "CANCELAR", "RESTAURAR", "FACT_META"):
        est = conn.execute(
            "SELECT hab, huesped, tarifa, noches, estado_estancia FROM estancias WHERE estancia_uuid = ?",
            (entity_uuid,),
        ).fetchone()
    hab = str(est["hab"]) if est else ""
    hues = str(est["huesped"]) if est else ""
    cab = f"Hab. {hab} — {hues}" if est else ""
    monto = 0.0

    def ultimo_corchete(texto: str) -> str:
        m = re.findall(r"\[([^\]]+)\]", str(texto or ""))
        return m[-1] if m else ""

    if op_type == "CHECKIN":
        accion = "Check-In"
        resumen = f"{cab} · S/ {float(est['tarifa'] or 0):.2f} × {int(est['noches'] or 1)} noche(s)" if est else "Check-In"
        monto = float(est["tarifa"] or 0) * int(est["noches"] or 1) if est else 0.0
    elif op_type == "CONTINUAR":
        accion = "Continuar Estancia"
        resumen = f"{cab} · S/ {float(est['tarifa'] or 0):.2f}" if est else "Continuar estancia"
    elif op_type == "PAGO":
        pr = payload.get("pago_row") or []
        tipo = str(pr[7]) if len(pr) > 7 else "Posterior"
        monto = float(pr[5]) if len(pr) > 5 else 0.0
        medio = str(pr[6]) if len(pr) > 6 else ""
        concepto = str(pr[8]) if len(pr) > 8 else ""
        accion = "Extorno / Vuelto" if tipo.lower().startswith("extorno") else "Pago Posterior"
        resumen = f"{cab} · S/ {monto:.2f} {medio} — {concepto}"
    elif op_type == "TIENDA":
        filas = payload.get("ventas_rows") or []
        monto = sum(float(r[8]) for r in filas if len(r) > 8)
        medio = str(filas[0][4]) if filas and len(filas[0]) > 4 else ""
        hab = str(filas[0][5]) if filas and len(filas[0]) > 5 else ""
        items = ", ".join(f"{r[3]}× {r[2]}" for r in filas if len(r) > 3)
        accion = "Venta Tienda"
        destino = f"Hab. {hab}" if hab else "Venta libre"
        resumen = f"{destino} · S/ {monto:.2f} {medio} — {items}"
    elif op_type == "GASTO":
        g = conn.execute(
            "SELECT descripcion, monto, categoria, metodo_pago FROM gastos WHERE gasto_uuid = ?", (entity_uuid,)
        ).fetchone()
        accion = "Gasto"
        if g:
            monto = float(g["monto"] or 0)
            resumen = f"S/ {monto:.2f} {g['metodo_pago']} — {g['descripcion']} ({g['categoria']})"
        else:
            resumen = "Gasto registrado"
    elif op_type == "MODIFICAR":
        accion = "Modificar Estancia"
        n = len(detalle or [])
        resumen = f"{cab} · {n} campo(s) modificado(s)" if n else f"{cab} · sin cambios de valor"
    elif op_type == "LIBERAR":
        accion = "Liberar Habitación"
        resumen = f"{cab} · {ultimo_corchete(payload.get('observaciones'))}"
    elif op_type == "CANCELAR":
        accion = "Cancelar Estancia"
        resumen = f"{cab} · {ultimo_corchete(payload.get('observaciones'))}"
    elif op_type == "RESTAURAR":
        accion = "Restaurar Estancia"
        resumen = cab
    elif op_type == "FACT_META":
        accion = "Facturación"
        resumen = f"{cab} · datos de comprobante actualizados"
    else:
        accion = op_type.title()
        resumen = cab or op_type
    _registrar_movimiento(conn, accion, hab=hab, huesped=hues, resumen=resumen, detalle=detalle, monto=monto)


def obtener_movimientos_local(filtros: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    f = filtros or {}
    fecha = str(f.get("fecha") or "").strip()
    hab_f = str(f.get("hab") or "").strip()
    acc_f = str(f.get("accion") or "").strip()
    limite = int(f.get("limite") or 300)

    vistos: Dict[str, Dict[str, Any]] = {}
    conn = get_connection()
    try:
        for r in conn.execute("SELECT * FROM historial_movimientos ORDER BY ts_iso DESC LIMIT 1500").fetchall():
            try:
                det = json.loads(r["detalle_json"] or "[]")
            except Exception:
                det = []
            vistos[r["mov_id"]] = {
                "mov_id": r["mov_id"], "fecha_dmy": r["fecha_dmy"], "hora": r["hora"], "ts_iso": r["ts_iso"],
                "accion": r["accion"], "hab": r["hab"], "huesped": r["huesped"], "equipo": r["equipo"],
                "resumen": r["resumen"], "detalle": det, "monto": r["monto"],
            }
    finally:
        conn.close()

    try:
        for fp in MOVIMIENTOS_DIR.glob("mov_*.jsonl"):
            lineas = fp.read_text(encoding="utf-8", errors="replace").splitlines()[-1500:]
            for ln in lineas:
                try:
                    m = json.loads(ln)
                except Exception:
                    continue
                if m.get("mov_id") and m["mov_id"] not in vistos:
                    vistos[m["mov_id"]] = m
    except Exception:
        pass

    todos = sorted(vistos.values(), key=lambda m: m.get("ts_iso", ""), reverse=True)
    acciones = sorted({m.get("accion", "") for m in todos if m.get("accion")})
    out = []
    for m in todos:
        if fecha and fecha != "todas" and m.get("fecha_dmy") != fecha:
            continue
        if hab_f and str(m.get("hab", "")) != hab_f:
            continue
        if acc_f and m.get("accion") != acc_f:
            continue
        out.append(m)
        if len(out) >= limite:
            break
    return {"movimientos": out, "acciones": acciones, "fecha": fecha, "total": len(out)}


# ==============================================================================
# REGISTRO DE PAGOS UNIFICADO (Registro_Pagos + Tienda pagada al instante)
# ==============================================================================

def _parse_ts_flexible(txt: Any) -> Optional[datetime]:
    s = str(txt or "").strip()
    for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _ts_de_pago(pago_id: str, fecha_txt: Any) -> Optional[datetime]:
    m = re.match(r"^(\d{12,13})", str(pago_id or ""))
    if m:
        try:
            return datetime.fromtimestamp(int(m.group(1)) / 1000)
        except Exception:
            pass
    return _parse_ts_flexible(fecha_txt)


def obtener_registro_pagos_local(filtros: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    f = filtros or {}
    fecha = str(f.get("fecha") or "").strip() or hoy_dmy()
    desde = str(f.get("desde") or "").strip()
    hasta = str(f.get("hasta") or "").strip()
    medio_f = str(f.get("medio") or "").strip()
    origen_f = str(f.get("origen") or "").strip()
    q = str(f.get("q") or "").strip().lower()

    d_desde = None
    d_hasta = None
    if desde and hasta:
        try:
            d_desde = datetime.strptime(desde, "%d/%m/%Y").date()
            d_hasta = datetime.strptime(hasta, "%d/%m/%Y").date()
        except Exception:
            pass

    filas: List[Dict[str, Any]] = []
    conn = get_connection()
    try:
        por_uuid: Dict[str, str] = {}
        por_id: Dict[str, str] = {}
        por_hab_huesp: Dict[Tuple[str, str], str] = {}
        por_hab_rangos: Dict[str, List[Tuple[int, int, str]]] = {}
        mapa_est_id: Dict[str, str] = {}

        for est in conn.execute(
            "SELECT estancia_uuid, id_timestamp_str, sheet_row, hab, huesped, fecha_in_dmy, ymd_in, ymd_out FROM estancias WHERE estado_estancia != 'Cancelada' ORDER BY rowid ASC"
        ).fetchall():
            f_in = str(est["fecha_in_dmy"] or "").strip()
            if not f_in:
                continue
            uid_e = str(est["estancia_uuid"] or "").strip()
            ts_id = str(est["id_timestamp_str"] or "").strip()
            s_row = int(est["sheet_row"] or 0)
            id_legible = f"#{s_row}" if s_row > 0 else (ts_id[:16] if ts_id else (uid_e[:8] if uid_e else "—"))

            hab_e = str(est["hab"] or "").strip()
            nom_e = str(est["huesped"] or "").strip().upper()
            if uid_e:
                por_uuid[uid_e] = f_in
                mapa_est_id[uid_e] = id_legible
            if ts_id:
                por_id[ts_id] = f_in
                mapa_est_id[ts_id] = id_legible
            if hab_e and nom_e:
                por_hab_huesp[(hab_e, nom_e)] = f_in
            if hab_e:
                por_hab_rangos.setdefault(hab_e, []).append(
                    (int(est["ymd_in"] or 0), int(est["ymd_out"] or 0), f_in)
                )

        def _resolver_checkin(uid_p: str, id_p: str, hab_p: str, nom_p: str, ts_p: Any, orig_p: str) -> str:
            if uid_p and uid_p in por_uuid:
                return por_uuid[uid_p]
            if id_p and id_p in por_id:
                return por_id[id_p]
            if hab_p and nom_p and (hab_p, nom_p.upper()) in por_hab_huesp:
                return por_hab_huesp[(hab_p, nom_p.upper())]
            ymd_pago = int(ts_p.strftime("%Y%m%d")) if ts_p else 0
            if hab_p and hab_p in por_hab_rangos and ymd_pago:
                for yin, yout, fin_val in reversed(por_hab_rangos[hab_p]):
                    if yin and yin <= ymd_pago and (not yout or ymd_pago <= yout):
                        return fin_val
            if orig_p == "Check-In" and ts_p:
                return ts_p.strftime("%d/%m/%Y")
            return "—"

        # 1. Cobros de estancias (pagos)
        raw_pagos = conn.execute("SELECT * FROM pagos ORDER BY rowid DESC LIMIT 1500").fetchall()
        estancias_con_ajuste: Dict[str, List[Dict[str, Any]]] = {}
        for rp in raw_pagos:
            t_rp = str(rp["tipo_pago"] or "").strip().lower()
            if t_rp.startswith("ajuste"):
                k_est = str(rp["id_estancia"] or rp["estancia_uuid"] or "").strip()
                if k_est:
                    estancias_con_ajuste.setdefault(k_est, []).append({
                        "medio": str(rp["medio_pago"] or "").strip().upper(),
                        "monto": float(rp["monto"] or 0),
                    })

        medios_en_cero_estancia: Dict[str, Dict[str, float]] = {}
        for est_m in conn.execute(
            "SELECT estancia_uuid, id_timestamp_str, pago_ini_efec, pago_post_efec, pago_ini_yape, pago_post_yape, pago_visa FROM estancias"
        ).fetchall():
            tot_m = {
                "EFECTIVO": float(est_m["pago_ini_efec"] or 0) + float(est_m["pago_post_efec"] or 0),
                "YAPE": float(est_m["pago_ini_yape"] or 0) + float(est_m["pago_post_yape"] or 0),
                "VISA": float(est_m["pago_visa"] or 0),
            }
            if est_m["estancia_uuid"]:
                medios_en_cero_estancia[str(est_m["estancia_uuid"]).strip()] = tot_m
            if est_m["id_timestamp_str"]:
                medios_en_cero_estancia[str(est_m["id_timestamp_str"]).strip()] = tot_m

        for r in raw_pagos:
            ts = _ts_de_pago(r["pago_id"], r["fecha_iso"])
            if not ts:
                continue
            tipo = str(r["tipo_pago"] or "").strip()
            low = tipo.lower()
            monto_val = float(r["monto"] or 0)
            medio_val = str(r["medio_pago"] or "").strip()
            medio_up = medio_val.upper()
            origen = (
                "Check-In" if low.startswith("inicial") else
                "Pago Posterior" if low.startswith("posterior") else
                "Extorno" if low.startswith("extorno") else
                "Ajuste Corregido" if low.startswith("ajuste") else (tipo or "Otro")
            )
            hab_str = str(r["hab"] or "").strip()
            huesp_str = str(r["huesped"] or "").strip()
            uid_p = str(r["estancia_uuid"] or "").strip()
            id_p = str(r["id_estancia"] or "").strip()
            k_est = id_p or uid_p
            f_ci = _resolver_checkin(uid_p, id_p, hab_str, huesp_str, ts, origen)
            id_est_show = mapa_est_id.get(uid_p) or mapa_est_id.get(id_p) or (f"#{r['sheet_row']}" if r["sheet_row"] > 0 else (id_p or "—"))

            concepto_txt = str(r["concepto"] or "").strip()
            fue_anulado_por_correccion = False

            if "[duplicado" in concepto_txt.lower():
                fue_anulado_por_correccion = True
            elif origen in ("Check-In", "Pago Posterior") and k_est:
                ajustes_est = estancias_con_ajuste.get(k_est) or []
                for aj in ajustes_est:
                    if aj["medio"] == medio_up and aj["monto"] < 0:
                        fue_anulado_por_correccion = True
                        break
                    if aj["medio"] != medio_up and abs(aj["monto"] - monto_val) < 0.01:
                        fue_anulado_por_correccion = True
                        break
                if not fue_anulado_por_correccion and k_est in medios_en_cero_estancia:
                    if medios_en_cero_estancia[k_est].get(medio_up, 1.0) <= 0.001:
                        fue_anulado_por_correccion = True

            if fue_anulado_por_correccion:
                origen = "Ajuste Corregido"
                concepto_txt = f"Registro de pago anulado por corrección o duplicación ({medio_val})"
            elif origen == "Ajuste Corregido":
                if monto_val < 0:
                    concepto_txt = f"Registro de pago anulado por corrección o duplicación ({medio_val})"
                elif not concepto_txt or concepto_txt.lower().startswith("ajuste modificaci"):
                    concepto_txt = f"Pago corregido en modificación de estancia ({medio_val})"

            filas.append({
                "ts": ts,
                "idEstancia": id_est_show,
                "idEstanciaCompleto": id_p or uid_p or id_est_show,
                "hab": hab_str,
                "huesped": huesp_str,
                "fechaCheckIn": f_ci,
                "origen": origen,
                "concepto": concepto_txt,
                "medio": medio_val,
                "nroOp": str(r["nro_operacion"] or ""),
                "monto": monto_val,
            })

        # 2. Ventas de Tienda (ventas_tienda)
        grupos: Dict[Tuple[str, str, str, str], Dict[str, Any]] = {}
        for r in conn.execute("SELECT * FROM ventas_tienda ORDER BY rowid DESC LIMIT 2500").fetchall():
            met = str(r["metodo_pago"] or "").strip()
            if met.lower().startswith("cargar"):
                continue
            ts = _parse_ts_flexible(r["fecha_iso"]) or _parse_ts_flexible(r["fecha_dmy"])
            if not ts:
                continue
            lote = str(r["lote_uuid"] or "")
            hab_t = str(r["hab"] or "").strip()
            uid_t = str(r["estancia_uuid"] or "").strip()
            id_est_t_raw = str(r["id_estancia"] or "").strip()
            clave = (str(r["fecha_iso"] or ""), met, hab_t, lote if lote != "SHEET" else "")
            f_ci_t = _resolver_checkin(uid_t, id_est_t_raw, hab_t, "", ts, "Tienda") if hab_t else "—"
            id_est_t = mapa_est_id.get(uid_t) or mapa_est_id.get(id_est_t_raw) or (id_est_t_raw[:16] if id_est_t_raw else (hab_t if hab_t else "—"))
            nom_hab = hab_t if hab_t else "V. Tienda"

            g = grupos.setdefault(clave, {
                "ts": ts,
                "idEstancia": id_est_t if hab_t else "—",
                "idEstanciaCompleto": uid_t or id_est_t_raw or id_est_t,
                "hab": nom_hab,
                "huesped": "Tienda",
                "fechaCheckIn": f_ci_t,
                "origen": "Tienda",
                "medio": met,
                "nroOp": str(r["nro_op_yape"] or ""),
                "monto": 0.0,
                "items": [],
            })
            g["monto"] = round(g["monto"] + float(r["subtotal"] or 0), 2)
            g["items"].append(f"{int(r['cantidad'] or 1)}× {r['producto']}")
        for g in grupos.values():
            g["concepto"] = ", ".join(g.pop("items"))
            filas.append(g)

        # 3. Gastos en Efectivo (gastos - solo efectivo)
        for r in conn.execute("SELECT * FROM gastos ORDER BY rowid DESC LIMIT 1000").fetchall():
            met_g = str(r["metodo_pago"] or "").strip()
            if "efectivo" not in met_g.lower() and met_g.lower() != "efec":
                continue
            ts_g = _parse_ts_flexible(r["fecha_gasto_iso"]) or _parse_ts_flexible(r["fecha_gasto_dmy"])
            if not ts_g:
                continue
            cat = str(r["categoria"] or "").strip()
            desc = str(r["descripcion"] or "").strip()
            concepto_g = f"[{cat}] {desc}".strip() if cat else desc
            monto_g = abs(float(r["monto"] or 0))
            resp_g = str(r["responsable"] or "Gasto Efectivo").strip()

            filas.append({
                "ts": ts_g,
                "idEstancia": "—",
                "idEstanciaCompleto": str(r["gasto_uuid"] or ""),
                "hab": "—",
                "huesped": resp_g,
                "fechaCheckIn": "—",
                "origen": "Gasto Efectivo",
                "concepto": concepto_g or "Salida de caja",
                "medio": "Efectivo",
                "nroOp": str(r["nro_comp"] or ""),
                "monto": -monto_g,
            })
    finally:
        conn.close()

    def pasa(x: Dict[str, Any]) -> bool:
        if fecha != "todas":
            if d_desde and d_hasta:
                if not (d_desde <= x["ts"].date() <= d_hasta):
                    return False
            elif fecha and fecha != "semana":
                if x["ts"].strftime("%d/%m/%Y") != fecha:
                    return False

        if medio_f:
            m = x["medio"].lower()
            if medio_f == "Otros":
                if m in ("efectivo", "yape", "visa"):
                    return False
            elif m != medio_f.lower():
                return False

        if origen_f and x["origen"] != origen_f and not (origen_f.startswith("Ajuste") and x["origen"].startswith("Ajuste")):
            return False

        if q:
            texto_todo = " ".join([
                str(x.get("idEstancia") or ""),
                str(x.get("hab") or ""),
                str(x.get("huesped") or ""),
                str(x.get("concepto") or ""),
                str(x.get("nroOp") or ""),
                str(x.get("medio") or ""),
                str(x.get("origen") or ""),
            ]).lower()
            if q not in texto_todo:
                return False
        return True

    sel = sorted([x for x in filas if pasa(x)], key=lambda x: x["ts"], reverse=True)[:1000]

    tot = {
        "total": 0.0,
        "ingresos": 0.0,
        "gastos_efectivo": 0.0,
        "efectivo": 0.0,
        "yape": 0.0,
        "visa": 0.0,
        "otros": 0.0,
        "tienda": 0.0,
        "neto": 0.0,
        "operaciones": len(sel),
    }

    for x in sel:
        m_val = x["monto"]
        m_medio = x["medio"].lower()
        if x["origen"] == "Gasto Efectivo":
            tot["gastos_efectivo"] += abs(m_val)
            tot["efectivo"] -= abs(m_val)
            tot["neto"] -= abs(m_val)
        else:
            tot["ingresos"] += m_val
            tot["total"] += m_val
            tot["neto"] += m_val
            clave = "efectivo" if m_medio == "efectivo" else "yape" if m_medio == "yape" else "visa" if m_medio == "visa" else "otros"
            tot[clave] += m_val
            if x["origen"] == "Tienda":
                tot["tienda"] += m_val

    tot = {k: (round(v, 2) if isinstance(v, float) else v) for k, v in tot.items()}

    salida = [
        {
            "idEstancia": x.get("idEstancia", "—"),
            "idEstanciaCompleto": x.get("idEstanciaCompleto", ""),
            "fecha": x["ts"].strftime("%d/%m/%Y"),
            "hora": x["ts"].strftime("%H:%M:%S"),
            "hab": x["hab"],
            "fechaCheckIn": x.get("fechaCheckIn", "—"),
            "huesped": x["huesped"],
            "monto": round(x["monto"], 2),
            "origen": x["origen"],
            "concepto": x["concepto"],
            "medio": x["medio"],
            "nroOp": x["nroOp"],
        }
        for x in sel
    ]
    return {"fecha": fecha, "desde": desde, "hasta": hasta, "filas": salida, "totales": tot}


# ==============================================================================

def _limpiar_header_vitrina(raw_hdr: str, fallback: str) -> str:
    s = str(raw_hdr or "").strip()
    if not s:
        return fallback
    lineas = [ln.strip() for ln in s.replace("\r\n", "\n").split("\n") if ln.strip()]
    if len(lineas) >= 3:
        return f"{lineas[1]} · {lineas[2]}"
    if len(lineas) == 2:
        return f"{lineas[0]} · {lineas[1]}"
    return s


CLAVE_GESTION_TIENDA = "nancy"


def _validar_clave_gestion_tienda(datos: Dict[str, Any]) -> None:
    clave = str((datos or {}).get("clave") or "").strip().lower()
    if clave != CLAVE_GESTION_TIENDA:
        raise ValueError("Contraseña de Gestión Tienda incorrecta.")


def verificar_clave_gestion_tienda_local(datos: Dict[str, Any]) -> Dict[str, Any]:
    _validar_clave_gestion_tienda(datos)
    return {"ok": True}


def _calcular_stocks_almacen_y_deltas_conn(conn) -> Tuple[Dict[str, float], Dict[str, float]]:
    """
    Calcula en 0 ms desde SQLite:
    1) stock_almacen por producto (INV_INICIAL_ALMACEN + COMPRA - TRANSFERENCIA - MERMA_ALMACEN - MERMA legado + AJUSTE_ALMACEN)
    2) delta_vitrina_pendiente por producto para movimientos aún con synced=0 (TRANSFERENCIA - MERMA + INV_INICIAL_VITRINA)
    """
    stock_alm: Dict[str, float] = {}
    delta_vit_pend: Dict[str, float] = {}
    filas = conn.execute(
        "SELECT producto, tipo_movimiento, cantidad, nota, synced FROM almacen_movimientos"
    ).fetchall()
    for r in filas:
        prod = str(r["producto"] or "").strip()
        if not prod:
            continue
        tipo = str(r["tipo_movimiento"] or "").strip().upper()
        cant = float(r["cantidad"] or 0)
        nota_up = str(r["nota"] or "").upper()
        synced = int(r["synced"] or 0)

        # Impacto en Almacén
        if tipo in ("INVENTARIO_INICIAL_ALMACEN", "COMPRA", "AJUSTE_ALMACEN"):
            stock_alm[prod] = stock_alm.get(prod, 0.0) + cant
        elif tipo in ("TRANSFERENCIA", "MERMA_ALMACEN"):
            stock_alm[prod] = stock_alm.get(prod, 0.0) - cant
        elif tipo == "MERMA":
            # Compatibilidad 100% con los 13 registros históricos de la hoja antigua (sin etiqueta [VITRINA]),
            # mientras que las nuevas mermas de vitrina llevan [VITRINA] y no descuentan doble del almacén.
            if "[VITRINA]" not in nota_up:
                stock_alm[prod] = stock_alm.get(prod, 0.0) - cant

        # Impacto en vivo (0 ms) sobre Vitrina mientras synced == 0 (antes de que Sheets recalcule B6)
        if synced == 0:
            if tipo in ("TRANSFERENCIA", "INVENTARIO_INICIAL_VITRINA"):
                delta_vit_pend[prod] = delta_vit_pend.get(prod, 0.0) + cant
            elif tipo == "MERMA":
                delta_vit_pend[prod] = delta_vit_pend.get(prod, 0.0) - cant

    return stock_alm, delta_vit_pend


def _fmt_num_stock(val: float) -> Any:
    v = round(float(val or 0), 2)
    return int(v) if v.is_integer() else v


def obtener_datos_vitrina_local() -> Dict[str, Any]:
    try:
        init_db()
    except Exception:
        pass
    conn = get_connection()
    try:
        # 1. Ventas locales de tienda aún no sincronizadas (synced = 0) para restar al stock de Sheets en vivo (0 ms)
        ventas_pend: Dict[str, int] = {}
        for rv in conn.execute(
            "SELECT producto, SUM(cantidad) AS cant FROM ventas_tienda WHERE synced = 0 GROUP BY producto"
        ).fetchall():
            p_nom = str(rv["producto"] or "").strip()
            if p_nom:
                ventas_pend[p_nom] = int(rv["cant"] or 0)

        # 1b. Precios de venta de productos y stocks de almacén + deltas pendientes de vitrina
        precios_map: Dict[str, float] = {}
        for rp in conn.execute(
            "SELECT clave, valor FROM catalogos_local WHERE categoria = 'PRODUCTO_TIENDA'"
        ).fetchall():
            k_p = str(rp["clave"] or "").strip()
            if k_p:
                precios_map[k_p] = round(parse_moneda(rp["valor"]), 2)

        stock_alm_map, delta_vit_pend_map = _calcular_stocks_almacen_y_deltas_conn(conn)

        # Asegurar columnas h4..h8 en caliente
        cols_vit_chk = [r[1] for r in conn.execute("PRAGMA table_info(vitrina_local)").fetchall()]
        if "h8_est" not in cols_vit_chk:
            for idx_h in range(4, 9):
                for suf in ("cant", "est"):
                    col_vh = f"h{idx_h}_{suf}"
                    if col_vh not in cols_vit_chk:
                        conn.execute(f"ALTER TABLE vitrina_local ADD COLUMN {col_vh} TEXT NOT NULL DEFAULT ''")
            conn.commit()

        # 2. Leer productos de vitrina_local (o fallback a catalogos_local si aún no hubo pull)
        filas_vit = conn.execute("SELECT * FROM vitrina_local ORDER BY orden ASC").fetchall()
        productos_out: List[Dict[str, Any]] = []

        if filas_vit:
            for r in filas_vit:
                prod_nom = str(r["producto"] or "").strip()
                if not prod_nom:
                    continue
                st_sheets = float(r["stock_sheets"] or 0)
                vend_local = ventas_pend.get(prod_nom, 0)
                delta_alm_vit = delta_vit_pend_map.get(prod_nom, 0.0)
                st_actual = st_sheets - vend_local + delta_alm_vit
                st_alm = stock_alm_map.get(prod_nom, 0.0)
                r_keys = r.keys()
                item_d = {
                    "orden": int(r["orden"] or 0),
                    "producto": prod_nom,
                    "precioVenta": precios_map.get(prod_nom, 0.0),
                    "stockAlmacen": _fmt_num_stock(st_alm),
                    "stockVitrina": _fmt_num_stock(st_actual),
                    "estadoActual": str(r["estado_actual"] or ""),
                }
                for idx_h in range(1, 9):
                    item_d[f"h{idx_h}Cant"] = str(r[f"h{idx_h}_cant"] or "") if f"h{idx_h}_cant" in r_keys else ""
                    item_d[f"h{idx_h}Est"] = str(r[f"h{idx_h}_est"] or "") if f"h{idx_h}_est" in r_keys else ""
                productos_out.append(item_d)
        else:
            for idx_p, rc in enumerate(
                conn.execute("SELECT clave, valor FROM catalogos_local WHERE categoria = 'PRODUCTO_TIENDA' ORDER BY orden").fetchall()
            ):
                prod_nom = str(rc["clave"] or "").strip()
                if not prod_nom:
                    continue
                st_alm = stock_alm_map.get(prod_nom, 0.0)
                delta_alm_vit = delta_vit_pend_map.get(prod_nom, 0.0)
                item_d = {
                    "orden": idx_p + 1,
                    "producto": prod_nom,
                    "precioVenta": round(parse_moneda(rc["valor"]), 2),
                    "stockAlmacen": _fmt_num_stock(st_alm),
                    "stockVitrina": _fmt_num_stock(delta_alm_vit),
                    "estadoActual": "",
                }
                for idx_h in range(1, 9):
                    item_d[f"h{idx_h}Cant"] = ""
                    item_d[f"h{idx_h}Est"] = ""
                productos_out.append(item_d)

        # 3. Metadatos E2, E3 y encabezados H4..V4 (8 conteos)
        meta_rows = conn.execute(
            "SELECT clave, valor FROM estado_sistema WHERE clave LIKE 'vitrina_%'"
        ).fetchall()
        meta = {r["clave"]: str(r["valor"] or "") for r in meta_rows}

        personal = [
            str(r["clave"]).strip()
            for r in conn.execute("SELECT clave FROM catalogos_local WHERE categoria = 'PERSONAL' ORDER BY orden").fetchall()
            if str(r["clave"] or "").strip()
        ]
        resp_e2 = meta.get("vitrina_responsable_e2", "").strip()
        if resp_e2 and resp_e2 not in personal:
            personal.insert(0, resp_e2)

        metodos_gasto = [
            str(r["clave"]).strip()
            for r in conn.execute("SELECT clave FROM catalogos_local WHERE categoria = 'MET_GASTO' ORDER BY orden").fetchall()
            if str(r["clave"] or "").strip()
        ] or ["Efectivo", "Yape", "BCP"]

        tipos_comp_gasto = [
            str(r["clave"]).strip()
            for r in conn.execute("SELECT clave FROM catalogos_local WHERE categoria = 'TIPO_COMP_GASTO' ORDER BY orden").fetchall()
            if str(r["clave"] or "").strip()
        ] or ["Boleta", "Factura", "Recibo / Ticket", "Sin comprobante"]

        # 4. Compras de tienda con saldo pendiente de desglosar
        compras_pend = []
        for cp in conn.execute(
            "SELECT * FROM compras_tienda_lotes WHERE estado = 'PENDIENTE' ORDER BY created_at DESC LIMIT 20"
        ).fetchall():
            m_tot = round(float(cp["monto_total"] or 0), 2)
            m_des = round(float(cp["monto_desglosado"] or 0), 2)
            falta = round(max(0.0, m_tot - m_des), 2)
            compras_pend.append({
                "compraUuid": cp["compra_uuid"],
                "fechaDmy": cp["fecha_dmy"],
                "montoTotal": m_tot,
                "montoDesglosado": m_des,
                "faltaDesglosar": falta,
                "metodoPago": cp["metodo_pago"],
                "responsable": cp["responsable"],
                "tipoComp": cp["tipo_comp"],
                "nroComp": cp["nro_comp"],
                "nota": cp["nota"],
            })

        # 5. Últimos movimientos del Kardex de Almacén
        ultimos_movs = []
        for mv in conn.execute(
            "SELECT * FROM almacen_movimientos ORDER BY synced ASC, sheet_row DESC, rowid DESC LIMIT 80"
        ).fetchall():
            ultimos_movs.append({
                "movUuid": mv["mov_uuid"],
                "loteUuid": mv["lote_uuid"],
                "idTransaccion": mv["id_transaccion"],
                "fechaDmy": mv["fecha_dmy"],
                "producto": mv["producto"],
                "cantidad": _fmt_num_stock(float(mv["cantidad"] or 0)),
                "costoTotal": round(float(mv["costo_total"] or 0), 2),
                "costoUnitario": round(float(mv["costo_unitario"] or 0), 2),
                "metodoPago": mv["metodo_pago"],
                "tipoMovimiento": mv["tipo_movimiento"],
                "nota": mv["nota"],
                "synced": int(mv["synced"] or 0),
            })

        return {
            "responsableE2": resp_e2,
            "fechaUltimoE3": meta.get("vitrina_fecha_e3", ""),
            "h1Header": _limpiar_header_vitrina(meta.get("vitrina_h1_header", ""), "Conteo 1 (Último)"),
            "h2Header": _limpiar_header_vitrina(meta.get("vitrina_h2_header", ""), "Conteo 2"),
            "h3Header": _limpiar_header_vitrina(meta.get("vitrina_h3_header", ""), "Conteo 3"),
            "h4Header": _limpiar_header_vitrina(meta.get("vitrina_h4_header", ""), "Conteo 4"),
            "h5Header": _limpiar_header_vitrina(meta.get("vitrina_h5_header", ""), "Conteo 5"),
            "h6Header": _limpiar_header_vitrina(meta.get("vitrina_h6_header", ""), "Conteo 6"),
            "h7Header": _limpiar_header_vitrina(meta.get("vitrina_h7_header", ""), "Conteo 7"),
            "h8Header": _limpiar_header_vitrina(meta.get("vitrina_h8_header", ""), "Conteo 8"),
            "listaPersonal": personal,
            "productos": productos_out,
            "gestionTienda": {
                "fechaHoyISO": hoy_iso(),
                "metodosGasto": metodos_gasto,
                "tiposCompGasto": tipos_comp_gasto,
                "comprasPendientes": compras_pend,
                "ultimosMovimientos": ultimos_movs,
            },
        }
    finally:
        conn.close()


def guardar_conteo_vitrina_local(datos: Dict[str, Any]) -> Dict[str, Any]:
    d = datos or {}
    responsable = str(d.get("responsable") or "").strip().upper()
    if not responsable:
        raise ValueError("Por favor escribe o selecciona el nombre del Responsable del Conteo (celda E2).")

    conteos_raw = d.get("conteos") or {}
    if not isinstance(conteos_raw, dict):
        raise ValueError("Formato de conteos inválido.")

    estado_actual_vit = obtener_datos_vitrina_local()
    prods_list = estado_actual_vit.get("productos") or []
    if not prods_list:
        raise ValueError("No hay productos cargados en Vitrina_Tienda.")

    items_map: Dict[str, Dict[str, Any]] = {}
    hay_conteo = False
    resumen_detalles: List[Dict[str, str]] = []
    ok_count = 0
    falta_count = 0
    sobra_count = 0

    for p in prods_list:
        nom_p = p["producto"]
        val_raw = conteos_raw.get(nom_p, "")
        val_str = str(val_raw if val_raw is not None else "").strip()
        if val_str != "":
            try:
                count_num = float(val_str)
            except ValueError:
                continue
            hay_conteo = True
            teoric = float(p.get("stockVitrina") or 0)
            diff = round(count_num - teoric, 2)
            if diff == 0:
                aviso = "OK"
                ok_count += 1
            elif diff < 0:
                av_abs = abs(diff)
                av_txt = str(int(av_abs)) if av_abs.is_integer() else f"{av_abs:.2f}"
                aviso = f"FALTA {av_txt}"
                falta_count += 1
            else:
                av_txt = str(int(diff)) if diff.is_integer() else f"{diff:.2f}"
                aviso = f"SOBRA {av_txt}"
                sobra_count += 1

            cant_fmt = str(int(count_num)) if count_num.is_integer() else str(round(count_num, 2))
            items_map[nom_p] = {"cant": cant_fmt, "estado": aviso}
            if aviso != "OK":
                resumen_detalles.append({"campo": nom_p, "antes": f"Vitrina: {p.get('stockVitrina')}", "despues": f"Físico: {cant_fmt} ({aviso})"})
        else:
            items_map[nom_p] = {"cant": "", "estado": ""}

    if not hay_conteo:
        raise ValueError("No has escrito ningún número en la columna de Conteo Físico Real.")

    ahora = datetime.now()
    fecha_hora_dmy = ahora.strftime("%d/%m/%Y %H:%M")
    ahora_iso = ahora.isoformat()
    nuevo_h1_hdr = f"Conteo:\n{responsable}\n{fecha_hora_dmy}"

    with db_transaction() as conn:
        # Desplazar encabezados H1 -> H2 -> ... -> H8
        old_hdrs = {}
        for idx_h in range(1, 8):
            r_h = conn.execute("SELECT valor FROM estado_sistema WHERE clave = ?", (f"vitrina_h{idx_h}_header",)).fetchone()
            old_hdrs[idx_h] = str(r_h["valor"] or "") if r_h else ""

        def _set_est(k: str, v: str) -> None:
            conn.execute(
                """
                INSERT INTO estado_sistema (clave, valor, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor, updated_at = excluded.updated_at
                """,
                (k, v, ahora_iso),
            )

        _set_est("vitrina_responsable_e2", responsable)
        _set_est("vitrina_fecha_e3", fecha_hora_dmy)
        for idx_h in range(8, 1, -1):
            _set_est(f"vitrina_h{idx_h}_header", old_hdrs.get(idx_h - 1, ""))
        _set_est("vitrina_h1_header", nuevo_h1_hdr)

        # Desplazar columnas en vitrina_local (h7 -> h8, ..., h1 -> h2, nuevo -> h1)
        for p in prods_list:
            nom_p = p["producto"]
            it = items_map.get(nom_p) or {"cant": "", "estado": ""}
            conn.execute(
                """
                INSERT INTO vitrina_local (
                    producto, orden, stock_sheets, estado_actual,
                    h1_cant, h1_est, h2_cant, h2_est, h3_cant, h3_est,
                    h4_cant, h4_est, h5_cant, h5_est, h6_cant, h6_est,
                    h7_cant, h7_est, h8_cant, h8_est, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(producto) DO UPDATE SET
                    h8_cant = vitrina_local.h7_cant,
                    h8_est  = vitrina_local.h7_est,
                    h7_cant = vitrina_local.h6_cant,
                    h7_est  = vitrina_local.h6_est,
                    h6_cant = vitrina_local.h5_cant,
                    h6_est  = vitrina_local.h5_est,
                    h5_cant = vitrina_local.h4_cant,
                    h5_est  = vitrina_local.h4_est,
                    h4_cant = vitrina_local.h3_cant,
                    h4_est  = vitrina_local.h3_est,
                    h3_cant = vitrina_local.h2_cant,
                    h3_est  = vitrina_local.h2_est,
                    h2_cant = vitrina_local.h1_cant,
                    h2_est  = vitrina_local.h1_est,
                    h1_cant = excluded.h1_cant,
                    h1_est  = excluded.h1_est,
                    estado_actual = CASE WHEN excluded.estado_actual != '' THEN excluded.estado_actual ELSE vitrina_local.estado_actual END,
                    updated_at = excluded.updated_at
                """,
                (
                    nom_p,
                    int(p.get("orden") or 0),
                    float(p.get("stockVitrina") or 0),
                    it["estado"],
                    it["cant"],
                    it["estado"],
                    p.get("h1Cant") or "",
                    p.get("h1Est") or "",
                    p.get("h2Cant") or "",
                    p.get("h2Est") or "",
                    p.get("h3Cant") or "",
                    p.get("h3Est") or "",
                    p.get("h4Cant") or "",
                    p.get("h4Est") or "",
                    p.get("h5Cant") or "",
                    p.get("h5Est") or "",
                    p.get("h6Cant") or "",
                    p.get("h6Est") or "",
                    p.get("h7Cant") or "",
                    p.get("h7Est") or "",
                    ahora_iso,
                ),
            )

        # Encolar subida idempotente a Vitrina_Tienda en Google Sheets
        op_uuid = str(uuid.uuid4())
        payload_sync = {
            "responsable": responsable,
            "fecha_hora_dmy": fecha_hora_dmy,
            "items_map": items_map,
        }
        conn.execute(
            """
            INSERT INTO sync_queue (op_uuid, op_type, entity_uuid, payload_json, delta_caja, status, created_at)
            VALUES (?, 'GUARDAR_CONTEO_VITRINA', ?, ?, 0, 'PENDING', ?)
            """,
            (op_uuid, f"VIT-{ahora.strftime('%Y%m%d%H%M%S')}", json.dumps(payload_sync, ensure_ascii=False), ahora_iso),
        )

        resumen_mov = f"Responsable: {responsable} · OK: {ok_count} · Falta: {falta_count} · Sobra: {sobra_count}"
        _registrar_movimiento(
            conn,
            accion="Conteo Vitrina",
            hab="Tienda",
            huesped=responsable,
            resumen=resumen_mov,
            detalle=resumen_detalles[:25],
            monto=0.0,
        )

    res_nuevo = obtener_datos_vitrina_local()
    res_nuevo["mensaje"] = f"¡El conteo de {responsable} ha sido guardado! (OK: {ok_count}, Falta: {falta_count}, Sobra: {sobra_count})"
    return res_nuevo


def registrar_compra_tienda_local(datos: Dict[str, Any]) -> Dict[str, Any]:
    _validar_clave_gestion_tienda(datos)
    compra_uuid_exist = str(datos.get("compraUuidExistente") or "").strip()
    fecha_raw = str(datos.get("fecha") or "").strip()
    fecha_dmy = normalizar_fecha_dmy(fecha_raw) or hoy_dmy()
    fecha_iso = dmy_a_iso(fecha_dmy)
    metodo_pago = str(datos.get("metodoPago") or "Efectivo").strip() or "Efectivo"
    responsable = str(datos.get("responsable") or "ELEIZA").strip().upper() or "ELEIZA"
    tipo_comp = str(datos.get("tipoComp") or "Boleta").strip() or "Boleta"
    nro_comp = str(datos.get("nroComp") or "").strip()
    nota = str(datos.get("nota") or "").strip()
    adic_comp = round(max(0.0, parse_moneda(datos.get("adicComp"))), 2)
    enviar_a_gastos = bool(datos.get("enviarAGastos", True))
    directo_vitrina = bool(datos.get("directoAVitrina", False))

    raw_items = datos.get("items") or []
    items_limpios: List[Dict[str, Any]] = []
    suma_costos = 0.0
    for it in raw_items:
        prod = str((it or {}).get("producto") or "").strip()
        if not prod:
            continue
        cant = round(parse_moneda((it or {}).get("cantidad")), 2)
        costo_t = round(parse_moneda((it or {}).get("costoTotal")), 2)
        if cant <= 0 and costo_t <= 0:
            continue
        if cant <= 0 and costo_t > 0:
            raise ValueError(f"Indicaste S/ {costo_t:.2f} en '{prod}', pero falta poner su Cantidad.")
        costo_u = round(costo_t / cant, 4) if cant > 0 else 0.0
        suma_costos = round(suma_costos + costo_t, 2)
        items_limpios.append({
            "producto": prod,
            "cantidad": cant,
            "costoTotal": costo_t,
            "costoUnitario": costo_u,
        })

    total_sustentado = round(suma_costos + adic_comp, 2)

    ahora = datetime.now()
    ahora_iso = ahora.isoformat()
    id_tx_str = ahora.strftime("%d/%m/%Y %H:%M:%S")
    hora_reg = ahora.strftime("%H:%M")

    with db_transaction() as conn:
        movs_para_sync: List[Dict[str, Any]] = []
        detalles_hist: List[Dict[str, str]] = []

        if compra_uuid_exist:
            # --- CONTINUACION DE DESGLOSE DE UNA COMPRA PENDIENTE ---
            lote_row = conn.execute(
                "SELECT * FROM compras_tienda_lotes WHERE compra_uuid = ?",
                (compra_uuid_exist,),
            ).fetchone()
            if not lote_row:
                raise ValueError("No se encontr\u00f3 la compra pendiente seleccionada.")
            if not items_limpios and adic_comp <= 0:
                raise ValueError("Escribe al menos un producto o el Adicional por Comprobante (IGV/Percepci\u00f3n) para avanzar el desglose.")

            m_tot_lote = round(float(lote_row["monto_total"] or 0), 2)
            m_des_prev = round(float(lote_row["monto_desglosado"] or 0), 2)
            disponible = round(max(0.0, m_tot_lote - m_des_prev), 2)
            if m_tot_lote > 0 and total_sustentado > disponible + 0.05:
                raise ValueError(
                    f"El desglose + IGV/Percepci\u00f3n (S/ {total_sustentado:.2f}) supera el saldo pendiente por desglosar (S/ {disponible:.2f})."
                )

            compra_uuid = compra_uuid_exist
            fecha_dmy = str(lote_row["fecha_dmy"] or fecha_dmy)
            metodo_pago = str(lote_row["metodo_pago"] or metodo_pago)
            nuevo_desg = round(m_des_prev + total_sustentado, 2)
            nuevo_estado = "COMPLETADO" if (m_tot_lote <= 0 or nuevo_desg >= m_tot_lote - 0.02) else "PENDIENTE"

            conn.execute(
                """
                UPDATE compras_tienda_lotes
                SET monto_desglosado = ?, estado = ?, updated_at = ?
                WHERE compra_uuid = ?
                """,
                (nuevo_desg, nuevo_estado, ahora_iso, compra_uuid),
            )
            falta_final = round(max(0.0, m_tot_lote - nuevo_desg), 2)
            extra_adic_txt = f" (incl. S/ {adic_comp:.2f} IGV/Percep.)" if adic_comp > 0 else ""
            msg_gasto = (
                f"Desglose completado al 100% (S/ {m_tot_lote:.2f}){extra_adic_txt}."
                if nuevo_estado == "COMPLETADO"
                else f"Avance guardado{extra_adic_txt}. Falta desglosar S/ {falta_final:.2f} de S/ {m_tot_lote:.2f}."
            )
        else:
            # --- COMPRA NUEVA ---
            monto_total_in = round(parse_moneda(datos.get("montoTotal")), 2)
            if monto_total_in <= 0 and total_sustentado > 0:
                monto_total_in = total_sustentado

            if monto_total_in <= 0 and not items_limpios:
                raise ValueError("Ingresa el Monto Total de la compra o al menos un producto con cantidad.")

            if monto_total_in > 0 and total_sustentado > monto_total_in + 0.05:
                raise ValueError(
                    f"La suma de productos (S/ {suma_costos:.2f}) + Adic. Comprobante (S/ {adic_comp:.2f}) = S/ {total_sustentado:.2f} excede el Monto Total (S/ {monto_total_in:.2f})."
                )

            compra_uuid = str(uuid.uuid4())
            gasto_uuid = ""
            if enviar_a_gastos and monto_total_in > 0:
                gasto_uuid = str(uuid.uuid4())
                resumen_prods = ", ".join(
                    f"{_fmt_num_stock(x['cantidad'])} {x['producto']}"
                    + (f" (S/{x['costoTotal']:g})" if x["costoTotal"] > 0 else "")
                    for x in items_limpios[:8]
                )
                if len(items_limpios) > 8:
                    resumen_prods += f" (+{len(items_limpios) - 8} m\u00e1s)"
                if adic_comp > 0:
                    resumen_prods = (resumen_prods + f" + IGV/Percep. S/{adic_comp:g}").strip(" +")
                if not resumen_prods:
                    desc_gasto = f"Compra Tienda (Pendiente desglose){' - ' + nota if nota else ''}"
                elif total_sustentado < monto_total_in - 0.02:
                    desc_gasto = f"Compra Tienda (Parcial S/{total_sustentado:g} de S/{monto_total_in:g}): {resumen_prods}"
                else:
                    desc_gasto = f"Compra Tienda: {resumen_prods}"
                if nota and nota not in desc_gasto:
                    desc_gasto = f"{desc_gasto} | {nota}"

                cat_tienda = "Compras para Tienda"
                conn.execute(
                    """
                    INSERT INTO gastos (
                        gasto_uuid, id_ts_str, fecha_gasto_iso, fecha_gasto_dmy, hora_registro,
                        descripcion, monto, categoria, metodo_pago, tipo_comp, nro_comp, responsable, synced
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                    """,
                    (
                        gasto_uuid,
                        id_tx_str,
                        fecha_iso,
                        fecha_dmy,
                        hora_reg,
                        desc_gasto[:240],
                        monto_total_in,
                        cat_tienda,
                        metodo_pago,
                        tipo_comp,
                        nro_comp,
                        responsable,
                    ),
                )
                delta_caja = -monto_total_in if metodo_pago == "Efectivo" else 0.0
                _encolar_operacion(
                    conn,
                    "GASTO",
                    gasto_uuid,
                    {
                        "gasto_uuid": gasto_uuid,
                        "id_ts_str": id_tx_str,
                        "fila_gasto": [
                            id_tx_str,
                            fecha_dmy,
                            desc_gasto[:240],
                            monto_total_in,
                            cat_tienda,
                            metodo_pago,
                            tipo_comp,
                            nro_comp,
                            responsable,
                        ],
                    },
                    delta_caja=delta_caja,
                )

            estado_lote = (
                "PENDIENTE"
                if (monto_total_in > 0 and total_sustentado < monto_total_in - 0.02)
                else "COMPLETADO"
            )
            nota_lote = f"{nota} [IGV/Percep: S/ {adic_comp:.2f}]".strip() if adic_comp > 0 else nota
            conn.execute(
                """
                INSERT INTO compras_tienda_lotes (
                    compra_uuid, fecha_dmy, monto_total, monto_desglosado, metodo_pago,
                    responsable, tipo_comp, nro_comp, nota, gasto_uuid, estado, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    compra_uuid,
                    fecha_dmy,
                    monto_total_in,
                    total_sustentado,
                    metodo_pago,
                    responsable,
                    tipo_comp,
                    nro_comp,
                    nota_lote,
                    gasto_uuid,
                    estado_lote,
                    ahora_iso,
                    ahora_iso,
                ),
            )
            falta_final = round(max(0.0, monto_total_in - total_sustentado), 2)
            txt_g = f" Gasto Total S/ {monto_total_in:.2f} enviado a Registro_Gastos" + (f" (Desglose Venta S/ {suma_costos:.2f} + IGV/Percep S/ {adic_comp:.2f})." if adic_comp > 0 else ".") if gasto_uuid else ""
            if estado_lote == "PENDIENTE":
                msg_gasto = f"Compra registrada.{txt_g} Queda pendiente desglosar S/ {falta_final:.2f}."
            else:
                msg_gasto = f"Compra registrada al 100%.{txt_g}"

        # Insertar movimientos de almacén por cada ítem desglosado
        for it in items_limpios:
            mov_c_uuid = str(uuid.uuid4())
            nota_mov = nota or ("Compra + Directo Vitrina" if directo_vitrina else "Compra Tienda")
            conn.execute(
                """
                INSERT INTO almacen_movimientos (
                    mov_uuid, lote_uuid, id_transaccion, fecha_dmy, producto,
                    cantidad, costo_total, costo_unitario, metodo_pago, tipo_movimiento, nota, synced
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'COMPRA', ?, 0)
                """,
                (
                    mov_c_uuid,
                    compra_uuid,
                    id_tx_str,
                    fecha_dmy,
                    it["producto"],
                    it["cantidad"],
                    it["costoTotal"],
                    it["costoUnitario"],
                    metodo_pago,
                    nota_mov,
                ),
            )
            movs_para_sync.append({
                "mov_uuid": mov_c_uuid,
                "fila": [
                    id_tx_str,
                    fecha_dmy,
                    it["producto"],
                    _fmt_num_stock(it["cantidad"]),
                    it["costoTotal"],
                    it["costoUnitario"],
                    metodo_pago,
                    "COMPRA",
                    nota_mov,
                    mov_c_uuid,
                ],
            })
            detalles_hist.append({
                "campo": it["producto"],
                "antes": "Compra",
                "despues": f"+{_fmt_num_stock(it['cantidad'])} und." + (f" (S/ {it['costoTotal']:.2f})" if it["costoTotal"] > 0 else ""),
            })

            if directo_vitrina:
                mov_t_uuid = str(uuid.uuid4())
                conn.execute(
                    """
                    INSERT INTO almacen_movimientos (
                        mov_uuid, lote_uuid, id_transaccion, fecha_dmy, producto,
                        cantidad, costo_total, costo_unitario, metodo_pago, tipo_movimiento, nota, synced
                    ) VALUES (?, ?, ?, ?, ?, ?, 0, 0, 'N/A', 'TRANSFERENCIA', 'Ingreso directo a Vitrina', 0)
                    """,
                    (mov_t_uuid, compra_uuid, id_tx_str, fecha_dmy, it["producto"], it["cantidad"]),
                )
                movs_para_sync.append({
                    "mov_uuid": mov_t_uuid,
                    "fila": [
                        id_tx_str,
                        fecha_dmy,
                        it["producto"],
                        _fmt_num_stock(it["cantidad"]),
                        0,
                        0,
                        "N/A",
                        "TRANSFERENCIA",
                        "Ingreso directo a Vitrina",
                        mov_t_uuid,
                    ],
                })

        if movs_para_sync:
            conn.execute(
                """
                INSERT INTO sync_queue (op_uuid, op_type, entity_uuid, payload_json, delta_caja, status, created_at)
                VALUES (?, 'MOVIMIENTO_ALMACEN', ?, ?, 0, 'PENDING', ?)
                """,
                (
                    str(uuid.uuid4()),
                    compra_uuid,
                    json.dumps({"movimientos": movs_para_sync}, ensure_ascii=False),
                    ahora_iso,
                ),
            )

        if adic_comp > 0:
            detalles_hist.append({
                "campo": "IGV / Percepción (Comprobante)",
                "antes": f"Subtotal Prod: S/ {suma_costos:.2f}",
                "despues": f"+S/ {adic_comp:.2f} -> Total Gasto: S/ {total_sustentado:.2f}",
            })
        _registrar_movimiento(
            conn,
            accion="Compra Tienda",
            hab="Tienda",
            huesped=responsable,
            resumen=msg_gasto,
            detalle=detalles_hist[:25],
            monto=total_sustentado,
        )

    res = obtener_datos_vitrina_local()
    res["mensaje"] = f"✅ {msg_gasto}"
    return res


def cerrar_compra_pendiente_tienda_local(datos: Dict[str, Any]) -> Dict[str, Any]:
    _validar_clave_gestion_tienda(datos)
    compra_uuid = str(datos.get("compraUuid") or "").strip()
    if not compra_uuid:
        raise ValueError("Falta identificar la compra pendiente.")
    with db_transaction() as conn:
        conn.execute(
            "UPDATE compras_tienda_lotes SET estado = 'COMPLETADO', updated_at = ? WHERE compra_uuid = ?",
            (datetime.now().isoformat(), compra_uuid),
        )
    res = obtener_datos_vitrina_local()
    res["mensaje"] = "✅ Compra marcada como completada."
    return res


def registrar_movimiento_tienda_local(datos: Dict[str, Any]) -> Dict[str, Any]:
    """
    Maneja Traspaso (Almacén -> Vitrina), Merma Vitrina, Merma Almacén y Ajuste de Stock Almacén.
    """
    _validar_clave_gestion_tienda(datos)
    modo = str(datos.get("modo") or "TRANSFERENCIA").strip().upper()
    if modo not in ("TRANSFERENCIA", "MERMA_VITRINA", "MERMA_ALMACEN", "AJUSTE_ALMACEN"):
        raise ValueError(f"Modo de movimiento inválido: {modo}")

    fecha_raw = str(datos.get("fecha") or "").strip()
    fecha_dmy = normalizar_fecha_dmy(fecha_raw) or hoy_dmy()
    responsable = str(datos.get("responsable") or "ELEIZA").strip().upper() or "ELEIZA"
    motivo = str(datos.get("nota") or "").strip()
    raw_items = datos.get("items") or []

    ahora = datetime.now()
    ahora_iso = ahora.isoformat()
    id_tx_str = ahora.strftime("%d/%m/%Y %H:%M:%S")
    lote_uuid = str(uuid.uuid4())

    with db_transaction() as conn:
        stock_alm_actual, _ = _calcular_stocks_almacen_y_deltas_conn(conn)
        movs_para_sync: List[Dict[str, Any]] = []
        detalles_hist: List[Dict[str, str]] = []

        for it in raw_items:
            prod = str((it or {}).get("producto") or "").strip()
            if not prod:
                continue
            raw_str = str((it or {}).get("cantidad") if (it or {}).get("cantidad") is not None else "").strip()
            if raw_str == "":
                continue
            try:
                val_num = round(float(raw_str), 2)
            except ValueError:
                continue

            if modo == "AJUSTE_ALMACEN":
                st_actual = round(stock_alm_actual.get(prod, 0.0), 2)
                diff = round(val_num - st_actual, 2)
                if abs(diff) < 0.001:
                    continue
                tipo_db = "AJUSTE_ALMACEN"
                cant_mov = diff
                nota_mov = f"[AJUSTE ALMACEN] De {_fmt_num_stock(st_actual)} a {_fmt_num_stock(val_num)}" + (f" · {motivo}" if motivo else "")
                detalles_hist.append({
                    "campo": prod,
                    "antes": f"Almacén: {_fmt_num_stock(st_actual)}",
                    "despues": f"Real: {_fmt_num_stock(val_num)} ({'+' if diff > 0 else ''}{_fmt_num_stock(diff)})",
                })
            else:
                if val_num <= 0:
                    continue
                cant_mov = val_num
                if modo == "TRANSFERENCIA":
                    tipo_db = "TRANSFERENCIA"
                    nota_mov = motivo or f"Traspaso a Vitrina ({responsable})"
                    detalles_hist.append({
                        "campo": prod,
                        "antes": f"Almacén: {_fmt_num_stock(stock_alm_actual.get(prod, 0.0))}",
                        "despues": f"Traspaso -> Vitrina: +{_fmt_num_stock(cant_mov)}",
                    })
                elif modo == "MERMA_VITRINA":
                    tipo_db = "MERMA"
                    nota_mov = f"[VITRINA] {motivo or 'Baja en vitrina'} ({responsable})"
                    detalles_hist.append({
                        "campo": prod,
                        "antes": "Vitrina",
                        "despues": f"Merma -{ _fmt_num_stock(cant_mov) }",
                    })
                else:  # MERMA_ALMACEN
                    tipo_db = "MERMA_ALMACEN"
                    nota_mov = f"[ALMACEN] {motivo or 'Baja en almacén'} ({responsable})"
                    detalles_hist.append({
                        "campo": prod,
                        "antes": "Almacén",
                        "despues": f"Merma -{_fmt_num_stock(cant_mov)}",
                    })

            mov_uuid = str(uuid.uuid4())
            conn.execute(
                """
                INSERT INTO almacen_movimientos (
                    mov_uuid, lote_uuid, id_transaccion, fecha_dmy, producto,
                    cantidad, costo_total, costo_unitario, metodo_pago, tipo_movimiento, nota, synced
                ) VALUES (?, ?, ?, ?, ?, ?, 0, 0, 'N/A', ?, ?, 0)
                """,
                (mov_uuid, lote_uuid, id_tx_str, fecha_dmy, prod, cant_mov, tipo_db, nota_mov),
            )
            movs_para_sync.append({
                "mov_uuid": mov_uuid,
                "fila": [
                    id_tx_str,
                    fecha_dmy,
                    prod,
                    _fmt_num_stock(cant_mov),
                    0,
                    0,
                    "N/A",
                    tipo_db,
                    nota_mov,
                    mov_uuid,
                ],
            })

        if not movs_para_sync:
            raise ValueError("Ingresa cantidades válidas en al menos un producto antes de guardar.")

        conn.execute(
            """
            INSERT INTO sync_queue (op_uuid, op_type, entity_uuid, payload_json, delta_caja, status, created_at)
            VALUES (?, 'MOVIMIENTO_ALMACEN', ?, ?, 0, 'PENDING', ?)
            """,
            (
                str(uuid.uuid4()),
                lote_uuid,
                json.dumps({"movimientos": movs_para_sync}, ensure_ascii=False),
                ahora_iso,
            ),
        )

        etiquetas = {
            "TRANSFERENCIA": "Traspaso a Vitrina",
            "MERMA_VITRINA": "Merma Vitrina",
            "MERMA_ALMACEN": "Merma Almacén",
            "AJUSTE_ALMACEN": "Ajuste Almacén",
        }
        nom_accion = etiquetas.get(modo, "Gestión Tienda")
        _registrar_movimiento(
            conn,
            accion=nom_accion,
            hab="Tienda",
            huesped=responsable,
            resumen=f"{nom_accion}: {len(movs_para_sync)} producto(s)" + (f" · {motivo}" if motivo else ""),
            detalle=detalles_hist[:25],
            monto=0.0,
        )

    res = obtener_datos_vitrina_local()
    res["mensaje"] = f"✅ {nom_accion} guardado ({len(movs_para_sync)} producto(s)). Stocks actualizados al instante."
    return res


def anular_movimiento_almacen_local(datos: Dict[str, Any]) -> Dict[str, Any]:
    _validar_clave_gestion_tienda(datos)
    mov_uuid = str(datos.get("movUuid") or "").strip()
    if not mov_uuid:
        raise ValueError("Falta identificar el movimiento a anular.")

    ahora_iso = datetime.now().isoformat()
    with db_transaction() as conn:
        row = conn.execute("SELECT * FROM almacen_movimientos WHERE mov_uuid = ?", (mov_uuid,)).fetchone()
        if not row:
            raise ValueError("El movimiento ya no existe.")
        prod = str(row["producto"] or "")
        cant = float(row["cantidad"] or 0)
        tipo = str(row["tipo_movimiento"] or "")
        costo_t = float(row["costo_total"] or 0)
        lote_u = str(row["lote_uuid"] or "")

        conn.execute("DELETE FROM almacen_movimientos WHERE mov_uuid = ?", (mov_uuid,))

        if lote_u and tipo == "COMPRA" and costo_t > 0:
            lote_r = conn.execute(
                "SELECT monto_total, monto_desglosado FROM compras_tienda_lotes WHERE compra_uuid = ?",
                (lote_u,),
            ).fetchone()
            if lote_r:
                nuevo_d = round(max(0.0, float(lote_r["monto_desglosado"] or 0) - costo_t), 2)
                m_tot = round(float(lote_r["monto_total"] or 0), 2)
                est_l = "PENDIENTE" if (m_tot > 0 and nuevo_d < m_tot - 0.02) else "COMPLETADO"
                conn.execute(
                    "UPDATE compras_tienda_lotes SET monto_desglosado = ?, estado = ?, updated_at = ? WHERE compra_uuid = ?",
                    (nuevo_d, est_l, ahora_iso, lote_u),
                )

        conn.execute(
            """
            INSERT INTO sync_queue (op_uuid, op_type, entity_uuid, payload_json, delta_caja, status, created_at)
            VALUES (?, 'ELIMINAR_MOVIMIENTO_ALMACEN', ?, ?, 0, 'PENDING', ?)
            """,
            (
                str(uuid.uuid4()),
                mov_uuid,
                json.dumps(
                    {
                        "mov_uuid": mov_uuid,
                        "sheet_row": int(row["sheet_row"] or 0),
                        "id_transaccion": str(row["id_transaccion"] or ""),
                        "producto": prod,
                        "tipo_movimiento": tipo,
                    },
                    ensure_ascii=False,
                ),
                ahora_iso,
            ),
        )

        _registrar_movimiento(
            conn,
            accion="Anular Mov. Tienda",
            hab="Tienda",
            huesped="ELEIZA",
            resumen=f"Anulado {tipo}: {_fmt_num_stock(cant)} {prod}",
            detalle=[],
            monto=0.0,
        )

    res = obtener_datos_vitrina_local()
    res["mensaje"] = f"✅ Movimiento anulado ({tipo}: {_fmt_num_stock(cant)} {prod})."
    return res



# ==============================================================================
# EMPRESAS (TERRAZAS): INGRESO GRUPAL + PAGO MASIVO CON ADELANTOS / DISTRIBUCION
# ==============================================================================

def guardar_checkin_grupal_empresa_local(datos: Dict[str, Any]) -> Dict[str, Any]:
    empresa = str(datos.get("empresa") or "").strip().upper()
    ruc = re.sub(r"\D", "", str(datos.get("ruc") or ""))
    proyecto = str(datos.get("proyecto") or "").strip()
    mot = str(datos.get("mot") or "Negocios y motivos profesionales").strip()
    items = datos.get("habitaciones") or []
    if not empresa:
        raise ValueError("La Empresa es obligatoria para un ingreso grupal.")
    if not items:
        raise ValueError("Selecciona al menos una habitación.")

    fecha_in_dmy = normalizar_fecha_dmy(datos.get("fechaIn")) or hoy_dmy()
    fecha_out_dmy = normalizar_fecha_dmy(datos.get("fechaOut")) or (datetime.now() + timedelta(days=1)).strftime("%d/%m/%Y")
    ymd_in = fecha_dmy_a_ymd(fecha_in_dmy)
    ymd_out = fecha_dmy_a_ymd(fecha_out_dmy) or (ymd_in + 1)
    if ymd_out <= ymd_in:
        raise ValueError("El Check-Out debe ser posterior al Check-In.")
    noches = calcular_noches(fecha_in_dmy, fecha_out_dmy)

    ahora = datetime.now()
    hora_reg = ahora.strftime("%H:%M")
    creadas: List[str] = []

    with db_transaction() as conn:
        habs_vistas = set()
        for it in items:
            hab = str(it.get("hab") or "").strip()
            if not hab or hab in habs_vistas:
                raise ValueError(f"Habitación inválida o repetida: {hab or '(vacía)'}")
            habs_vistas.add(hab)
            choque = conn.execute(
                """
                SELECT huesped FROM estancias
                WHERE hab = ? AND estado_estancia = 'En Curso' AND ymd_in < ? AND ymd_out > ?
                LIMIT 1
                """,
                (hab, ymd_out, ymd_in),
            ).fetchone()
            if choque:
                raise ValueError(f"La habitación {hab} ya está ocupada en esas fechas ({choque['huesped']}).")

        for i, it in enumerate(items):
            hab = str(it.get("hab") or "").strip()
            tarifa = parse_moneda(it.get("tarifa"))
            nombre = str(it.get("nombre") or "").strip().upper() or empresa
            nac = "Perú"
            dni = normalizar_dni(it.get("dni"), nac) if str(it.get("dni") or "").strip() else ""
            tipo_hab = str(it.get("tipoHab") or "").strip()
            if not tipo_hab:
                hc = conn.execute("SELECT tipo FROM habitaciones_config WHERE hab = ?", (hab,)).fetchone()
                tipo_hab = hc["tipo"] if hc else ""

            # id_timestamp_str único (resolución de segundos): se desplaza 1 s por habitación si choca
            seg = 0
            while True:
                id_ts_str = (ahora + timedelta(seconds=i + seg)).strftime("%d/%m/%Y %H:%M:%S")
                if not conn.execute("SELECT 1 FROM estancias WHERE id_timestamp_str = ?", (id_ts_str,)).fetchone():
                    break
                seg += 1

            estancia_uuid = str(uuid.uuid4())
            rec = {
                "estancia_uuid": estancia_uuid, "id_timestamp_str": id_ts_str, "sheet_row": 0, "parent_uuid": "",
                "fecha_in_dmy": fecha_in_dmy, "fecha_out_dmy": fecha_out_dmy, "ymd_in": ymd_in, "ymd_out": ymd_out,
                "hora_registro": hora_reg, "huesped": nombre, "dni": dni, "sexo": "M",
                "n_acomp": "", "dni_acomp": "", "nac": nac, "reg": "Lima", "mot": mot,
                "hab": hab, "tipo_hab": tipo_hab, "visita": "Visita N° 1", "noches": noches, "tarifa": tarifa,
                "adic_snacks": 0.0, "adic_servicios": 0.0, "adic_comp": 0.0,
                "pago_ini_efec": 0.0, "pago_ini_yape": 0.0, "pago_post_efec": 0.0, "pago_post_yape": 0.0, "pago_visa": 0.0,
                "deuda_anterior": 0.0, "cuenta_destino": "", "solicita_comp": "Sí", "tipo_comp": "Factura",
                "estado_comp": "Pendiente", "nro_comp": "", "monto_factura": 0.0, "estado_estancia": "En Curso",
                "empresa": empresa, "proyecto": proyecto, "triple1": "", "triple2": "", "hotel_apoyo": "",
                "observaciones": "", "nro_op_yape": "", "celular": "", "ruc": ruc,
            }
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
                    dirty, updated_at
                ) VALUES (
                    :estancia_uuid, :id_timestamp_str, 0, '',
                    :fecha_in_dmy, :fecha_out_dmy, :ymd_in, :ymd_out, :hora_registro,
                    :huesped, :dni, :sexo, :n_acomp, :dni_acomp, :nac, :reg, :mot,
                    :hab, :tipo_hab, :visita, :noches, :tarifa,
                    :adic_snacks, :adic_servicios, :adic_comp,
                    :pago_ini_efec, :pago_ini_yape, :pago_post_efec, :pago_post_yape, :pago_visa,
                    :deuda_anterior, :cuenta_destino, :solicita_comp, :tipo_comp, :estado_comp,
                    :nro_comp, :monto_factura, :estado_estancia, :empresa, :proyecto,
                    :triple1, :triple2, :hotel_apoyo, :observaciones, :nro_op_yape, :celular, :ruc,
                    1, :updated_at
                )
                """,
                {**rec, "updated_at": ahora.isoformat()},
            )
            _encolar_operacion(
                conn,
                "CHECKIN",
                estancia_uuid,
                {"id_timestamp_str": id_ts_str, "fila_estancia": construir_fila_sheets_estancia(rec), "pagos_rows": []},
                delta_caja=0.0,
            )
            creadas.append(hab)

        if ruc:
            conn.execute(
                """
                INSERT INTO empresas_padron (ruc, razon_social, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(ruc) DO UPDATE SET razon_social = excluded.razon_social, updated_at = excluded.updated_at
                """,
                (ruc, empresa, ahora.isoformat()),
            )

    return {"ok": True, "empresa": empresa, "habitaciones": creadas, "cantidad": len(creadas),
            "detalle": f"{len(creadas)} habitaciones registradas para {empresa}"}


def obtener_estancias_pago_masivo_local(filtros: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Todas las estancias de empresa con saldo pendiente (los filtros finos se aplican en el cliente)."""
    panel = obtener_datos_panel_local()
    estancias: List[Dict[str, Any]] = []
    resumen: Dict[str, Dict[str, Any]] = {}
    vistos = set()
    for st in panel["estanciasCache"]:
        empresa = str(st.get("empresa") or "").strip()
        saldo = round(float(st.get("saldo") or 0), 2)
        if not es_empresa_corporativa(empresa) or saldo <= 0:
            continue
        if st.get("esCancelada") or st.get("esInactiva") or st.get("esContinuacionCerrada"):
            continue
        if st["estanciaUuid"] in vistos:
            continue
        vistos.add(st["estanciaUuid"])
        estancias.append({
            "estanciaUuid": st["estanciaUuid"],
            "fila": st["fila"],
            "id": st["id"],
            "hab": st["hab"],
            "huesped": st["huesped"],
            "empresa": empresa.upper(),
            "proyecto": st.get("proyecto") or "",
            "fechaIn": st["checkIn"],
            "fechaOut": st["checkOut"],
            "ymdIn": st.get("ymdIn") or 0,
            "saldo": saldo,
            "pagado": round(float(st.get("pagado") or 0), 2),
        })
        r = resumen.setdefault(empresa.upper(), {"empresa": empresa.upper(), "deuda": 0.0, "habs": 0, "proyectos": []})
        r["deuda"] = round(r["deuda"] + saldo, 2)
        r["habs"] += 1
        pj = st.get("proyecto") or ""
        if pj and pj not in r["proyectos"]:
            r["proyectos"].append(pj)
    estancias.sort(key=lambda e: (e["ymdIn"], e["hab"]))
    return {"estancias": estancias, "empresas": sorted(resumen.values(), key=lambda r: r["empresa"])}


def guardar_pago_masivo_local(datos: Dict[str, Any]) -> Dict[str, Any]:
    items = datos.get("items") or []
    met = str(datos.get("metodo") or "Transferencia").strip()
    cuenta = str(datos.get("cuentaDestino") or "").strip()
    nro_op = str(datos.get("nroOp") or "").strip()
    tramo = str(datos.get("tramo") or "Pago").strip()
    empresa = str(datos.get("empresa") or "").strip()
    nro_fact = str(datos.get("nroFactura") or "").strip()
    if not items:
        raise ValueError("No hay habitaciones con monto a abonar.")
    if met not in ("Efectivo", "Yape", "Transferencia", "BCP", "Interbank", "BBVA", "VISA"):
        raise ValueError(f"Medio de pago no válido: {met}")
    # Nro de operación opcional (si no se proporciona se registra 'S/N' para no bloquear la operación)
    if not nro_op and met != "Efectivo":
        nro_op = "S/N"

    panel = obtener_datos_panel_local()
    saldos = {s["estanciaUuid"]: round(float(s.get("saldo") or 0), 2) for s in panel["estanciasCache"]}
    validos = []
    for it in items:
        monto = parse_moneda(it.get("monto"))
        if monto <= 0:
            continue
        uid = str(it.get("estanciaUuid") or "")
        if uid not in saldos:
            raise ValueError(f"Estancia no encontrada: {it.get('hab')}")
        if monto > saldos[uid] + 0.01:
            raise ValueError(f"Hab. {it.get('hab')}: el abono S/ {monto:.2f} supera el saldo S/ {saldos[uid]:.2f}.")
        validos.append((uid, str(it.get("hab") or ""), monto))
    if not validos:
        raise ValueError("No hay montos mayores a 0 para registrar.")

    concepto = f"{tramo} Empresa {empresa}".strip()
    if nro_op:
        concepto += f" [Op: {nro_op}]"
    if nro_fact:
        concepto += f" [Fact: {nro_fact}]"
    total = 0.0
    for uid, hab, monto in validos:
        guardar_pago_posterior_local({
            "estanciaUuid": uid,
            "hab": hab,
            "monto": monto,
            "concepto": concepto,
            "metodo": met,
            "nroOpYape": nro_op if met == "Yape" else "",
            "nroOpBanco": nro_op if met not in ("Efectivo", "Yape") else "",
            "cuentaDestino": cuenta if met not in ("Efectivo", "Yape") else "",
        })
        total = round(total + monto, 2)
    return {"ok": True, "cantidad": len(validos), "total": total,
            "detalle": f"{tramo}: S/ {total:.2f} distribuido en {len(validos)} habitaciones"}


# ==============================================================================
# VISTA BASE DE DATOS: 📋 REGISTRO DE ESTANCIAS (Consulta 0 ms + Edición con clave Admin)
# ==============================================================================

def obtener_registro_estancias_bd_local(datos: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    datos = datos or {}
    desde_raw = str(datos.get("desde") or "").strip()
    hasta_raw = str(datos.get("hasta") or "").strip()
    hab_f = str(datos.get("hab") or "").strip()
    tipo_f = str(datos.get("tipoHab") or "").strip()
    emp_f = str(datos.get("empresa") or "").strip()
    est_pago_f = str(datos.get("estadoPago") or "").strip()
    busq = str(datos.get("q") or "").strip().lower()
    limite = min(2000, max(50, int(datos.get("limite") or 600)))

    ymd_desde = fecha_dmy_a_ymd(normalizar_fecha_dmy(desde_raw)) if desde_raw else 0
    ymd_hasta = fecha_dmy_a_ymd(normalizar_fecha_dmy(hasta_raw)) if hasta_raw else 0

    conn = get_connection()
    try:
        # Catálogos únicos para los desplegables de filtro
        habs_cfg = [r["hab"] for r in conn.execute("SELECT hab FROM habitaciones_config ORDER BY orden").fetchall()]
        tipos_cfg = [
            r["tipo_hab"] for r in conn.execute(
                "SELECT DISTINCT tipo_hab FROM estancias WHERE tipo_hab != '' ORDER BY tipo_hab"
            ).fetchall()
        ]
        empresas_cfg = [
            r["empresa"] for r in conn.execute(
                "SELECT DISTINCT empresa FROM estancias WHERE trim(empresa) != '' ORDER BY empresa"
            ).fetchall()
        ]
        total_bd = conn.execute("SELECT COUNT(*) FROM estancias").fetchone()[0]

        where_clauses = ["1=1"]
        params: List[Any] = []
        if ymd_desde > 0:
            where_clauses.append("(ymd_in >= ? OR ymd_out > ?)")
            params.extend([ymd_desde, ymd_desde])
        if ymd_hasta > 0:
            where_clauses.append("ymd_in <= ?")
            params.append(ymd_hasta)
        if hab_f:
            where_clauses.append("hab = ?")
            params.append(hab_f)
        if tipo_f:
            where_clauses.append("tipo_hab = ? COLLATE NOCASE")
            params.append(tipo_f)
        if emp_f == "__CON_EMPRESA__":
            where_clauses.append("trim(empresa) != ''")
        elif emp_f:
            where_clauses.append("empresa = ? COLLATE NOCASE")
            params.append(emp_f)
        if busq:
            like_q = f"%{busq}%"
            where_clauses.append(
                "(lower(huesped) LIKE ? OR lower(dni) LIKE ? OR lower(n_acomp) LIKE ? OR lower(dni_acomp) LIKE ? "
                "OR lower(triple1) LIKE ? OR lower(triple2) LIKE ? OR lower(empresa) LIKE ? "
                "OR lower(nro_op_yape) LIKE ? OR lower(celular) LIKE ? OR lower(ruc) LIKE ? "
                "OR lower(id_timestamp_str) LIKE ? OR lower(tipo_adicional) LIKE ?)"
            )
            params.extend([like_q] * 12)

        sql = (
            "SELECT * FROM estancias WHERE "
            + " AND ".join(where_clauses)
            + " ORDER BY CASE WHEN sheet_row = 0 THEN 999999999 ELSE sheet_row END DESC, ymd_in DESC, rowid DESC LIMIT ?"
        )
        params.append(limite)

        filas_out = []
        hay_tercer_huesped = False
        tot_aloj = tot_adic = tot_cuenta = tot_pagado = tot_saldo = 0.0

        for r in conn.execute(sql, params).fetchall():
            noches = max(1, int(r["noches"] or 1))
            tarifa = round(float(r["tarifa"] or 0), 2)
            es_canc = "cancel" in str(r["estado_estancia"] or "").lower()
            subtotal = 0.0 if es_canc else round(noches * tarifa, 2)
            snacks = round(float(r["adic_snacks"] or 0), 2)
            servicios = round(float(r["adic_servicios"] or 0), 2)
            adic_comp = round(float(r["adic_comp"] or 0), 2)
            deuda_ant = round(float(r["deuda_anterior"] or 0), 2)
            total_cuenta = round(subtotal + snacks + servicios + adic_comp + deuda_ant, 2)

            p_ini_e = round(float(r["pago_ini_efec"] or 0), 2)
            p_ini_y = round(float(r["pago_ini_yape"] or 0), 2)
            p_post_e = round(float(r["pago_post_efec"] or 0), 2)
            p_post_y = round(float(r["pago_post_yape"] or 0), 2)
            p_visa = round(float(r["pago_visa"] or 0), 2)
            total_pagado = round(p_ini_e + p_ini_y + p_post_e + p_post_y + p_visa, 2)
            saldo_final = round(total_cuenta - total_pagado, 2)

            if es_canc:
                estado_an = "Cancelado"
                cod_an = "CANCELADO"
            elif total_cuenta <= 0.01 and total_pagado <= 0.01:
                estado_an = "Sin cargos"
                cod_an = "SIN_CARGOS"
            elif abs(saldo_final) <= 0.05:
                estado_an = "Pagos Completados"
                cod_an = "COMPLETADO"
            elif saldo_final > 0.05:
                estado_an = f"Falta S/ {saldo_final:.2f}"
                cod_an = "PENDIENTE"
            else:
                estado_an = f"Exceso S/ {abs(saldo_final):.2f}"
                cod_an = "EXCESO"

            if est_pago_f:
                if est_pago_f == "PENDIENTE" and cod_an != "PENDIENTE":
                    continue
                if est_pago_f == "COMPLETADO" and cod_an != "COMPLETADO":
                    continue
                if est_pago_f == "CANCELADO" and cod_an != "CANCELADO":
                    continue

            t1 = str(r["triple1"] or "").strip()
            t2 = str(r["triple2"] or "").strip()
            if t1 or t2:
                hay_tercer_huesped = True

            # Excedente efectivo (Col AZ)
            exc_efec = round(float(r["excedente_efec_sunat"] or 0), 2)
            tipo_adic = str(r["tipo_adicional"] if "tipo_adicional" in r.keys() else "").strip()

            tot_aloj = round(tot_aloj + subtotal, 2)
            tot_adic = round(tot_adic + snacks + servicios + adic_comp, 2)
            tot_cuenta = round(tot_cuenta + total_cuenta, 2)
            tot_pagado = round(tot_pagado + total_pagado, 2)
            if saldo_final > 0.05 and not es_canc:
                tot_saldo = round(tot_saldo + saldo_final, 2)

            filas_out.append({
                "estanciaUuid": r["estancia_uuid"],
                "fila": int(r["sheet_row"] or 0),
                "id": r["id_timestamp_str"],
                "fechaIn": r["fecha_in_dmy"],
                "fechaOut": r["fecha_out_dmy"],
                "huesped": r["huesped"],
                "dni": r["dni"],
                "nAcomp": r["n_acomp"],
                "dniAcomp": r["dni_acomp"],
                "triple1": t1,
                "triple2": t2,
                "hab": r["hab"],
                "tipoHab": r["tipo_hab"],
                "noches": noches,
                "tarifa": tarifa,
                "subtotal": subtotal,
                "snacks": snacks,
                "servicios": servicios,
                "adicComp": adic_comp,
                "tipoAdicional": tipo_adic,
                "totalCuenta": total_cuenta,
                "pEfecIni": p_ini_e,
                "pElecIni": p_ini_y,
                "pEfecPost": p_post_e,
                "pYapePost": p_post_y,
                "totalPagado": total_pagado,
                "saldoFinal": saldo_final,
                "deudaAnt": deuda_ant,
                "pVisa": p_visa,
                "estadoPagoAN": estado_an,
                "codPagoAN": cod_an,
                "estadoEstancia": r["estado_estancia"],
                "empresa": r["empresa"],
                "nroOpYape": r["nro_op_yape"],
                "celular": r["celular"],
                "ruc": r["ruc"],
                "excedenteEfec": exc_efec,
                "observaciones": r["observaciones"],
                "dirty": int(r["dirty"] or 0),
            })

        return {
            "filas": filas_out,
            "totalEnBD": total_bd,
            "hayTercerHuesped": hay_tercer_huesped,
            "habitaciones": habs_cfg,
            "tiposHab": tipos_cfg,
            "empresas": empresas_cfg,
            "resumen": {
                "cantidad": len(filas_out),
                "subtotalAloj": tot_aloj,
                "totalAdic": tot_adic,
                "totalCuenta": tot_cuenta,
                "totalPagado": tot_pagado,
                "saldoPendiente": tot_saldo,
            },
        }
    finally:
        conn.close()


def editar_estancia_bd_admin_local(datos: Dict[str, Any]) -> Dict[str, Any]:
    import horario_logic as hl
    hl.validar_clave_cuentas(datos)
    return modificar_estancia_local(datos)


def eliminar_estancia_bd_admin_local(datos: Dict[str, Any]) -> Dict[str, Any]:
    import horario_logic as hl
    hl.validar_clave_cuentas(datos)

    e_uuid = str(datos.get("estanciaUuid") or "").strip()
    id_ts = str(datos.get("id") or "").strip()
    fila_hint = int(datos.get("fila") or 0)

    if not e_uuid and not id_ts and fila_hint < 2:
        raise ValueError("Falta el identificador de la estancia a eliminar.")

    with db_transaction() as conn:
        actual = None
        if e_uuid:
            actual = conn.execute("SELECT * FROM estancias WHERE estancia_uuid = ?", (e_uuid,)).fetchone()
        if not actual and id_ts:
            actual = conn.execute("SELECT * FROM estancias WHERE id_timestamp_str = ?", (id_ts,)).fetchone()
        if not actual and fila_hint >= 2:
            actual = conn.execute("SELECT * FROM estancias WHERE sheet_row = ?", (fila_hint,)).fetchone()
        if not actual:
            raise ValueError("No se encontró la estancia en la base de datos interna.")

        est_uuid_real = actual["estancia_uuid"]
        id_ts_real = str(actual["id_timestamp_str"] or "").strip()
        s_row_real = int(actual["sheet_row"] or 0)

        efec_cobrado = round(float(actual["pago_ini_efec"] or 0) + float(actual["pago_post_efec"] or 0), 2)
        delta_caja = round(-efec_cobrado, 2)

        pagos_vinc = conn.execute(
            "SELECT pago_id, sheet_row FROM pagos WHERE (estancia_uuid != '' AND estancia_uuid = ?) OR (id_estancia != '' AND id_estancia = ?)",
            (est_uuid_real, id_ts_real),
        ).fetchall()
        lista_pagos_vinc = [{"pago_id": r["pago_id"], "sheet_row": int(r["sheet_row"] or 0)} for r in pagos_vinc]
        if lista_pagos_vinc:
            conn.execute(
                "DELETE FROM pagos WHERE (estancia_uuid != '' AND estancia_uuid = ?) OR (id_estancia != '' AND id_estancia = ?)",
                (est_uuid_real, id_ts_real),
            )
            for pv in sorted([p["sheet_row"] for p in lista_pagos_vinc if p["sheet_row"] >= 2], reverse=True):
                conn.execute("UPDATE pagos SET sheet_row = sheet_row - 1 WHERE sheet_row > ?", (pv,))

        conn.execute("DELETE FROM estancias WHERE estancia_uuid = ?", (est_uuid_real,))
        if s_row_real >= 3:
            conn.execute("UPDATE estancias SET sheet_row = sheet_row - 1 WHERE sheet_row > ?", (s_row_real,))

        _encolar_operacion(
            conn,
            "ELIMINAR_ESTANCIA_ADMIN",
            est_uuid_real,
            {
                "estancia_uuid": est_uuid_real,
                "id_timestamp_str": id_ts_real,
                "sheet_row": s_row_real,
                "pagos_vinculados": lista_pagos_vinc,
            },
            delta_caja=delta_caja,
        )

    return {"ok": True, "estanciaUuid": est_uuid_real}


def _parse_ts_hms_key(ts_str: str, fecha_dmy: str, sheet_row: int) -> tuple:
    """Clave de ordenamiento descendente: última anotación / fecha arriba (Fila 1)."""
    s = str(ts_str or "").strip()
    ymd_ts = 0
    hms_ts = 0
    if " " in s:
        p_d, p_t = s.split(" ", 1)
        ymd_ts = fecha_dmy_a_ymd(normalizar_fecha_dmy(p_d))
        partes_h = re.findall(r"\d+", p_t)
        if len(partes_h) >= 2:
            hh = int(partes_h[0])
            mm = int(partes_h[1])
            ss = int(partes_h[2]) if len(partes_h) >= 3 else 0
            hms_ts = hh * 10000 + mm * 100 + ss
    elif "/" in s or "-" in s:
        ymd_ts = fecha_dmy_a_ymd(normalizar_fecha_dmy(s))
    ymd_f = fecha_dmy_a_ymd(normalizar_fecha_dmy(fecha_dmy))
    ymd_efectivo = max(ymd_ts, ymd_f)
    fila_ord = 999999999 if int(sheet_row or 0) == 0 else int(sheet_row or 0)
    return (ymd_efectivo, hms_ts, fila_ord)


def obtener_registro_gastos_bd_local(datos: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Base de datos completa de Registro_Gastos con filtros en 0 ms y última anotación en Fila 1."""
    datos = datos or {}
    desde_raw = str(datos.get("desde") or "").strip()
    hasta_raw = str(datos.get("hasta") or "").strip()
    cat_f = str(datos.get("categoria") or "").strip()
    met_f = str(datos.get("metodoPago") or "").strip()
    resp_f = str(datos.get("responsable") or "").strip()
    pers_f = str(datos.get("personal") or "").strip()
    busq = str(datos.get("q") or "").strip().lower()
    limite = min(2500, max(50, int(datos.get("limite") or 1000)))

    ymd_desde = fecha_dmy_a_ymd(normalizar_fecha_dmy(desde_raw)) if desde_raw else 0
    ymd_hasta = fecha_dmy_a_ymd(normalizar_fecha_dmy(hasta_raw)) if hasta_raw else 0

    conn = get_connection()
    try:
        total_bd = conn.execute("SELECT COUNT(*) FROM gastos").fetchone()[0]
        cats_cfg = [
            r["clave"] for r in conn.execute(
                "SELECT clave FROM catalogos_local WHERE categoria = 'CAT_GASTO' ORDER BY orden"
            ).fetchall()
        ]
        for r in conn.execute("SELECT DISTINCT categoria FROM gastos WHERE trim(categoria) != '' ORDER BY categoria").fetchall():
            if r["categoria"] not in cats_cfg:
                cats_cfg.append(r["categoria"])

        mets_cfg = [
            r["clave"] for r in conn.execute(
                "SELECT clave FROM catalogos_local WHERE categoria = 'MET_GASTO' ORDER BY orden"
            ).fetchall()
        ]
        for r in conn.execute("SELECT DISTINCT metodo_pago FROM gastos WHERE trim(metodo_pago) != '' ORDER BY metodo_pago").fetchall():
            if r["metodo_pago"] not in mets_cfg:
                mets_cfg.append(r["metodo_pago"])

        resps_cfg = [
            r["responsable"] for r in conn.execute(
                "SELECT DISTINCT responsable FROM gastos WHERE trim(responsable) != '' ORDER BY responsable"
            ).fetchall()
        ]
        pers_cfg = [
            r["clave"] for r in conn.execute(
                "SELECT clave FROM catalogos_local WHERE categoria = 'PERSONAL' ORDER BY orden"
            ).fetchall()
        ]
        for r in conn.execute("SELECT DISTINCT personal FROM gastos WHERE trim(personal) != '' ORDER BY personal").fetchall():
            if r["personal"] not in pers_cfg:
                pers_cfg.append(r["personal"])

        where_clauses = ["1=1"]
        params: List[Any] = []
        if cat_f:
            where_clauses.append("categoria = ? COLLATE NOCASE")
            params.append(cat_f)
        if met_f:
            where_clauses.append("metodo_pago = ? COLLATE NOCASE")
            params.append(met_f)
        if resp_f:
            where_clauses.append("responsable = ? COLLATE NOCASE")
            params.append(resp_f)
        if pers_f == "__CON_PERSONAL__":
            where_clauses.append("trim(personal) != ''")
        elif pers_f:
            where_clauses.append("personal = ? COLLATE NOCASE")
            params.append(pers_f)
        if busq:
            like_q = f"%{busq}%"
            where_clauses.append(
                "(lower(descripcion) LIKE ? OR lower(categoria) LIKE ? OR lower(metodo_pago) LIKE ? "
                "OR lower(tipo_comp) LIKE ? OR lower(nro_comp) LIKE ? OR lower(responsable) LIKE ? "
                "OR lower(personal) LIKE ? OR lower(id_ts_str) LIKE ? OR lower(fecha_gasto_dmy) LIKE ?)"
            )
            params.extend([like_q] * 9)

        sql = (
            "SELECT * FROM gastos WHERE "
            + " AND ".join(where_clauses)
            + " ORDER BY CASE WHEN sheet_row = 0 THEN 999999999 ELSE sheet_row END DESC, rowid DESC"
        )
        rows_raw = conn.execute(sql, params).fetchall()

        candidatos = []
        for r in rows_raw:
            f_dmy = normalizar_fecha_dmy(r["fecha_gasto_dmy"])
            ymd_g = fecha_dmy_a_ymd(f_dmy)
            if ymd_desde > 0 and ymd_g > 0 and ymd_g < ymd_desde:
                continue
            if ymd_hasta > 0 and ymd_g > 0 and ymd_g > ymd_hasta:
                continue
            candidatos.append((r, f_dmy, _parse_ts_hms_key(r["id_ts_str"], f_dmy, r["sheet_row"])))

        # Ordenar: última fecha o anotación en la primera fila (arriba) y los anteriores hacia abajo
        candidatos.sort(key=lambda x: x[2], reverse=True)

        filas_out = []
        hay_personal = False
        tot_monto = tot_efec = tot_yape = tot_sueldos = tot_tienda = 0.0

        for r, f_dmy, _ in candidatos[:limite]:
            monto = round(float(r["monto"] or 0), 2)
            met = str(r["metodo_pago"] or "").strip()
            cat = str(r["categoria"] or "").strip()
            pers = str(r["personal"] if "personal" in r.keys() else "").strip()
            if pers:
                hay_personal = True

            tot_monto = round(tot_monto + monto, 2)
            if "efectivo" in met.lower():
                tot_efec = round(tot_efec + monto, 2)
            else:
                tot_yape = round(tot_yape + monto, 2)
            cat_l = cat.lower()
            if "sueldo" in cat_l or "adelanto" in cat_l or "planilla" in cat_l or "personal" in cat_l:
                tot_sueldos = round(tot_sueldos + monto, 2)
            elif "tienda" in cat_l:
                tot_tienda = round(tot_tienda + monto, 2)

            filas_out.append({
                "gastoUuid": r["gasto_uuid"],
                "fila": int(r["sheet_row"] or 0),
                "id": r["id_ts_str"],
                "fecha": f_dmy,
                "hora": r["hora_registro"] or "",
                "descripcion": r["descripcion"] or "",
                "monto": monto,
                "categoria": cat,
                "metodoPago": met,
                "tipoComp": r["tipo_comp"] or "",
                "nroComp": r["nro_comp"] or "",
                "responsable": r["responsable"] or "Admin",
                "personal": pers,
                "synced": int(r["synced"] or 0),
            })

        return {
            "filas": filas_out,
            "totalEnBD": total_bd,
            "hayPersonal": hay_personal,
            "categorias": cats_cfg,
            "metodosPago": mets_cfg,
            "responsables": resps_cfg,
            "listaPersonal": pers_cfg,
            "resumen": {
                "cantidad": len(filas_out),
                "totalMonto": tot_monto,
                "totalEfectivo": tot_efec,
                "totalYapeBanco": tot_yape,
                "totalSueldosAdelantos": tot_sueldos,
                "totalTienda": tot_tienda,
            },
        }
    finally:
        conn.close()


def editar_gasto_bd_admin_local(datos: Dict[str, Any]) -> Dict[str, Any]:
    import horario_logic as hl
    hl.validar_clave_cuentas(datos)

    g_uuid = str(datos.get("gastoUuid") or "").strip()
    if not g_uuid:
        raise ValueError("Falta el identificador del gasto a editar.")

    fecha_dmy = normalizar_fecha_dmy(datos.get("fecha")) or hoy_dmy()
    desc = str(datos.get("descripcion") or "").strip()
    monto = round(parse_moneda(datos.get("monto")), 2)
    cat = str(datos.get("categoria") or "").strip()
    met = str(datos.get("metodoPago") or "").strip()
    tipo_c = str(datos.get("tipoComp") or "").strip()
    nro_c = str(datos.get("nroComp") or "").strip()
    resp = str(datos.get("responsable") or "Admin").strip() or "Admin"
    pers = str(datos.get("personal") or "").strip()

    if not desc:
        raise ValueError("La descripción del gasto no puede quedar vacía.")
    if monto <= 0:
        raise ValueError("El monto del gasto debe ser mayor a 0.")

    with db_transaction() as conn:
        actual = conn.execute("SELECT * FROM gastos WHERE gasto_uuid = ?", (g_uuid,)).fetchone()
        if not actual:
            raise ValueError("No se encontró el gasto en la base de datos interna.")

        monto_prev = round(float(actual["monto"] or 0), 2)
        met_prev = str(actual["metodo_pago"] or "").strip()
        efec_prev = monto_prev if "efectivo" in met_prev.lower() else 0.0
        efec_nuevo = monto if "efectivo" in met.lower() else 0.0
        delta_caja = round(-(efec_nuevo - efec_prev), 2)

        conn.execute(
            """
            UPDATE gastos SET
                fecha_gasto_dmy = ?, descripcion = ?, monto = ?, categoria = ?,
                metodo_pago = ?, tipo_comp = ?, nro_comp = ?, responsable = ?,
                personal = ?, synced = 0
            WHERE gasto_uuid = ?
            """,
            (fecha_dmy, desc, monto, cat, met, tipo_c, nro_c, resp, pers, g_uuid),
        )

        _encolar_operacion(
            conn,
            "EDITAR_GASTO_ADMIN",
            g_uuid,
            {
                "gasto_uuid": g_uuid,
                "id_ts_str": actual["id_ts_str"],
                "sheet_row": int(actual["sheet_row"] or 0),
                "fila_bj": [fecha_dmy, desc, monto, cat, met, tipo_c, nro_c, resp, pers],
            },
            delta_caja=delta_caja,
        )

    return {"ok": True, "gastoUuid": g_uuid}


def eliminar_gasto_bd_admin_local(datos: Dict[str, Any]) -> Dict[str, Any]:
    import horario_logic as hl
    hl.validar_clave_cuentas(datos)

    g_uuid = str(datos.get("gastoUuid") or "").strip()
    if not g_uuid:
        raise ValueError("Falta el identificador del gasto a eliminar.")

    with db_transaction() as conn:
        actual = conn.execute("SELECT * FROM gastos WHERE gasto_uuid = ?", (g_uuid,)).fetchone()
        if not actual:
            raise ValueError("No se encontró el gasto en la base de datos interna.")

        monto_prev = round(float(actual["monto"] or 0), 2)
        met_prev = str(actual["metodo_pago"] or "").strip()
        delta_caja = monto_prev if "efectivo" in met_prev.lower() else 0.0

        s_row_g = int(actual["sheet_row"] or 0)
        conn.execute("DELETE FROM gastos WHERE gasto_uuid = ?", (g_uuid,))
        if s_row_g >= 2:
            conn.execute("UPDATE gastos SET sheet_row = sheet_row - 1 WHERE sheet_row > ?", (s_row_g,))
        _encolar_operacion(
            conn,
            "ELIMINAR_GASTO_ADMIN",
            g_uuid,
            {
                "gasto_uuid": g_uuid,
                "id_ts_str": actual["id_ts_str"],
                "descripcion": actual["descripcion"],
                "monto": monto_prev,
                "sheet_row": s_row_g,
            },
            delta_caja=delta_caja,
        )

    return {"ok": True, "gastoUuid": g_uuid}


def obtener_registro_ventas_tienda_bd_local(datos: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Base de datos completa de 2.Registro_Ventas_Tienda con filtros en 0 ms y última anotación en Fila 1."""
    datos = datos or {}
    desde_raw = str(datos.get("desde") or "").strip()
    hasta_raw = str(datos.get("hasta") or "").strip()
    prod_f = str(datos.get("producto") or "").strip()
    met_f = str(datos.get("metodoPago") or "").strip()
    hab_f = str(datos.get("hab") or "").strip()
    pers_f = str(datos.get("personal") or "").strip()
    busq = str(datos.get("q") or "").strip().lower()
    limite = min(2500, max(50, int(datos.get("limite") or 1000)))

    ymd_desde = fecha_dmy_a_ymd(normalizar_fecha_dmy(desde_raw)) if desde_raw else 0
    ymd_hasta = fecha_dmy_a_ymd(normalizar_fecha_dmy(hasta_raw)) if hasta_raw else 0

    conn = get_connection()
    try:
        total_bd = conn.execute("SELECT COUNT(*) FROM ventas_tienda").fetchone()[0]
        prods_cfg = [
            r["clave"] for r in conn.execute(
                "SELECT clave FROM catalogos_local WHERE categoria = 'PRODUCTO_TIENDA' ORDER BY orden"
            ).fetchall()
        ]
        for r in conn.execute("SELECT DISTINCT producto FROM ventas_tienda WHERE trim(producto) != '' ORDER BY producto").fetchall():
            if r["producto"] not in prods_cfg:
                prods_cfg.append(r["producto"])

        mets_cfg = ["Efectivo", "Yape", "VISA", "Cargar a Habitación", "Cargar a la Cuenta"]
        for r in conn.execute("SELECT DISTINCT metodo_pago FROM ventas_tienda WHERE trim(metodo_pago) != '' ORDER BY metodo_pago").fetchall():
            if r["metodo_pago"] not in mets_cfg:
                mets_cfg.append(r["metodo_pago"])

        habs_cfg = [r["hab"] for r in conn.execute("SELECT hab FROM habitaciones_config ORDER BY orden").fetchall()]
        pers_cfg = [
            r["clave"] for r in conn.execute(
                "SELECT clave FROM catalogos_local WHERE categoria = 'PERSONAL' ORDER BY orden"
            ).fetchall()
        ]
        for r in conn.execute("SELECT DISTINCT personal FROM ventas_tienda WHERE trim(personal) != '' ORDER BY personal").fetchall():
            if r["personal"] not in pers_cfg:
                pers_cfg.append(r["personal"])

        where_clauses = ["1=1"]
        params: List[Any] = []
        if prod_f:
            where_clauses.append("producto = ? COLLATE NOCASE")
            params.append(prod_f)
        if met_f:
            where_clauses.append("metodo_pago = ? COLLATE NOCASE")
            params.append(met_f)
        if hab_f == "__CON_HAB__":
            where_clauses.append("trim(hab) != ''")
        elif hab_f:
            where_clauses.append("hab = ?")
            params.append(hab_f)
        if pers_f == "__CON_PERSONAL__":
            where_clauses.append("trim(personal) != ''")
        elif pers_f:
            where_clauses.append("personal = ? COLLATE NOCASE")
            params.append(pers_f)
        if busq:
            like_q = f"%{busq}%"
            where_clauses.append(
                "(lower(producto) LIKE ? OR lower(metodo_pago) LIKE ? OR lower(hab) LIKE ? "
                "OR lower(personal) LIKE ? OR lower(nro_op_yape) LIKE ? OR lower(id_estancia) LIKE ? "
                "OR lower(fecha_iso) LIKE ? OR lower(fecha_dmy) LIKE ?)"
            )
            params.extend([like_q] * 8)

        sql = (
            "SELECT * FROM ventas_tienda WHERE "
            + " AND ".join(where_clauses)
            + " ORDER BY CASE WHEN sheet_row = 0 THEN 999999999 ELSE sheet_row END DESC, rowid DESC"
        )
        rows_raw = conn.execute(sql, params).fetchall()

        candidatos = []
        for r in rows_raw:
            f_dmy = normalizar_fecha_dmy(r["fecha_dmy"] or r["fecha_iso"])
            ymd_v = fecha_dmy_a_ymd(f_dmy)
            if ymd_desde > 0 and ymd_v > 0 and ymd_v < ymd_desde:
                continue
            if ymd_hasta > 0 and ymd_v > 0 and ymd_v > ymd_hasta:
                continue
            candidatos.append((r, f_dmy, _parse_ts_hms_key(r["fecha_iso"], f_dmy, r["sheet_row"])))

        # Ordenar: última fecha o anotación en la primera fila (arriba) y los anteriores hacia abajo
        candidatos.sort(key=lambda x: x[2], reverse=True)

        filas_out = []
        tot_unid = 0
        tot_venta = tot_efec = tot_yape_visa = tot_cargo_hab = tot_cargo_pers = 0.0

        for r, f_dmy, _ in candidatos[:limite]:
            cant = max(1, int(r["cantidad"] or 1))
            p_unit = round(float(r["precio_unit"] or 0), 2)
            sub = round(float(r["subtotal"] or 0), 2)
            met = str(r["metodo_pago"] or "").strip()
            hab_v = str(r["hab"] or "").strip()
            pers_v = str(r["personal"] or "").strip()

            tot_unid += cant
            tot_venta = round(tot_venta + sub, 2)
            met_l = met.lower()
            if "efectivo" in met_l:
                tot_efec = round(tot_efec + sub, 2)
            elif "habitaci" in met_l or (hab_v and "cuenta" in met_l and not pers_v):
                tot_cargo_hab = round(tot_cargo_hab + sub, 2)
            elif "cuenta" in met_l or pers_v:
                tot_cargo_pers = round(tot_cargo_pers + sub, 2)
            else:
                tot_yape_visa = round(tot_yape_visa + sub, 2)

            filas_out.append({
                "ventaUuid": r["venta_uuid"],
                "fila": int(r["sheet_row"] or 0),
                "id": r["fecha_iso"] or f_dmy,
                "fecha": f_dmy,
                "producto": r["producto"] or "",
                "cantidad": cant,
                "precioUnit": p_unit,
                "subtotal": sub,
                "metodoPago": met,
                "hab": hab_v,
                "personal": pers_v,
                "idEstancia": r["id_estancia"] or "",
                "nroOpYape": r["nro_op_yape"] or "",
                "synced": int(r["synced"] or 0),
            })

        return {
            "filas": filas_out,
            "totalEnBD": total_bd,
            "productos": prods_cfg,
            "metodosPago": mets_cfg,
            "habitaciones": habs_cfg,
            "listaPersonal": pers_cfg,
            "resumen": {
                "cantidadFilas": len(filas_out),
                "totalUnidades": tot_unid,
                "totalVenta": tot_venta,
                "totalEfectivo": tot_efec,
                "totalYapeVisa": tot_yape_visa,
                "totalCargoHab": tot_cargo_hab,
                "totalCargoPersonal": tot_cargo_pers,
            },
        }
    finally:
        conn.close()


def editar_venta_tienda_bd_admin_local(datos: Dict[str, Any]) -> Dict[str, Any]:
    import horario_logic as hl
    hl.validar_clave_cuentas(datos)

    v_uuid = str(datos.get("ventaUuid") or "").strip()
    if not v_uuid:
        raise ValueError("Falta el identificador de la venta a editar.")

    fecha_dmy = normalizar_fecha_dmy(datos.get("fecha")) or hoy_dmy()
    prod = str(datos.get("producto") or "").strip()
    cant = max(1, int(parse_moneda(datos.get("cantidad")) or 1))
    p_unit = round(parse_moneda(datos.get("precioUnit")), 2)
    sub_in = datos.get("subtotal")
    sub = round(parse_moneda(sub_in), 2) if sub_in not in (None, "") else round(cant * p_unit, 2)
    if abs(sub - round(cant * p_unit, 2)) > 0.05 and p_unit > 0:
        sub = round(cant * p_unit, 2)
    met = str(datos.get("metodoPago") or "").strip()
    hab = str(datos.get("hab") or "").strip()
    pers = str(datos.get("personal") or "").strip()
    nro_op = str(datos.get("nroOpYape") or "").strip()

    if not prod:
        raise ValueError("El producto no puede quedar vacío.")
    if sub <= 0:
        raise ValueError("El subtotal de la venta debe ser mayor a 0.")

    with db_transaction() as conn:
        actual = conn.execute("SELECT * FROM ventas_tienda WHERE venta_uuid = ?", (v_uuid,)).fetchone()
        if not actual:
            raise ValueError("No se encontró la venta en la base de datos interna.")

        sub_prev = round(float(actual["subtotal"] or 0), 2)
        met_prev = str(actual["metodo_pago"] or "").strip()
        efec_prev = sub_prev if "efectivo" in met_prev.lower() else 0.0
        efec_nuevo = sub if "efectivo" in met.lower() else 0.0
        delta_caja = round(efec_nuevo - efec_prev, 2)

        # Si estaba o está cargada a una habitación vinculada, ajustar adic_snacks en la estancia
        est_updates = None
        id_est = str(actual["id_estancia"] or "").strip()
        est_u = str(actual["estancia_uuid"] or "").strip()
        era_cargo_hab = met_prev in ("Cargar a Habitación", "Cargar a la Cuenta") and bool(actual["hab"])
        es_cargo_hab = met in ("Cargar a Habitación", "Cargar a la Cuenta") and bool(hab)
        delta_snacks = (sub if es_cargo_hab else 0.0) - (sub_prev if era_cargo_hab else 0.0)
        if abs(delta_snacks) > 0.005 and (est_u or id_est):
            st = None
            if est_u:
                st = conn.execute("SELECT * FROM estancias WHERE estancia_uuid = ?", (est_u,)).fetchone()
            if not st and id_est:
                st = conn.execute("SELECT * FROM estancias WHERE id_timestamp_str = ?", (id_est,)).fetchone()
            if st:
                snacks_new = max(0.0, round(float(st["adic_snacks"] or 0) + delta_snacks, 2))
                conn.execute(
                    "UPDATE estancias SET adic_snacks = ?, dirty = 1, updated_at = ? WHERE estancia_uuid = ?",
                    (snacks_new, datetime.now().isoformat(), st["estancia_uuid"]),
                )
                est_updates = {
                    "estancia_uuid": st["estancia_uuid"],
                    "id_timestamp_str": st["id_timestamp_str"],
                    "sheet_row": int(st["sheet_row"] or 0),
                    "adic_snacks": snacks_new,
                }

        conn.execute(
            """
            UPDATE ventas_tienda SET
                fecha_dmy = ?, producto = ?, cantidad = ?, metodo_pago = ?,
                hab = ?, personal = ?, precio_unit = ?, subtotal = ?, nro_op_yape = ?, synced = 0
            WHERE venta_uuid = ?
            """,
            (fecha_dmy, prod, cant, met, hab, pers, p_unit, sub, nro_op, v_uuid),
        )

        _encolar_operacion(
            conn,
            "EDITAR_VENTA_TIENDA_ADMIN",
            v_uuid,
            {
                "venta_uuid": v_uuid,
                "fecha_iso": actual["fecha_iso"],
                "producto_prev": actual["producto"],
                "subtotal_prev": sub_prev,
                "sheet_row": int(actual["sheet_row"] or 0),
                "fila_bi": [fecha_dmy, prod, cant, met, hab, pers, p_unit, sub],
                "nro_op_yape": nro_op,
                "estancia_updates": est_updates,
            },
            delta_caja=delta_caja,
        )

    return {"ok": True, "ventaUuid": v_uuid}


def eliminar_venta_tienda_bd_admin_local(datos: Dict[str, Any]) -> Dict[str, Any]:
    import horario_logic as hl
    hl.validar_clave_cuentas(datos)

    v_uuid = str(datos.get("ventaUuid") or "").strip()
    if not v_uuid:
        raise ValueError("Falta el identificador de la venta a eliminar.")

    with db_transaction() as conn:
        actual = conn.execute("SELECT * FROM ventas_tienda WHERE venta_uuid = ?", (v_uuid,)).fetchone()
        if not actual:
            raise ValueError("No se encontró la venta en la base de datos interna.")

        sub_prev = round(float(actual["subtotal"] or 0), 2)
        met_prev = str(actual["metodo_pago"] or "").strip()
        delta_caja = -sub_prev if "efectivo" in met_prev.lower() else 0.0

        est_updates = None
        id_est = str(actual["id_estancia"] or "").strip()
        est_u = str(actual["estancia_uuid"] or "").strip()
        era_cargo_hab = met_prev in ("Cargar a Habitación", "Cargar a la Cuenta") and bool(actual["hab"])
        if era_cargo_hab and sub_prev > 0 and (est_u or id_est):
            st = None
            if est_u:
                st = conn.execute("SELECT * FROM estancias WHERE estancia_uuid = ?", (est_u,)).fetchone()
            if not st and id_est:
                st = conn.execute("SELECT * FROM estancias WHERE id_timestamp_str = ?", (id_est,)).fetchone()
            if st:
                snacks_new = max(0.0, round(float(st["adic_snacks"] or 0) - sub_prev, 2))
                conn.execute(
                    "UPDATE estancias SET adic_snacks = ?, dirty = 1, updated_at = ? WHERE estancia_uuid = ?",
                    (snacks_new, datetime.now().isoformat(), st["estancia_uuid"]),
                )
                est_updates = {
                    "estancia_uuid": st["estancia_uuid"],
                    "id_timestamp_str": st["id_timestamp_str"],
                    "sheet_row": int(st["sheet_row"] or 0),
                    "adic_snacks": snacks_new,
                }

        s_row_vt = int(actual["sheet_row"] or 0)
        conn.execute("DELETE FROM ventas_tienda WHERE venta_uuid = ?", (v_uuid,))
        if s_row_vt >= 2:
            conn.execute("UPDATE ventas_tienda SET sheet_row = sheet_row - 1 WHERE sheet_row > ?", (s_row_vt,))
        _encolar_operacion(
            conn,
            "ELIMINAR_VENTA_TIENDA_ADMIN",
            v_uuid,
            {
                "venta_uuid": v_uuid,
                "fecha_iso": actual["fecha_iso"],
                "producto_prev": actual["producto"],
                "subtotal_prev": sub_prev,
                "sheet_row": s_row_vt,
                "estancia_updates": est_updates,
            },
            delta_caja=delta_caja,
        )

    return {"ok": True, "ventaUuid": v_uuid}





def _clasificar_columna_pago_estancia(medio: str, tipo_p: str) -> str:
    """Devuelve qué columna de estancias ('pago_ini_efec', 'pago_ini_yape', 'pago_post_efec', 'pago_post_yape', 'pago_visa') afecta un registro de pago."""
    m = str(medio or "").strip().lower()
    t = str(tipo_p or "").strip().lower()
    es_ini = "ini" in t
    if "efectivo" in m:
        return "pago_ini_efec" if es_ini else "pago_post_efec"
    if "visa" in m or "tarjeta" in m or "pos" in m or "transfer" in m or "bcp" in m or "bbva" in m or "interbank" in m:
        return "pago_visa"
    return "pago_ini_yape" if es_ini else "pago_post_yape"


def obtener_registro_pagos_bd_local(datos: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Base de datos completa de Registro_Pagos con filtros en 0 ms y última anotación en Fila 1."""
    datos = datos or {}
    desde_raw = str(datos.get("desde") or "").strip()
    hasta_raw = str(datos.get("hasta") or "").strip()
    hab_f = str(datos.get("hab") or "").strip()
    med_f = str(datos.get("medioPago") or "").strip()
    tip_f = str(datos.get("tipoPago") or "").strip()
    busq = str(datos.get("q") or "").strip().lower()
    limite = min(2500, max(50, int(datos.get("limite") or 1000)))

    ymd_desde = fecha_dmy_a_ymd(normalizar_fecha_dmy(desde_raw)) if desde_raw else 0
    ymd_hasta = fecha_dmy_a_ymd(normalizar_fecha_dmy(hasta_raw)) if hasta_raw else 0

    conn = get_connection()
    try:
        total_bd = conn.execute("SELECT COUNT(*) FROM pagos").fetchone()[0]
        habs_cfg = [r["hab"] for r in conn.execute("SELECT hab FROM habitaciones_config ORDER BY orden").fetchall()]
        medios_cfg = ["Efectivo", "Yape", "Plin", "VISA", "Transferencia"]
        for r in conn.execute("SELECT DISTINCT medio_pago FROM pagos WHERE trim(medio_pago) != '' ORDER BY medio_pago").fetchall():
            if r["medio_pago"] not in medios_cfg:
                medios_cfg.append(r["medio_pago"])
        tipos_cfg = ["Inicial", "Posterior", "Grupal/Masivo", "Extorno/Vuelto"]
        for r in conn.execute("SELECT DISTINCT tipo_pago FROM pagos WHERE trim(tipo_pago) != '' ORDER BY tipo_pago").fetchall():
            if r["tipo_pago"] not in tipos_cfg:
                tipos_cfg.append(r["tipo_pago"])

        where_clauses = ["1=1"]
        params: List[Any] = []
        if hab_f:
            where_clauses.append("hab = ?")
            params.append(hab_f)
        if med_f:
            where_clauses.append("medio_pago = ? COLLATE NOCASE")
            params.append(med_f)
        if tip_f:
            where_clauses.append("tipo_pago = ? COLLATE NOCASE")
            params.append(tip_f)
        if busq:
            like_q = f"%{busq}%"
            where_clauses.append(
                "(lower(huesped) LIKE ? OR lower(hab) LIKE ? OR lower(medio_pago) LIKE ? "
                "OR lower(tipo_pago) LIKE ? OR lower(concepto) LIKE ? OR lower(nro_operacion) LIKE ? "
                "OR lower(id_estancia) LIKE ? OR lower(fecha_iso) LIKE ? OR lower(pago_id) LIKE ?)"
            )
            params.extend([like_q] * 9)

        sql = (
            "SELECT * FROM pagos WHERE "
            + " AND ".join(where_clauses)
            + " ORDER BY CASE WHEN sheet_row = 0 THEN 999999999 ELSE sheet_row END DESC, rowid DESC"
        )
        rows_raw = conn.execute(sql, params).fetchall()

        candidatos = []
        for r in rows_raw:
            f_iso = str(r["fecha_iso"] or "").strip()
            p_fecha = f_iso.split(" ")[0] if " " in f_iso else f_iso
            f_dmy = normalizar_fecha_dmy(p_fecha)
            ymd_p = fecha_dmy_a_ymd(f_dmy)
            if ymd_desde > 0 and ymd_p > 0 and ymd_p < ymd_desde:
                continue
            if ymd_hasta > 0 and ymd_p > 0 and ymd_p > ymd_hasta:
                continue
            candidatos.append((r, f_dmy, _parse_ts_hms_key(f_iso, f_dmy, r["sheet_row"])))

        # Ordenar: última fecha o anotación en la primera fila (arriba)
        candidatos.sort(key=lambda x: x[2], reverse=True)

        filas_out = []
        tot_monto = tot_ini_efec = tot_ini_elec = tot_post_efec = tot_post_elec = 0.0

        for r, f_dmy, _ in candidatos[:limite]:
            monto = round(float(r["monto"] or 0), 2)
            med = str(r["medio_pago"] or "").strip()
            tip = str(r["tipo_pago"] or "").strip()
            col_dest = _clasificar_columna_pago_estancia(med, tip)

            tot_monto = round(tot_monto + monto, 2)
            if col_dest == "pago_ini_efec":
                tot_ini_efec = round(tot_ini_efec + monto, 2)
            elif col_dest == "pago_ini_yape":
                tot_ini_elec = round(tot_ini_elec + monto, 2)
            elif col_dest == "pago_post_efec":
                tot_post_efec = round(tot_post_efec + monto, 2)
            else:
                tot_post_elec = round(tot_post_elec + monto, 2)

            filas_out.append({
                "pagoId": r["pago_id"],
                "fila": int(r["sheet_row"] or 0),
                "fechaHora": r["fecha_iso"] or f_dmy,
                "fecha": f_dmy,
                "idEstancia": r["id_estancia"] or "",
                "hab": r["hab"] or "",
                "huesped": r["huesped"] or "",
                "monto": monto,
                "medioPago": med,
                "tipoPago": tip,
                "concepto": r["concepto"] or "",
                "nroOperacion": r["nro_operacion"] or "",
                "synced": int(r["synced"] or 0),
            })

        return {
            "filas": filas_out,
            "totalEnBD": total_bd,
            "habitaciones": habs_cfg,
            "mediosPago": medios_cfg,
            "tiposPago": tipos_cfg,
            "resumen": {
                "cantidad": len(filas_out),
                "totalMonto": tot_monto,
                "iniEfectivoW": tot_ini_efec,
                "iniElectronicoX": tot_ini_elec,
                "postEfectivoY": tot_post_efec,
                "postElectronicoZ": tot_post_elec,
            },
        }
    finally:
        conn.close()


def editar_pago_bd_admin_local(datos: Dict[str, Any]) -> Dict[str, Any]:
    import horario_logic as hl
    hl.validar_clave_cuentas(datos)

    pid = str(datos.get("pagoId") or "").strip()
    if not pid:
        raise ValueError("Falta el identificador del pago a editar.")

    fecha_hora = str(datos.get("fechaHora") or "").strip() or datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    id_est = str(datos.get("idEstancia") or "").strip()
    hab = str(datos.get("hab") or "").strip()
    huesped = str(datos.get("huesped") or "").strip()
    monto = round(parse_moneda(datos.get("monto")), 2)
    med = str(datos.get("medioPago") or "").strip()
    tip = str(datos.get("tipoPago") or "").strip()
    conc = str(datos.get("concepto") or "").strip()
    nro_op = str(datos.get("nroOperacion") or "").strip()

    with db_transaction() as conn:
        actual = conn.execute("SELECT * FROM pagos WHERE pago_id = ?", (pid,)).fetchone()
        if not actual:
            raise ValueError("No se encontró el pago en la base de datos interna.")

        monto_prev = round(float(actual["monto"] or 0), 2)
        med_prev = str(actual["medio_pago"] or "").strip()
        tip_prev = str(actual["tipo_pago"] or "").strip()
        efec_prev = monto_prev if "efectivo" in med_prev.lower() else 0.0
        efec_nuevo = monto if "efectivo" in med.lower() else 0.0
        delta_caja = round(efec_nuevo - efec_prev, 2)

        est_updates = None
        id_est_vinc = id_est or str(actual["id_estancia"] or "").strip()
        est_u_vinc = str(actual["estancia_uuid"] or "").strip()
        if id_est_vinc or est_u_vinc:
            st = None
            if est_u_vinc:
                st = conn.execute("SELECT * FROM estancias WHERE estancia_uuid = ?", (est_u_vinc,)).fetchone()
            if not st and id_est_vinc:
                st = conn.execute("SELECT * FROM estancias WHERE id_timestamp_str = ?", (id_est_vinc,)).fetchone()
            if st:
                cols_p = {
                    "pago_ini_efec": round(float(st["pago_ini_efec"] or 0), 2),
                    "pago_ini_yape": round(float(st["pago_ini_yape"] or 0), 2),
                    "pago_post_efec": round(float(st["pago_post_efec"] or 0), 2),
                    "pago_post_yape": round(float(st["pago_post_yape"] or 0), 2),
                    "pago_visa": round(float(st["pago_visa"] or 0), 2),
                }
                c_old = _clasificar_columna_pago_estancia(med_prev, tip_prev)
                c_new = _clasificar_columna_pago_estancia(med, tip)
                cols_p[c_old] = max(0.0, round(cols_p[c_old] - monto_prev, 2))
                cols_p[c_new] = max(0.0, round(cols_p[c_new] + monto, 2))
                conn.execute(
                    """
                    UPDATE estancias SET
                        pago_ini_efec = ?, pago_ini_yape = ?, pago_post_efec = ?, pago_post_yape = ?, pago_visa = ?,
                        dirty = 1, updated_at = ?
                    WHERE estancia_uuid = ?
                    """,
                    (
                        cols_p["pago_ini_efec"], cols_p["pago_ini_yape"],
                        cols_p["pago_post_efec"], cols_p["pago_post_yape"],
                        cols_p["pago_visa"], datetime.now().isoformat(), st["estancia_uuid"],
                    ),
                )
                est_updates = {
                    "estancia_uuid": st["estancia_uuid"],
                    "id_timestamp_str": st["id_timestamp_str"],
                    "sheet_row": int(st["sheet_row"] or 0),
                    "bloque_wz": [cols_p["pago_ini_efec"], cols_p["pago_ini_yape"], cols_p["pago_post_efec"], cols_p["pago_post_yape"]],
                    "pago_visa": cols_p["pago_visa"],
                }

        conn.execute(
            """
            UPDATE pagos SET
                fecha_iso = ?, id_estancia = ?, hab = ?, huesped = ?, monto = ?,
                medio_pago = ?, tipo_pago = ?, concepto = ?, nro_operacion = ?, synced = 0
            WHERE pago_id = ?
            """,
            (fecha_hora, id_est, hab, huesped, monto, med, tip, conc, nro_op, pid),
        )

        _encolar_operacion(
            conn,
            "EDITAR_PAGO_ADMIN",
            pid,
            {
                "pago_id": pid,
                "sheet_row": int(actual["sheet_row"] or 0),
                "fila_bj": [fecha_hora, id_est, hab, huesped, monto, med, tip, conc, nro_op],
                "estancia_updates": est_updates,
            },
            delta_caja=delta_caja,
        )

    return {"ok": True, "pagoId": pid}


def eliminar_pago_bd_admin_local(datos: Dict[str, Any]) -> Dict[str, Any]:
    import horario_logic as hl
    hl.validar_clave_cuentas(datos)

    pid = str(datos.get("pagoId") or "").strip()
    if not pid:
        raise ValueError("Falta el identificador del pago a eliminar.")

    with db_transaction() as conn:
        actual = conn.execute("SELECT * FROM pagos WHERE pago_id = ?", (pid,)).fetchone()
        if not actual:
            raise ValueError("No se encontró el pago en la base de datos interna.")

        monto_prev = round(float(actual["monto"] or 0), 2)
        med_prev = str(actual["medio_pago"] or "").strip()
        tip_prev = str(actual["tipo_pago"] or "").strip()
        delta_caja = -monto_prev if "efectivo" in med_prev.lower() else 0.0

        est_updates = None
        id_est_vinc = str(actual["id_estancia"] or "").strip()
        est_u_vinc = str(actual["estancia_uuid"] or "").strip()
        if id_est_vinc or est_u_vinc:
            st = None
            if est_u_vinc:
                st = conn.execute("SELECT * FROM estancias WHERE estancia_uuid = ?", (est_u_vinc,)).fetchone()
            if not st and id_est_vinc:
                st = conn.execute("SELECT * FROM estancias WHERE id_timestamp_str = ?", (id_est_vinc,)).fetchone()
            if st:
                cols_p = {
                    "pago_ini_efec": round(float(st["pago_ini_efec"] or 0), 2),
                    "pago_ini_yape": round(float(st["pago_ini_yape"] or 0), 2),
                    "pago_post_efec": round(float(st["pago_post_efec"] or 0), 2),
                    "pago_post_yape": round(float(st["pago_post_yape"] or 0), 2),
                    "pago_visa": round(float(st["pago_visa"] or 0), 2),
                }
                c_old = _clasificar_columna_pago_estancia(med_prev, tip_prev)
                cols_p[c_old] = max(0.0, round(cols_p[c_old] - monto_prev, 2))
                conn.execute(
                    """
                    UPDATE estancias SET
                        pago_ini_efec = ?, pago_ini_yape = ?, pago_post_efec = ?, pago_post_yape = ?, pago_visa = ?,
                        dirty = 1, updated_at = ?
                    WHERE estancia_uuid = ?
                    """,
                    (
                        cols_p["pago_ini_efec"], cols_p["pago_ini_yape"],
                        cols_p["pago_post_efec"], cols_p["pago_post_yape"],
                        cols_p["pago_visa"], datetime.now().isoformat(), st["estancia_uuid"],
                    ),
                )
                est_updates = {
                    "estancia_uuid": st["estancia_uuid"],
                    "id_timestamp_str": st["id_timestamp_str"],
                    "sheet_row": int(st["sheet_row"] or 0),
                    "bloque_wz": [cols_p["pago_ini_efec"], cols_p["pago_ini_yape"], cols_p["pago_post_efec"], cols_p["pago_post_yape"]],
                    "pago_visa": cols_p["pago_visa"],
                }

        s_row_p = int(actual["sheet_row"] or 0)
        conn.execute("DELETE FROM pagos WHERE pago_id = ?", (pid,))
        if s_row_p >= 2:
            conn.execute("UPDATE pagos SET sheet_row = sheet_row - 1 WHERE sheet_row > ?", (s_row_p,))
        _encolar_operacion(
            conn,
            "ELIMINAR_PAGO_ADMIN",
            pid,
            {
                "pago_id": pid,
                "sheet_row": s_row_p,
                "estancia_updates": est_updates,
            },
            delta_caja=delta_caja,
        )

    return {"ok": True, "pagoId": pid}


# ==============================================================================
# AUTO-INYECCIÓN EN CALIENTE DE RUTAS RPC PARA PROCESOS EN EJECUCIÓN SIN REINICIO
# ==============================================================================
def _inyectar_rutas_en_servidor_activo() -> None:
    try:
        import domain_logic as _dl_self
        from sunat_wsp_service import consultar_dni_local_o_reniec, consultar_ruc_local_o_sunat
        from fastapi import HTTPException
        from fastapi.responses import FileResponse

        for mod_name in ("main", "__main__"):
            m = _sys.modules.get(mod_name)
            if not m or not hasattr(m, "app"):
                continue
            app_inst = getattr(m, "app")
            if hasattr(m, "WRITE_METHODS") and isinstance(m.WRITE_METHODS, set):
                m.WRITE_METHODS.update({
                    "editarEstanciaBDAdmin", "eliminarEstanciaBDAdmin",
                    "editarGastoBDAdmin", "eliminarGastoBDAdmin",
                    "editarVentaTiendaBDAdmin", "eliminarVentaTiendaBDAdmin",
                    "editarPagoBDAdmin", "eliminarPagoBDAdmin",
                    "marcarAsistenciaHorario", "editarCeldaHorario",
                    "guardarPersonalConfig", "guardarLiquidacionHorario",
                    "guardarConteoVitrina", "registrarCompraTienda",
                    "cerrarCompraPendienteTienda", "registrarMovimientoTienda",
                    "anularMovimientoAlmacen", "guardarCheckInGrupalEmpresa",
                    "guardarPagoMasivo",
                })

            # Asegurar ruta /api/cuentas/pdf si el proceso era del 28/10
            rutas_existentes = { getattr(r, "path", "") for r in getattr(app_inst, "routes", []) }
            if "/api/cuentas/pdf" not in rutas_existentes:
                @app_inst.get("/api/cuentas/pdf")
                def _hot_pdf_cuenta(hab: str = "", modo: str = "ingreso", fecha: str = "", estanciaUuid: str = ""):
                    import pases_logic as pl
                    pdf_path = pl.generar_pdf_cuenta_hab_local({"hab": hab, "modo": modo, "fecha": fecha, "estanciaUuid": estanciaUuid})
                    return FileResponse(str(pdf_path), media_type="application/pdf", filename=pdf_path.name, headers={"Cache-Control": "no-store"})

            # Reemplazar el endpoint /api/rpc/{method_name} en la tabla de rutas de FastAPI en vivo
            def _nuevo_rpc_handler(method_name: str, body: Any):
                args = getattr(body, "args", None) or []
                try:
                    if method_name in ("obtenerDatosPanelDisponibilidad", "cambiarFechaPanelDisponibilidad"):
                        res = _dl_self.obtener_datos_panel_local(args[0] if len(args) > 0 and args[0] else None)
                    elif method_name == "obtenerVistaFormulario":
                        res = m._obtener_vista_formulario_local(args[0] if args else "")
                    elif method_name == "obtenerDatosInicialesCheckIn":
                        res = _dl_self.obtener_datos_iniciales_checkin_local()
                    elif method_name in ("consultarDniReniec", "consultarDocumentoPadron"):
                        res = consultar_dni_local_o_reniec(args[0] if args else "")
                    elif method_name in ("consultarRucSunat", "consultarRucPadron"):
                        res = consultar_ruc_local_o_sunat(args[0] if args else "")
                    elif method_name in ("guardarCheckInDesdeApp", "procesarCheckInApp"):
                        res = _dl_self.guardar_checkin_local(args[0] if args else {})
                    elif method_name in ("obtenerDatosPagosPosteriores", "obtenerEstanciasConDeuda"):
                        res = _dl_self.obtener_datos_pagos_posteriores_local()
                    elif method_name in ("guardarPagoPosteriorDesdeApp", "procesarPagoPosteriorApp"):
                        res = _dl_self.guardar_pago_posterior_local(args[0] if args else {})
                    elif method_name == "obtenerDatosTienda":
                        res = _dl_self.obtener_datos_tienda_local()
                    elif method_name in ("guardarVentaTiendaDesdeApp", "procesarVentaTiendaApp"):
                        res = _dl_self.guardar_venta_tienda_local(args[0] if args else {})
                    elif method_name == "obtenerDatosGastos":
                        res = _dl_self.obtener_datos_gastos_local()
                    elif method_name in ("guardarGastoDesdeApp", "procesarGastoApp"):
                        res = _dl_self.guardar_gasto_local(args[0] if args else {})
                    elif method_name in ("continuarEstanciaDesdeApp", "continuarEstanciaRapida"):
                        res = _dl_self.continuar_estancia_local(args[0] if args else "")
                    elif method_name == "liberarHabitacionDesdeApp":
                        res = _dl_self.liberar_habitacion_local(args[0] if args else {})
                    elif method_name == "cancelarEstanciaDesdeApp":
                        res = _dl_self.cancelar_estancia_local(args[0] if args else {})
                    elif method_name == "restaurarEstanciaDesdeApp":
                        res = _dl_self.restaurar_estancia_local(args[0] if args else {})
                    elif method_name == "obtenerEstanciaParaModificar":
                        res = _dl_self.obtener_estancia_para_modificar_local(args[0] if len(args) > 0 else "", args[1] if len(args) > 1 else None)
                    elif method_name in ("modificarEstanciaDesdeApp", "guardarModificacionEstancia"):
                        res = _dl_self.modificar_estancia_local(args[0] if args else {})
                    elif method_name == "actualizarEstadoComprobanteDesdePanel":
                        res = _dl_self.actualizar_comprobante_desde_panel_local(args[0] if args else {})
                    elif method_name == "obtenerRegistroPagos":
                        res = _dl_self.obtener_registro_pagos_local(args[0] if args else {})
                    elif method_name == "obtenerMovimientos":
                        res = _dl_self.obtener_movimientos_local(args[0] if args else {})
                    elif method_name == "obtenerEstanciasBD":
                        res = _dl_self.obtener_estancias_bd_local(args[0] if args else {})
                    elif method_name == "verificarClaveAdminBD":
                        res = _dl_self.verificar_clave_admin_bd_local(args[0] if args else {})
                    elif method_name == "editarEstanciaBDAdmin":
                        res = _dl_self.editar_estancia_bd_admin_local(args[0] if args else {})
                    elif method_name == "eliminarEstanciaBDAdmin":
                        res = _dl_self.eliminar_estancia_bd_admin_local(args[0] if args else {})
                    elif method_name == "obtenerGastosBD":
                        res = _dl_self.obtener_gastos_bd_local(args[0] if args else {})
                    elif method_name == "editarGastoBDAdmin":
                        res = _dl_self.editar_gasto_bd_admin_local(args[0] if args else {})
                    elif method_name == "eliminarGastoBDAdmin":
                        res = _dl_self.eliminar_gasto_bd_admin_local(args[0] if args else {})
                    elif method_name == "obtenerVentasTiendaBD":
                        res = _dl_self.obtener_ventas_tienda_bd_local(args[0] if args else {})
                    elif method_name == "editarVentaTiendaBDAdmin":
                        res = _dl_self.editar_venta_tienda_bd_admin_local(args[0] if args else {})
                    elif method_name == "eliminarVentaTiendaBDAdmin":
                        res = _dl_self.eliminar_venta_tienda_bd_admin_local(args[0] if args else {})
                    elif method_name == "obtenerPagosBD":
                        res = _dl_self.obtener_pagos_bd_local(args[0] if args else {})
                    elif method_name == "editarPagoBDAdmin":
                        res = _dl_self.editar_pago_bd_admin_local(args[0] if args else {})
                    elif method_name == "eliminarPagoBDAdmin":
                        res = _dl_self.eliminar_pago_bd_admin_local(args[0] if args else {})
                    elif method_name == "guardarColorFondoHabitacion":
                        res = _dl_self.guardar_color_habitacion_local(
                            args[0] if len(args) > 0 else "",
                            args[1] if len(args) > 1 else "",
                            args[2] if len(args) > 2 else None,
                            args[3] if len(args) > 3 else "",
                        )
                    elif method_name == "obtenerDatosVitrina":
                        res = _dl_self.obtener_datos_vitrina_local()
                    elif method_name == "guardarConteoVitrina":
                        res = _dl_self.guardar_conteo_vitrina_local(args[0] if args else {})
                    elif method_name == "verificarClaveGestionTienda":
                        res = _dl_self.verificar_clave_gestion_tienda_local(args[0] if args else {})
                    elif method_name == "registrarCompraTienda":
                        res = _dl_self.registrar_compra_tienda_local(args[0] if args else {})
                    elif method_name == "cerrarCompraPendienteTienda":
                        res = _dl_self.cerrar_compra_pendiente_tienda_local(args[0] if args else {})
                    elif method_name == "registrarMovimientoTienda":
                        res = _dl_self.registrar_movimiento_tienda_local(args[0] if args else {})
                    elif method_name == "anularMovimientoAlmacen":
                        res = _dl_self.anular_movimiento_almacen_local(args[0] if args else {})
                    elif method_name == "obtenerHorarioMes":
                        import horario_logic as hl
                        res = hl.obtener_horario_mes_local(args[0] if args else {})
                    elif method_name == "marcarAsistenciaHorario":
                        import horario_logic as hl
                        res = hl.marcar_asistencia_local(args[0] if args else {})
                    elif method_name == "editarCeldaHorario":
                        import horario_logic as hl
                        res = hl.editar_celda_horario_local(args[0] if args else {})
                    elif method_name == "verificarClaveCuentasHorario":
                        import horario_logic as hl
                        res = hl.verificar_clave_cuentas_horario_local(args[0] if args else {})
                    elif method_name == "obtenerLiquidacionHorario":
                        import horario_logic as hl
                        res = hl.obtener_liquidacion_horario_local(args[0] if args else {})
                    elif method_name == "obtenerAuditoriaHorario":
                        import horario_logic as hl
                        res = hl.obtener_auditoria_horario_local(args[0] if args else {})
                    elif method_name == "obtenerPersonalConfig":
                        import horario_logic as hl
                        res = hl.obtener_personal_config_local(args[0] if args else {})
                    elif method_name == "guardarPersonalConfig":
                        import horario_logic as hl
                        res = hl.guardar_personal_config_local(args[0] if args else {})
                    elif method_name == "guardarLiquidacionHorario":
                        import horario_logic as hl
                        res = hl.guardar_liquidacion_horario_local(args[0] if args else {})
                    elif method_name == "guardarCheckInGrupalEmpresa":
                        res = _dl_self.guardar_checkin_grupal_empresa_local(args[0] if args else {})
                    elif method_name == "obtenerEstanciasPagoMasivo":
                        res = _dl_self.obtener_estancias_pago_masivo_local(args[0] if args else {})
                    elif method_name == "guardarPagoMasivo":
                        res = _dl_self.guardar_pago_masivo_local(args[0] if args else {})
                    elif method_name == "obtenerCuentaDetalladaHab":
                        import pases_logic as pl
                        res = pl.obtener_cuenta_detallada_hab_local(args[0] if args else {})
                    else:
                        raise HTTPException(status_code=404, detail=f"Método RPC no soportado: {method_name}")

                    if hasattr(m, "WRITE_METHODS") and method_name in m.WRITE_METHODS and hasattr(m, "_sync_wakeup_event"):
                        m._sync_wakeup_event.set()

                    sync_st = _dl_self.obtener_estado_red_cola()
                    if isinstance(res, dict) and "syncStatus" not in res:
                        res["syncStatus"] = sync_st
                    return {"result": res, "syncStatus": sync_st}
                except HTTPException:
                    raise
                except Exception as exc:
                    raise HTTPException(status_code=400, detail=str(exc))

            for r in getattr(app_inst, "routes", []):
                if getattr(r, "path", "") == "/api/rpc/{method_name}":
                    r.endpoint = _nuevo_rpc_handler
                    if hasattr(r, "dependant") and hasattr(r.dependant, "call"):
                        r.dependant.call = _nuevo_rpc_handler
    except Exception:
        pass


_inyectar_rutas_en_servidor_activo()
