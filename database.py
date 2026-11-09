#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Motor SQLite Offline-First (Modo WAL + Synchronous FULL)
Resiste apagones y cortes de internet en 0 ms.
"""

import sqlite3
import threading
from contextlib import contextmanager
from typing import Iterator
from config import DB_PATH

_db_init_lock = threading.Lock()


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=FULL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA busy_timeout=15000;")
    return conn


@contextmanager
def db_transaction() -> Iterator[sqlite3.Connection]:
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE;")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with _db_init_lock:
        conn = get_connection()
        try:
            conn.executescript(
                """
                -- 1. Configuración de Habitaciones (Panel_Disponibilidad!A3:D31 + Config!A2:B40)
                CREATE TABLE IF NOT EXISTS habitaciones_config (
                    hab                 TEXT PRIMARY KEY,
                    orden               INTEGER NOT NULL DEFAULT 0,
                    tipo                TEXT NOT NULL DEFAULT '',
                    precio_ref          TEXT NOT NULL DEFAULT '',
                    color_personalizado TEXT NOT NULL DEFAULT '',
                    dirty_color         INTEGER NOT NULL DEFAULT 0
                );

                -- 1b. Colores/notas del panel (SOLO LOCAL, desvinculado de Panel_Disponibilidad en Sheets).
                --     fecha_dmy = '*' -> global permanente (Rojo Inutilizada); 'DD/MM/YYYY' -> solo ese día.
                CREATE TABLE IF NOT EXISTS habitaciones_colores (
                    hab         TEXT NOT NULL,
                    fecha_dmy   TEXT NOT NULL,
                    color_hex   TEXT NOT NULL,
                    nota        TEXT NOT NULL DEFAULT '',
                    updated_at  TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (hab, fecha_dmy)
                );

                -- 2. Catálogos generales de Config (Productos tienda, Personal, Categorías gastos, Hoteles apoyo)
                CREATE TABLE IF NOT EXISTS catalogos_local (
                    categoria TEXT NOT NULL,
                    clave     TEXT NOT NULL,
                    valor     TEXT NOT NULL DEFAULT '',
                    orden     INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (categoria, clave)
                );

                -- 3. Padrón local de huéspedes y empresas (Autocompletado 0 ms de DNI/RUC y Visita N° X sin internet)
                CREATE TABLE IF NOT EXISTS huespedes_padron (
                    dni          TEXT PRIMARY KEY,
                    nombre       TEXT NOT NULL DEFAULT '',
                    sexo         TEXT NOT NULL DEFAULT 'M',
                    nac          TEXT NOT NULL DEFAULT 'Perú',
                    reg          TEXT NOT NULL DEFAULT 'Lima',
                    mot          TEXT NOT NULL DEFAULT 'Vacaciones, recreo u ocio',
                    hab_frec     TEXT NOT NULL DEFAULT '',
                    tarifa_frec  REAL NOT NULL DEFAULT 0,
                    empresa      TEXT NOT NULL DEFAULT '',
                    celular      TEXT NOT NULL DEFAULT '',
                    ruc          TEXT NOT NULL DEFAULT '',
                    visitas      INTEGER NOT NULL DEFAULT 1,
                    updated_at   TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS empresas_padron (
                    ruc          TEXT PRIMARY KEY,
                    razon_social TEXT NOT NULL DEFAULT '',
                    updated_at   TEXT NOT NULL DEFAULT ''
                );

                -- 4. Registro_Estancias (Espejo completo de las 52 columnas A..AZ)
                CREATE TABLE IF NOT EXISTS estancias (
                    estancia_uuid        TEXT PRIMARY KEY,
                    id_timestamp_str     TEXT UNIQUE NOT NULL,
                    sheet_row            INTEGER NOT NULL DEFAULT 0,
                    parent_uuid          TEXT NOT NULL DEFAULT '',
                    fecha_in_dmy         TEXT NOT NULL DEFAULT '',
                    fecha_out_dmy        TEXT NOT NULL DEFAULT '',
                    ymd_in               INTEGER NOT NULL DEFAULT 0,
                    ymd_out              INTEGER NOT NULL DEFAULT 0,
                    hora_registro        TEXT NOT NULL DEFAULT '',
                    huesped              TEXT NOT NULL DEFAULT '',
                    dni                  TEXT NOT NULL DEFAULT '',
                    sexo                 TEXT NOT NULL DEFAULT 'M',
                    n_acomp              TEXT NOT NULL DEFAULT '',
                    dni_acomp            TEXT NOT NULL DEFAULT '',
                    nac                  TEXT NOT NULL DEFAULT 'Perú',
                    reg                  TEXT NOT NULL DEFAULT 'Lima',
                    mot                  TEXT NOT NULL DEFAULT 'Vacaciones, recreo u ocio',
                    hab                  TEXT NOT NULL DEFAULT '',
                    tipo_hab             TEXT NOT NULL DEFAULT '',
                    visita               TEXT NOT NULL DEFAULT 'Visita N° 1',
                    noches               INTEGER NOT NULL DEFAULT 1,
                    tarifa               REAL NOT NULL DEFAULT 0,
                    adic_snacks          REAL NOT NULL DEFAULT 0,
                    adic_servicios       REAL NOT NULL DEFAULT 0,
                    adic_comp            REAL NOT NULL DEFAULT 0,
                    pago_ini_efec        REAL NOT NULL DEFAULT 0,
                    pago_ini_yape        REAL NOT NULL DEFAULT 0,
                    pago_post_efec       REAL NOT NULL DEFAULT 0,
                    pago_post_yape       REAL NOT NULL DEFAULT 0,
                    pago_visa            REAL NOT NULL DEFAULT 0,
                    deuda_anterior       REAL NOT NULL DEFAULT 0,
                    cuenta_destino       TEXT NOT NULL DEFAULT '',
                    solicita_comp        TEXT NOT NULL DEFAULT 'No',
                    tipo_comp            TEXT NOT NULL DEFAULT '',
                    estado_comp          TEXT NOT NULL DEFAULT '',
                    nro_comp             TEXT NOT NULL DEFAULT '',
                    monto_factura        REAL NOT NULL DEFAULT 0,
                    estado_estancia      TEXT NOT NULL DEFAULT 'En Curso',
                    empresa              TEXT NOT NULL DEFAULT '',
                    proyecto             TEXT NOT NULL DEFAULT '',
                    triple1              TEXT NOT NULL DEFAULT '',
                    triple2              TEXT NOT NULL DEFAULT '',
                    hotel_apoyo          TEXT NOT NULL DEFAULT '',
                    observaciones        TEXT NOT NULL DEFAULT '',
                    nro_op_yape          TEXT NOT NULL DEFAULT '',
                    celular              TEXT NOT NULL DEFAULT '',
                    ruc                  TEXT NOT NULL DEFAULT '',
                    excedente_efec_sunat REAL NOT NULL DEFAULT 0,
                    dirty                INTEGER NOT NULL DEFAULT 0,
                    updated_at           TEXT NOT NULL DEFAULT ''
                );

                CREATE INDEX IF NOT EXISTS idx_estancias_hab_ymd ON estancias (hab, ymd_in, ymd_out);
                CREATE INDEX IF NOT EXISTS idx_estancias_ymd ON estancias (ymd_in, ymd_out);
                CREATE INDEX IF NOT EXISTS idx_estancias_sheet_row ON estancias (sheet_row);
                CREATE INDEX IF NOT EXISTS idx_estancias_dni ON estancias (dni);

                -- 5. Registro_Pagos (10 columnas)
                CREATE TABLE IF NOT EXISTS pagos (
                    pago_id       TEXT PRIMARY KEY,
                    estancia_uuid TEXT NOT NULL DEFAULT '',
                    id_estancia   TEXT NOT NULL DEFAULT '',
                    fecha_iso     TEXT NOT NULL DEFAULT '',
                    hab           TEXT NOT NULL DEFAULT '',
                    huesped       TEXT NOT NULL DEFAULT '',
                    monto         REAL NOT NULL DEFAULT 0,
                    medio_pago    TEXT NOT NULL DEFAULT '',
                    tipo_pago     TEXT NOT NULL DEFAULT '',
                    concepto      TEXT NOT NULL DEFAULT '',
                    nro_operacion TEXT NOT NULL DEFAULT '',
                    sheet_row     INTEGER NOT NULL DEFAULT 0,
                    synced        INTEGER NOT NULL DEFAULT 0
                );

                -- 6. 2.Registro_Ventas_Tienda (12 columnas)
                CREATE TABLE IF NOT EXISTS ventas_tienda (
                    venta_uuid    TEXT PRIMARY KEY,
                    lote_uuid     TEXT NOT NULL DEFAULT '',
                    estancia_uuid TEXT NOT NULL DEFAULT '',
                    id_estancia   TEXT NOT NULL DEFAULT '',
                    fecha_iso     TEXT NOT NULL DEFAULT '',
                    fecha_dmy     TEXT NOT NULL DEFAULT '',
                    producto      TEXT NOT NULL DEFAULT '',
                    cantidad      INTEGER NOT NULL DEFAULT 1,
                    metodo_pago   TEXT NOT NULL DEFAULT '',
                    hab           TEXT NOT NULL DEFAULT '',
                    personal      TEXT NOT NULL DEFAULT '',
                    precio_unit   REAL NOT NULL DEFAULT 0,
                    subtotal      REAL NOT NULL DEFAULT 0,
                    nro_op_yape   TEXT NOT NULL DEFAULT '',
                    sheet_row     INTEGER NOT NULL DEFAULT 0,
                    synced        INTEGER NOT NULL DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS idx_tienda_hab_fecha ON ventas_tienda (hab, fecha_dmy);

                -- 6b. Vitrina_Tienda (Inventario y últimos 8 conteos H:W)
                CREATE TABLE IF NOT EXISTS vitrina_local (
                    producto      TEXT PRIMARY KEY,
                    orden         INTEGER NOT NULL DEFAULT 0,
                    stock_sheets  REAL NOT NULL DEFAULT 0,
                    estado_actual TEXT NOT NULL DEFAULT '',
                    h1_cant       TEXT NOT NULL DEFAULT '',
                    h1_est        TEXT NOT NULL DEFAULT '',
                    h2_cant       TEXT NOT NULL DEFAULT '',
                    h2_est        TEXT NOT NULL DEFAULT '',
                    h3_cant       TEXT NOT NULL DEFAULT '',
                    h3_est        TEXT NOT NULL DEFAULT '',
                    h4_cant       TEXT NOT NULL DEFAULT '',
                    h4_est        TEXT NOT NULL DEFAULT '',
                    h5_cant       TEXT NOT NULL DEFAULT '',
                    h5_est        TEXT NOT NULL DEFAULT '',
                    h6_cant       TEXT NOT NULL DEFAULT '',
                    h6_est        TEXT NOT NULL DEFAULT '',
                    h7_cant       TEXT NOT NULL DEFAULT '',
                    h7_est        TEXT NOT NULL DEFAULT '',
                    h8_cant       TEXT NOT NULL DEFAULT '',
                    h8_est        TEXT NOT NULL DEFAULT '',
                    updated_at    TEXT NOT NULL DEFAULT ''
                );

                -- 6c. Gestión de Tienda / Kardex de Almacén (import_Almacen nativo)
                CREATE TABLE IF NOT EXISTS almacen_movimientos (
                    mov_uuid        TEXT PRIMARY KEY,
                    lote_uuid       TEXT NOT NULL DEFAULT '',
                    id_transaccion  TEXT NOT NULL DEFAULT '',
                    fecha_dmy       TEXT NOT NULL DEFAULT '',
                    producto        TEXT NOT NULL DEFAULT '',
                    cantidad        REAL NOT NULL DEFAULT 0,
                    costo_total     REAL NOT NULL DEFAULT 0,
                    costo_unitario  REAL NOT NULL DEFAULT 0,
                    metodo_pago     TEXT NOT NULL DEFAULT 'N/A',
                    tipo_movimiento TEXT NOT NULL DEFAULT '',
                    nota            TEXT NOT NULL DEFAULT '',
                    sheet_row       INTEGER NOT NULL DEFAULT 0,
                    synced          INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_alm_prod_tipo ON almacen_movimientos (producto, tipo_movimiento);
                CREATE INDEX IF NOT EXISTS idx_alm_lote ON almacen_movimientos (lote_uuid);

                -- 6d. Lotes de Compras de Tienda (Monto Total -> Registro_Gastos + Desglose progresivo por partes)
                CREATE TABLE IF NOT EXISTS compras_tienda_lotes (
                    compra_uuid      TEXT PRIMARY KEY,
                    fecha_dmy        TEXT NOT NULL DEFAULT '',
                    monto_total      REAL NOT NULL DEFAULT 0,
                    monto_desglosado REAL NOT NULL DEFAULT 0,
                    metodo_pago      TEXT NOT NULL DEFAULT 'Efectivo',
                    responsable      TEXT NOT NULL DEFAULT 'ELEIZA',
                    tipo_comp        TEXT NOT NULL DEFAULT 'Boleta',
                    nro_comp         TEXT NOT NULL DEFAULT '',
                    nota             TEXT NOT NULL DEFAULT '',
                    gasto_uuid       TEXT NOT NULL DEFAULT '',
                    estado           TEXT NOT NULL DEFAULT 'PENDIENTE',
                    created_at       TEXT NOT NULL DEFAULT '',
                    updated_at       TEXT NOT NULL DEFAULT ''
                );

                -- 7. Registro_Gastos (9 columnas)
                CREATE TABLE IF NOT EXISTS gastos (
                    gasto_uuid      TEXT PRIMARY KEY,
                    id_ts_str       TEXT UNIQUE NOT NULL,
                    fecha_gasto_iso TEXT NOT NULL DEFAULT '',
                    fecha_gasto_dmy TEXT NOT NULL DEFAULT '',
                    hora_registro   TEXT NOT NULL DEFAULT '',
                    descripcion     TEXT NOT NULL DEFAULT '',
                    monto           REAL NOT NULL DEFAULT 0,
                    categoria       TEXT NOT NULL DEFAULT '',
                    metodo_pago     TEXT NOT NULL DEFAULT '',
                    tipo_comp       TEXT NOT NULL DEFAULT '',
                    nro_comp        TEXT NOT NULL DEFAULT '',
                    responsable     TEXT NOT NULL DEFAULT 'Admin',
                    sheet_row       INTEGER NOT NULL DEFAULT 0,
                    synced          INTEGER NOT NULL DEFAULT 0
                );

                -- 8. Índice cruzado de SUNAT_OCT y CALZADO_OCT
                CREATE TABLE IF NOT EXISTS sunat_lineas_cache (
                    id_linea      INTEGER PRIMARY KEY AUTOINCREMENT,
                    nro_cp        TEXT NOT NULL DEFAULT '',
                    tipo          TEXT NOT NULL DEFAULT 'BOLETA',
                    noche_dmy     TEXT NOT NULL DEFAULT '',
                    hab           TEXT NOT NULL DEFAULT '',
                    monto_linea   REAL NOT NULL DEFAULT 0,
                    total_cp      REAL NOT NULL DEFAULT 0,
                    cliente       TEXT NOT NULL DEFAULT '',
                    doc           TEXT NOT NULL DEFAULT '',
                    fecha_emision TEXT NOT NULL DEFAULT '',
                    estado        TEXT NOT NULL DEFAULT 'VIGENTE',
                    descripcion   TEXT NOT NULL DEFAULT ''
                );

                CREATE INDEX IF NOT EXISTS idx_sunat_cp ON sunat_lineas_cache (nro_cp);
                CREATE INDEX IF NOT EXISTS idx_sunat_noche_hab ON sunat_lineas_cache (noche_dmy, hab);

                CREATE TABLE IF NOT EXISTS calzado_cache (
                    clave_calzado       TEXT PRIMARY KEY,
                    fila_calzado        INTEGER NOT NULL DEFAULT 0,
                    noche_dmy           TEXT NOT NULL DEFAULT '',
                    hab                 TEXT NOT NULL DEFAULT '',
                    huesped             TEXT NOT NULL DEFAULT '',
                    dni                 TEXT NOT NULL DEFAULT '',
                    fila_est            TEXT NOT NULL DEFAULT '',
                    monto_sunat         REAL NOT NULL DEFAULT 0,
                    comprobantes        TEXT NOT NULL DEFAULT '',
                    monto_aj            REAL NOT NULL DEFAULT 0,
                    yape_ab             REAL NOT NULL DEFAULT 0,
                    efectivo_aa         REAL NOT NULL DEFAULT 0,
                    excedente_efec      REAL NOT NULL DEFAULT 0,
                    yape_bcp            REAL NOT NULL DEFAULT 0,
                    estado_bcp          TEXT NOT NULL DEFAULT '',
                    estado_conciliacion TEXT NOT NULL DEFAULT '',
                    accion              TEXT NOT NULL DEFAULT ''
                );

                CREATE INDEX IF NOT EXISTS idx_calzado_fila_est ON calzado_cache (fila_est);
                CREATE INDEX IF NOT EXISTS idx_calzado_noche_hab ON calzado_cache (noche_dmy, hab);

                -- 8b. Historial de movimientos (reloj tipo Sheets): quién hizo qué y cuándo
                CREATE TABLE IF NOT EXISTS historial_movimientos (
                    mov_id       TEXT PRIMARY KEY,
                    fecha_dmy    TEXT NOT NULL DEFAULT '',
                    hora         TEXT NOT NULL DEFAULT '',
                    ts_iso       TEXT NOT NULL DEFAULT '',
                    accion       TEXT NOT NULL DEFAULT '',
                    hab          TEXT NOT NULL DEFAULT '',
                    huesped      TEXT NOT NULL DEFAULT '',
                    equipo       TEXT NOT NULL DEFAULT '',
                    resumen      TEXT NOT NULL DEFAULT '',
                    detalle_json TEXT NOT NULL DEFAULT '[]',
                    monto        REAL NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_mov_fecha ON historial_movimientos (fecha_dmy);

                -- 9. Cola Transaccional de Sincronización (Outbox FIFO Idempotente)
                CREATE TABLE IF NOT EXISTS sync_queue (
                    queue_id     INTEGER PRIMARY KEY AUTOINCREMENT,
                    op_uuid      TEXT UNIQUE NOT NULL,
                    op_type      TEXT NOT NULL,
                    entity_uuid  TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    delta_caja   REAL NOT NULL DEFAULT 0,
                    status       TEXT NOT NULL DEFAULT 'PENDING',
                    attempts     INTEGER NOT NULL DEFAULT 0,
                    last_error   TEXT NOT NULL DEFAULT '',
                    created_at   TEXT NOT NULL DEFAULT '',
                    synced_at    TEXT NOT NULL DEFAULT ''
                );

                CREATE INDEX IF NOT EXISTS idx_sync_status ON sync_queue (status, queue_id);

                -- 9b. Horario y Pagos de Personal
                CREATE TABLE IF NOT EXISTS personal_config (
                    empleado        TEXT PRIMARY KEY,
                    rol             TEXT NOT NULL DEFAULT '',
                    categoria_tienda TEXT NOT NULL DEFAULT '',
                    alias_json      TEXT NOT NULL DEFAULT '[]',
                    sueldo          REAL NOT NULL DEFAULT 0,
                    diario_manana   REAL NOT NULL DEFAULT 50,
                    diario_noche    REAL NOT NULL DEFAULT 55,
                    afp             REAL NOT NULL DEFAULT 0,
                    descanso_dow    INTEGER NOT NULL DEFAULT 0,
                    turno           TEXT NOT NULL DEFAULT 'DIA',
                    orden           INTEGER NOT NULL DEFAULT 0,
                    activo          INTEGER NOT NULL DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS horario_registros (
                    empleado     TEXT NOT NULL,
                    fecha_dmy    TEXT NOT NULL,
                    ymd          INTEGER NOT NULL DEFAULT 0,
                    e1           TEXT NOT NULL DEFAULT '',
                    s1           TEXT NOT NULL DEFAULT '',
                    e2           TEXT NOT NULL DEFAULT '',
                    s2           TEXT NOT NULL DEFAULT '',
                    estado       TEXT NOT NULL DEFAULT '',
                    extras       TEXT NOT NULL DEFAULT '',
                    extra_noche  INTEGER NOT NULL DEFAULT 0,
                    nota         TEXT NOT NULL DEFAULT '',
                    ult_marca_ts TEXT NOT NULL DEFAULT '',
                    updated_at   TEXT NOT NULL DEFAULT '',
                    equipo       TEXT NOT NULL DEFAULT '',
                    synced       INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (empleado, fecha_dmy)
                );
                CREATE INDEX IF NOT EXISTS idx_hor_emp_ymd ON horario_registros (empleado, ymd);

                CREATE TABLE IF NOT EXISTS horario_auditoria (
                    audit_uuid     TEXT PRIMARY KEY,
                    empleado       TEXT NOT NULL DEFAULT '',
                    fecha_dmy      TEXT NOT NULL DEFAULT '',
                    campo          TEXT NOT NULL DEFAULT '',
                    valor_anterior TEXT NOT NULL DEFAULT '',
                    valor_nuevo    TEXT NOT NULL DEFAULT '',
                    editado_ts     TEXT NOT NULL DEFAULT '',
                    equipo         TEXT NOT NULL DEFAULT '',
                    synced         INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_hor_aud_ts ON horario_auditoria (editado_ts);

                CREATE TABLE IF NOT EXISTS horario_liquidaciones (
                    liq_uuid     TEXT PRIMARY KEY,
                    empleado     TEXT NOT NULL DEFAULT '',
                    desde_dmy    TEXT NOT NULL DEFAULT '',
                    hasta_dmy    TEXT NOT NULL DEFAULT '',
                    neto         REAL NOT NULL DEFAULT 0,
                    yape         REAL NOT NULL DEFAULT 0,
                    efectivo     REAL NOT NULL DEFAULT 0,
                    a_efec       REAL NOT NULL DEFAULT 0,
                    detalle_json TEXT NOT NULL DEFAULT '{}',
                    created_at   TEXT NOT NULL DEFAULT '',
                    equipo       TEXT NOT NULL DEFAULT ''
                );

                -- 10. Estado de sistema y Caja Efectivo
                CREATE TABLE IF NOT EXISTS estado_sistema (
                    clave      TEXT PRIMARY KEY,
                    valor      TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT ''
                );

                -- 11. Control interno de Visitas / Pases (privado, sin exponer DNI en Registro_Estancias/Pagos)
                CREATE TABLE IF NOT EXISTS control_pases (
                    pase_uuid       TEXT PRIMARY KEY,
                    fecha_dmy       TEXT NOT NULL,
                    ymd             INTEGER NOT NULL DEFAULT 0,
                    hora            TEXT NOT NULL DEFAULT '',
                    hab             TEXT NOT NULL DEFAULT '',
                    estancia_uuid   TEXT NOT NULL DEFAULT '',
                    id_estancia     TEXT NOT NULL DEFAULT '',
                    huesped_titular TEXT NOT NULL DEFAULT '',
                    dni_visita      TEXT NOT NULL DEFAULT '',
                    nombre_visita   TEXT NOT NULL DEFAULT '',
                    estado          TEXT NOT NULL DEFAULT 'ACEPTADO',
                    monto           REAL NOT NULL DEFAULT 10.0,
                    motivo_rechazo  TEXT NOT NULL DEFAULT '',
                    equipo          TEXT NOT NULL DEFAULT '',
                    created_at      TEXT NOT NULL DEFAULT '',
                    synced          INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_pases_ymd ON control_pases(ymd);
                CREATE INDEX IF NOT EXISTS idx_pases_dni ON control_pases(dni_visita);

                -- 12. Desglose individual de Servicios Adicionales por estancia (Garaje, Lavandería, Ducha, Visita, etc.)
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
                );
                CREATE INDEX IF NOT EXISTS idx_serv_det_est ON servicios_detalle(estancia_uuid);
                """
            )
            # Migración única: el panel de colores queda desvinculado de Sheets; se parte limpio.
            ya = conn.execute("SELECT 1 FROM estado_sistema WHERE clave = 'colores_desvinculados_v1'").fetchone()
            if not ya:
                conn.execute("UPDATE habitaciones_config SET color_personalizado = '', dirty_color = 0")
                conn.execute("UPDATE sync_queue SET status = 'SYNCED' WHERE op_type = 'COLOR_HAB' AND status != 'SYNCED'")
                conn.execute(
                    "INSERT OR REPLACE INTO estado_sistema (clave, valor, updated_at) VALUES ('colores_desvinculados_v1', '1', '')"
                )
            # Migración: Tipo de Adicional (Guardar equipaje, Ducha, Lavandería...)
            cols_est = [r[1] for r in conn.execute("PRAGMA table_info(estancias)").fetchall()]
            if "tipo_adicional" not in cols_est:
                conn.execute("ALTER TABLE estancias ADD COLUMN tipo_adicional TEXT NOT NULL DEFAULT ''")
            # Migración: personal asociado a gastos (Adelanto de sueldo -> columna J de Registro_Gastos)
            cols_gas = [r[1] for r in conn.execute("PRAGMA table_info(gastos)").fetchall()]
            if "personal" not in cols_gas:
                conn.execute("ALTER TABLE gastos ADD COLUMN personal TEXT NOT NULL DEFAULT ''")
            # Migración: pago por Acumulado Efectivo (A. Efec) en liquidaciones
            # Migración: ampliar historial de Vitrina_Tienda a 8 conteos hacia atrás (h4..h8)
            cols_vit = [r[1] for r in conn.execute("PRAGMA table_info(vitrina_local)").fetchall()]
            for idx_h in range(4, 9):
                for suf in ("cant", "est"):
                    col_vh = f"h{idx_h}_{suf}"
                    if col_vh not in cols_vit:
                        conn.execute(f"ALTER TABLE vitrina_local ADD COLUMN {col_vh} TEXT NOT NULL DEFAULT ''")
            cols_liq = [r[1] for r in conn.execute("PRAGMA table_info(horario_liquidaciones)").fetchall()]
            if "a_efec" not in cols_liq:
                conn.execute("ALTER TABLE horario_liquidaciones ADD COLUMN a_efec REAL NOT NULL DEFAULT 0")
            # Semilla de personal (editable luego desde Cuentas / Pagos)
            if not conn.execute("SELECT 1 FROM personal_config LIMIT 1").fetchone():
                semilla = [
                    ("Eliana", "Recepcionista", "Eliana", '["eliana"]', 1500, 50, 55, 128.48, 3, "DIA", 0),
                    ("Eleiza", "Limpieza", "Limpieza", '["eleiza","eleisa"]', 1350, 50, 55, 153.49, 1, "DIA", 1),
                    ("Yolyi", "Turno Noche", "Turno Noche", '["yolyi","yoli","yoly"]', 1450, 50, 55, 0, 2, "NOCHE", 2),
                ]
                conn.executemany(
                    "INSERT INTO personal_config (empleado, rol, categoria_tienda, alias_json, sueldo, diario_manana, "
                    "diario_noche, afp, descanso_dow, turno, orden, activo) VALUES (?,?,?,?,?,?,?,?,?,?,?,1)",
                    semilla,
                )
            conn.commit()
        finally:
            conn.close()
