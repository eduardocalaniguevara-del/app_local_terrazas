#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Servicio Local Asíncrono de Integración con SUNAT (IGV 10.5% Multi-Ítem + Notas de Crédito),
WhatsApp Desktop / Edge y Padrón DNI/RUC.
Reutiliza los motores probados de C:\\Hostal_Sistema:
- scripts/emitir_comprobante_sunat.py
- scripts/emitir_nota_credito_sunat.py
- scripts/brazo_wsp.py
- ejecutar_remoto.py (fallback remoto hacia Laptop 2)
"""

import base64
import glob
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx

from config import (
    APISPERU_DNI_URL,
    APISPERU_RUC_URL,
    APISPERU_TOKEN,
    BRAZO_WSP_SCRIPT,
    DESCARGAS_RECEPCION_DIR,
    EJECUTAR_REMOTO_SCRIPT,
    EMITIR_COMPROBANTE_SCRIPT,
    HOSTAL_SISTEMA_DIR,
    REGLAS_AUDITORIA_JSON,
    SCRIPTS_HOSTAL_DIR,
)
from database import db_transaction, get_connection
from domain_logic import _registrar_movimiento, actualizar_comprobante_desde_panel_local

FACTOR_IGV_HOTEL = 1.105
EMITIR_NC_SCRIPT = SCRIPTS_HOSTAL_DIR / "emitir_nota_credito_sunat.py"
CAPTURAS_SUNAT_DIR = HOSTAL_SISTEMA_DIR / "capturas"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if os.name == "nt" else 0

_sunat_emission_lock = threading.Lock()
_jobs_lock = threading.Lock()
_SUNAT_JOBS: Dict[str, Dict[str, Any]] = {}


def calcular_valor_unitario_105(precio_con_igv: float) -> float:
    """Ley MYPE Hotelería/Turismo (IGV 10.5%): Valor Unitario = Precio / 1.105 (4 decimales)"""
    return round(float(precio_con_igv) / FACTOR_IGV_HOTEL, 4)


def _python_cli_bin() -> str:
    exe = sys.executable
    if exe.lower().endswith("pythonw.exe"):
        cand = exe[:-11] + "python.exe"
        if os.path.exists(cand):
            return cand
    return exe


def es_laptop_recepcion_directa() -> bool:
    """Detecta si el servidor está corriendo directamente en Laptop 2 (Recepción)."""
    if os.environ.get("HOSTAL_EJECUCION_DIRECTA") == "1":
        return True
    if os.path.exists(r"C:\Users\ovalo mariategui"):
        return True
    return False


def _whatsapp_local_activo() -> bool:
    """Detecta si WhatsApp Desktop está corriendo en esta misma PC."""
    try:
        res = subprocess.run(
            ["tasklist", "/NH", "/FI", "IMAGENAME eq WhatsApp*"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=CREATE_NO_WINDOW,
            timeout=3,
        )
        out = (res.stdout or "").lower()
        return "whatsapp.exe" in out or "whatsapp.root.exe" in out
    except Exception:
        return False


# ==============================================================================
# 1. CONSULTA DNI (RENIEC) Y RUC (SUNAT) CON FALLBACK OFFLINE EN 0 MS
# ==============================================================================

def consultar_dni_local_o_reniec(dni_buscado: str) -> Dict[str, Any]:
    dni = re.sub(r"\D", "", str(dni_buscado or "").strip())
    if len(dni) == 7:
        dni = dni.zfill(8)
    if len(dni) < 8:
        return {"ok": False, "error": "DNI debe tener 8 dígitos."}

    conn = get_connection()
    try:
        pad = conn.execute("SELECT * FROM huespedes_padron WHERE dni = ?", (dni,)).fetchone()
        est_rows = conn.execute(
            """
            SELECT huesped, sexo, nac, reg, mot, hab, tarifa, empresa, celular, ruc
            FROM estancias
            WHERE dni = ?
            ORDER BY ymd_in DESC, sheet_row DESC, rowid DESC
            """,
            (dni,),
        ).fetchall()

        if (pad and pad["nombre"]) or est_rows:
            nombre = (pad["nombre"] if pad and pad["nombre"] else "") or (est_rows[0]["huesped"] if est_rows else "")
            sexo = (pad["sexo"] if pad and pad["sexo"] else "") or "M"
            nac = (pad["nac"] if pad and pad["nac"] else "") or "Perú"
            reg = (pad["reg"] if pad and pad["reg"] else "") or ""
            mot = (pad["mot"] if pad and pad["mot"] else "") or "Vacaciones, recreo u ocio"
            celular = (pad["celular"] if pad and pad["celular"] else "") or ""
            ruc = (pad["ruc"] if pad and pad["ruc"] else "") or ""
            empresa = (pad["empresa"] if pad and pad["empresa"] else "") or ""
            hab_frec = (pad["hab_frec"] if pad and pad["hab_frec"] else "") or ""
            tarifa_frec = float(pad["tarifa_frec"] or 0) if pad else 0.0
            visitas = int(pad["visitas"] or 0) if pad else len(est_rows)

            for er in est_rows:
                if not reg and (er["reg"] or "").strip():
                    reg = str(er["reg"]).strip()
                if not ruc and (er["ruc"] or "").strip():
                    ruc = re.sub(r"\D", "", str(er["ruc"]).strip())
                if not empresa and (er["empresa"] or "").strip():
                    empresa = str(er["empresa"]).strip().upper()
                if not celular and (er["celular"] or "").strip():
                    celular = str(er["celular"]).strip()
                if not hab_frec and (er["hab"] or "").strip():
                    hab_frec = str(er["hab"]).strip()
                if tarifa_frec <= 0 and float(er["tarifa"] or 0) > 0:
                    tarifa_frec = float(er["tarifa"] or 0)

            if ruc and not empresa:
                emp_r = conn.execute("SELECT razon_social FROM empresas_padron WHERE ruc = ?", (ruc,)).fetchone()
                if emp_r and emp_r["razon_social"]:
                    empresa = str(emp_r["razon_social"]).strip().upper()
            elif empresa and not ruc:
                emp_r = conn.execute(
                    "SELECT ruc FROM empresas_padron WHERE UPPER(TRIM(razon_social)) = ? LIMIT 1",
                    (empresa.strip().upper(),),
                ).fetchone()
                if emp_r and emp_r["ruc"]:
                    ruc = str(emp_r["ruc"]).strip()

            if nombre:
                return {
                    "ok": True,
                    "dni": dni,
                    "nombre": nombre,
                    "nombreCompleto": nombre,
                    "sexo": sexo,
                    "nac": nac,
                    "reg": reg,
                    "mot": mot,
                    "celular": celular,
                    "ruc": ruc,
                    "empresa": empresa,
                    "hab": hab_frec,
                    "tarifa": tarifa_frec if tarifa_frec > 0 else "",
                    "visitas": max(visitas, 1),
                    "fuente": "PADRON_LOCAL",
                }
    finally:
        conn.close()

    try:
        url = f"{APISPERU_DNI_URL}/{dni}?token={APISPERU_TOKEN}"
        with httpx.Client(timeout=4.5, follow_redirects=True) as client:
            resp = client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                nombres = str(data.get("nombres") or "").strip()
                ap_pat = str(data.get("apellidoPaterno") or "").strip()
                ap_mat = str(data.get("apellidoMaterno") or "").strip()
                nombre_completo = " ".join(x for x in [nombres, ap_pat, ap_mat] if x).strip().upper()
                if nombre_completo:
                    with db_transaction() as c_save:
                        c_save.execute(
                            """
                            INSERT INTO huespedes_padron (dni, nombre, visitas, updated_at)
                            VALUES (?, ?, 0, ?)
                            ON CONFLICT(dni) DO UPDATE SET nombre = excluded.nombre, updated_at = excluded.updated_at
                            """,
                            (dni, nombre_completo, datetime.now().isoformat()),
                        )
                    return {
                        "ok": True,
                        "dni": dni,
                        "nombre": nombre_completo,
                        "nombreCompleto": nombre_completo,
                        "visitas": 0,
                        "fuente": "RENIEC",
                    }
    except Exception:
        pass

    return {"ok": False, "dni": dni, "error": "Sin conexión o DNI no encontrado (ingrese nombre manualmente)."}


def _guardar_empresa_padron_seguro(ruc: str, razon: str) -> None:
    if not ruc or not razon:
        return
    try:
        with db_transaction() as c_save:
            c_save.execute(
                """
                INSERT INTO empresas_padron (ruc, razon_social, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(ruc) DO UPDATE SET
                    razon_social = excluded.razon_social,
                    updated_at = excluded.updated_at
                """,
                (ruc, razon, datetime.now().isoformat()),
            )
    except Exception:
        pass


def consultar_ruc_local_o_sunat(ruc_buscado: str) -> Dict[str, Any]:
    ruc = re.sub(r"\D", "", str(ruc_buscado or "").strip())
    if len(ruc) != 11 or not re.match(r"^(10|15|17|20)", ruc):
        return {"ok": False, "error": "RUC inválido (debe tener 11 dígitos e iniciar con 10 o 20)."}

    # 1. Consulta en Padrón Local (empresas_padron / estancias / huespedes_padron)
    razon_local = ""
    conn = get_connection()
    try:
        emp = conn.execute("SELECT ruc, razon_social FROM empresas_padron WHERE ruc = ?", (ruc,)).fetchone()
        if emp and (emp["razon_social"] or "").strip():
            razon_local = str(emp["razon_social"]).strip().upper()
        if not razon_local:
            est_emp = conn.execute(
                "SELECT empresa FROM estancias WHERE ruc = ? AND trim(empresa) != '' ORDER BY ymd_in DESC, sheet_row DESC, rowid DESC LIMIT 1",
                (ruc,),
            ).fetchone()
            if est_emp and (est_emp["empresa"] or "").strip():
                razon_local = str(est_emp["empresa"]).strip().upper()
        if not razon_local:
            hp_emp = conn.execute(
                "SELECT empresa FROM huespedes_padron WHERE ruc = ? AND trim(empresa) != '' LIMIT 1",
                (ruc,),
            ).fetchone()
            if hp_emp and (hp_emp["empresa"] or "").strip():
                razon_local = str(hp_emp["empresa"]).strip().upper()
    except Exception:
        pass
    finally:
        conn.close()

    # 2. Consulta en línea SUNAT (APIsPERU principal + apis.net.pe respaldo)
    urls_consulta = [
        ("APISPERU", f"{APISPERU_RUC_URL}/{ruc}?token={APISPERU_TOKEN}"),
        ("APIS_NET_PE", f"https://api.apis.net.pe/v1/ruc?numero={ruc}"),
    ]
    for fuente_api, url in urls_consulta:
        try:
            with httpx.Client(timeout=6.0, follow_redirects=True) as client:
                resp = client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, dict):
                        razon = str(
                            data.get("razonSocial")
                            or data.get("nombre")
                            or data.get("nombre_o_razon_social")
                            or ""
                        ).strip().upper()
                        if razon:
                            estado = str(data.get("estado") or "ACTIVO").strip().upper()
                            condicion = str(data.get("condicion") or "HABIDO").strip().upper()
                            direccion = str(data.get("direccion") or "").strip()
                            _guardar_empresa_padron_seguro(ruc, razon)
                            return {
                                "ok": True,
                                "ruc": ruc,
                                "razonSocial": razon,
                                "empresa": razon,
                                "nombre": razon,
                                "estado": estado,
                                "condicion": condicion,
                                "direccion": direccion,
                                "fuente": "SUNAT",
                            }
        except Exception:
            continue

    # 3. Si no hubo respuesta online pero estaba en padrón local, devolver padrón local
    if razon_local:
        _guardar_empresa_padron_seguro(ruc, razon_local)
        return {
            "ok": True,
            "ruc": ruc,
            "razonSocial": razon_local,
            "empresa": razon_local,
            "nombre": razon_local,
            "estado": "ACTIVO",
            "condicion": "HABIDO",
            "direccion": "",
            "fuente": "PADRON_LOCAL",
        }

    return {"ok": False, "ruc": ruc, "error": "Sin conexión o RUC no encontrado (escriba la Razón Social manualmente)."}


# ==============================================================================
# 2. AUDITORÍA PREVENTIVA Y BÚSQUEDA DE PDF
# ==============================================================================

def cargar_reglas_auditoria() -> Dict[str, Any]:
    if REGLAS_AUDITORIA_JSON.exists():
        try:
            with open(REGLAS_AUDITORIA_JSON, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "umbrales_auditoria": {"umbral_alerta_monto": 250.0, "umbral_alerta_critico": 500.0},
        "regla_cero_extra": {"activo": True, "valores_sospechosos_cero_extra": [300.0, 350.0, 400.0, 500.0, 600.0, 700.0]},
    }


def validar_monto_preventivo(monto: float, forzar: bool = False) -> Tuple[bool, str]:
    if forzar:
        return True, "Autorizado por operador."
    reglas = cargar_reglas_auditoria()
    umbrales = reglas.get("umbrales_auditoria", {})
    umbral_alerta = float(umbrales.get("umbral_alerta_monto", 250.0))
    cero_extra = reglas.get("regla_cero_extra", {})
    valores_sospechosos = [float(x) for x in cero_extra.get("valores_sospechosos_cero_extra", [])]

    motivos = []
    if monto in valores_sospechosos:
        motivos.append(f"El monto S/ {monto:.2f} coincide con posible error de 'Cero Extra' (S/ {monto/10:.2f} con un cero de más).")
    if monto > umbral_alerta:
        motivos.append(f"El monto S/ {monto:.2f} supera el umbral habitual de una noche (S/ {umbral_alerta:.2f}).")

    if motivos:
        return False, " | ".join(motivos)
    return True, "OK"


def normalizar_y_validar_dni_servicio(
    raw_dni: Any,
    telefono: str = "",
    exigir_por_wsp: bool = False,
) -> Tuple[bool, str, str]:
    """
    Normaliza DNI peruano (7 dígitos -> 8 dígitos con cero inicial) y aplica regla estricta:
    - Si se ingresó un DNI no vacío y no tiene 7 u 8 dígitos (ej. 4 dígitos), bloquea la emisión/envío.
    - Si el huésped tiene número de celular registrado o solicita envío WSP, es OBLIGATORIO tener DNI válido (7 u 8 dígitos).
    """
    raw_str = str(raw_dni or "").strip()
    primer_token = re.split(r"[/,;]+", raw_str)[0].strip() if raw_str else ""
    es_vacio = primer_token in ("", "-", "SIN DOCUMENTO", "S/D")
    solo_dig = "" if es_vacio else re.sub(r"\D", "", primer_token)
    if len(solo_dig) == 7:
        solo_dig = solo_dig.zfill(8)

    tel_dig = re.sub(r"\D", "", str(telefono or "").strip())

    if not es_vacio and len(solo_dig) != 8:
        return (
            False,
            "",
            f"El DNI registrado ('{primer_token}' — {len(solo_dig)} dígitos) es incorrecto. "
            f"Corríjalo con 7 u 8 dígitos válidos antes de emitir o enviar.",
        )

    if (tel_dig or exigir_por_wsp) and len(solo_dig) != 8:
        return (
            False,
            "",
            f"El huésped tiene celular registrado ({tel_dig or 'WhatsApp'}), por lo que es OBLIGATORIO "
            f"que tenga su DNI correcto (7 u 8 dígitos) antes de emitir o enviar el comprobante.",
        )

    return True, solo_dig, ""


def buscar_pdf_comprobante(nro_cp: str = "", ruc_dni: str = "", cliente: str = "") -> Optional[str]:
    patrones = [
        str(DESCARGAS_RECEPCION_DIR / "*.pdf"),
        str(HOSTAL_SISTEMA_DIR / "boletas_sunat" / "**" / "*.pdf"),
        str(HOSTAL_SISTEMA_DIR / "facturas_sunat" / "**" / "*.pdf"),
    ]
    archivos: List[str] = []
    for pat in patrones:
        archivos.extend(glob.glob(pat, recursive=True))
    if not archivos:
        return None
    archivos.sort(key=os.path.getmtime, reverse=True)

    terminos = []
    if nro_cp:
        terminos.append(nro_cp.replace("-", "").lower())
        terminos.append(nro_cp.lower())
    if ruc_dni:
        terminos.append(str(ruc_dni).strip().lower())
    if cliente:
        palabras = [p.lower() for p in str(cliente).split() if len(p) > 3]
        if palabras:
            terminos.append(palabras[0])

    terminos_limpios = [t for t in terminos if t]
    for ruta in archivos[:120]:
        nom = os.path.basename(ruta).lower()
        if any(t in nom for t in terminos_limpios):
            return ruta
    return None if terminos_limpios else archivos[0]


# ==============================================================================
# 3. GESTOR DE TAREAS EN SEGUNDO PLANO (NO BLOQUEA LA WEBAPP)
# ==============================================================================

def _prune_old_jobs() -> None:
    now_ts = time.time()
    to_del = [
        jid for jid, j in _SUNAT_JOBS.items()
        if j.get("state") in ("DONE", "ERROR") and (now_ts - float(j.get("finished_ts") or now_ts)) > 180
    ]
    for jid in to_del:
        _SUNAT_JOBS.pop(jid, None)


def _update_job(job_id: str, **kwargs: Any) -> None:
    with _jobs_lock:
        if job_id in _SUNAT_JOBS:
            _SUNAT_JOBS[job_id].update(kwargs)
            _SUNAT_JOBS[job_id]["updated_ts"] = time.time()


def obtener_estado_job_sunat(job_id: str) -> Optional[Dict[str, Any]]:
    with _jobs_lock:
        _prune_old_jobs()
        j = _SUNAT_JOBS.get(job_id)
        return dict(j) if j else None


def listar_jobs_sunat_recientes() -> List[Dict[str, Any]]:
    with _jobs_lock:
        _prune_old_jobs()
        jobs = [dict(j) for j in _SUNAT_JOBS.values()]
    jobs.sort(key=lambda x: float(x.get("created_ts") or 0), reverse=True)
    return jobs


def _despachar_wsp_pdf(
    telefono: str,
    pdf_path: str,
    mensaje: str,
    nro_cp: str = "",
) -> Tuple[bool, str, str]:
    """
    Despacha mensaje + PDF siempre por el WhatsApp de Recepción (Laptop 2):
    - En Laptop 2 (es_laptop_recepcion_directa): ejecuta brazo_wsp.py directo en esta PC.
    - En Laptop 1 (Controlador): delega a Laptop 2 vía ejecutar_remoto.py para no tocar el WhatsApp personal
      de Laptop 1 y usar el archivo PDF guardado en Laptop 2.
    """
    py_bin = _python_cli_bin()
    en_recepcion = es_laptop_recepcion_directa()
    pista_archivo = os.path.basename(pdf_path) if pdf_path else (nro_cp or "")

    if en_recepcion and BRAZO_WSP_SCRIPT.exists():
        cmd = [py_bin, str(BRAZO_WSP_SCRIPT), "--telefono", telefono, "--mensaje", mensaje]
        if pdf_path:
            cmd.extend(["--archivo", pdf_path])
        elif nro_cp:
            cmd.extend(["--archivo", nro_cp])
    elif EJECUTAR_REMOTO_SCRIPT.exists():
        cmd = [
            py_bin,
            str(EJECUTAR_REMOTO_SCRIPT),
            "brazo_wsp.py",
            "--telefono", telefono,
            "--mensaje", mensaje,
        ]
        if pista_archivo:
            cmd.extend(["--archivo", pista_archivo])
        cmd.extend(["--timeout-remoto", "135"])
    else:
        return False, "", "No se encontró brazo_wsp.py ni ejecutar_remoto.py en C:\\Hostal_Sistema."

    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=160,
        creationflags=CREATE_NO_WINDOW,
    )
    ok = proc.returncode == 0
    out_txt = (proc.stdout or "").strip()
    err_txt = (proc.stderr or proc.stdout or "").strip() if not ok else ""
    return ok, out_txt, err_txt


def _ejecutar_trabajo_sunat_wsp(
    job_id: Optional[str],
    data_norm: Dict[str, Any],
    wakeup_cb: Optional[Callable[[], None]] = None,
) -> Dict[str, Any]:
    """Ejecuta en serie segura (con lock SUNAT) la orden solicitada y actualiza SQLite recién al concluir."""
    if job_id:
        _update_job(job_id, etapa="⏳ En cola esperando turno del motor SUNAT...")

    with _sunat_emission_lock:
        accion = data_norm["accion"]
        hab = data_norm["hab"]
        estancia_uuid = data_norm["estanciaUuid"]
        id_estancia = data_norm["id_estancia"]
        fila_calzado = data_norm["fila_calzado"]
        modo_borrador = data_norm["modoBorrador"]
        tipo = data_norm["tipo"]
        py_bin = _python_cli_bin()
        en_recepcion = es_laptop_recepcion_directa()
        CAPTURAS_SUNAT_DIR.mkdir(parents=True, exist_ok=True)

        # ------------------------------------------------------------------
        # CASO A: REENVÍO SOLO WHATSAPP (Directo en Laptop 2 o Delegado desde Laptop 1)
        # ------------------------------------------------------------------
        if accion == "SOLO_WSP":
            tel = data_norm["telefono"]
            nro_cp = data_norm["nro_comprobante"]
            doc = data_norm["documento"]
            cli = data_norm["cliente"]
            pdf_path = data_norm["archivo"]
            if en_recepcion and (not pdf_path or not os.path.isfile(pdf_path)):
                pdf_path = buscar_pdf_comprobante(nro_cp=nro_cp, ruc_dni=doc, cliente=cli) or ""
            msg = (
                data_norm.get("mensaje")
                or f"Estimado(a) {cli or 'cliente'}, adjuntamos su comprobante electrónico {nro_cp} del Hostal El Óvalo Mariátegui. ¡Gracias por su preferencia!"
            )
            if job_id:
                pref_pc = "" if en_recepcion else "📡 [Laptop 1 → Recepción] "
                _update_job(job_id, state="RUNNING", etapa=f"{pref_pc}📲 Enviando {nro_cp or 'comprobante'} por WhatsApp a {tel}...")
            ok_wsp, out_wsp, err_wsp = _despachar_wsp_pdf(tel, pdf_path, msg, nro_cp=nro_cp)
            return {
                "status": "OK" if ok_wsp else "ERROR",
                "accion": "SOLO_WSP",
                "hab": hab,
                "wsp_status": "ENVIADO" if ok_wsp else "ERROR_WSP",
                "archivo_pdf": pdf_path,
                "mensaje": f"Comprobante enviado por WhatsApp al {tel}." if ok_wsp else f"Falló envío WhatsApp: {err_wsp}",
                "error": err_wsp,
                "stdout": out_wsp,
            }

        # ------------------------------------------------------------------
        # CASO B: NOTA DE CRÉDITO ELECTRÓNICA (ANULACIÓN EN SUNAT)
        # ------------------------------------------------------------------
        if accion == "NOTA_CREDITO":
            nro_origen = data_norm["nro_comprobante"]
            motivo = data_norm["motivo"]
            cap_name = f"nc_{'borrador' if modo_borrador else 'emitida'}_{tipo.lower()}_{int(time.time())}.png"
            cap_path = CAPTURAS_SUNAT_DIR / cap_name
            cap_remota_str = f"C:\\Hostal_Sistema\\capturas\\{cap_name}"

            if job_id:
                pref_pc = "" if en_recepcion else "📡 [Laptop 1 → Recepción] "
                _update_job(
                    job_id,
                    state="RUNNING",
                    etapa=(
                        f"{pref_pc}🧪 Generando Vista Preliminar de Nota de Crédito ({nro_origen})..."
                        if modo_borrador
                        else f"{pref_pc}🧾↩️ Emitiendo Nota de Crédito en SUNAT para anular {nro_origen}..."
                    ),
                )

            if en_recepcion or not EJECUTAR_REMOTO_SCRIPT.exists():
                cmd_nc = [
                    py_bin,
                    str(EMITIR_NC_SCRIPT),
                    "--tipo", tipo,
                    "--numero", nro_origen,
                    "--motivo", motivo,
                    "--headless",
                    "--captura", str(cap_path),
                ]
                if not modo_borrador:
                    cmd_nc.append("--emitir")
            else:
                cmd_nc = [
                    py_bin,
                    str(EJECUTAR_REMOTO_SCRIPT),
                    "emitir_nota_credito_sunat.py",
                    "--tipo", tipo,
                    "--numero", nro_origen,
                    "--motivo", motivo,
                    "--headless",
                    "--captura", cap_remota_str,
                ]
                if not modo_borrador:
                    cmd_nc.append("--emitir")
                cmd_nc.extend(["--timeout-remoto", "180"])

            proc_nc = subprocess.run(
                cmd_nc,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=210,
                creationflags=CREATE_NO_WINDOW,
            )
            stdout_nc = proc_nc.stdout or ""
            res_nc: Dict[str, Any] = {}
            idx_res = stdout_nc.find("--- RESUMEN FINAL ---")
            if idx_res != -1:
                bloque = stdout_nc[idx_res + len("--- RESUMEN FINAL ---"):].strip()
                # Si vino por ejecutar_remoto.py, cortar antes del separador STDERR
                if "------------------------------------------------------------" in bloque:
                    bloque = bloque.split("------------------------------------------------------------")[0].strip()
                try:
                    res_nc = json.loads(bloque)
                except Exception:
                    pass

            st_nc = str(res_nc.get("status") or "").upper()
            if modo_borrador and st_nc == "BORRADOR_GENERADO":
                if not en_recepcion and not cap_path.exists():
                    for _ in range(16):
                        if cap_path.exists():
                            break
                        time.sleep(0.5)
                return {
                    "status": "OK",
                    "modo": "borrador",
                    "accion": "NOTA_CREDITO",
                    "hab": hab,
                    "tipo": tipo,
                    "cp_origen": nro_origen,
                    "captura": str(cap_path),
                    "nombre_captura": cap_name if cap_path.exists() else "",
                    "datos_cp_origen": res_nc.get("datos_cp_origen") or {},
                    "mensaje": f"Vista Preliminar de Nota de Crédito para {nro_origen} lista (sin emitir).",
                }

            if not modo_borrador and st_nc == "EMITIDO":
                nro_nc = str(res_nc.get("nro_nota_credito") or f"NC-{nro_origen}").strip()
                # Revertir habitación a Pendiente y limpiar el comprobante anulado para permitir re-emitir
                try:
                    actualizar_comprobante_desde_panel_local({
                        "hab": hab,
                        "estanciaUuid": estancia_uuid,
                        "fila": id_estancia,
                        "estadoComp": "Pendiente",
                        "tipoComp": "Factura" if tipo == "FACTURA" else "Boleta",
                        "nroComp": "",
                        "forzarNroComp": True,
                    })
                    with db_transaction() as conn_m:
                        _registrar_movimiento(
                            conn_m,
                            "Nota de Crédito SUNAT",
                            hab=hab,
                            huesped=data_norm.get("cliente", ""),
                            resumen=f"Hab. {hab} · {nro_nc} anuló {nro_origen} ({motivo}) · Estado vuelve a Pendiente",
                            detalle=[
                                {"campo": "Comprobante Anulado", "antes": nro_origen, "despues": "Anulado"},
                                {"campo": "Nota de Crédito", "antes": "-", "despues": nro_nc},
                                {"campo": "Motivo", "antes": "-", "despues": motivo},
                            ],
                            monto=float((res_nc.get("datos_cp_origen") or {}).get("monto") or 0),
                        )
                    if wakeup_cb:
                        wakeup_cb()
                except Exception as exc_db:
                    return {
                        "status": "OK",
                        "accion": "NOTA_CREDITO",
                        "hab": hab,
                        "nro_nota_credito": nro_nc,
                        "cp_origen": nro_origen,
                        "warn_sqlite": str(exc_db),
                        "mensaje": f"Nota de Crédito {nro_nc} emitida en SUNAT, pero revise actualización local: {exc_db}",
                    }

                return {
                    "status": "OK",
                    "accion": "NOTA_CREDITO",
                    "hab": hab,
                    "tipo": tipo,
                    "cp_origen": nro_origen,
                    "nro_nota_credito": nro_nc,
                    "archivo_pdf": res_nc.get("pdf_descargado") or "",
                    "captura": str(cap_path) if cap_path.exists() else "",
                    "nombre_captura": cap_name if cap_path.exists() else "",
                    "mensaje": f"Nota de Crédito {nro_nc} emitida en SUNAT (anuló {nro_origen}). Hab. {hab} volvió a Pendiente.",
                }

            return {
                "status": "ERROR",
                "accion": "NOTA_CREDITO",
                "hab": hab,
                "error": res_nc.get("error") or (proc_nc.stderr or stdout_nc)[-500:] or "No se pudo emitir la Nota de Crédito en SUNAT.",
            }

        # ------------------------------------------------------------------
        # CASO C: EMISIÓN DE BOLETA / FACTURA (LAPTOP 2 DIRECTO O LAPTOP 1 -> LAPTOP 2)
        # ------------------------------------------------------------------
        emitir_en_firme = not modo_borrador
        items_normalizados = data_norm["items"]
        total_monto = data_norm["total_monto"]
        doc_num = data_norm["documento"]
        dni_huesped = data_norm.get("dni_huesped", "")
        cliente = data_norm["cliente"]
        telefono = data_norm["telefono"]

        # En Laptop 2 (Recepción) corre directo local; en Laptop 1 delega el flujo completo a Laptop 2
        # vía ejecutar_remoto.py (Syncthing) para emitir y despachar el PDF desde WhatsApp Recepción.
        usar_remoto_completo = (not en_recepcion) and EJECUTAR_REMOTO_SCRIPT.exists()

        if job_id:
            pref_pc = "📡 [Laptop 1 → Recepción] " if usar_remoto_completo else ""
            suf_wsp = " + Envío WSP" if (usar_remoto_completo and accion == "EMITIR_Y_WSP" and emitir_en_firme) else ""
            _update_job(
                job_id,
                state="RUNNING",
                etapa=(
                    f"{pref_pc}🧪 Abriendo Vista Preliminar de {tipo.title()} en SUNAT SEE-SOL..."
                    if modo_borrador
                    else f"{pref_pc}⚡ Emitiendo {tipo.title()} S/ {total_monto:.2f} en SUNAT SEE-SOL (IGV 10.5%){suf_wsp}..."
                ),
            )

        payload_sunat = {
            "accion": accion,
            "tipo": tipo,
            "documento": doc_num,
            "dni_huesped": dni_huesped,
            "cliente": cliente,
            "items": items_normalizados,
            "telefono": telefono,
            "enviar_wsp": bool(usar_remoto_completo and accion == "EMITIR_Y_WSP" and emitir_en_firme),
            "emitir": emitir_en_firme,
            "omitir_sheets": True,
            "headless": True,
            "id_estancia": id_estancia,
            "fila_calzado": fila_calzado,
        }

        if usar_remoto_completo:
            cmd = [
                py_bin,
                str(EJECUTAR_REMOTO_SCRIPT),
                "emitir_comprobante_sunat.py",
                "--payload-json",
                json.dumps(payload_sunat, ensure_ascii=False),
                "--timeout-remoto", "260",
            ]
        else:
            cmd = [
                py_bin,
                str(EMITIR_COMPROBANTE_SCRIPT),
                "--payload-json",
                json.dumps(payload_sunat, ensure_ascii=False),
            ]

        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
            creationflags=CREATE_NO_WINDOW,
        )
        stdout_all = proc.stdout or ""
        res_json: Dict[str, Any] = {}
        m = re.search(r"JSON_RESULT_START\s*(\{.*?\})\s*JSON_RESULT_END", stdout_all, re.DOTALL)
        if m:
            try:
                res_json = json.loads(m.group(1))
            except Exception:
                pass

        if res_json.get("status") != "OK":
            return {
                "status": "ERROR",
                "accion": accion,
                "hab": hab,
                "error": res_json.get("error") or (proc.stderr or stdout_all)[-500:] or "Error desconocido en emisión SUNAT.",
            }

        # Si fue Modo Borrador, materializar captura en disco local (instantáneo en Laptop 1 vía captura_b64)
        if modo_borrador:
            nom_cap = str(res_json.get("nombre_captura") or "").strip()
            cap_b64 = str(res_json.get("captura_b64") or "").strip()
            if nom_cap and cap_b64:
                try:
                    local_cap = CAPTURAS_SUNAT_DIR / Path(nom_cap).name
                    if not local_cap.exists():
                        local_cap.write_bytes(base64.b64decode(cap_b64))
                except Exception:
                    pass
            return {
                "status": "OK",
                "modo": "borrador",
                "accion": accion,
                "hab": hab,
                "tipo": tipo,
                "nro_comprobante": "BORRADOR-PRELIMINAR",
                "captura": res_json.get("captura", ""),
                "nombre_captura": nom_cap,
                "monto": total_monto,
                "mensaje": f"Vista Preliminar de {tipo.title()} generada en SUNAT (sin emitir).",
            }

        nro_cp = str(res_json.get("nro_comprobante") or "").strip()
        archivo_pdf = str(res_json.get("archivo_pdf") or "").strip()
        wsp_status = res_json.get("wsp_status", "NO_SOLICITADO")
        wsp_err = str(res_json.get("wsp_error") or "").strip()

        # Etapa 2: Enviar por WhatsApp (cuando corrió directo en Laptop 2) ANTES de cambiar a Emitido en el Panel
        if accion == "EMITIR_Y_WSP" and not usar_remoto_completo and telefono:
            if job_id:
                _update_job(
                    job_id,
                    etapa=f"📲 {nro_cp} generada · Enviando PDF por WhatsApp al {telefono}...",
                )
            tipo_lbl = "Factura" if tipo == "FACTURA" else "Boleta"
            msg_wsp = (
                f"Estimado(a) {cliente}, le adjuntamos su {tipo_lbl} Electrónica {nro_cp}. "
                f"¡Muchas gracias por su preferencia en Hostal El Óvalo Mariátegui!"
            )
            ok_wsp, _, err_wsp = _despachar_wsp_pdf(telefono, archivo_pdf, msg_wsp, nro_cp=nro_cp)
            wsp_status = "ENVIADO" if ok_wsp else "ERROR_WSP"
            wsp_err = err_wsp

        # Etapa 3: Recién al finalizar TODO el proceso (SUNAT + WSP), cambiar estado a Emitido (✅) en SQLite y Sheets
        if hab or estancia_uuid or id_estancia:
            try:
                actualizar_comprobante_desde_panel_local({
                    "hab": hab,
                    "estanciaUuid": estancia_uuid,
                    "fila": id_estancia,
                    "dni": dni_huesped if dni_huesped else None,
                    "estadoComp": "Emitido",
                    "tipoComp": "Factura" if tipo == "FACTURA" else "Boleta",
                    "nroComp": nro_cp,
                    "montoFact": total_monto,
                    "celular": telefono,
                    "ruc": doc_num if tipo == "FACTURA" else "",
                    "empresa": cliente if tipo == "FACTURA" else "",
                    "forzarNroComp": True,
                })
                if wakeup_cb:
                    wakeup_cb()
            except Exception as exc_upd:
                res_json["warn_sqlite"] = str(exc_upd)

        msg_final = f"{'Factura' if tipo == 'FACTURA' else 'Boleta'} {nro_cp} emitida exitosamente."
        if accion == "EMITIR_Y_WSP":
            if wsp_status == "ENVIADO":
                msg_final = f"✅ {nro_cp} emitida en SUNAT y enviada por WhatsApp al {telefono}."
            else:
                msg_final = f"⚠️ {nro_cp} emitida en SUNAT, pero WhatsApp reportó: {wsp_err or wsp_status}. Puede usar 'Reenviar WSP'."

        return {
            "status": "OK",
            "accion": accion,
            "hab": hab,
            "tipo": tipo,
            "nro_comprobante": nro_cp,
            "monto": total_monto,
            "archivo_pdf": archivo_pdf,
            "wsp_status": wsp_status,
            "wsp_error": wsp_err,
            "mensaje": msg_final,
        }


def _thread_runner(
    job_id: str,
    data_norm: Dict[str, Any],
    wakeup_cb: Optional[Callable[[], None]] = None,
) -> None:
    try:
        res = _ejecutar_trabajo_sunat_wsp(job_id, data_norm, wakeup_cb=wakeup_cb)
        st = "DONE" if res.get("status") == "OK" else "ERROR"
        _update_job(
            job_id,
            state=st,
            etapa=res.get("mensaje") or (res.get("error") if st == "ERROR" else "Completado"),
            result=res,
            finished_ts=time.time(),
        )
    except Exception as exc:
        _update_job(
            job_id,
            state="ERROR",
            etapa=f"Error: {exc}",
            result={"status": "ERROR", "error": str(exc), "hab": data_norm.get("hab", "")},
            finished_ts=time.time(),
        )


def ejecutar_orden_sunat_wsp(
    data: Dict[str, Any],
    wakeup_cb: Optional[Callable[[], None]] = None,
) -> Dict[str, Any]:
    """
    Valida instantáneamente (<5 ms) los datos de la orden:
    - Si hay error de validación o requiere confirmación de auditoría, responde de inmediato.
    - Por defecto lanza la ejecución en segundo plano (async=True) y devuelve job_id en 0 ms
      para que Recepción pueda cerrar el modal y seguir operando sin esperar.
    - Si data['sincrono'] == True, espera y devuelve el resultado directo.
    """
    accion_raw = str(data.get("accion") or "").upper().strip()
    modo_borrador = bool(data.get("modoBorrador", False))
    if accion_raw == "BORRADOR":
        accion = "EMITIR"
        modo_borrador = True
    else:
        accion = accion_raw

    hab = str(data.get("hab") or data.get("habNum") or "").strip()
    estancia_uuid = str(data.get("estanciaUuid") or "").strip()
    id_estancia = data.get("id_estancia") or data.get("fila") or 0
    fila_calzado = data.get("fila_calzado")
    sincrono = bool(data.get("sincrono", False))

    if accion not in ("EMITIR", "EMITIR_Y_WSP", "SOLO_WSP", "NOTA_CREDITO"):
        return {"status": "ERROR", "error": f"Acción '{accion_raw}' no soportada."}

    # 1. Validación rápida SOLO_WSP
    if accion == "SOLO_WSP":
        tel = re.sub(r"\D", "", str(data.get("telefono") or "").strip())
        if not tel:
            return {"status": "ERROR", "error": "Ingrese un número de celular válido para enviar por WhatsApp."}
        dni_wsp_in = data.get("dni_huesped") if data.get("dni_huesped") is not None else data.get("dni")
        if dni_wsp_in is not None:
            ok_d, _, err_d = normalizar_y_validar_dni_servicio(dni_wsp_in, telefono=tel, exigir_por_wsp=True)
            if not ok_d:
                return {"status": "ERROR", "error": err_d}
        data_norm = {
            "accion": "SOLO_WSP",
            "hab": hab,
            "estanciaUuid": estancia_uuid,
            "id_estancia": id_estancia,
            "fila_calzado": fila_calzado,
            "modoBorrador": False,
            "tipo": str(data.get("tipo") or "BOLETA").upper().strip(),
            "telefono": tel,
            "nro_comprobante": str(data.get("nro_comprobante") or "").strip(),
            "documento": str(data.get("documento") or "").strip(),
            "cliente": str(data.get("cliente") or "").strip(),
            "archivo": str(data.get("archivo") or "").strip(),
            "mensaje": str(data.get("mensaje") or "").strip(),
        }

    # 2. Validación rápida NOTA_CREDITO
    elif accion == "NOTA_CREDITO":
        nro_cp = str(data.get("nro_comprobante") or "").strip().upper()
        if not re.search(r"(\d+)$", nro_cp):
            return {
                "status": "ERROR",
                "error": "Para emitir Nota de Crédito debe figurar el N° de comprobante emitido (ej. EB01-2069 o E001-355).",
            }
        tipo_nc = str(data.get("tipo") or "").upper().strip()
        if nro_cp.startswith("E001") or nro_cp.startswith("F"):
            tipo_nc = "FACTURA"
        elif nro_cp.startswith("EB01") or nro_cp.startswith("B"):
            tipo_nc = "BOLETA"
        elif tipo_nc not in ("BOLETA", "FACTURA"):
            tipo_nc = "BOLETA"

        motivo = str(data.get("motivo") or "ANULACION DE LA OPERACION").strip().upper()
        if len(motivo) < 5:
            return {"status": "ERROR", "error": "Ingrese un motivo válido para la Nota de Crédito (mínimo 5 caracteres)."}

        data_norm = {
            "accion": "NOTA_CREDITO",
            "hab": hab,
            "estanciaUuid": estancia_uuid,
            "id_estancia": id_estancia,
            "fila_calzado": fila_calzado,
            "modoBorrador": modo_borrador,
            "tipo": tipo_nc,
            "nro_comprobante": nro_cp,
            "motivo": motivo,
            "cliente": str(data.get("cliente") or "").strip().upper(),
        }

    # 3. Validación rápida EMITIR / EMITIR_Y_WSP
    else:
        tipo = str(data.get("tipo") or data.get("tipoDoc") or "BOLETA").upper().strip()
        doc_raw = str(data.get("documento") or data.get("docReceptor") or "").strip()
        dni_huesped_raw = str(data.get("dni_huesped") if data.get("dni_huesped") is not None else (data.get("dni") or "")).strip()
        cliente = str(data.get("cliente") or data.get("nombreReceptor") or "CLIENTES VARIOS").strip().upper()
        telefono = re.sub(r"\D", "", str(data.get("telefono") or "").strip())
        forzar = bool(data.get("forzar", False))
        raw_items = data.get("items") or []
        exigir_wsp = bool(accion == "EMITIR_Y_WSP" and not modo_borrador)

        if exigir_wsp and not telefono:
            return {"status": "ERROR", "error": "Para 'Emitir y Enviar WSP' debe ingresar el número de WhatsApp del cliente."}

        if tipo == "FACTURA":
            doc_num = re.sub(r"\D", "", doc_raw)
            if len(doc_num) != 11 or not re.match(r"^(10|15|17|20)", doc_num):
                return {"status": "ERROR", "error": "Para emitir FACTURA se requiere un RUC válido de 11 dígitos (10 o 20)."}
            dni_para_validar = dni_huesped_raw
            if not dni_para_validar and doc_num.startswith("10"):
                dni_para_validar = doc_num[2:10]
            ok_dni, dni_huesped_norm, err_dni = normalizar_y_validar_dni_servicio(
                dni_para_validar,
                telefono=telefono,
                exigir_por_wsp=exigir_wsp,
            )
            if not ok_dni:
                return {"status": "ERROR", "error": err_dni}
        else:
            dni_fuente = doc_raw if doc_raw else dni_huesped_raw
            ok_dni, doc_num, err_dni = normalizar_y_validar_dni_servicio(
                dni_fuente,
                telefono=telefono,
                exigir_por_wsp=exigir_wsp,
            )
            if not ok_dni:
                return {"status": "ERROR", "error": err_dni}
            dni_huesped_norm = doc_num

        if not raw_items:
            return {"status": "ERROR", "error": "Debe incluir al menos un ítem para emitir en SUNAT."}

        items_normalizados = []
        total_monto = 0.0
        for it in raw_items:
            cant = max(1, int(it.get("cantidad") or 1))
            precio_u = round(float(it.get("precio_unitario") or it.get("precioUnitario") or it.get("monto") or 0), 2)
            if precio_u <= 0:
                return {"status": "ERROR", "error": "Todos los ítems deben tener Precio Unitario mayor a S/ 0.00."}
            valor_u = calcular_valor_unitario_105(precio_u)
            desc = str(it.get("descripcion") or f"DIA DE ALOJAMIENTO HAB N {hab}").strip().upper()
            items_normalizados.append({
                "cantidad": cant,
                "descripcion": desc,
                "precio_unitario": precio_u,
                "valor_unitario": valor_u,
            })
            total_monto = round(total_monto + (cant * precio_u), 2)

        if not modo_borrador:
            ok_audit, msg_audit = validar_monto_preventivo(total_monto, forzar=forzar)
            if not ok_audit:
                return {
                    "status": "CONFIRM_REQUIRED",
                    "requiereConfirmacion": True,
                    "monto": total_monto,
                    "mensajeAuditoria": msg_audit,
                }

        data_norm = {
            "accion": accion,
            "hab": hab,
            "estanciaUuid": estancia_uuid,
            "id_estancia": id_estancia,
            "fila_calzado": fila_calzado,
            "modoBorrador": modo_borrador,
            "tipo": tipo,
            "documento": doc_num,
            "dni_huesped": dni_huesped_norm,
            "cliente": cliente,
            "telefono": telefono,
            "items": items_normalizados,
            "total_monto": total_monto,
        }

    # Modo síncrono opcional para pruebas por script
    if sincrono:
        return _ejecutar_trabajo_sunat_wsp(None, data_norm, wakeup_cb=wakeup_cb)

    # Modo asíncrono por defecto: responde en 0 ms y corre en segundo plano
    job_id = f"sunat_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
    etapa_ini = (
        "🧪 Iniciando captura de Vista Preliminar en SUNAT..."
        if modo_borrador
        else (
            f"📲 Preparando envío WhatsApp (Hab. {hab})..."
            if accion == "SOLO_WSP"
            else (
                f"🧾↩️ Iniciando Nota de Crédito en SUNAT (Hab. {hab})..."
                if accion == "NOTA_CREDITO"
                else f"⚡ Iniciando emisión en SUNAT{' + WSP' if accion == 'EMITIR_Y_WSP' else ''} (Hab. {hab})..."
            )
        )
    )
    job_info = {
        "job_id": job_id,
        "hab": hab,
        "estanciaUuid": estancia_uuid,
        "accion": accion,
        "tipo": data_norm["tipo"],
        "modoBorrador": modo_borrador,
        "state": "QUEUED",
        "etapa": etapa_ini,
        "created_ts": time.time(),
        "updated_ts": time.time(),
        "finished_ts": None,
        "result": None,
    }
    with _jobs_lock:
        _prune_old_jobs()
        _SUNAT_JOBS[job_id] = job_info

    th = threading.Thread(
        target=_thread_runner,
        args=(job_id, data_norm, wakeup_cb),
        daemon=True,
    )
    th.start()

    return {
        "status": "PROCESSING",
        "async": True,
        "job_id": job_id,
        "hab": hab,
        "accion": accion,
        "tipo": data_norm["tipo"],
        "modoBorrador": modo_borrador,
        "etapa": etapa_ini,
    }
