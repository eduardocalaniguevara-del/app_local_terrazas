#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Módulo ⏰ Horario y Pagos de Personal (offline-first, SQLite).

- Marcación abierta (sin contraseña) + edición libre de horas con rastro de auditoría inborrable.
- Liquidación de pagos protegida por contraseña (sueldo, faltas, descansos, feriados, horas extras,
  AFP, adelantos de sueldo desde Gastos y consumos de tienda 'Cargar a la Cuenta').
"""

from __future__ import annotations

import calendar
import hashlib
import json
import re
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from database import db_transaction, get_connection
import domain_logic as dl

# Hash SHA-256 de la clave de Cuentas/Pagos (la clave nunca se guarda ni se muestra en claro).
_CLAVE_CUENTAS_SHA256 = "947ebb53c00a42907060aea69f503600eec08ef75da83109f2958865a4d8f934"

CAMPOS_HORA = ("e1", "s1", "e2", "s2")
CAMPOS_ADMIN: Tuple[str, ...] = ()  # Estado (F) y Horas Extras (G) libres sin clave (auditados siempre)
CAMPOS_EDITABLES = CAMPOS_HORA + ("estado", "extras", "extra_noche", "nota")
ETIQUETA_CAMPO = {
    "e1": "Entrada 1", "s1": "Salida 1", "e2": "Entrada 2", "s2": "Salida 2",
    "estado": "Estado (F)", "extras": "Horas extras (G)", "extra_noche": "Extra nocturno", "nota": "Nota",
}
ESTADOS_VALIDOS = ("", "FALTÓ", "MEDIO TIEMPO", "MEDIA FALTA", "DESCANSO", "MEDIO DESCANSO", "LIBRE", "FERIADO")
DOW_ES = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
DOW_LARGO = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]


# ==============================================================================
# UTILIDADES
# ==============================================================================

def validar_clave_cuentas(datos: Dict[str, Any]) -> None:
    clave = str((datos or {}).get("clave") or "").strip().lower()
    if hashlib.sha256(clave.encode("utf-8")).hexdigest() != _CLAVE_CUENTAS_SHA256:
        raise ValueError("Contraseña incorrecta.")


def verificar_clave_cuentas_horario_local(datos: Dict[str, Any]) -> Dict[str, Any]:
    validar_clave_cuentas(datos)
    return {"ok": True}


def _norm_hora(v: Any) -> str:
    """'8' -> '08:00', '8:5' -> '08:05', '0805' -> '08:05', '8.30' -> '08:30', '8:30 pm' -> '20:30'. Vacío -> ''."""
    s = str(v or "").strip().lower().replace(".", ":").replace("h", ":").replace(" ", "")
    if not s:
        return ""
    pm = s.endswith("pm") or s.endswith("p.m.")
    am = s.endswith("am") or s.endswith("a.m.")
    s = re.sub(r"(a\.?m\.?|p\.?m\.?)$", "", s).rstrip(":")
    if re.fullmatch(r"\d{3,4}", s):
        s = s[:-2] + ":" + s[-2:]
    m = re.fullmatch(r"(\d{1,2})(?::(\d{1,2}))?", s)
    if not m:
        raise ValueError(f"Hora no válida: '{v}'. Usa formato 8:30 o 20:00.")
    h = int(m.group(1))
    mi = int(m.group(2) or 0)
    if pm and h < 12:
        h += 12
    if am and h == 12:
        h = 0
    if h > 23 or mi > 59:
        raise ValueError(f"Hora no válida: '{v}'.")
    return f"{h:02d}:{mi:02d}"


def _minutos(hm: str) -> int:
    p = hm.split(":")
    return int(p[0]) * 60 + int(p[1])


def _horas_pares(r: Dict[str, Any]) -> float:
    total = 0
    for a, b in (("e1", "s1"), ("e2", "s2")):
        if r.get(a) and r.get(b):
            d = _minutos(r[b]) - _minutos(r[a])
            if d <= 0:
                d += 24 * 60  # turno que cruza medianoche
            total += d
    # Si marcó entrada en E1 y salida final en S2 (turno corrido sin S1/E2 intermedios)
    if total == 0 and r.get("e1") and r.get("s2") and not r.get("s1") and not r.get("e2"):
        d = _minutos(r["s2"]) - _minutos(r["e1"])
        if d <= 0:
            d += 24 * 60
        total = d
    return round(total / 60.0, 2)


def _analizar_jornada_dia(r: Dict[str, Any], cfg: Dict[str, Any], fer_nom: str = "") -> Dict[str, Any]:
    """Detecta excesos de horas (horas extras) y cortes de hora (horas de menos) explicando los tramos marcados."""
    h_dia = _horas_pares(r)
    es_noche = str(cfg.get("turno") or "").upper() == "NOCHE"
    jornada_base = 12.0 if es_noche else 8.0
    est = str(r.get("estado") or "").strip().upper()
    if est in ("MEDIO TIEMPO", "MEDIA FALTA", "MEDIO DESCANSO"):
        esperado = round(jornada_base / 2.0, 2)
    elif est in ("FALTÓ", "LIBRE"):
        esperado = 0.0
    else:
        esperado = jornada_base

    e1, s1, e2, s2 = str(r.get("e1") or ""), str(r.get("s1") or ""), str(r.get("e2") or ""), str(r.get("s2") or "")
    tramos = []
    if e1 and s1:
        tramos.append(f"{e1}–{s1}")
    if e2 and s2:
        tramos.append(f"{e2}–{s2}")
    if not tramos and e1 and s2 and not s1 and not e2:
        tramos.append(f"{e1}–{s2} (corrido)")
    elif (e1 and not s1 and not s2) or (e2 and not s2):
        marcadas = [f"{k.upper()}={v}" for k, v in (("e1", e1), ("s1", s1), ("e2", e2), ("s2", s2)) if v]
        tramos.append(" ".join(marcadas) + " (incompleto)")
    tramos_str = " y ".join(tramos)

    hx_raw = str(r.get("extras") or "").strip()
    hx_val = parse_extras(hx_raw) if hx_raw else 0.0
    es_fer = bool(fer_nom) or (est == "FERIADO")
    mult_hx = 2 if es_fer else 1
    hx_efec = round(hx_val * mult_hx, 2)

    diff = round(h_dia - esperado, 2) if (h_dia > 0 and esperado > 0) else 0.0
    detalle_corte = ""
    if diff <= -0.75:
        # Identificar en qué bloque estuvo el corte de hora
        notas_c = []
        if not es_noche:
            if e1 and _minutos(e1) >= 9 * 60:
                notas_c.append(f"entró T1 a las {e1}")
            if e1 and s1:
                h_t1 = round((_minutos(s1) - _minutos(e1)) / 60.0, 1)
                if h_t1 < 3.5 and not e2:
                    notas_c.append(f"solo hizo {h_t1}h en T1")
            if e2 and _minutos(e2) >= 16 * 60 + 45:
                notas_c.append(f"entró T2 a las {e2}")
            if e2 and s2 and _minutos(s2) <= 19 * 60 + 15:
                notas_c.append(f"salió T2 a las {s2}")
            if e1 and s1 and not e2 and not s2 and esperado >= 8.0 and h_dia <= 5.5:
                notas_c.append("solo marcó 1 turno sin estado de medio tiempo")
        detalle_corte = ", ".join(notas_c) if notas_c else f"faltaron {abs(diff):.1f} h para completar {esperado:g} h"
    elif diff >= 0.75:
        notas_e = []
        if not es_noche:
            if e1 and s1 and not e2 and _minutos(s1) >= 17 * 60:
                notas_e.append(f"salió {s1}")
            elif e2 and s2 and _minutos(s2) >= 20 * 60 + 45:
                notas_e.append(f"salió {s2}")
            elif e1 and s2 and not s1 and not e2:
                notas_e.append("turno corrido completo")
            elif e1 and s1 and _minutos(s1) >= 13 * 60:
                notas_e.append(f"salió T1 {s1}")
        else:
            if e1 and 12 * 60 <= _minutos(e1) <= 19 * 60:
                notas_e.append(f"entró {e1}")
        detalle_corte = ", ".join(notas_e)

    tipo_obs = ""
    if diff <= -0.75:
        tipo_obs = "CORTE"
    elif hx_val > 0:
        tipo_obs = "EXTRA_ANOTADA"
    elif diff >= 0.85:
        tipo_obs = "EXTRA_SIN_ANOTAR"

    return {
        "horas": h_dia,
        "esperado": esperado,
        "diffHoras": diff,
        "tramos": tramos_str,
        "detalleMotivo": detalle_corte,
        "tipoObs": tipo_obs,
        "extrasBase": hx_val,
        "extrasEfectivas": hx_efec,
        "extrasMult": mult_hx,
    }


def parse_extras(txt: Any) -> float:
    """'4' -> 4 h, '3/8' -> 3 h (numerador sobre jornada de 8), '1.5' -> 1.5 h."""
    t = str(txt or "").strip().replace(",", ".")
    if not t:
        return 0.0
    if "/" in t:
        a, b = t.split("/", 1)
        num, den = float(a), float(b)
        if den <= 0:
            raise ValueError("Horas extras no válidas.")
        return round(num / den * 8.0, 4)
    return round(float(t), 4)


def _norm_extras(v: Any) -> str:
    t = str(v or "").strip().replace(",", ".")
    if not t:
        return ""
    try:
        parse_extras(t)
    except Exception:
        raise ValueError("Horas extras no válidas. Ejemplos: 4, 2.5 o 4/8.")
    return t


def _norm_estado(v: Any) -> str:
    t = str(v or "").strip().upper().replace("FALTO", "FALTÓ").replace("MEDIO TIMEPO", "MEDIO TIEMPO")
    if t not in ESTADOS_VALIDOS:
        raise ValueError(f"Estado no válido: '{v}'.")
    return t


def _easter(year: int) -> date:
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mes = (h + l - 7 * m + 114) // 31
    dia = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, mes, dia)


def feriados_peru(year: int) -> Dict[str, str]:
    """Feriados nacionales (clave dd/mm/yyyy)."""
    fijos = [
        (1, 1, "Año Nuevo"), (1, 5, "Día del Trabajo"), (7, 6, "Batalla de Arica"),
        (29, 6, "San Pedro y San Pablo"), (23, 7, "Día de la Fuerza Aérea"),
        (28, 7, "Fiestas Patrias"), (29, 7, "Fiestas Patrias"), (6, 8, "Batalla de Junín"),
        (30, 8, "Santa Rosa de Lima"), (8, 10, "Combate de Angamos"), (1, 11, "Todos los Santos"),
        (8, 12, "Inmaculada Concepción"), (9, 12, "Batalla de Ayacucho"), (25, 12, "Navidad"),
    ]
    res = {f"{d:02d}/{m:02d}/{year}": n for d, m, n in fijos}
    pascua = _easter(year)
    jueves = pascua - timedelta(days=3)
    viernes = pascua - timedelta(days=2)
    res[jueves.strftime("%d/%m/%Y")] = "Jueves Santo"
    res[viernes.strftime("%d/%m/%Y")] = "Viernes Santo"
    return res


def _fecha(dmy: str) -> date:
    return datetime.strptime(dl.normalizar_fecha_dmy(dmy), "%d/%m/%Y").date()


def _dmy(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def _ymd(d: date) -> int:
    return d.year * 10000 + d.month * 100 + d.day


def _cfg_empleado(conn, empleado: str) -> Dict[str, Any]:
    r = conn.execute("SELECT * FROM personal_config WHERE empleado = ? COLLATE NOCASE", (str(empleado or "").strip(),)).fetchone()
    if not r:
        raise ValueError(f"Empleado no encontrado: {empleado}")
    return dict(r)


def _lista_empleados(conn, solo_activos: bool = True) -> List[Dict[str, Any]]:
    q = "SELECT * FROM personal_config" + (" WHERE activo = 1" if solo_activos else "") + " ORDER BY orden, empleado"
    return [dict(r) for r in conn.execute(q).fetchall()]


def _fila_vacia(emp: str, fecha_dmy: str) -> Dict[str, Any]:
    return {"empleado": emp, "fecha_dmy": fecha_dmy, "ymd": dl.fecha_dmy_a_ymd(fecha_dmy), "e1": "", "s1": "", "e2": "", "s2": "",
            "estado": "", "extras": "", "extra_noche": 0, "nota": "", "ult_marca_ts": "", "updated_at": "", "equipo": "", "synced": 1}


def _leer_fila(conn, emp: str, fecha_dmy: str) -> Dict[str, Any]:
    r = conn.execute("SELECT * FROM horario_registros WHERE empleado = ? AND fecha_dmy = ?", (emp, fecha_dmy)).fetchone()
    return dict(r) if r else _fila_vacia(emp, fecha_dmy)


def _guardar_fila(conn, fila: Dict[str, Any]) -> None:
    ahora = datetime.now().isoformat(timespec="seconds")
    fila["updated_at"] = ahora
    fila["equipo"] = dl._nombre_equipo()
    fila["synced"] = 0
    fila["ymd"] = dl.fecha_dmy_a_ymd(fila["fecha_dmy"])
    conn.execute(
        """
        INSERT OR REPLACE INTO horario_registros
            (empleado, fecha_dmy, ymd, e1, s1, e2, s2, estado, extras, extra_noche, nota, ult_marca_ts, updated_at, equipo, synced)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)
        """,
        (fila["empleado"], fila["fecha_dmy"], fila["ymd"], fila["e1"], fila["s1"], fila["e2"], fila["s2"], fila["estado"],
         fila["extras"], int(fila["extra_noche"] or 0), fila["nota"], fila.get("ult_marca_ts", ""), ahora, fila["equipo"]),
    )
    dl._encolar_operacion(
        conn, "HORARIO_UPSERT", f"{fila['empleado']}|{fila['fecha_dmy']}",
        {"empleado": fila["empleado"], "fecha_dmy": fila["fecha_dmy"], "updated_at": ahora,
         "fila": [fila["empleado"], fila["fecha_dmy"], fila["e1"], fila["s1"], fila["e2"], fila["s2"], fila["estado"],
                  fila["extras"], int(fila["extra_noche"] or 0), fila["nota"], ahora, fila["equipo"]]},
    )


def _auditar(conn, emp: str, fecha_dmy: str, campo: str, antes: str, nuevo: str) -> None:
    audit_uuid = str(uuid.uuid4())
    ts = datetime.now().isoformat(timespec="seconds")
    equipo = dl._nombre_equipo()
    conn.execute(
        "INSERT INTO horario_auditoria (audit_uuid, empleado, fecha_dmy, campo, valor_anterior, valor_nuevo, editado_ts, equipo, synced) "
        "VALUES (?,?,?,?,?,?,?,?,0)",
        (audit_uuid, emp, fecha_dmy, campo, str(antes), str(nuevo), ts, equipo),
    )
    dl._encolar_operacion(
        conn, "HORARIO_AUDIT", audit_uuid,
        {"audit_uuid": audit_uuid, "fila": [audit_uuid, emp, fecha_dmy, ETIQUETA_CAMPO.get(campo, campo), str(antes), str(nuevo), ts, equipo]},
    )


def _fila_publica(r: Dict[str, Any], cfg: Dict[str, Any], hoy: date, feriados: Dict[str, str]) -> Dict[str, Any]:
    d = _fecha(r["fecha_dmy"])
    fer_nom = feriados.get(r["fecha_dmy"], "")
    info_j = _analizar_jornada_dia(r, cfg, fer_nom)
    # El feriado manda sobre el descanso: si el día es feriado, no se marca como descanso
    return {
        "fecha": r["fecha_dmy"], "fechaIso": d.isoformat(), "dia": d.day, "dow": d.weekday(), "dowNombre": DOW_ES[d.weekday()],
        "esDescanso": (d.weekday() == int(cfg["descanso_dow"])) and not bool(fer_nom),
        "feriado": fer_nom,
        "e1": r["e1"], "s1": r["s1"], "e2": r["e2"], "s2": r["s2"],
        "estado": r["estado"], "extras": r["extras"], "extraNoche": int(r["extra_noche"] or 0), "nota": r["nota"],
        "horas": info_j["horas"], "esperado": info_j["esperado"], "diffHoras": info_j["diffHoras"],
        "tramos": info_j["tramos"], "detalleMotivo": info_j["detalleMotivo"], "tipoObs": info_j["tipoObs"],
        "extrasBase": info_j["extrasBase"], "extrasEfectivas": info_j["extrasEfectivas"], "extrasMult": info_j["extrasMult"],
        "desvios": {},
        "esHoy": d == hoy, "futuro": d > hoy,
    }


def _media_robusta_min(vals: List[int]) -> Optional[int]:
    if not vals:
        return None
    s_vals = sorted(vals)
    med = s_vals[len(s_vals) // 2]
    cercanos = [x for x in vals if abs(x - med) <= 120] or vals
    return int(round(sum(cercanos) / len(cercanos)))


def _fmt_hm(p_min: int) -> str:
    return f"{(p_min // 60) % 24:02d}:{p_min % 60:02d}"


def _es_fila_corrida_dia(f: Dict[str, Any]) -> bool:
    """Detecta si un día fue trabajado en horario continuado (E1–S1 >= 6h sin E2/S2) en vez de partido."""
    if f.get("e2") or f.get("s2"):
        return False
    if not f.get("e1") or not f.get("s1"):
        return False
    d = _minutos(str(f["s1"])) - _minutos(str(f["e1"]))
    if d <= 0:
        d += 1440
    return d >= 360  # >= 6 horas continuas en el bloque 1


def _calcular_promedios_y_desvios(filas: List[Dict[str, Any]], cfg: Optional[Dict[str, Any]] = None, umbral_min: int = 30) -> Dict[str, str]:
    """Calcula el promedio por columna (soportando meses mixtos continuado + partido) y aplica
    concordancia de jornada: si un día cumple su bloque/jornada exacta en orden cronológico (ej. cambiar
    15 días a turno continuado 08:00-16:00 o partido 08:00-12:00 / 16:00-20:00 dando ~8h sin horas extras),
    no genera falsas alertas naranjas; solo resalta cuando hay horas extras, cortes o falta de concordancia."""
    es_noche = str((cfg or {}).get("turno") or "").upper() == "NOCHE"
    jornada_base = 12.0 if es_noche else 8.0
    hoy_iso = datetime.now().date().isoformat()

    promedios: Dict[str, str] = {}
    prom_mins: Dict[str, int] = {}

    s1_corrido_vals = [_minutos(str(f["s1"])) for f in filas if not es_noche and f.get("s1") and _es_fila_corrida_dia(f)]
    s1_partido_vals = [_minutos(str(f["s1"])) for f in filas if (es_noche or not _es_fila_corrida_dia(f)) and f.get("s1")]
    usar_s1_dual = (not es_noche) and len(s1_corrido_vals) >= 1 and len(s1_partido_vals) >= 1
    p_s1_corr = _media_robusta_min(s1_corrido_vals) if s1_corrido_vals else None
    p_s1_part = _media_robusta_min(s1_partido_vals) if s1_partido_vals else None

    for c in CAMPOS_HORA:
        if c == "s1" and usar_s1_dual and p_s1_part is not None and p_s1_corr is not None:
            promedios["s1"] = f"{_fmt_hm(p_s1_part)}/{_fmt_hm(p_s1_corr)}"
            continue
        vals = [_minutos(str(f.get(c) or "")) for f in filas if f.get(c)]
        p_min = _media_robusta_min(vals)
        if p_min is None:
            promedios[c] = ""
            continue
        prom_mins[c] = p_min
        promedios[c] = _fmt_hm(p_min)

    for f in filas:
        desv: Dict[str, Any] = {}
        e1, s1, e2, s2 = (str(f.get(k) or "").strip() for k in CAMPOS_HORA)
        if not any((e1, s1, e2, s2)):
            f["desvios"] = desv
            continue

        h_dia = _horas_pares(f)
        est = str(f.get("estado") or "").strip().upper()
        esp = round(jornada_base / 2.0, 2) if est in ("MEDIO TIEMPO", "MEDIA FALTA", "MEDIO DESCANSO") else jornada_base
        es_hoy = bool(f.get("esHoy")) or (str(f.get("fechaIso") or "") == hoy_iso) or (f.get("fecha_dmy") == _dmy(datetime.now().date()))

        # 1. Validar concordancia cronológica y pares completos (excepto si hoy sigue en turno abierto)
        if not es_hoy:
            if e1 and not s1 and not s2:
                desv["e1"] = {"campo": "e1", "etiqueta": "Entrada 1", "hora": e1, "prom": promedios.get("e1") or "—", "diffMin": 0, "diffTxt": "falta salida"}
            if e2 and not s2:
                desv["e2"] = {"campo": "e2", "etiqueta": "Entrada 2", "hora": e2, "prom": promedios.get("e2") or "—", "diffMin": 0, "diffTxt": "falta salida"}
        if not es_noche:
            if e1 and s1 and _minutos(s1) <= _minutos(e1):
                desv["s1"] = {"campo": "s1", "etiqueta": "Salida 1", "hora": s1, "prom": e1, "diffMin": _minutos(s1) - _minutos(e1), "diffTxt": "sin concordancia"}
            if s1 and e2 and _minutos(e2) < _minutos(s1):
                desv["e2"] = {"campo": "e2", "etiqueta": "Entrada 2", "hora": e2, "prom": s1, "diffMin": _minutos(e2) - _minutos(s1), "diffTxt": "solapa T1"}
            if e2 and s2 and _minutos(s2) <= _minutos(e2):
                desv["s2"] = {"campo": "s2", "etiqueta": "Salida 2", "hora": s2, "prom": e2, "diffMin": _minutos(s2) - _minutos(e2), "diffTxt": "sin concordancia"}

        # 2. En turno NOCHE (12h en E1-S1), si además marca E2-S2 es un bloque extra fuera de su turno habitual
        if es_noche and (e2 or s2) and h_dia > jornada_base + 0.5:
            extra_m = int(round((h_dia - jornada_base) * 60))
            txt_ex = f"+{extra_m // 60}h {extra_m % 60:02d}m" if extra_m >= 60 else f"+{extra_m}m"
            if s2:
                desv["s2"] = {"campo": "s2", "etiqueta": "Salida 2", "hora": s2, "prom": "turno extra", "diffMin": extra_m, "diffTxt": txt_ex}
            elif e2:
                desv["e2"] = {"campo": "e2", "etiqueta": "Entrada 2", "hora": e2, "prom": "turno extra", "diffMin": extra_m, "diffTxt": txt_ex}

        # 3. Si la jornada del día tiene concordancia completa con sus horas esperadas (sin horas extras ni corte),
        #    permitir tanto modalidad continuada (ej. 08:00-16:00 o 12:00-20:00 = 8h) como partida (4h + 4h = 8h) sin falso naranja.
        sin_extras_anotadas = not bool(str(f.get("extras") or "").strip())
        jornada_concordante = (not desv) and sin_extras_anotadas and h_dia > 0 and abs(h_dia - esp) <= 0.55
        if jornada_concordante and not es_noche and e1 and s1 and e2 and s2:
            b1 = (_minutos(s1) - _minutos(e1)) / 60.0
            b2 = (_minutos(s2) - _minutos(e2)) / 60.0
            # Si compensó un corte grande en un bloque con exceso grande en el otro, igual revisar desvío por columna
            if not (3.25 <= b1 <= 4.75 and 3.25 <= b2 <= 4.75):
                jornada_concordante = False

        if jornada_concordante:
            f["desvios"] = desv
            continue

        es_corr = (not es_noche) and _es_fila_corrida_dia(f)
        for c in CAMPOS_HORA:
            if c in desv:
                continue
            val_str = str(f.get(c) or "").strip()
            if not val_str:
                continue
            if c == "s1" and usar_s1_dual and p_s1_part is not None and p_s1_corr is not None:
                # Si solo hubo 1 día corrido y fue con hora extra, usar E1 + 8h como referencia concordante
                if es_corr and len(s1_corrido_vals) == 1 and e1:
                    ref_min = (_minutos(e1) + 480) % 1440
                else:
                    ref_min = p_s1_corr if es_corr else p_s1_part
                ref_hm = _fmt_hm(ref_min)
            elif c in prom_mins:
                ref_min = prom_mins[c]
                ref_hm = promedios[c]
            else:
                continue
            m_val = _minutos(val_str)
            diff_m = m_val - ref_min
            if diff_m > 720:
                diff_m -= 1440
            elif diff_m < -720:
                diff_m += 1440
            if abs(diff_m) >= umbral_min:
                signo = "+" if diff_m > 0 else "−"
                ab = abs(diff_m)
                diff_txt = f"{signo}{ab // 60}h {ab % 60:02d}m" if ab >= 60 else f"{signo}{ab}m"
                desv[c] = {
                    "campo": c,
                    "etiqueta": ETIQUETA_CAMPO.get(c, c.upper()),
                    "hora": val_str,
                    "prom": ref_hm,
                    "diffMin": diff_m,
                    "diffTxt": diff_txt,
                }
        f["desvios"] = desv
    return promedios


# ==============================================================================
# VISTA ABIERTA: HORARIO MENSUAL + MARCACIÓN
# ==============================================================================

def obtener_horario_local(datos: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    datos = datos or {}
    hoy = datetime.now().date()
    mes = str(datos.get("mes") or hoy.strftime("%Y-%m"))
    y, m = int(mes[:4]), int(mes[5:7])
    conn = get_connection()
    try:
        emps = _lista_empleados(conn)
        if not emps:
            return {"empleados": [], "dias": [], "promedios": {}}
        nombre = str(datos.get("empleado") or emps[0]["empleado"])
        cfg = next((e for e in emps if e["empleado"].lower() == nombre.lower()), emps[0])
        feriados = feriados_peru(y)
        n_dias = calendar.monthrange(y, m)[1]
        ini, fin = _ymd(date(y, m, 1)), _ymd(date(y, m, n_dias))
        reg = {r["fecha_dmy"]: dict(r) for r in conn.execute(
            "SELECT * FROM horario_registros WHERE empleado = ? AND ymd BETWEEN ? AND ?", (cfg["empleado"], ini, fin)).fetchall()}
        dias = []
        for dd in range(1, n_dias + 1):
            f = _dmy(date(y, m, dd))
            dias.append(_fila_publica(reg.get(f) or _fila_vacia(cfg["empleado"], f), cfg, hoy, feriados))
        promedios = _calcular_promedios_y_desvios(dias, cfg)
        total_h = round(sum(x["horas"] for x in dias), 2)
        trab = sum(1 for x in dias if x["horas"] > 0 or x["e1"])
        return {
            "empleados": [{"id": e["empleado"], "rol": e["rol"], "turno": e["turno"], "descansoDow": e["descanso_dow"],
                           "descansoNombre": DOW_LARGO[int(e["descanso_dow"])]} for e in emps],
            "empleado": cfg["empleado"], "rol": cfg["rol"], "turno": cfg["turno"],
            "descansoDow": cfg["descanso_dow"], "descansoNombre": DOW_LARGO[int(cfg["descanso_dow"])],
            "mes": f"{y:04d}-{m:02d}", "hoyDmy": _dmy(hoy), "ahoraHM": datetime.now().strftime("%H:%M"),
            "dias": dias, "promedios": promedios, "totalHoras": total_h, "diasTrabajados": trab,
            "proximaMarca": _proxima_marca_txt(conn, cfg),
        }
    finally:
        conn.close()


def _decidir_marca(conn, cfg: Dict[str, Any], ahora: datetime) -> Tuple[str, str]:
    emp = cfg["empleado"]
    hoy_dmy = _dmy(ahora.date())
    if str(cfg["turno"]).upper() == "NOCHE" and ahora.hour < 15:
        ayer = _dmy(ahora.date() - timedelta(days=1))
        fa = _leer_fila(conn, emp, ayer)
        if fa["e1"] and not fa["s1"]:
            return ayer, "s1"
    fh = _leer_fila(conn, emp, hoy_dmy)
    for c in CAMPOS_HORA:
        if not fh[c]:
            return hoy_dmy, c
    raise ValueError("Ya tienes las 4 casillas de hoy marcadas. Si necesitas corregir, edita la hora en la tabla.")


def _proxima_marca_txt(conn, cfg: Dict[str, Any]) -> str:
    try:
        f, c = _decidir_marca(conn, cfg, datetime.now())
        return f"{ETIQUETA_CAMPO[c]} ({f[:5]})"
    except ValueError:
        return "Día completo"


def marcar_asistencia_local(datos: Dict[str, Any]) -> Dict[str, Any]:
    emp = str((datos or {}).get("empleado") or "").strip()
    ahora = datetime.now()
    with db_transaction() as conn:
        cfg = _cfg_empleado(conn, emp)
        fecha, campo = _decidir_marca(conn, cfg, ahora)
        fila = _leer_fila(conn, cfg["empleado"], fecha)
        prev = str(fila.get("ult_marca_ts") or "")
        if prev:
            try:
                if (ahora - datetime.fromisoformat(prev)).total_seconds() < 120:
                    raise ValueError("Ya marcaste hace menos de 2 minutos. Espera un momento si quieres marcar la siguiente casilla.")
            except ValueError as e:
                if "marcaste" in str(e):
                    raise
        hm = ahora.strftime("%H:%M")
        fila[campo] = hm
        fila["ult_marca_ts"] = ahora.isoformat(timespec="seconds")
        _guardar_fila(conn, fila)
    return {"ok": True, "empleado": cfg["empleado"], "fecha": fecha, "campo": campo, "etiqueta": ETIQUETA_CAMPO[campo], "hora": hm}


def editar_celda_horario_local(datos: Dict[str, Any]) -> Dict[str, Any]:
    emp = str(datos.get("empleado") or "").strip()
    campo = str(datos.get("campo") or "").strip()
    fecha = dl.normalizar_fecha_dmy(datos.get("fecha"))
    if campo not in CAMPOS_EDITABLES:
        raise ValueError("Campo no editable.")
    if not re.fullmatch(r"\d{2}/\d{2}/\d{4}", fecha or ""):
        raise ValueError("Fecha no válida.")
    if campo in CAMPOS_ADMIN:
        validar_clave_cuentas(datos)

    raw = datos.get("valor")
    if campo in CAMPOS_HORA:
        nuevo: Any = _norm_hora(raw)
    elif campo == "estado":
        nuevo = _norm_estado(raw)
    elif campo == "extras":
        nuevo = _norm_extras(raw)
    elif campo == "extra_noche":
        nuevo = 1 if str(raw).strip().lower() in ("1", "true", "si", "sí", "on") else 0
    else:
        nuevo = str(raw or "").strip()[:200]

    with db_transaction() as conn:
        cfg = _cfg_empleado(conn, emp)
        fila = _leer_fila(conn, cfg["empleado"], fecha)
        antes = fila[campo]
        if str(antes) == str(nuevo):
            return {"ok": True, "sinCambios": True}
        fila[campo] = nuevo
        _guardar_fila(conn, fila)
        _auditar(conn, cfg["empleado"], fecha, campo, str(antes), str(nuevo))
    return {"ok": True, "valor": nuevo}


# ==============================================================================
# CUENTAS / PAGOS (con contraseña)
# ==============================================================================

def _match_personal(g_personal: str, desc: str, cfg: Dict[str, Any]) -> str:
    claves = {cfg["empleado"].lower(), str(cfg["categoria_tienda"]).lower()}
    try:
        alias = [a.lower() for a in json.loads(cfg.get("alias_json") or "[]")]
    except Exception:
        alias = []
    gp = str(g_personal or "").strip().lower()
    if gp:
        return "personal" if gp in claves or gp in alias else ""
    d = str(desc or "").lower()
    return "descripcion" if any(a and re.search(r"\b" + re.escape(a) + r"\b", d) for a in alias + [cfg["empleado"].lower()]) else ""


def _calcular_liquidacion(conn, cfg: Dict[str, Any], desde: date, hasta: date) -> Dict[str, Any]:
    hoy = datetime.now().date()
    valor_dia = float(cfg["diario_noche"] if str(cfg["turno"]).upper() == "NOCHE" else cfg["diario_manana"])
    reg = {r["fecha_dmy"]: dict(r) for r in conn.execute(
        "SELECT * FROM horario_registros WHERE empleado = ? AND ymd BETWEEN ? AND ?",
        (cfg["empleado"], _ymd(desde), _ymd(hasta))).fetchall()}
    feriados: Dict[str, str] = {}
    for yy in range(desde.year, hasta.year + 1):
        feriados.update(feriados_peru(yy))

    faltas = descansos = feriados_trab = 0.0
    d_falta: List[str] = []
    d_desc: List[str] = []
    d_fer: List[str] = []
    h_m = h_n = 0.0
    extras_det: List[Dict[str, Any]] = []
    observaciones_horas: List[Dict[str, Any]] = []
    sin_marcar: List[str] = []
    horas_totales = 0.0
    filas_periodo = []
    cur_p = desde
    while cur_p <= hasta:
        fp = _dmy(cur_p)
        filas_periodo.append(dict(reg.get(fp) or _fila_vacia(cfg["empleado"], fp)))
        cur_p += timedelta(days=1)
    promedios_liq = _calcular_promedios_y_desvios(filas_periodo, cfg)
    desv_por_fecha = {x["fecha_dmy"]: x.get("desvios", {}) for x in filas_periodo}

    cur = desde
    while cur <= hasta:
        f = _dmy(cur)
        r = reg.get(f) or _fila_vacia(cfg["empleado"], f)
        trabajo = any(r[c] for c in CAMPOS_HORA)
        nom_fer_dia = feriados.get(f, "")
        info_j = _analizar_jornada_dia(r, cfg, nom_fer_dia)
        h_dia = info_j["horas"]
        horas_totales += h_dia
        es_desc = (cur.weekday() == int(cfg["descanso_dow"])) and not bool(nom_fer_dia)
        est = r["estado"]
        if est == "FALTÓ":
            faltas += 1
            d_falta.append(f)
        elif est == "MEDIA FALTA" or (est == "MEDIO TIEMPO" and not es_desc):
            faltas += 0.5
            d_falta.append(f + " (½ medio tiempo)")

        # El feriado manda sobre el descanso: si coincide con feriado, cuenta como feriado x2 y nunca como descanso
        es_fer_trab = (bool(nom_fer_dia) and (trabajo or est in ("FERIADO", "DESCANSO"))) or (est == "FERIADO")
        if es_fer_trab:
            feriados_trab += 1
            nom_fer = nom_fer_dia or "Feriado"
            d_fer.append(f"{f} {nom_fer}".strip())
        else:
            if est in ("MEDIO DESCANSO",) or (est == "MEDIO TIEMPO" and es_desc):
                descansos += 0.5
                d_desc.append(f + " (½ medio tiempo)")
            elif est == "DESCANSO":
                descansos += 1
                d_desc.append(f)
            elif es_desc and trabajo and est not in ("LIBRE", "FALTÓ"):
                # Si en su día de descanso solo trabajó un turno / medio tiempo (<= 5.5 h), cuenta como ½ descanso
                if 0 < h_dia <= 5.5:
                    descansos += 0.5
                    d_desc.append(f + " (½ medio tiempo)")
                else:
                    descansos += 1
                    d_desc.append(f)
        hx = info_j["extrasBase"]
        if hx:
            # En feriado las horas extras también se duplican (2x); todas se calculan sobre la base de mañana (S/ 50 / 8h)
            hx_efec = info_j["extrasEfectivas"]
            mult_hx = info_j["extrasMult"]
            h_m += hx_efec
            extras_det.append({
                "fecha": f,
                "horas": hx_efec,
                "horasBase": hx,
                "multiplicador": mult_hx,
                "esFeriado": mult_hx == 2,
                "feriadoNombre": nom_fer_dia or ("Feriado" if mult_hx == 2 else ""),
                "raw": r["extras"],
                "tramos": info_j["tramos"],
                "horasDia": h_dia,
            })
        desv_dia = desv_por_fecha.get(f) or {}
        if info_j["tipoObs"] or desv_dia:
            det_motivo = info_j["detalleMotivo"]
            if desv_dia:
                txt_desv = ", ".join(
                    f"{v['etiqueta']} {v['hora']} vs prom {v['prom']} ({v['diffTxt']})"
                    for v in desv_dia.values()
                )
                det_motivo = f"{det_motivo} · {txt_desv}" if det_motivo else txt_desv
            observaciones_horas.append({
                "fecha": f,
                "dow": DOW_ES[cur.weekday()],
                "tramos": info_j["tramos"],
                "horas": h_dia,
                "esperado": info_j["esperado"],
                "diffHoras": info_j["diffHoras"],
                "detalleMotivo": det_motivo,
                "tipoObs": info_j["tipoObs"] or "DESVIO_MEDIA",
                "desvios": desv_dia,
                "esFeriado": bool(nom_fer_dia) or (est == "FERIADO"),
                "feriadoNombre": nom_fer_dia,
                "esDescanso": es_desc,
                "estado": est,
                "extrasRaw": r["extras"],
                "extrasBase": hx,
                "extrasEfectivas": info_j["extrasEfectivas"],
                "extrasMult": info_j["extrasMult"],
            })
        if cur < hoy and not trabajo and not est and not es_desc and f not in feriados:
            sin_marcar.append(f)
        cur += timedelta(days=1)

    # Adelantos de sueldo (Registro_Gastos: categorías 'Adelanto de sueldo' y 'Sueldos y Salarios') y tienda (Cargar a la Cuenta)
    ini_y, fin_y = _ymd(desde), _ymd(hasta)
    adelantos, asignados = [], set()
    todos_cfg = _lista_empleados(conn, solo_activos=False)
    sin_asignar = []
    for g in conn.execute(
        "SELECT * FROM gastos WHERE (lower(categoria) LIKE 'adelanto%' OR lower(categoria) LIKE '%sueldo%salario%' OR lower(categoria) = 'sueldos y salarios') ORDER BY rowid"
    ).fetchall():
        ymd = dl.fecha_dmy_a_ymd(g["fecha_gasto_dmy"])
        if not (ini_y <= ymd <= fin_y):
            continue
        desc = str(g["descripcion"] or "").strip()
        # Omitir liquidaciones mensuales previas emitidas para no descontar el pago final como si fuera adelanto
        if desc.lower().startswith("pago sueldo "):
            continue
        cat_l = str(g["categoria"] or "").strip().lower()
        pers = str(g["personal"] or "").strip()

        # Un gasto califica como adelanto si:
        # a) Categoría empieza con 'adelanto' (ej. 'Adelanto de sueldo')
        # b) O en 'Sueldos y Salarios', Col J (personal) indica a qué personal pertenece
        # c) O en 'Sueldos y Salarios', la descripción menciona explícitamente adelanto ('adelanto', 'adel')
        es_adelanto = cat_l.startswith("adelanto") or bool(pers) or ("adelanto" in desc.lower() or "adel" in desc.lower())
        if not es_adelanto:
            continue

        item = {
            "fecha": g["fecha_gasto_dmy"],
            "descripcion": desc,
            "monto": float(g["monto"] or 0),
            "personal": pers,
            "categoria": g["categoria"] or "",
        }
        how = _match_personal(pers, desc, cfg)
        if how:
            item["via"] = how
            adelantos.append(item)
            continue
        if not any(_match_personal(pers, desc, c) for c in todos_cfg):
            if cat_l.startswith("adelanto") or "adelanto" in desc.lower():
                sin_asignar.append(item)
    tienda = []
    for v in conn.execute(
        "SELECT * FROM ventas_tienda WHERE metodo_pago = 'Cargar a la Cuenta' AND personal != '' ORDER BY rowid"
    ).fetchall():
        ymd = dl.fecha_dmy_a_ymd(v["fecha_dmy"])
        if not (ini_y <= ymd <= fin_y):
            continue
        if str(v["personal"]).strip().lower() in (cfg["empleado"].lower(), str(cfg["categoria_tienda"]).lower()):
            tienda.append({"fecha": v["fecha_dmy"], "producto": v["producto"], "cantidad": v["cantidad"], "subtotal": float(v["subtotal"] or 0)})

    n_dias = (hasta - desde).days + 1
    mes_completo = desde.day == 1 and hasta.day == calendar.monthrange(hasta.year, hasta.month)[1] and desde.month == hasta.month and desde.year == hasta.year
    return {
        "empleado": cfg["empleado"], "rol": cfg["rol"], "turno": cfg["turno"], "desde": _dmy(desde), "hasta": _dmy(hasta),
        "sueldo": float(cfg["sueldo"]), "diarioManana": float(cfg["diario_manana"]), "diarioNoche": float(cfg["diario_noche"]),
        "valorDia": valor_dia, "afp": float(cfg["afp"]),
        "diasBase": 30 if mes_completo else min(30, n_dias), "diasPeriodo": n_dias,
        "faltas": faltas, "faltasDias": d_falta, "descansos": descansos, "descansosDias": d_desc,
        "feriados": feriados_trab, "feriadosDias": d_fer, "feriadoMultiplicador": 2,
        "horasExtraM": round(h_m, 2), "horasExtraN": round(h_n, 2), "extrasDetalle": extras_det,
        "observacionesHoras": observaciones_horas,
        "adelantos": adelantos, "adelantosTotal": round(sum(a["monto"] for a in adelantos), 2),
        "adelantosSinAsignar": sin_asignar,
        "tienda": tienda, "tiendaTotal": round(sum(t["subtotal"] for t in tienda), 2),
        "sinMarcar": sin_marcar, "horasTotales": round(horas_totales, 2),
    }


def obtener_liquidacion_horario_local(datos: Dict[str, Any]) -> Dict[str, Any]:
    validar_clave_cuentas(datos)
    hoy = datetime.now().date()
    desde = _fecha(datos["desde"]) if datos.get("desde") else date(hoy.year, hoy.month, 1)
    hasta = _fecha(datos["hasta"]) if datos.get("hasta") else date(hoy.year, hoy.month, calendar.monthrange(hoy.year, hoy.month)[1])
    if hasta < desde:
        raise ValueError("La fecha 'hasta' no puede ser menor que 'desde'.")
    conn = get_connection()
    try:
        cfg = _cfg_empleado(conn, datos.get("empleado"))
        liq = _calcular_liquidacion(conn, cfg, desde, hasta)
        liq["empleados"] = [e["empleado"] for e in _lista_empleados(conn)]
        liq["historial"] = [
            {"liqUuid": r["liq_uuid"], "desde": r["desde_dmy"], "hasta": r["hasta_dmy"], "neto": r["neto"], "yape": r["yape"],
             "efectivo": r["efectivo"], "aEfec": (r["a_efec"] if "a_efec" in r.keys() else 0.0) or 0.0, "creado": r["created_at"]}
            for r in conn.execute("SELECT * FROM horario_liquidaciones WHERE empleado = ? ORDER BY created_at DESC LIMIT 12", (cfg["empleado"],)).fetchall()
        ]
        return liq
    finally:
        conn.close()


def obtener_personal_config_local(datos: Dict[str, Any]) -> Dict[str, Any]:
    validar_clave_cuentas(datos)
    conn = get_connection()
    try:
        return {"personal": [
            {"empleado": e["empleado"], "rol": e["rol"], "categoriaTienda": e["categoria_tienda"], "sueldo": e["sueldo"],
             "diarioManana": e["diario_manana"], "diarioNoche": e["diario_noche"], "afp": e["afp"],
             "descansoDow": e["descanso_dow"], "turno": e["turno"], "activo": e["activo"]}
            for e in _lista_empleados(conn, solo_activos=False)]}
    finally:
        conn.close()


def guardar_personal_config_local(datos: Dict[str, Any]) -> Dict[str, Any]:
    validar_clave_cuentas(datos)
    emp = str(datos.get("empleado") or "").strip()
    if not emp:
        raise ValueError("Falta el nombre del empleado.")
    turno = "NOCHE" if str(datos.get("turno") or "").upper() == "NOCHE" else "DIA"
    vals = {
        "rol": str(datos.get("rol") or "").strip(),
        "categoria_tienda": str(datos.get("categoriaTienda") or "").strip(),
        "sueldo": dl.parse_moneda(datos.get("sueldo")),
        "diario_manana": dl.parse_moneda(datos.get("diarioManana")),
        "diario_noche": dl.parse_moneda(datos.get("diarioNoche")),
        "afp": dl.parse_moneda(datos.get("afp")),
        "descanso_dow": max(0, min(6, int(datos.get("descansoDow") or 0))),
        "turno": turno,
        "activo": 1 if datos.get("activo", 1) in (1, True, "1", "true") else 0,
    }
    with db_transaction() as conn:
        ex = conn.execute("SELECT empleado FROM personal_config WHERE empleado = ? COLLATE NOCASE", (emp,)).fetchone()
        if ex:
            conn.execute(
                "UPDATE personal_config SET rol=?, categoria_tienda=?, sueldo=?, diario_manana=?, diario_noche=?, afp=?, "
                "descanso_dow=?, turno=?, activo=? WHERE empleado = ?",
                (vals["rol"], vals["categoria_tienda"], vals["sueldo"], vals["diario_manana"], vals["diario_noche"], vals["afp"],
                 vals["descanso_dow"], vals["turno"], vals["activo"], ex["empleado"]))
        else:
            orden = conn.execute("SELECT COALESCE(MAX(orden), -1) + 1 FROM personal_config").fetchone()[0]
            conn.execute(
                "INSERT INTO personal_config (empleado, rol, categoria_tienda, alias_json, sueldo, diario_manana, diario_noche, afp, "
                "descanso_dow, turno, orden, activo) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (emp, vals["rol"], vals["categoria_tienda"], json.dumps([emp.lower()]), vals["sueldo"], vals["diario_manana"],
                 vals["diario_noche"], vals["afp"], vals["descanso_dow"], vals["turno"], orden, vals["activo"]))
    return {"ok": True}


def obtener_auditoria_horario_local(datos: Dict[str, Any]) -> Dict[str, Any]:
    validar_clave_cuentas(datos)
    dias = int(datos.get("dias") or 60)
    emp = str(datos.get("empleado") or "").strip()
    desde_ts = (datetime.now() - timedelta(days=dias)).isoformat(timespec="seconds")
    conn = get_connection()
    try:
        turnos = {e["empleado"].lower(): str(e["turno"]).upper() for e in _lista_empleados(conn, solo_activos=False)}
        q = "SELECT * FROM horario_auditoria WHERE editado_ts >= ?"
        par: List[Any] = [desde_ts]
        if emp:
            q += " AND empleado = ? COLLATE NOCASE"
            par.append(emp)
        q += " ORDER BY editado_ts DESC LIMIT 400"
        out = []
        for r in conn.execute(q, par).fetchall():
            try:
                editado = datetime.fromisoformat(r["editado_ts"])
            except Exception:
                continue
            fecha = _fecha(r["fecha_dmy"])
            demora_min = None
            alerta = ""
            if r["campo"] in CAMPOS_HORA and r["valor_nuevo"]:
                h, mi = int(r["valor_nuevo"][:2]), int(r["valor_nuevo"][3:5])
                dia_real = fecha
                if r["campo"] == "s1" and turnos.get(r["empleado"].lower()) == "NOCHE" and h < 14:
                    dia_real = fecha + timedelta(days=1)
                momento = datetime(dia_real.year, dia_real.month, dia_real.day, h, mi)
                demora_min = int((editado - momento).total_seconds() // 60)
            if editado.date() > fecha:
                alerta = "rojo" if (editado.date() - fecha).days >= 1 and (demora_min is None or demora_min > 12 * 60) else "ambar"
            elif demora_min is not None and demora_min > 30:
                alerta = "ambar"
            out.append({
                "empleado": r["empleado"], "fecha": r["fecha_dmy"], "campo": ETIQUETA_CAMPO.get(r["campo"], r["campo"]),
                "antes": r["valor_anterior"], "nuevo": r["valor_nuevo"],
                "editadoFecha": editado.strftime("%d/%m/%Y"), "editadoHora": editado.strftime("%H:%M"),
                "demoraMin": demora_min, "alerta": alerta, "equipo": r["equipo"],
            })
        return {"ediciones": out}
    finally:
        conn.close()


def guardar_liquidacion_horario_local(datos: Dict[str, Any]) -> Dict[str, Any]:
    validar_clave_cuentas(datos)
    emp = str(datos.get("empleado") or "").strip()
    neto = round(float(datos.get("neto") or 0), 2)
    yape = round(float(datos.get("yape") or 0), 2)
    efectivo = round(float(datos.get("efectivo") or 0), 2)
    a_efec = round(float(datos.get("aEfec") or datos.get("a_efec") or 0), 2)
    total_pagos = round(yape + efectivo + a_efec, 2)
    dif = round(abs(total_pagos - neto), 2)

    # Flexibilidad con centavos: hasta S/ 1.00 de redondeo permitido sin bloquear
    if dif > 1.00 and not datos.get("ignorarDiferencia"):
        raise ValueError(
            f"La suma de pagos (Yape S/ {yape:.2f} + Efectivo S/ {efectivo:.2f} + A. Efec S/ {a_efec:.2f} = S/ {total_pagos:.2f}) "
            f"difiere del Total a pagar (S/ {neto:.2f}) por S/ {dif:.2f}."
        )

    desde = dl.normalizar_fecha_dmy(datos.get("desde"))
    hasta = dl.normalizar_fecha_dmy(datos.get("hasta"))
    with db_transaction() as conn:
        cfg = _cfg_empleado(conn, emp)
    gastos_creados = 0
    registrar_en_gastos = datos.get("registrarGastos", True)
    if registrar_en_gastos:
        periodo = f"{desde} al {hasta}"
        for monto, metodo in ((yape, "Yape"), (efectivo, "Efectivo"), (a_efec, "A. Efec")):
            if monto > 0:
                dl.guardar_gasto_local({
                    "fecha": dl.hoy_iso(), "descripcion": f"Pago sueldo {cfg['empleado']} ({periodo}) - {metodo}", "monto": monto,
                    "categoria": "Sueldos y Salarios", "metodo": metodo, "responsable": "Admin",
                    "personal": cfg["categoria_tienda"],
                })
                gastos_creados += 1
    liq_uuid = str(uuid.uuid4())
    with db_transaction() as conn:
        conn.execute(
            "INSERT INTO horario_liquidaciones (liq_uuid, empleado, desde_dmy, hasta_dmy, neto, yape, efectivo, a_efec, detalle_json, created_at, equipo) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (liq_uuid, cfg["empleado"], desde, hasta, neto, yape, efectivo, a_efec,
             json.dumps(datos.get("detalle") or {}, ensure_ascii=False), datetime.now().isoformat(timespec="seconds"), dl._nombre_equipo()))
    return {"ok": True, "liqUuid": liq_uuid, "gastosCreados": gastos_creados}
