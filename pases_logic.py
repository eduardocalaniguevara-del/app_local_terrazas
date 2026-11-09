# -*- coding: utf-8 -*-
"""
pases_logic.py — Control Interno de Visitas (Corte 12:00 m.) + Cuentas Multi-Día por Habitación
===============================================================================================
1. El día operativo de visitas corta cada mediodía (12:00 m. a 11:59 a.m. del día siguiente).
2. Todo pase aceptado se carga a la cuenta como Servicio Adicional = 'Visita' (S/ 10 por defecto).
3. El estado RECHAZADO se aplica sobre un registro existente en la tabla (sin borrarlo) y descuenta
   automáticamente los S/ 10 de la cuenta.
4. `obtener_cuenta_detallada_hab_local`: reconstruye la cadena completa de días de cualquier habitación
   ocupada (huéspedes que van pasando varios días), detallando día por día hospedaje, adicionales
   (tienda, visitas, servicios, comprobante) y pagos realizados con su medio de pago.
"""

import uuid
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Set

from database import db_transaction, get_connection
import domain_logic as dl
from sunat_wsp_service import consultar_dni_local_o_reniec

PASES_CABECERA = [
    "PASE_UUID", "FECHA", "HORA", "HAB", "ESTANCIA_UUID", "ID_ESTANCIA",
    "HUESPED_TITULAR", "DNI_VISITA", "NOMBRE_VISITA", "ESTADO",
    "MONTO", "MOTIVO", "EQUIPO", "CREATED_AT",
]

DOW_CORTA = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]


def fecha_corte_mediodia_actual(ahora: Optional[datetime] = None) -> str:
    """El corte diario es a las 12:00 m. (mediodía)."""
    dt = ahora or datetime.now()
    d_base = dt.date() - timedelta(days=1) if dt.hour < 12 else dt.date()
    return d_base.strftime("%d/%m/%Y")


def _fecha_a_dmy(valor: Optional[str] = None) -> str:
    s = str(valor or "").strip()
    if not s:
        return fecha_corte_mediodia_actual()
    if len(s) >= 10 and s[4:5] == "-" and s[7:8] == "-":
        y, m, d = s[:10].split("-")
        return f"{int(d):02d}/{int(m):02d}/{int(y):04d}"
    return dl.normalizar_fecha_dmy(s)


def _dmy_a_date(dmy: str) -> date:
    return datetime.strptime(dl.normalizar_fecha_dmy(dmy), "%d/%m/%Y").date()


def _dmy_a_iso(dmy: str) -> str:
    return _dmy_a_date(dmy).isoformat()


def _rango_corte_txt(dmy: str) -> str:
    d0 = _dmy_a_date(dmy)
    d1 = d0 + timedelta(days=1)
    return f"{d0.strftime('%d/%m')} 12:00 m. → {d1.strftime('%d/%m')} 11:59 a.m."


def _habitaciones_ocupadas_activas(fecha_iso: Optional[str] = None) -> List[Dict[str, Any]]:
    """Devuelve las habitaciones ocupadas del Panel de Disponibilidad."""
    panel = dl.obtener_datos_panel_local(fecha_iso)
    habs_out: List[Dict[str, Any]] = []
    for h in panel.get("habitaciones") or []:
        est = str(h.get("estado") or "").strip().lower()
        hab_num = str(h.get("hab") or "").strip()
        huesped = str(h.get("huesped") or "").strip()
        if est == "libre" or not hab_num or huesped in ("", "-"):
            continue
        habs_out.append({
            "hab": hab_num,
            "huesped": huesped,
            "estanciaUuid": str(h.get("estanciaUuid") or "").strip(),
            "idEstancia": str(h.get("idEstancia") or h.get("id") or "").strip(),
            "saldo": round(float(h.get("saldo") or 0.0), 2),
            "adicional": round(float(h.get("adicional") or 0.0), 2),
            "tipoAdicional": str(h.get("tipoAdicional") or ""),
        })
    return sorted(habs_out, key=lambda x: (int(x["hab"]) if x["hab"].isdigit() else 9999, x["hab"]))


