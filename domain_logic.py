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
except Exception:
    pass

from config import (
    COL_TIPO_ADICIONAL,
    COL_TIPO_ADICIONAL_IDX,
    FORMULA_SNACKS_EN_S2,
    NOMBRE_HOTEL,
    SPREADSHEET_ID,
)
from database import db_transaction, get_connection

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
        if _old_sh_id != SPREADSHEET_ID and hasattr(_se_mod, "_reset_client"):
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
                "empresa": str(r["empresa"] or "").strip(),
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
                saldo = float(st["saldo"] or 0)
                deuda_ant = float(st["deudaAnt"] or 0)
                continuo = bool(st["continuoEstancia"])
                es_vuelto = (saldo < 0) and (continuo or deuda_ant < 0)
                if saldo > 0 and (continuo or deuda_ant > 0):
                    estado_clave = "ocupado_deuda_continua"
                elif saldo > 0:
                    estado_clave = "ocupado_debe"
                else:
                    estado_clave = "ocupado_aldia"

                snacks_carg = float(st["snacks"] or 0)
                serv_carg = float(st["servicios"] or 0)
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
        return {
            "nombreHotel": NOMBRE_HOTEL,
            "coloresGlobales": colores_panel["globales"],
            "coloresPorFecha": colores_panel["porFecha"],
            "fechaHoyISO": fecha_hoy_iso_val,
            "fechaVisualizada": fecha_vis,
            "totalCaja": total_caja,
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

    fecha_dmy = normalizar_fecha_dmy(fecha_raw) or hoy_dmy()
    fecha_iso = dmy_a_iso(fecha_dmy)
    ahora = datetime.now()
    id_ts_str = ahora.strftime("%d/%m/%Y %H:%M:%S")
    hora_reg = ahora.strftime("%H:%M")
    gasto_uuid = str(uuid.uuid4())

    with db_transaction() as conn:
        conn.execute(
            """
            INSERT INTO gastos (
                gasto_uuid, id_ts_str, fecha_gasto_iso, fecha_gasto_dmy, hora_registro,
                descripcion, monto, categoria, metodo_pago, tipo_comp, nro_comp, responsable, synced
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (gasto_uuid, id_ts_str, fecha_iso, fecha_dmy, hora_reg, desc, monto, cat, met, tip, nro, resp),
        )

        delta_caja = -monto if met == "Efectivo" else 0.0
        _encolar_operacion(
            conn,
            "GASTO",
            gasto_uuid,
            {
                "gasto_uuid": gasto_uuid,
                "id_ts_str": id_ts_str,
                "fila_gasto": [id_ts_str, fecha_dmy, desc, monto, cat, met, tip, nro, resp],
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
        old = _buscar_estancia_local(
            conn,
            fila=int(datos.get("filaAnterior") or 0),
            id_busq=str(datos.get("idAnterior") or ""),
            estancia_uuid=str(datos.get("estanciaUuid") or ""),
            hab=str(datos.get("habAnterior") or datos.get("habNueva") or ""),
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
        if datos.get("deudaArrastre") not in (None, ""):
            deuda_arrastre = parse_moneda(datos.get("deudaArrastre"))
        else:
            deuda_arrastre = round(cargos_ant - pagos_ant, 2)

        ahora = datetime.now()
        id_ts_str = ahora.strftime("%d/%m/%Y %H:%M:%S")
        hora_reg = ahora.strftime("%H:%M")
        f_in_nueva = normalizar_fecha_dmy(datos.get("fechaInNueva")) or hoy_dmy()
        f_out_nueva = normalizar_fecha_dmy(datos.get("fechaOutNueva")) or (ahora + timedelta(days=1)).strftime("%d/%m/%Y")
        ymd_in = fecha_dmy_a_ymd(f_in_nueva)
        ymd_out = fecha_dmy_a_ymd(f_out_nueva) or (ymd_in + 1)
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
            "reg": r["reg"] or "Lima",
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
            "empresa": r["empresa"],
            "proyecto": r["proyecto"],
            "triple1": r["triple1"],
            "triple2": r["triple2"],
            "hotelApoyo": r["hotel_apoyo"],
            "observaciones": r["observaciones"],
            "nroOpYape": r["nro_op_yape"],
            "celular": r["celular"],
            "ruc": r["ruc"],
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
        servicios = num_val("servicios", float(actual["adic_servicios"] or 0))
        adic_comp = num_val("adicComp", float(actual["adic_comp"] or 0))

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
                adic_servicios = ?, adic_comp = ?,
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
                servicios, adic_comp,
                p_efec_ini, p_elec_ini, p_efec_post, p_yape_post, p_visa,
                solicita, tipo_c, estado_c, nro_comp, monto_fact,
                estado_est, deuda_ant,
                empresa, proyecto, triple1, triple2, hotel_apoyo,
                observaciones, nro_op_yape, celular, ruc,
                datetime.now().isoformat(), actual["estancia_uuid"],
            ),
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
                "bloque_su": [float(actual["adic_snacks"] or 0), servicios, adic_comp],
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

    with db_transaction() as conn:
        st = _buscar_estancia_local(
            conn,
            fila=int(datos.get("fila") or 0),
            id_busq=str(datos.get("id") or ""),
            estancia_uuid=str(datos.get("estanciaUuid") or ""),
            hab=hab_busq,
        )

        ruc_final = ruc_entrada if ruc_entrada else str(st["ruc"] or "").strip()
        emp_final = emp_entrada if emp_entrada else str(st["empresa"] or "").strip()

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

        conn.execute(
            """
            UPDATE estancias SET
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
                tipo_final, nuevo_estado, nro_final, monto_final, adic_final,
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
            },
            delta_caja=0.0,
        )

    return {
        "ok": True,
        "fila": int(st["sheet_row"] or 0),
        "estanciaUuid": st["estancia_uuid"],
        "hab": st["hab"],
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

        huespedes_dict = {}
        for p in conn.execute("SELECT * FROM huespedes_padron").fetchall():
            huespedes_dict[p["dni"]] = {
                "nombre": p["nombre"],
                "sexo": p["sexo"],
                "nac": p["nac"],
                "reg": p["reg"],
                "mot": p["mot"],
                "hab": p["hab_frec"],
                "tarifa": p["tarifa_frec"],
                "empresa": p["empresa"],
                "celular": p["celular"],
                "ruc": p["ruc"],
                "visitas": int(p["visitas"] or 1),
            }

        estancias_activas = []
        habs_ya_incluidas = set()
        for st in panel["estanciasCache"]:
            if not st.get("esInactiva") and not st.get("esContinuacionCerrada"):
                hab_key = str(st.get("hab") or "").strip()
                if not hab_key or hab_key in habs_ya_incluidas:
                    continue
                habs_ya_incluidas.add(hab_key)
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
            })

        return {
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


def obtener_datos_vitrina_local() -> Dict[str, Any]:
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
                st_actual = st_sheets - vend_local
                st_num = int(st_actual) if st_actual.is_integer() else round(st_actual, 2)
                productos_out.append({
                    "orden": int(r["orden"] or 0),
                    "producto": prod_nom,
                    "stockVitrina": st_num,
                    "estadoActual": str(r["estado_actual"] or ""),
                    "h1Cant": str(r["h1_cant"] or ""),
                    "h1Est": str(r["h1_est"] or ""),
                    "h2Cant": str(r["h2_cant"] or ""),
                    "h2Est": str(r["h2_est"] or ""),
                    "h3Cant": str(r["h3_cant"] or ""),
                    "h3Est": str(r["h3_est"] or ""),
                })
        else:
            for idx_p, rc in enumerate(
                conn.execute("SELECT clave FROM catalogos_local WHERE categoria = 'PRODUCTO_TIENDA' ORDER BY orden").fetchall()
            ):
                prod_nom = str(rc["clave"] or "").strip()
                if not prod_nom:
                    continue
                productos_out.append({
                    "orden": idx_p + 1,
                    "producto": prod_nom,
                    "stockVitrina": 0,
                    "estadoActual": "",
                    "h1Cant": "",
                    "h1Est": "",
                    "h2Cant": "",
                    "h2Est": "",
                    "h3Cant": "",
                    "h3Est": "",
                })

        # 3. Metadatos E2, E3 y encabezados H4, J4, L4
        meta_rows = conn.execute(
            "SELECT clave, valor FROM estado_sistema WHERE clave IN ('vitrina_responsable_e2', 'vitrina_fecha_e3', 'vitrina_h1_header', 'vitrina_h2_header', 'vitrina_h3_header')"
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

        return {
            "responsableE2": resp_e2,
            "fechaUltimoE3": meta.get("vitrina_fecha_e3", ""),
            "h1Header": _limpiar_header_vitrina(meta.get("vitrina_h1_header", ""), "Conteo 1 (Último)"),
            "h2Header": _limpiar_header_vitrina(meta.get("vitrina_h2_header", ""), "Conteo 2 (Anterior)"),
            "h3Header": _limpiar_header_vitrina(meta.get("vitrina_h3_header", ""), "Conteo 3 (Previo)"),
            "listaPersonal": personal,
            "productos": productos_out,
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
        # Desplazar encabezados H1 -> H2 -> H3
        row_h1 = conn.execute("SELECT valor FROM estado_sistema WHERE clave = 'vitrina_h1_header'").fetchone()
        row_h2 = conn.execute("SELECT valor FROM estado_sistema WHERE clave = 'vitrina_h2_header'").fetchone()
        old_h1 = str(row_h1["valor"] or "") if row_h1 else ""
        old_h2 = str(row_h2["valor"] or "") if row_h2 else ""

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
        _set_est("vitrina_h3_header", old_h2)
        _set_est("vitrina_h2_header", old_h1)
        _set_est("vitrina_h1_header", nuevo_h1_hdr)

        # Desplazar columnas en vitrina_local (h2 -> h3, h1 -> h2, nuevo -> h1)
        for p in prods_list:
            nom_p = p["producto"]
            it = items_map.get(nom_p) or {"cant": "", "estado": ""}
            conn.execute(
                """
                INSERT INTO vitrina_local (
                    producto, orden, stock_sheets, estado_actual,
                    h1_cant, h1_est, h2_cant, h2_est, h3_cant, h3_est, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(producto) DO UPDATE SET
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
        if not empresa or saldo <= 0:
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