def consultar_dni_visita_local(datos: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    datos = datos or {}
    q_raw = str(datos.get("dni") or datos.get("q") or "").strip()
    solo_dig = "".join(ch for ch in q_raw if ch.isdigit())
    dni = solo_dig if len(solo_dig) >= 7 else "".join(ch for ch in q_raw if ch.isalnum())
    if len(dni) == 7 and dni.isdigit():
        dni = dni.zfill(8)
    if not dni and not q_raw:
        return {"encontrado": False, "dni": "", "nombre": "", "rechazosPrevios": [], "visitasPrevias": 0}

    nombre = ""
    if len(dni) == 8 and dni.isdigit():
        try:
            info = consultar_dni_local_o_reniec(dni)
            if isinstance(info, dict) and info.get("nombre"):
                nombre = str(info["nombre"]).strip()
        except Exception:
            nombre = ""

    conn = get_connection()
    try:
        if not nombre:
            prev = conn.execute(
                "SELECT nombre_visita FROM control_pases WHERE dni_visita = ? AND trim(nombre_visita) != '' ORDER BY ymd DESC LIMIT 1",
                (dni,),
            ).fetchone()
            if prev:
                nombre = str(prev["nombre_visita"] or "").strip()

        rechazos = [
            {
                "fecha": str(r["fecha_dmy"] or ""),
                "hora": str(r["hora"] or ""),
                "hab": str(r["hab"] or ""),
                "motivo": str(r["motivo_rechazo"] or ""),
            }
            for r in conn.execute(
                "SELECT fecha_dmy, hora, hab, motivo_rechazo FROM control_pases WHERE dni_visita = ? AND estado = 'RECHAZADO' ORDER BY ymd DESC, hora DESC LIMIT 5",
                (dni,),
            ).fetchall()
        ]
        tot_vis = conn.execute(
            "SELECT COUNT(*) AS c FROM control_pases WHERE dni_visita = ? AND estado = 'ACEPTADO'",
            (dni,),
        ).fetchone()
        return {
            "encontrado": bool(nombre),
            "dni": dni,
            "nombre": nombre,
            "rechazosPrevios": rechazos,
            "visitasPrevias": int(tot_vis["c"] or 0) if tot_vis else 0,
        }
    finally:
        conn.close()


def obtener_visitas_pases_local(datos: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    datos = datos or {}
    hoy_corte_dmy = fecha_corte_mediodia_actual()
    fecha_dmy = _fecha_a_dmy(datos.get("fecha") or hoy_corte_dmy)
    ymd = dl.fecha_dmy_a_ymd(fecha_dmy)

    conn = get_connection()
    try:
        iso_panel = None if fecha_dmy == hoy_corte_dmy else _dmy_a_iso(fecha_dmy)
        habs_activas = _habitaciones_ocupadas_activas(iso_panel)
        rows = conn.execute(
            """
            SELECT * FROM control_pases
            WHERE ymd = ?
            ORDER BY created_at DESC, hora DESC
            """,
            (ymd,),
        ).fetchall()

        conteo_hab: Dict[str, int] = {}
        aceptados = 0
        rechazados = 0
        total_cargo = 0.0
        registros: List[Dict[str, Any]] = []

        for r in rows:
            est = str(r["estado"] or "ACEPTADO").upper()
            hab_r = str(r["hab"] or "-").strip() or "-"
            monto_r = round(float(r["monto"] or 0.0), 2)
            if est == "RECHAZADO":
                rechazados += 1
            else:
                aceptados += 1
                total_cargo = round(total_cargo + monto_r, 2)
                if hab_r != "-":
                    conteo_hab[hab_r] = conteo_hab.get(hab_r, 0) + 1

            registros.append({
                "paseUuid": str(r["pase_uuid"]),
                "fecha": str(r["fecha_dmy"] or fecha_dmy),
                "hora": str(r["hora"] or ""),
                "hab": hab_r,
                "huespedTitular": str(r["huesped_titular"] or ""),
                "dni": str(r["dni_visita"] or ""),
                "nombreVisita": str(r["nombre_visita"] or ""),
                "estado": est,
                "monto": monto_r,
                "motivo": str(r["motivo_rechazo"] or ""),
            })

        for h in habs_activas:
            h["pasesHoy"] = conteo_hab.get(h["hab"], 0)

        por_hab_lista = [
            {"hab": k, "conteo": v}
            for k, v in sorted(conteo_hab.items(), key=lambda x: (int(x[0]) if x[0].isdigit() else 9999, x[0]))
        ]

        rows_dias = conn.execute(
            """
            SELECT fecha_dmy, ymd,
                   SUM(CASE WHEN estado = 'ACEPTADO' THEN 1 ELSE 0 END) AS aceptados,
                   SUM(CASE WHEN estado = 'RECHAZADO' THEN 1 ELSE 0 END) AS rechazados,
                   SUM(CASE WHEN estado = 'ACEPTADO' THEN monto ELSE 0 END) AS monto_total
            FROM control_pases
            GROUP BY ymd, fecha_dmy
            ORDER BY ymd DESC
            LIMIT 10
            """
        ).fetchall()
        mapa_dias = {
            str(rd["fecha_dmy"]): {
                "fechaDmy": str(rd["fecha_dmy"]),
                "fechaIso": _dmy_a_iso(str(rd["fecha_dmy"])),
                "dow": DOW_CORTA[_dmy_a_date(str(rd["fecha_dmy"])).weekday()],
                "aceptados": int(rd["aceptados"] or 0),
                "rechazados": int(rd["rechazados"] or 0),
                "totalCargo": round(float(rd["monto_total"] or 0.0), 2),
                "rangoCorte": _rango_corte_txt(str(rd["fecha_dmy"])),
            }
            for rd in rows_dias if rd["fecha_dmy"]
        }
        for f_aseg in (hoy_corte_dmy, fecha_dmy):
            if f_aseg not in mapa_dias:
                dt_a = _dmy_a_date(f_aseg)
                mapa_dias[f_aseg] = {
                    "fechaDmy": f_aseg,
                    "fechaIso": dt_a.isoformat(),
                    "dow": DOW_CORTA[dt_a.weekday()],
                    "aceptados": 0,
                    "rechazados": 0,
                    "totalCargo": 0.0,
                    "rangoCorte": _rango_corte_txt(f_aseg),
                }
        historial_por_dia = sorted(
            mapa_dias.values(),
            key=lambda x: dl.fecha_dmy_a_ymd(x["fechaDmy"]),
            reverse=True,
        )[:8]

        dt_sel = _dmy_a_date(fecha_dmy)
        return {
            "fechaDmy": fecha_dmy,
            "fechaIso": dt_sel.isoformat(),
            "dowNombre": DOW_CORTA[dt_sel.weekday()],
            "rangoCorteTxt": _rango_corte_txt(fecha_dmy),
            "hoyCorteDmy": hoy_corte_dmy,
            "hoyCorteIso": _dmy_a_iso(hoy_corte_dmy),
            "esHoy": fecha_dmy == hoy_corte_dmy,
            "ahoraHM": datetime.now().strftime("%H:%M"),
            "habitacionesOcupadas": habs_activas,
            "registros": registros,
            "historialPorDia": historial_por_dia,
            "resumen": {
                "aceptados": aceptados,
                "rechazados": rechazados,
                "totalRegistros": aceptados + rechazados,
                "totalCargo": total_cargo,
                "porHab": por_hab_lista,
            },
        }
    finally:
        conn.close()


def registrar_visita_pase_local(datos: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Registra la visita inicialmente como ACEPTADO (+S/ 10 a la cuenta con tipoAdicional = 'Visita')."""
    datos = datos or {}
    hab = str(datos.get("hab") or "").strip()
    dni_raw = str(datos.get("dni") or "").strip()
    nombre_vis = str(datos.get("nombreVisita") or "").strip()
    motivo = str(datos.get("motivo") or "").strip()

    hoy_corte_dmy = fecha_corte_mediodia_actual()
    fecha_req = str(datos.get("fecha") or "").strip()
    fecha_dmy = _fecha_a_dmy(fecha_req) if fecha_req else hoy_corte_dmy
    ymd = dl.fecha_dmy_a_ymd(fecha_dmy)
    hora = str(datos.get("hora") or "").strip() or datetime.now().strftime("%H:%M")

    if not hab or hab == "-":
        raise ValueError("Selecciona o escribe primero el número de la habitación ocupada (ej. 206).")
    if not dni_raw:
        raise ValueError("Ingresa el DNI de la visita.")

    monto = dl.parse_moneda(datos.get("monto") if datos.get("monto") not in (None, "") else 10.0)
    if monto <= 0:
        monto = 10.0

    dni_limpio = "".join(ch for ch in dni_raw if ch.isalnum() or ch in (",", " ", "-")).strip()
    primer_dni = "".join(ch for ch in dni_limpio.split(",")[0].split()[0] if ch.isalnum()) if dni_limpio else ""
    if not nombre_vis and len(primer_dni) == 8 and primer_dni.isdigit():
        try:
            r_dni = consultar_dni_local_o_reniec(primer_dni)
            if isinstance(r_dni, dict) and r_dni.get("nombre"):
                nombre_vis = str(r_dni["nombre"]).strip()
        except Exception:
            nombre_vis = ""

    conn_chk = get_connection()
    try:
        st = dl._buscar_estancia_local(conn_chk, hab=hab)
        est_uuid = str(st["estancia_uuid"] or "")
        id_estancia = str(st["id_timestamp_str"] or "")
        huesped_titular = str(st["huesped"] or "")
    finally:
        conn_chk.close()

    # Cargar S/ 10 a la cuenta de la habitación como Servicio Adicional ('Visita') sin exponer DNI
    dl.guardar_pago_posterior_local({
        "estanciaUuid": est_uuid,
        "id": id_estancia,
        "hab": hab,
        "monto": monto,
        "concepto": "Servicio Adicional",
        "metodo": "Cargar a la Cuenta",
        "tipoAdicional": "Visita",
    })

    pase_uuid = str(uuid.uuid4())
    ahora_iso = datetime.now().isoformat(timespec="seconds")
    equipo = dl._nombre_equipo()

    with db_transaction() as conn:
        conn.execute(
            """
            INSERT INTO control_pases (
                pase_uuid, fecha_dmy, ymd, hora, hab, estancia_uuid, id_estancia,
                huesped_titular, dni_visita, nombre_visita, estado, monto,
                motivo_rechazo, equipo, created_at, synced
            ) VALUES (?,?,?,?,?,?,?,?,?,?,'ACEPTADO',?,?,?,?,0)
            """,
            (
                pase_uuid, fecha_dmy, ymd, hora, hab, est_uuid, id_estancia,
                huesped_titular, dni_limpio, nombre_vis, monto,
                motivo, equipo, ahora_iso,
            ),
        )
        dl._encolar_operacion(
            conn,
            "PASE_UPSERT",
            pase_uuid,
            {
                "pase_uuid": pase_uuid,
                "fila": [
                    pase_uuid, fecha_dmy, hora, hab, est_uuid, id_estancia,
                    huesped_titular, dni_limpio, nombre_vis, "ACEPTADO",
                    monto, motivo, equipo, ahora_iso,
                ],
            },
        )

    vista = obtener_visitas_pases_local({"fecha": fecha_dmy})
    vista["exito"] = True
    vista["mensaje"] = f"✅ Visita registrada en Hab. {hab} (+S/ {monto:.2f} cargado a la cuenta)"
    return vista


def cambiar_estado_visita_pase_local(datos: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Marca un pase YA registrado como RECHAZADO (o lo reactiva a ACEPTADO) SIN borrarlo de la lista."""
    datos = datos or {}
    pase_uuid = str(datos.get("paseUuid") or "").strip()
    nuevo_estado = str(datos.get("estado") or "RECHAZADO").strip().upper()
    motivo = str(datos.get("motivo") or "").strip()
    if not pase_uuid:
        raise ValueError("Falta identificador del pase.")
    if nuevo_estado not in ("RECHAZADO", "ACEPTADO"):
        raise ValueError("Estado no válido.")

    with db_transaction() as conn:
        row = conn.execute("SELECT * FROM control_pases WHERE pase_uuid = ?", (pase_uuid,)).fetchone()
        if not row:
            raise ValueError("El registro de visita no existe.")

        estado_ant = str(row["estado"] or "ACEPTADO").upper()
        fecha_dmy = str(row["fecha_dmy"] or fecha_corte_mediodia_actual())
        hab = str(row["hab"] or "")
        est_uuid = str(row["estancia_uuid"] or "").strip()
        monto_reg = float(row["monto"] or 10.0)
        if monto_reg <= 0:
            monto_reg = 10.0

        if estado_ant != nuevo_estado and est_uuid:
            st = conn.execute("SELECT * FROM estancias WHERE estancia_uuid = ?", (est_uuid,)).fetchone()
            if st:
                adic_actual = float(st["adic_servicios"] or 0.0)
                if nuevo_estado == "RECHAZADO":
                    nuevo_adic = max(0.0, round(adic_actual - monto_reg, 2))
                else:
                    nuevo_adic = round(adic_actual + monto_reg, 2)

                otros_activos = conn.execute(
                    "SELECT COUNT(*) AS c FROM control_pases WHERE estancia_uuid = ? AND estado = 'ACEPTADO' AND pase_uuid != ?",
                    (est_uuid, pase_uuid),
                ).fetchone()
                tipo_adic_actual = str(st["tipo_adicional"] or "").strip()
                # Normalizar cualquier etiqueta 'Pase' previa a 'Visita'
                tags_base = [
                    ("Visita" if t.strip().lower() == "pase" else t.strip())
                    for t in tipo_adic_actual.split(",") if t.strip()
                ]
                tags_unicos = []
                for t in tags_base:
                    if t.lower() not in [x.lower() for x in tags_unicos]:
                        tags_unicos.append(t)

                if nuevo_estado == "RECHAZADO" and (not otros_activos or int(otros_activos["c"] or 0) == 0) and nuevo_adic == 0:
                    tags_unicos = [t for t in tags_unicos if t.lower() not in ("visita", "pase")]
                elif nuevo_estado == "ACEPTADO":
                    if "visita" not in [x.lower() for x in tags_unicos]:
                        tags_unicos.append("Visita")
                tipo_adic_actual = ", ".join(tags_unicos)

                conn.execute(
                    "UPDATE estancias SET adic_servicios = ?, tipo_adicional = ?, dirty = 1, updated_at = ? WHERE estancia_uuid = ?",
                    (nuevo_adic, tipo_adic_actual, datetime.now().isoformat(), est_uuid),
                )
                dl._encolar_operacion(
                    conn,
                    "PAGO",
                    est_uuid,
                    {
                        "estancia_uuid": est_uuid,
                        "id_timestamp_str": st["id_timestamp_str"],
                        "sheet_row": int(st["sheet_row"] or 0),
                        "hab": st["hab"],
                        "adic_servicios": nuevo_adic,
                        "adic_comp": float(st["adic_comp"] or 0.0),
                        "deuda_anterior": float(st["deuda_anterior"] or 0.0),
                        "pago_post_efec": float(st["pago_post_efec"] or 0.0),
                        "pago_post_yape": float(st["pago_post_yape"] or 0.0),
                        "pago_visa": float(st["pago_visa"] or 0.0),
                        "nro_op_yape": str(st["nro_op_yape"] or ""),
                        "solicita_comp": str(st["solicita_comp"] or "No"),
                        "tipo_comp": str(st["tipo_comp"] or ""),
                        "estado_comp": str(st["estado_comp"] or ""),
                        "monto_factura": float(st["monto_factura"] or 0.0),
                        "tipo_adicional": tipo_adic_actual,
                        "cuenta_destino": str(st["cuenta_destino"] or ""),
                        "pago_row": None,
                    },
                )

        motivo_final = motivo if nuevo_estado == "RECHAZADO" else ""
        if nuevo_estado == "RECHAZADO" and not motivo_final:
            motivo_final = str(row["motivo_rechazo"] or "").strip() or "Rechazado"

        conn.execute(
            "UPDATE control_pases SET estado = ?, monto = ?, motivo_rechazo = ?, synced = 0 WHERE pase_uuid = ?",
            (nuevo_estado, monto_reg, motivo_final, pase_uuid),
        )
        dl._encolar_operacion(
            conn,
            "PASE_UPSERT",
            pase_uuid,
            {
                "pase_uuid": pase_uuid,
                "fila": [
                    pase_uuid,
                    fecha_dmy,
                    str(row["hora"] or ""),
                    hab,
                    est_uuid,
                    str(row["id_estancia"] or ""),
                    str(row["huesped_titular"] or ""),
                    str(row["dni_visita"] or ""),
                    str(row["nombre_visita"] or ""),
                    nuevo_estado,
                    0.0 if nuevo_estado == "RECHAZADO" else monto_reg,
                    motivo_final,
                    str(row["equipo"] or ""),
                    str(row["created_at"] or ""),
                ],
            },
        )

    vista = obtener_visitas_pases_local({"fecha": fecha_dmy})
    vista["exito"] = True
    vista["mensaje"] = (
        f"🚫 Visita en Hab. {hab} marcada como RECHAZADA (permanece en la lista y se descontó S/ {monto_reg:.2f} de la cuenta)."
        if nuevo_estado == "RECHAZADO"
        else f"✅ Visita en Hab. {hab} reactivada (+S/ {monto_reg:.2f} cargado a la cuenta)."
    )
    return vista


def eliminar_visita_pase_local(datos: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    datos = dict(datos or {})
    datos["estado"] = "RECHAZADO"
    return cambiar_estado_visita_pase_local(datos)


# ==============================================================================
# CUENTAS DETALLADAS MULTI-DÍA POR HABITACIÓN (DESDE DÍA DE INGRESO + PDF + WSP)
# ==============================================================================

def _saldo_fin_estancia(r: Dict[str, Any]) -> float:
    noches = max(1, int(r.get("noches") or 1))
    aloj = round(noches * float(r.get("tarifa") or 0.0), 2)
    cargos = (
        aloj
        + float(r.get("adic_snacks") or 0.0)
        + float(r.get("adic_servicios") or 0.0)
        + float(r.get("adic_comp") or 0.0)
        + float(r.get("deuda_anterior") or 0.0)
    )
    pagos = (
        float(r.get("pago_ini_efec") or 0.0)
        + float(r.get("pago_ini_yape") or 0.0)
        + float(r.get("pago_post_efec") or 0.0)
        + float(r.get("pago_post_yape") or 0.0)
        + float(r.get("pago_visa") or 0.0)
    )
    return round(cargos - pagos, 2)


def _reconstruir_cadena_estancia(
    conn,
    st_actual: Dict[str, Any],
    solo_ciclo_actual: bool = False,
) -> List[Dict[str, Any]]:
    """Reconstruye toda la cadena de noches jaladas de la estancia seleccionada:
    1) Retrocede desde `st_actual` hasta el Día de Ingreso Inicial del huésped.
    2) Si `st_actual` es de una fecha pasada (ej. 04/11/2026) y tiene 'Continúa', también avanza
       hacia adelante para incluir las noches continuadas posteriores de ese mismo huésped.
    """
    dni = str(st_actual.get("dni") or "").strip()
    huesped = str(st_actual.get("huesped") or "").strip()

    if dni and dni not in ("-", "0"):
        cand_rows = conn.execute(
            """
            SELECT * FROM estancias
            WHERE (dni = ? OR lower(trim(huesped)) = lower(trim(?)))
              AND lower(coalesce(estado_estancia, '')) NOT LIKE '%cancel%'
            ORDER BY ymd_in DESC, sheet_row DESC
            """,
            (dni, huesped),
        ).fetchall()
    else:
        cand_rows = conn.execute(
            """
            SELECT * FROM estancias
            WHERE lower(trim(huesped)) = lower(trim(?))
              AND lower(coalesce(estado_estancia, '')) NOT LIKE '%cancel%'
            ORDER BY ymd_in DESC, sheet_row DESC
            """,
            (huesped,),
        ).fetchall()

    todos = [dict(r) for r in cand_rows]
    cadena_atras = [dict(st_actual)]
    usados: Set[str] = {str(st_actual["estancia_uuid"])}
    cur = dict(st_actual)

    # A. Caminar hacia atrás hasta el Día de Ingreso Inicial
    for paso in range(90):
        cur_in_d = _dmy_a_date(cur["fecha_in_dmy"])
        deuda_arr = round(float(cur.get("deuda_anterior") or 0.0), 2)
        cur_hab = str(cur.get("hab") or "").strip()
        cur_tar = round(float(cur.get("tarifa") or 0.0), 2)

        if solo_ciclo_actual and paso >= 1 and abs(deuda_arr) <= 0.01:
            break

        posibles = []
        for cand in todos:
            cu = str(cand["estancia_uuid"])
            if cu in usados:
                continue
            c_in = _dmy_a_date(cand["fecha_in_dmy"])
            c_out = _dmy_a_date(cand["fecha_out_dmy"]) if cand.get("fecha_out_dmy") else (c_in + timedelta(days=1))
            mismo_dia_cont = (cur_in_d == c_in and "contin" in str(cand.get("estado_estancia") or "").lower())
            if c_out != cur_in_d and (cur_in_d - c_in).days != 1 and not mismo_dia_cont:
                continue

            s_fin = _saldo_fin_estancia(cand)
            hab_c = str(cand.get("hab") or "").strip()
            tar_c = round(float(cand.get("tarifa") or 0.0), 2)
            est_c = str(cand.get("estado_estancia") or "").lower()

            score = 0
            if hab_c == cur_hab:
                score += 140
            if abs(tar_c - cur_tar) <= 0.1:
                score += 90
            if abs(s_fin - deuda_arr) <= 0.1 and abs(deuda_arr) > 0.1:
                score += 110
            elif abs(s_fin - deuda_arr) <= 0.1:
                score += 30
            if "contin" in est_c:
                score += 20

            if score < 35:
                continue
            if solo_ciclo_actual and paso == 0 and abs(deuda_arr) <= 0.01 and hab_c != cur_hab:
                continue

            posibles.append((score, int(cand.get("sheet_row") or 0), cand))

        if not posibles:
            break
        posibles.sort(key=lambda x: (x[0], x[1]), reverse=True)
        elegido = posibles[0][2]

        usados.add(str(elegido["estancia_uuid"]))
        cadena_atras.append(elegido)
        cur = elegido

    cadena_atras.reverse()  # Orden cronológico: Día de Ingreso -> st_actual

    # B. Si st_actual tuvo continuación ("Continúa"), caminar también hacia adelante
    cadena_adelante = []
    cur_f = dict(st_actual)
    for _paso_f in range(90):
        est_f = str(cur_f.get("estado_estancia") or "").lower()
        if "contin" not in est_f:
            break
        cur_in_f = _dmy_a_date(cur_f["fecha_in_dmy"])
        cur_out_f = _dmy_a_date(cur_f["fecha_out_dmy"]) if cur_f.get("fecha_out_dmy") else (cur_in_f + timedelta(days=1))
        s_fin_f = _saldo_fin_estancia(cur_f)
        cur_hab_f = str(cur_f.get("hab") or "").strip()
        cur_tar_f = round(float(cur_f.get("tarifa") or 0.0), 2)

        posibles_f = []
        for cand in todos:
            cu = str(cand["estancia_uuid"])
            if cu in usados:
                continue
            c_in = _dmy_a_date(cand["fecha_in_dmy"])
            if c_in != cur_out_f and (c_in - cur_in_f).days not in (0, 1):
                continue
            deuda_c = round(float(cand.get("deuda_anterior") or 0.0), 2)
            hab_c = str(cand.get("hab") or "").strip()
            tar_c = round(float(cand.get("tarifa") or 0.0), 2)

            score_f = 0
            if hab_c == cur_hab_f:
                score_f += 140
            if abs(tar_c - cur_tar_f) <= 0.1:
                score_f += 80
            if abs(deuda_c - s_fin_f) <= 0.1 and abs(s_fin_f) > 0.1:
                score_f += 120
            elif abs(deuda_c - s_fin_f) <= 0.1:
                score_f += 40
            if c_in == cur_out_f:
                score_f += 25

            if score_f < 35:
                continue
            posibles_f.append((score_f, -int(cand.get("sheet_row") or 0), cand))

        if not posibles_f:
            break
        posibles_f.sort(key=lambda x: (x[0], x[1]), reverse=True)
        elegido_f = posibles_f[0][2]
        usados.add(str(elegido_f["estancia_uuid"]))
        cadena_adelante.append(elegido_f)
        cur_f = elegido_f

    return cadena_atras + cadena_adelante


def _icono_y_nombre_servicio(tipo_raw: str) -> tuple[str, str]:
    t = str(tipo_raw or "").strip()
    tl = t.lower()
    if not t or tl == "servicio adicional":
        return ("🛎️", "Servicio")
    if "visita" in tl or "pase" in tl:
        return ("🚶", "Visita")
    if "garaje" in tl or "cochera" in tl:
        return ("🚗", "Garaje")
    if "lavand" in tl:
        return ("🧺", "Lavandería")
    if "ducha" in tl:
        return ("🚿", "Ducha")
    if "equipaje" in tl or "maleta" in tl:
        return ("🧳", "Guardar equipaje")
    if "baño" in tl or "bano" in tl:
        return ("🚻", "Uso de baño")
    if "impres" in tl:
        return ("🖨️", "Impresiones")
    if "comprob" in tl or "factura" in tl or "boleta" in tl:
        return ("🧾", t)
    return ("🛎️", t)


def _icono_y_nombre_servicio(tipo_raw: str) -> tuple[str, str]:
    t = str(tipo_raw or "").strip()
    tl = t.lower()
    if not t or tl == "servicio adicional":
        return ("🛎️", "Servicio")
    if "visita" in tl or "pase" in tl:
        return ("🚶", "Visita")
    if "garaje" in tl or "cochera" in tl:
        return ("🚗", "Garaje")
    if "lavand" in tl:
        return ("🧺", "Lavandería")
    if "ducha" in tl:
        return ("🚿", "Ducha")
    if "equipaje" in tl or "maleta" in tl:
        return ("🧳", "Guardar equipaje")
    if "baño" in tl or "bano" in tl:
        return ("🚻", "Uso de baño")
    if "impres" in tl:
        return ("🖨️", "Impresiones")
    if "comprob" in tl or "factura" in tl or "boleta" in tl:
        return ("🧾", t)
    return ("🛎️", t)


def obtener_cuenta_detallada_hab_local(datos: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Reconstruye desde el Día de Ingreso todas las noches jaladas de la habitación indicada,
    separando con claridad:
    - Día de Ingreso, Huésped, Recorrido, Noches Jaladas y Deuda Pendiente Actual.
    - En cada día: Deuda Ant., Tarifa, Adicional Servicio (Visitas, Garaje, Lavandería, etc. con hora),
      Consumo de Tienda, Total del Día, Pagos Diarios y Deuda Cierre Día.
    """
    datos = datos or {}
    hab_req = str(datos.get("hab") or "").strip()
    modo = str(datos.get("modo") or "ingreso").strip().lower()
    fecha_req_raw = str(datos.get("fecha") or "").strip()
    est_uuid_req = str(datos.get("estanciaUuid") or "").strip()
    solo_ciclo = (modo == "ciclo") if "modo" in datos else (not bool(datos.get("todoHistorial", True)))

    conn = get_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS servicios_detalle (
                serv_uuid     TEXT PRIMARY KEY,
                estancia_uuid TEXT NOT NULL DEFAULT '',
                id_estancia   TEXT NOT NULL DEFAULT '',
                fecha_dmy     TEXT NOT NULL DEFAULT '',
                hora          TEXT NOT NULL DEFAULT '',
                hab           TEXT NOT NULL DEFAULT '',
                tipo_servicio TEXT NOT NULL DEFAULT '',
                metodo_pago   TEXT NOT NULL DEFAULT 'Cargar a la Cuenta',
                monto         REAL NOT NULL DEFAULT 0.0,
                created_at    TEXT NOT NULL DEFAULT ''
            )
            """
        )
        habs_activas = _habitaciones_ocupadas_activas(None)
        habs_multidia = []
        cadenas_full_por_hab: Dict[str, List[Dict[str, Any]]] = {}
        cadenas_ciclo_por_hab: Dict[str, List[Dict[str, Any]]] = {}

        for h in habs_activas:
            try:
                st_h = dl._buscar_estancia_local(conn, estancia_uuid=h["estanciaUuid"], id_busq=h["idEstancia"], hab=h["hab"])
                cad_full = _reconstruir_cadena_estancia(conn, dict(st_h), solo_ciclo_actual=False)
                cad_ciclo = _reconstruir_cadena_estancia(conn, dict(st_h), solo_ciclo_actual=True)
            except Exception:
                cad_full = []
                cad_ciclo = []
            cadenas_full_por_hab[h["hab"]] = cad_full
            cadenas_ciclo_por_hab[h["hab"]] = cad_ciclo
            noches_full = sum(max(1, int(x.get("noches") or 1)) for x in cad_full) if cad_full else 1
            if len(cad_full) > 1 or noches_full > 1:
                habs_multidia.append({
                    "hab": h["hab"],
                    "huesped": h["huesped"],
                    "dias": len(cad_full),
                    "noches": noches_full,
                    "desdeDmy": cad_full[0]["fecha_in_dmy"] if cad_full else "",
                    "saldo": h["saldo"],
                })

        if not hab_req:
            if habs_multidia:
                ordenadas = sorted(
                    habs_multidia,
                    key=lambda x: (1 if x["saldo"] > 0 else 0, x["dias"]),
                    reverse=True,
                )
                hab_req = ordenadas[0]["hab"]
            elif habs_activas:
                hab_req = habs_activas[0]["hab"]

        # Historial reciente de estancias de esta habitación (para selector de fechas/huéspedes en la barra)
        estancias_hab_lista = []
        if hab_req:
            rows_hist_hab = conn.execute(
                """
                SELECT estancia_uuid, fecha_in_dmy, fecha_out_dmy, ymd_in, hab, huesped, dni, tarifa, estado_estancia
                FROM estancias
                WHERE hab = ? AND lower(coalesce(estado_estancia, '')) NOT LIKE '%cancel%'
                ORDER BY ymd_in DESC, sheet_row DESC
                LIMIT 20
                """,
                (hab_req,),
            ).fetchall()
            for rh in rows_hist_hab:
                f_dmy_h = str(rh["fecha_in_dmy"] or "")
                try:
                    f_iso_h = _dmy_a_iso(f_dmy_h) if f_dmy_h else ""
                except Exception:
                    f_iso_h = ""
                estancias_hab_lista.append({
                    "estanciaUuid": str(rh["estancia_uuid"] or ""),
                    "fechaInDmy": f_dmy_h,
                    "fechaOutDmy": str(rh["fecha_out_dmy"] or ""),
                    "fechaInIso": f_iso_h,
                    "hab": str(rh["hab"] or hab_req),
                    "huesped": str(rh["huesped"] or ""),
                    "dni": str(rh["dni"] or ""),
                    "tarifa": round(float(rh["tarifa"] or 0.0), 2),
                    "estadoEstancia": str(rh["estado_estancia"] or ""),
                })

        # Resolver la estancia base según estanciaUuid, fecha indicada o estancia activa actual
        row_objetivo = None
        fecha_dmy_busq = ""
        if est_uuid_req:
            row_objetivo = conn.execute(
                "SELECT * FROM estancias WHERE estancia_uuid = ? LIMIT 1",
                (est_uuid_req,),
            ).fetchone()
            if row_objetivo and not hab_req:
                hab_req = str(row_objetivo["hab"] or "")
        elif fecha_req_raw and hab_req:
            try:
                fecha_dmy_busq = _fecha_a_dmy(fecha_req_raw)
                ymd_busq = dl.fecha_dmy_a_ymd(fecha_dmy_busq)
            except Exception:
                fecha_dmy_busq = ""
                ymd_busq = 0
            if fecha_dmy_busq:
                # 1) Coincidencia exacta con fecha_in_dmy en esa habitación
                row_objetivo = conn.execute(
                    """
                    SELECT * FROM estancias
                    WHERE hab = ? AND fecha_in_dmy = ?
                      AND lower(coalesce(estado_estancia, '')) NOT LIKE '%cancel%'
                    ORDER BY sheet_row DESC, rowid DESC LIMIT 1
                    """,
                    (hab_req, fecha_dmy_busq),
                ).fetchone()
                # 2) Si no ingresó ese día exacto, estancia vigente durante esa fecha en esa habitación
                if not row_objetivo and ymd_busq > 0:
                    row_objetivo = conn.execute(
                        """
                        SELECT * FROM estancias
                        WHERE hab = ? AND ymd_in <= ? AND ymd_out > ?
                          AND lower(coalesce(estado_estancia, '')) NOT LIKE '%cancel%'
                        ORDER BY ymd_in DESC, sheet_row DESC LIMIT 1
                        """,
                        (hab_req, ymd_busq, ymd_busq),
                    ).fetchone()
                # 3) Si tampoco hubo ese día, buscar si un huésped que ingresó ese día estuvo en otra hab y pasó por hab_req
                if not row_objetivo and ymd_busq > 0:
                    row_objetivo = conn.execute(
                        """
                        SELECT * FROM estancias
                        WHERE hab = ? AND ymd_in <= ?
                          AND lower(coalesce(estado_estancia, '')) NOT LIKE '%cancel%'
                        ORDER BY ymd_in DESC, sheet_row DESC LIMIT 1
                        """,
                        (hab_req, ymd_busq),
                    ).fetchone()

        if row_objetivo is not None:
            cadena_full = _reconstruir_cadena_estancia(conn, dict(row_objetivo), solo_ciclo_actual=False)
            cadena_ciclo = _reconstruir_cadena_estancia(conn, dict(row_objetivo), solo_ciclo_actual=True)
        else:
            cadena_full = cadenas_full_por_hab.get(hab_req) or []
            cadena_ciclo = cadenas_ciclo_por_hab.get(hab_req) or []
            if not cadena_full and hab_req:
                row_fb = conn.execute(
                    """
                    SELECT * FROM estancias
                    WHERE hab = ? AND lower(coalesce(estado_estancia, '')) NOT LIKE '%cancel%'
                    ORDER BY ymd_in DESC, sheet_row DESC LIMIT 1
                    """,
                    (hab_req,),
                ).fetchone()
                if row_fb:
                    cadena_full = _reconstruir_cadena_estancia(conn, dict(row_fb), solo_ciclo_actual=False)
                    cadena_ciclo = _reconstruir_cadena_estancia(conn, dict(row_fb), solo_ciclo_actual=True)

        cadena = cadena_ciclo if solo_ciclo else cadena_full

        if not cadena:
            return {
                "encontrado": False,
                "mensaje": f"No se encontró estancia para la Hab. {hab_req}" + (f" en la fecha {fecha_dmy_busq}." if fecha_dmy_busq else ". Escribe el número de habitación (ej. 206)."),
                "habitacionesMultiDia": habs_multidia,
                "estanciasDeLaHab": estancias_hab_lista,
                "hab": hab_req,
            }

        dias_detalle = []
        todos_pagos = []
        todos_adicionales = []
        todos_tienda = []
        todos_servicios = []

        tot_noches = 0
        tot_aloj = 0.0
        tot_tienda_cargada = 0.0
        tot_tienda_pagada = 0.0
        tot_serv = 0.0
        tot_comp = 0.0
        tot_pagado_efec = 0.0
        tot_pagado_yape = 0.0
        tot_pagado_visa = 0.0
        habs_recorridas: List[str] = []
        celular_huesped = ""

        for idx_d, st in enumerate(cadena, start=1):
            est_uuid = str(st.get("estancia_uuid") or "")
            id_ts = str(st.get("id_timestamp_str") or "")
            hab_d = str(st.get("hab") or "")
            if hab_d and hab_d not in habs_recorridas:
                habs_recorridas.append(hab_d)
            cel_st = "".join(ch for ch in str(st.get("celular") or "") if ch.isdigit())
            if len(cel_st) >= 9:
                celular_huesped = cel_st[-9:]

            f_in = str(st.get("fecha_in_dmy") or "")
            f_out = str(st.get("fecha_out_dmy") or "")
            noches_d = max(1, int(st.get("noches") or 1))
            tarifa_d = round(float(st.get("tarifa") or 0.0), 2)
            aloj_d = round(noches_d * tarifa_d, 2)

            snacks_d = round(float(st.get("adic_snacks") or 0.0), 2)
            serv_d = round(float(st.get("adic_servicios") or 0.0), 2)
            comp_d = round(float(st.get("adic_comp") or 0.0), 2)
            tipo_adic_d = str(st.get("tipo_adicional") or "").strip()
            deuda_ant_d = round(float(st.get("deuda_anterior") or 0.0), 2)

            ini_e = round(float(st.get("pago_ini_efec") or 0.0), 2)
            ini_y = round(float(st.get("pago_ini_yape") or 0.0), 2)
            post_e = round(float(st.get("pago_post_efec") or 0.0), 2)
            post_y = round(float(st.get("pago_post_yape") or 0.0), 2)
            visa_d = round(float(st.get("pago_visa") or 0.0), 2)
            pagado_dia_est = round(ini_e + ini_y + post_e + post_y + visa_d, 2)

            tot_noches += noches_d
            tot_aloj = round(tot_aloj + aloj_d, 2)
            tot_tienda_cargada = round(tot_tienda_cargada + snacks_d, 2)
            tot_serv = round(tot_serv + serv_d, 2)
            tot_comp = round(tot_comp + comp_d, 2)
            tot_pagado_efec = round(tot_pagado_efec + ini_e + post_e, 2)
            tot_pagado_yape = round(tot_pagado_yape + ini_y + post_y, 2)
            tot_pagado_visa = round(tot_pagado_visa + visa_d, 2)

            # 1. Pagos de ese día en Registro_Pagos (excluyendo filas marcadas como DUPLICADO/ANULADO)
            rows_p = conn.execute(
                """
                SELECT fecha_iso, hab, monto, medio_pago, tipo_pago, concepto, nro_operacion
                FROM pagos
                WHERE ((id_estancia = ? AND trim(id_estancia) != '')
                   OR (estancia_uuid = ? AND trim(estancia_uuid) != ''))
                  AND upper(coalesce(concepto, '')) NOT LIKE '%DUPLICADO%'
                  AND upper(coalesce(concepto, '')) NOT LIKE '%ANULAD%'
                ORDER BY sheet_row ASC, fecha_iso ASC
                """,
                (id_ts, est_uuid),
            ).fetchall()

            def _etiqueta_corta_pago(tipo_raw: str, concepto_raw: str) -> str:
                t_low = (tipo_raw or "").lower()
                c_low = (concepto_raw or "").lower()
                if "inicial" in t_low or "inicial" in c_low:
                    return "Inicial"
                if "adicional" in t_low or "servicio" in c_low or "visita" in c_low:
                    return "Adic."
                if "tienda" in t_low or "snack" in c_low:
                    return "Tienda"
                if "posterior" in t_low or "saldo" in c_low or "posterior" in c_low:
                    return "Abono"
                return "Abono"

            pagos_dia_lista = []
            suma_efec_rows = 0.0
            suma_yape_rows = 0.0
            suma_visa_rows = 0.0
            for rp in rows_p:
                m_val = round(float(rp["monto"] or 0.0), 2)
                if m_val == 0:
                    continue
                fh = str(rp["fecha_iso"] or "")
                hora_p = fh.split(" ")[1][:5] if " " in fh else ""
                fecha_p_raw = fh.split(" ")[0] if " " in fh else f_in
                fecha_p = _fecha_a_dmy(fecha_p_raw) if fecha_p_raw else f_in
                med_p = str(rp["medio_pago"] or "Efectivo")
                tip_p = str(rp["tipo_pago"] or "")
                con_p = str(rp["concepto"] or "Pago de estancia")
                med_low = med_p.lower()
                if "yape" in med_low or "plin" in med_low:
                    suma_yape_rows = round(suma_yape_rows + m_val, 2)
                elif "visa" in med_low or "banco" in med_low or "tarj" in med_low or "transf" in med_low:
                    suma_visa_rows = round(suma_visa_rows + m_val, 2)
                else:
                    suma_efec_rows = round(suma_efec_rows + m_val, 2)
                item_p = {
                    "fecha": fecha_p,
                    "hora": hora_p,
                    "hab": str(rp["hab"] or hab_d),
                    "monto": m_val,
                    "medioPago": med_p,
                    "tipoPago": tip_p,
                    "etiquetaCorta": _etiqueta_corta_pago(tip_p, con_p),
                    "concepto": con_p,
                    "nroOperacion": str(rp["nro_operacion"] or ""),
                }
                pagos_dia_lista.append(item_p)
                todos_pagos.append(item_p)

            # Si en la hoja Estancias hay montos abonados (Efectivo/Yape/Visa) que no tenían fila en Registro_Pagos, completarlos
            op_yape = str(st.get("nro_op_yape") or "")
            hora_reg = str(st.get("hora_registro") or "")[:5]
            if not pagos_dia_lista and pagado_dia_est != 0:
                for monto_col, med_col, tip_col, etq_col in [
                    (ini_e, "Efectivo", "Inicial", "Inicial"),
                    (ini_y, "Yape", "Inicial", "Inicial"),
                    (post_e, "Efectivo", "Posterior", "Abono"),
                    (post_y, "Yape", "Posterior", "Abono"),
                    (visa_d, "VISA / Banco", "Posterior", "Abono"),
                ]:
                    if monto_col != 0:
                        item_s = {
                            "fecha": f_in,
                            "hora": hora_reg,
                            "hab": hab_d,
                            "monto": monto_col,
                            "medioPago": med_col,
                            "tipoPago": tip_col,
                            "etiquetaCorta": etq_col,
                            "concepto": f"Pago {tip_col} ({med_col})",
                            "nroOperacion": op_yape if med_col == "Yape" else "",
                        }
                        pagos_dia_lista.append(item_s)
                        todos_pagos.append(item_s)
            elif pagos_dia_lista and pagado_dia_est > round(suma_efec_rows + suma_yape_rows + suma_visa_rows + 0.01, 2):
                falt_e = round((ini_e + post_e) - suma_efec_rows, 2)
                falt_y = round((ini_y + post_y) - suma_yape_rows, 2)
                falt_v = round(visa_d - suma_visa_rows, 2)
                for m_f, med_f in [(falt_e, "Efectivo"), (falt_y, "Yape"), (falt_v, "VISA / Banco")]:
                    if m_f > 0.01:
                        item_f = {
                            "fecha": f_in,
                            "hora": hora_reg,
                            "hab": hab_d,
                            "monto": m_f,
                            "medioPago": med_f,
                            "tipoPago": "Estancia",
                            "etiquetaCorta": "Abono",
                            "concepto": f"Abono en estancia ({med_f})",
                            "nroOperacion": op_yape if med_f == "Yape" else "",
                        }
                        pagos_dia_lista.append(item_f)
                        todos_pagos.append(item_f)

            # 2A. Consumo de Tienda de ese día
            rows_t = conn.execute(
                """
                SELECT fecha_iso, fecha_dmy, producto, cantidad, metodo_pago, precio_unit, subtotal, nro_op_yape
                FROM ventas_tienda
                WHERE (id_estancia = ? AND trim(id_estancia) != '')
                   OR (estancia_uuid = ? AND trim(estancia_uuid) != '')
                ORDER BY sheet_row ASC, fecha_iso ASC
                """,
                (id_ts, est_uuid),
            ).fetchall()

            tienda_dia_lista = []
            adicionales_dia_lista = []
            suma_tienda_rows_cargada = 0.0
            for rt in rows_t:
                sub_t = round(float(rt["subtotal"] or 0.0), 2)
                met_t = str(rt["metodo_pago"] or "")
                es_cargado_t = "cargar" in met_t.lower() or "cuenta" in met_t.lower() or "habitac" in met_t.lower()
                if es_cargado_t:
                    suma_tienda_rows_cargada = round(suma_tienda_rows_cargada + sub_t, 2)
                else:
                    tot_tienda_pagada = round(tot_tienda_pagada + sub_t, 2)
                f_iso_t = str(rt["fecha_iso"] or "")
                hora_t = f_iso_t.split(" ")[1][:5] if " " in f_iso_t else ""
                item_ad = {
                    "fecha": str(rt["fecha_dmy"] or f_in),
                    "hora": hora_t,
                    "hab": hab_d,
                    "icono": "🛒",
                    "categoria": "Tienda",
                    "etiqueta": f"{int(rt['cantidad'] or 1)}x {rt['producto']}",
                    "descripcion": f"{int(rt['cantidad'] or 1)}x {rt['producto']}" + ("" if es_cargado_t else f" [Pagado {met_t}]"),
                    "monto": sub_t,
                    "modo": "Cargado a cuenta" if es_cargado_t else f"Pagado ({met_t})",
                    "cargadoACuenta": es_cargado_t,
                }
                tienda_dia_lista.append(item_ad)
                adicionales_dia_lista.append(item_ad)
                todos_adicionales.append(item_ad)
                todos_tienda.append(item_ad)

            # Si snacks_d tiene saldo en la estancia sin fila detallada en ventas_tienda
            resto_snacks = round(snacks_d - suma_tienda_rows_cargada, 2)
            if resto_snacks > 0.009 and not tienda_dia_lista:
                item_sn = {
                    "fecha": f_in,
                    "hora": "",
                    "hab": hab_d,
                    "icono": "🛒",
                    "categoria": "Tienda",
                    "etiqueta": "Consumo Tienda",
                    "descripcion": "Consumo de Tienda (cargado a habitación)",
                    "monto": resto_snacks,
                    "modo": "Cargado a cuenta",
                    "cargadoACuenta": True,
                }
                tienda_dia_lista.append(item_sn)
                adicionales_dia_lista.append(item_sn)
                todos_adicionales.append(item_sn)
                todos_tienda.append(item_sn)

            # 2B. Servicios Adicionales del día (Visitas en control_pases + servicios_detalle + respaldo histórico limpio)
            servicios_dia_lista = []
            suma_serv_items = 0.0

            if serv_d > 0:
                # Paso 1: Visitas registradas en control_pases para esta estancia
                vis_rows = conn.execute(
                    "SELECT hora, monto FROM control_pases WHERE estancia_uuid = ? AND estado = 'ACEPTADO' ORDER BY hora ASC",
                    (est_uuid,),
                ).fetchall()
                for vr in vis_rows:
                    m_vis = round(float(vr["monto"] or 10.0), 2)
                    if suma_serv_items + m_vis <= serv_d + 0.01:
                        h_v = str(vr["hora"] or "").strip()
                        item_v = {
                            "fecha": f_in,
                            "hora": h_v,
                            "hab": hab_d,
                            "icono": "🚶",
                            "categoria": "Visita",
                            "etiqueta": "Visita",
                            "descripcion": "Visita",
                            "monto": m_vis,
                            "modo": "Cargo a cuenta",
                            "cargadoACuenta": True,
                        }
                        servicios_dia_lista.append(item_v)
                        adicionales_dia_lista.append(item_v)
                        todos_adicionales.append(item_v)
                        todos_servicios.append(item_v)
                        suma_serv_items = round(suma_serv_items + m_vis, 2)

                # Paso 2: Servicios registrados en tabla servicios_detalle
                if suma_serv_items < serv_d - 0.009:
                    det_rows = conn.execute(
                        """
                        SELECT hora, tipo_servicio, metodo_pago, monto
                        FROM servicios_detalle
                        WHERE (estancia_uuid = ? AND trim(estancia_uuid) != '')
                           OR (id_estancia = ? AND trim(id_estancia) != '')
                        ORDER BY rowid ASC
                        """,
                        (est_uuid, id_ts),
                    ).fetchall()
                    for dr in det_rows:
                        m_det = round(float(dr["monto"] or 0.0), 2)
                        if m_det <= 0:
                            continue
                        if suma_serv_items + m_det <= serv_d + 0.01:
                            ico_s, nom_s = _icono_y_nombre_servicio(dr["tipo_servicio"])
                            h_s = str(dr["hora"] or "").strip()
                            item_sd = {
                                "fecha": f_in,
                                "hora": h_s,
                                "hab": hab_d,
                                "icono": ico_s,
                                "categoria": nom_s,
                                "etiqueta": nom_s,
                                "descripcion": nom_s,
                                "monto": m_det,
                                "modo": str(dr["metodo_pago"] or "En cuenta"),
                                "cargadoACuenta": True,
                            }
                            servicios_dia_lista.append(item_sd)
                            adicionales_dia_lista.append(item_sd)
                            todos_adicionales.append(item_sd)
                            todos_servicios.append(item_sd)
                            suma_serv_items = round(suma_serv_items + m_det, 2)

                # Paso 3: Respaldo histórico si queda saldo en serv_d sin ítem individual
                resto_serv = round(serv_d - suma_serv_items, 2)
                if resto_serv > 0.009:
                    tags_raw = [t.strip() for t in tipo_adic_d.split(",") if t.strip()]
                    if vis_rows:
                        tags_sin_vis = [t for t in tags_raw if t.lower() not in ("visita", "pase")]
                    else:
                        tags_sin_vis = tags_raw
                    etiqueta_fb = ", ".join(tags_sin_vis) if tags_sin_vis else (tags_raw[0] if tags_raw else "Servicio")
                    ico_fb, nom_fb = _icono_y_nombre_servicio(etiqueta_fb)
                    # Si es múltiplo exacto de 10 y dice Visita, indicar cantidad de visitas
                    if nom_fb == "Visita" and resto_serv >= 20 and int(resto_serv) % 10 == 0:
                        n_v = int(resto_serv // 10)
                        nom_fb = f"Visita ({n_v}x)"
                    item_fb = {
                        "fecha": f_in,
                        "hora": "",
                        "hab": hab_d,
                        "icono": ico_fb,
                        "categoria": nom_fb,
                        "etiqueta": nom_fb,
                        "descripcion": nom_fb,
                        "monto": resto_serv,
                        "modo": "En cuenta de estancia",
                        "cargadoACuenta": True,
                    }
                    servicios_dia_lista.append(item_fb)
                    adicionales_dia_lista.append(item_fb)
                    todos_adicionales.append(item_fb)
                    todos_servicios.append(item_fb)

            if comp_d > 0:
                nom_cp = f"Comprobante ({st.get('tipo_comp') or 'Fact/Bol'})"
                item_cp = {
                    "fecha": f_in,
                    "hora": "",
                    "hab": hab_d,
                    "icono": "🧾",
                    "categoria": "Comprobante",
                    "etiqueta": nom_cp,
                    "descripcion": nom_cp,
                    "monto": comp_d,
                    "modo": "En cuenta de estancia",
                    "cargadoACuenta": True,
                }
                servicios_dia_lista.append(item_cp)
                adicionales_dia_lista.append(item_cp)
                todos_adicionales.append(item_cp)
                todos_servicios.append(item_cp)

            adic_total_dia = round(snacks_d + serv_d + comp_d, 2)
            cargo_total_dia = round(deuda_ant_d + aloj_d + adic_total_dia, 2)
            saldo_cierre_dia = _saldo_fin_estancia(st)
            dt_in = _dmy_a_date(f_in)

            dias_detalle.append({
                "diaNum": idx_d,
                "fechaIn": f_in,
                "fechaOut": f_out,
                "horaIn": str(st.get("hora_registro") or "")[:5],
                "dow": DOW_CORTA[dt_in.weekday()],
                "hab": hab_d,
                "noches": noches_d,
                "tarifa": tarifa_d,
                "alojamiento": aloj_d,
                "adicSnacks": snacks_d,
                "adicServicios": serv_d,
                "adicComp": comp_d,
                "adicTotalDia": adic_total_dia,
                "tipoAdicional": tipo_adic_d,
                "deudaAnterior": deuda_ant_d,
                "cargoTotalDia": cargo_total_dia,
                "pagadoDia": pagado_dia_est,
                "pagadoEfecDia": round(ini_e + post_e, 2),
                "pagadoYapeDia": round(ini_y + post_y, 2),
                "pagadoVisaDia": visa_d,
                "saldoFinDia": saldo_cierre_dia,
                "serviciosDetalle": servicios_dia_lista,
                "tiendaDetalle": tienda_dia_lista,
                "adicionalesDetalle": adicionales_dia_lista,
                "pagosDetalle": pagos_dia_lista,
            })

        ult = cadena[-1]
        prim = cadena[0]
        prim_ingreso_real = cadena_full[0] if cadena_full else prim
        deuda_inicial_cadena = round(float(prim.get("deuda_anterior") or 0.0), 2)
        tot_adicionales_cuenta = round(tot_tienda_cargada + tot_serv + tot_comp, 2)
        tot_cargos_cadena = round(deuda_inicial_cadena + tot_aloj + tot_adicionales_cuenta, 2)
        tot_pagado_cadena = round(tot_pagado_efec + tot_pagado_yape + tot_pagado_visa, 2)
        saldo_final_actual = _saldo_fin_estancia(ult)

        est_base_uuid = str(row_objetivo["estancia_uuid"]) if row_objetivo is not None else str(ult.get("estancia_uuid") or "")
        fecha_base_dmy = str(row_objetivo["fecha_in_dmy"]) if row_objetivo is not None else str(ult.get("fecha_in_dmy") or "")
        try:
            fecha_base_iso = _dmy_a_iso(fecha_base_dmy) if fecha_base_dmy else ""
        except Exception:
            fecha_base_iso = ""

        return {
            "encontrado": True,
            "hab": str(hab_req or ult.get("hab") or ""),
            "habActualHuesped": str(ult.get("hab") or hab_req),
            "estanciaUuidSeleccionada": est_base_uuid,
            "fechaSeleccionadaDmy": fecha_base_dmy,
            "fechaSeleccionadaIso": fecha_base_iso,
            "estanciasDeLaHab": estancias_hab_lista,
            "huesped": str(ult.get("huesped") or ""),
            "dni": str(ult.get("dni") or ""),
            "celular": celular_huesped,
            "empresa": str(ult.get("empresa") or ""),
            "modo": "ciclo" if solo_ciclo else "ingreso",
            "fechaIngresoInicial": str(prim_ingreso_real.get("fecha_in_dmy") or ""),
            "horaIngresoInicial": str(prim_ingreso_real.get("hora_registro") or "")[:5],
            "totalNochesDesdeIngreso": len(cadena_full),
            "tieneCicloMasCorto": len(cadena_full) > len(cadena_ciclo),
            "nochesCicloActual": len(cadena_ciclo),
            "habitacionesMultiDia": habs_multidia,
            "resumen": {
                "diasCount": len(dias_detalle),
                "totalNoches": tot_noches,
                "habsRecorridas": habs_recorridas,
                "fechaInicio": str(prim.get("fecha_in_dmy") or ""),
                "horaInicio": str(prim.get("hora_registro") or "")[:5],
                "fechaUltima": str(ult.get("fecha_in_dmy") or ""),
                "fechaSalidaUltima": str(ult.get("fecha_out_dmy") or ""),
                "deudaInicialArrastrada": deuda_inicial_cadena,
                "totalAlojamiento": tot_aloj,
                "totalTienda": tot_tienda_cargada,
                "totalTiendaPagadaDirecto": tot_tienda_pagada,
                "totalServicios": tot_serv,
                "totalComp": tot_comp,
                "totalAdicionales": tot_adicionales_cuenta,
                "granTotalCargos": tot_cargos_cadena,
                "totalPagado": tot_pagado_cadena,
                "pagadoEfectivo": tot_pagado_efec,
                "pagadoYape": tot_pagado_yape,
                "pagadoVisaBanco": tot_pagado_visa,
                "saldoActual": saldo_final_actual,
            },
            "todosLosPagos": todos_pagos,
            "todosLosAdicionales": todos_adicionales,
            "todosTienda": todos_tienda,
            "todosServicios": todos_servicios,
            "dias": dias_detalle,
        }
    finally:
        conn.close()


def generar_pdf_cuenta_hab_local(datos: Optional[Dict[str, Any]] = None) -> Path:
    """Genera un archivo PDF formal A4 con el Estado de Cuenta por Habitación:
    - Franja Ejecutiva (Día de Ingreso, Huésped y Recorrido, Noches Jaladas y Deuda Pendiente Actual).
    - Tabla Día por Día separando Adic. Servicio y Tienda.
    - Lista de Pagos Realizados y Lista de Tienda / Adicionales."""
    from pathlib import Path as _Path
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from config import DESCARGAS_RECEPCION_DIR, NOMBRE_HOTEL, PERFIL_HOTEL

    info = obtener_cuenta_detallada_hab_local(datos or {})
    if not info.get("encontrado"):
        raise ValueError(info.get("mensaje") or "No se encontró cuenta para la habitación indicada.")

    hab = str(info.get("hab") or "")
    huesped = str(info.get("huesped") or "HUÉSPED")
    dni = str(info.get("dni") or "-")
    R = info.get("resumen") or {}
    dias = info.get("dias") or []
    pagos_tot = info.get("todosLosPagos") or []
    adic_tot = info.get("todosLosAdicionales") or []

    out_dir = DESCARGAS_RECEPCION_DIR
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        out_dir = _Path(__file__).resolve().parent / "data" / "pdfs"
        out_dir.mkdir(parents=True, exist_ok=True)

    fecha_tag = datetime.now().strftime("%Y%m%d_%H%M")
    pdf_path = out_dir / f"Estado_Cuenta_Hab_{hab}_{fecha_tag}.pdf"

    doc = SimpleDocTemplate(
        str(pdf_path),
        pagesize=A4,
        leftMargin=9 * mm,
        rightMargin=9 * mm,
        topMargin=10 * mm,
        bottomMargin=10 * mm,
        title=f"Estado de Cuenta Hab. {hab} - {huesped}",
    )

    styles = getSampleStyleSheet()
    st_title = ParagraphStyle(
        "TitleCuenta",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=13,
        leading=15,
        textColor=colors.HexColor("#1E293B"),
        spaceAfter=2,
    )
    st_sub = ParagraphStyle(
        "SubCuenta",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor("#475569"),
    )
    st_sec = ParagraphStyle(
        "SecCuenta",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=9.5,
        leading=12,
        textColor=colors.HexColor("#1E293B"),
        spaceBefore=6,
        spaceAfter=3,
    )
    st_cell = ParagraphStyle(
        "CellCuenta",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=7.2,
        leading=9.0,
        textColor=colors.HexColor("#0F172A"),
    )
    st_cell_b = ParagraphStyle(
        "CellBoldCuenta",
        parent=st_cell,
        fontName="Helvetica-Bold",
    )

    story = []
    ahora_str = datetime.now().strftime("%d/%m/%Y %H:%M")
    ruc_hotel = str(PERFIL_HOTEL.get("ruc_empresa") or "")
    story.append(Paragraph(f"{NOMBRE_HOTEL.upper()} {'(RUC: ' + ruc_hotel + ')' if ruc_hotel else ''}", st_sub))
    story.append(Paragraph(f"CUENTA DETALLADA POR HABITACIÓN — HAB. {hab}", st_title))
    story.append(Paragraph(f"Emitido el: {ahora_str}", st_sub))
    story.append(Spacer(1, 3 * mm))

    habs_rec = " -> ".join(R.get("habsRecorridas") or [hab])
    f_ing = info.get("fechaIngresoInicial") or R.get("fechaInicio") or "-"
    h_ing = info.get("horaIngresoInicial") or R.get("horaInicio") or ""
    f_ult = R.get("fechaUltima") or "-"
    saldo_act = float(R.get("saldoActual") or 0.0)
    etiqueta_saldo = "DEUDA PENDIENTE" if saldo_act > 0.009 else ("SALDO A FAVOR" if saldo_act < -0.009 else "CUENTA AL DÍA")

    # Franja Ejecutiva de 4 bloques (Día de Ingreso | Huésped y Recorrido | Noches Jaladas | Deuda Pendiente)
    datos_resumen = [
        [
            Paragraph("<b>DÍA DE INGRESO</b>", ParagraphStyle("h0", parent=st_cell_b, textColor=colors.white)),
            Paragraph("<b>HUÉSPED Y RECORRIDO</b>", ParagraphStyle("h1", parent=st_cell_b, textColor=colors.white)),
            Paragraph("<b>NOCHES JALADAS</b>", ParagraphStyle("h2", parent=st_cell_b, textColor=colors.white)),
            Paragraph(f"<b>{etiqueta_saldo}</b>", ParagraphStyle("h3", parent=st_cell_b, textColor=colors.white)),
        ],
        [
            Paragraph(f"<b>{f_ing}</b><br/>{('Hora: ' + h_ing) if h_ing else ''}", st_cell),
            Paragraph(f"<b>{huesped}</b> (DNI: {dni})<br/>Recorrido: <b>{habs_rec}</b>", st_cell),
            Paragraph(f"<b>{R.get('diasCount', 1)} noche(s)</b><br/>{R.get('fechaInicio', '')} -> {f_ult}", st_cell),
            Paragraph(f"<b>S/ {abs(saldo_act):.2f}</b><br/>Al cierre del {f_ult}", st_cell_b),
        ],
    ]
    t_res = Table(datos_resumen, colWidths=[38 * mm, 72 * mm, 42 * mm, 40 * mm])
    t_res.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (2, 0), colors.HexColor("#1E293B")),
        ("BACKGROUND", (3, 0), (3, 0), colors.HexColor("#991B1B") if saldo_act > 0.009 else colors.HexColor("#065F46")),
        ("BACKGROUND", (0, 1), (2, 1), colors.HexColor("#F8FAFC")),
        ("BACKGROUND", (3, 1), (3, 1), colors.HexColor("#FEF2F2") if saldo_act > 0.009 else colors.HexColor("#ECFDF5")),
        ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#94A3B8")),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(t_res)

    story.append(Paragraph("1. DESGLOSE DÍA POR DÍA", st_sec))

    filas_dias = [[
        Paragraph("<b>Noche / Fecha</b>", ParagraphStyle("th1", parent=st_cell_b, textColor=colors.white)),
        Paragraph("<b>Hab.</b>", ParagraphStyle("th2", parent=st_cell_b, textColor=colors.white)),
        Paragraph("<b>Deuda Ant.</b>", ParagraphStyle("th3", parent=st_cell_b, textColor=colors.white)),
        Paragraph("<b>Tarifa</b>", ParagraphStyle("th4", parent=st_cell_b, textColor=colors.white)),
        Paragraph("<b>Adic. Servicio</b>", ParagraphStyle("th5", parent=st_cell_b, textColor=colors.white)),
        Paragraph("<b>Tienda</b>", ParagraphStyle("th6", parent=st_cell_b, textColor=colors.white)),
        Paragraph("<b>Total Día</b>", ParagraphStyle("th7", parent=st_cell_b, textColor=colors.white)),
        Paragraph("<b>Pagos del Día</b>", ParagraphStyle("th8", parent=st_cell_b, textColor=colors.white)),
        Paragraph("<b>Deuda Día</b>", ParagraphStyle("th9", parent=st_cell_b, textColor=colors.white)),
    ]]

    for d in dias:
        serv_txts = []
        for sv in d.get("serviciosDetalle") or []:
            h_sv = f" ({sv['hora']})" if sv.get("hora") else ""
            serv_txts.append(f"• {sv['etiqueta']}{h_sv}: S/ {float(sv['monto']):.2f}")
        serv_str = "<br/>".join(serv_txts) if serv_txts else "—"

        tnd_txts = []
        for tn in d.get("tiendaDetalle") or []:
            tnd_txts.append(f"• {tn['etiqueta']}: S/ {float(tn['monto']):.2f}")
        tnd_str = "<br/>".join(tnd_txts) if tnd_txts else "—"

        pag_dia_monto = float(d.get("pagadoDia") or 0.0)
        pag_txts = []
        if pag_dia_monto > 0.009:
            pag_txts.append(f"<b>Abonó día: S/ {pag_dia_monto:.2f}</b>")
        for p in d.get("pagosDetalle") or []:
            op_s = f" [Op:{p['nroOperacion']}]" if p.get("nroOperacion") else ""
            h_s = f" ({p['hora']})" if p.get("hora") else ""
            pag_txts.append(f"• {p['medioPago']}: <b>S/ {float(p['monto']):.2f}</b>{h_s}{op_s}")
        pag_str = "<br/>".join(pag_txts) if pag_txts else "S/ 0.00 (Sin abono)"

        s_fin = float(d.get("saldoFinDia") or 0.0)
        s_txt = f"Debe S/ {s_fin:.2f}" if s_fin > 0.009 else (f"A favor S/ {abs(s_fin):.2f}" if s_fin < -0.009 else "S/ 0.00")

        filas_dias.append([
            Paragraph(f"<b>#{d['diaNum']}</b> {d['dow']} {d['fechaIn']}", st_cell),
            Paragraph(f"<b>{d['hab']}</b>", st_cell),
            Paragraph(f"S/ {float(d.get('deudaAnterior') or 0.0):.2f}", st_cell),
            Paragraph(f"S/ {float(d.get('alojamiento') or 0.0):.2f}", st_cell),
            Paragraph(serv_str, st_cell),
            Paragraph(tnd_str, st_cell),
            Paragraph(f"<b>S/ {float(d.get('cargoTotalDia') or 0.0):.2f}</b>", st_cell),
            Paragraph(pag_str, st_cell),
            Paragraph(f"<b>{s_txt}</b>", st_cell_b),
        ])

    t_dias = Table(
        filas_dias,
        colWidths=[23 * mm, 10 * mm, 16 * mm, 15 * mm, 29 * mm, 27 * mm, 17 * mm, 35 * mm, 20 * mm],
        repeatRows=1,
    )
    estilo_dias = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1E293B")),
        ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#94A3B8")),
        ("INNERGRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#CBD5E1")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 2.5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2.5),
    ]
    for idx_r in range(1, len(filas_dias)):
        if idx_r % 2 == 0:
            estilo_dias.append(("BACKGROUND", (0, idx_r), (-1, idx_r), colors.HexColor("#F8FAFC")))
    t_dias.setStyle(TableStyle(estilo_dias))
    story.append(t_dias)

    if pagos_tot:
        story.append(Paragraph(f"2. LISTA DE PAGOS REALIZADOS ({len(pagos_tot)})", st_sec))
        filas_p = [[
            Paragraph("<b>Fecha y Hora</b>", ParagraphStyle("tp1", parent=st_cell_b, textColor=colors.white)),
            Paragraph("<b>Hab.</b>", ParagraphStyle("tp2", parent=st_cell_b, textColor=colors.white)),
            Paragraph("<b>Medio de Pago</b>", ParagraphStyle("tp3", parent=st_cell_b, textColor=colors.white)),
            Paragraph("<b>Concepto / Tipo</b>", ParagraphStyle("tp4", parent=st_cell_b, textColor=colors.white)),
            Paragraph("<b>N° Operación</b>", ParagraphStyle("tp5", parent=st_cell_b, textColor=colors.white)),
            Paragraph("<b>Monto S/</b>", ParagraphStyle("tp6", parent=st_cell_b, textColor=colors.white)),
        ]]
        for p in pagos_tot:
            filas_p.append([
                Paragraph(f"{p['fecha']} {p.get('hora') or ''}", st_cell),
                Paragraph(str(p.get("hab") or ""), st_cell),
                Paragraph(f"<b>{p.get('medioPago') or ''}</b>", st_cell),
                Paragraph(str(p.get("concepto") or p.get("tipoPago") or ""), st_cell),
                Paragraph(str(p.get("nroOperacion") or "—"), st_cell),
                Paragraph(f"<b>S/ {float(p.get('monto') or 0.0):.2f}</b>", st_cell),
            ])
        t_pag = Table(filas_p, colWidths=[30 * mm, 14 * mm, 28 * mm, 60 * mm, 35 * mm, 25 * mm], repeatRows=1)
        t_pag.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#065F46")),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#94A3B8")),
            ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#CBD5E1")),
            ("TOPPADDING", (0, 0), (-1, -1), 2.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ]))
        story.append(t_pag)

    if adic_tot:
        story.append(Paragraph(f"3. LISTA DE TIENDA Y SERVICIOS ADICIONALES ({len(adic_tot)})", st_sec))
        filas_a = [[
            Paragraph("<b>Fecha y Hora</b>", ParagraphStyle("ta1", parent=st_cell_b, textColor=colors.white)),
            Paragraph("<b>Hab.</b>", ParagraphStyle("ta2", parent=st_cell_b, textColor=colors.white)),
            Paragraph("<b>Categoría</b>", ParagraphStyle("ta3", parent=st_cell_b, textColor=colors.white)),
            Paragraph("<b>Detalle</b>", ParagraphStyle("ta4", parent=st_cell_b, textColor=colors.white)),
            Paragraph("<b>Monto S/</b>", ParagraphStyle("ta5", parent=st_cell_b, textColor=colors.white)),
        ]]
        for a in adic_tot:
            filas_a.append([
                Paragraph(f"{a['fecha']} {a.get('hora') or ''}", st_cell),
                Paragraph(str(a.get("hab") or ""), st_cell),
                Paragraph(f"<b>{a.get('categoria') or ''}</b>", st_cell),
                Paragraph(str(a.get("descripcion") or ""), st_cell),
                Paragraph(f"<b>S/ {float(a.get('monto') or 0.0):.2f}</b>", st_cell),
            ])
        t_adic = Table(filas_a, colWidths=[30 * mm, 14 * mm, 32 * mm, 91 * mm, 25 * mm], repeatRows=1)
        t_adic.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#78350F")),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#94A3B8")),
            ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#CBD5E1")),
            ("TOPPADDING", (0, 0), (-1, -1), 2.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ]))
        story.append(t_adic)

    doc.build(story)
    return pdf_path


def enviar_wsp_cuenta_hab_local(datos: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Genera el PDF del Estado de Cuenta de la habitación y lo envía por WhatsApp al número indicado."""
    from sunat_wsp_service import _despachar_wsp_pdf
    from config import NOMBRE_HOTEL

    datos = datos or {}
    hab = str(datos.get("hab") or "").strip()
    tel_raw = str(datos.get("telefono") or "").strip()
    solo_dig = "".join(ch for ch in tel_raw if ch.isdigit())
    if len(solo_dig) == 11 and solo_dig.startswith("51"):
        solo_dig = solo_dig[2:]
    if len(solo_dig) < 9:
        raise ValueError("Ingresa un número de WhatsApp válido de 9 dígitos (ej. 987654321).")

    info = obtener_cuenta_detallada_hab_local(datos)
    if not info.get("encontrado"):
        raise ValueError(info.get("mensaje") or f"No se encontró cuenta para la Hab. {hab}.")

    pdf_path = generar_pdf_cuenta_hab_local(datos)
    R = info.get("resumen") or {}
    saldo_act = float(R.get("saldoActual") or 0.0)
    habs_rec = " → ".join(R.get("habsRecorridas") or [str(info["hab"])])
    estado_saldo = (
        f"DEUDA PENDIENTE: S/ {saldo_act:.2f}"
        if saldo_act > 0.009
        else (f"SALDO A FAVOR: S/ {abs(saldo_act):.2f}" if saldo_act < -0.009 else "CUENTA AL DÍA (S/ 0.00)")
    )

    mensaje = (
        f"📋 *{NOMBRE_HOTEL} — CUENTA DETALLADA HAB. {info['hab']}*\n"
        f"👤 *Huésped:* {info['huesped']}\n"
        f"📅 *Día de Ingreso:* {info.get('fechaIngresoInicial') or R.get('fechaInicio')}\n"
        f"🛏️ *Recorrido:* {habs_rec}\n"
        f"🌙 *Noches Jaladas:* {R.get('diasCount', 1)} noche(s) (hasta {R.get('fechaUltima')})\n"
        f"⚖️ *{estado_saldo}*\n\n"
        f"Se adjunta el PDF con el desglose detallado por día, lista de pagos y consumo de tienda."
    )

    ok, out_txt, err_txt = _despachar_wsp_pdf(
        telefono=solo_dig,
        pdf_path=str(pdf_path),
        mensaje=mensaje,
        nro_cp=pdf_path.name,
        hab=str(info["hab"]),
    )
    if not ok:
        raise RuntimeError(err_txt or out_txt or "No se pudo enviar el PDF por WhatsApp.")

    return {
        "ok": True,
        "hab": info["hab"],
        "telefono": solo_dig,
        "pdfNombre": pdf_path.name,
        "mensaje": f"✅ Estado de cuenta en PDF enviado por WhatsApp al {solo_dig}.",
    }
