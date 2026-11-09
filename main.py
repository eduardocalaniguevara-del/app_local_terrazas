# -*- coding: utf-8 -*-
"""
Servidor Web Local Offline-First (FastAPI + SQLite WAL + Background Google Sheets Sync)
Recepción — Hotel Óvalo Mariátegui
Puerto: http://127.0.0.1:8080
"""

from contextlib import asynccontextmanager
from pathlib import Path
import re
import sys
import threading
import time
from typing import Any, Dict, List

_APP_DIR = str(Path(__file__).resolve().parent)
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from config import APP_HOST, APP_PORT, STATIC_DIR, SYNC_INTERVAL_SECONDS, NOMBRE_HOTEL
from database import get_connection, init_db
from domain_logic import (
    actualizar_comprobante_desde_panel_local,
    cancelar_estancia_local,
    continuar_estancia_local,
    guardar_checkin_local,
    guardar_color_habitacion_local,
    guardar_gasto_local,
    guardar_pago_posterior_local,
    guardar_venta_tienda_local,
    liberar_habitacion_local,
    modificar_estancia_local,
    obtener_datos_gastos_local,
    obtener_datos_iniciales_checkin_local,
    obtener_datos_pagos_posteriores_local,
    obtener_datos_panel_local,
    obtener_datos_tienda_local,
    obtener_datos_vitrina_local,
    guardar_conteo_vitrina_local,
    guardar_checkin_grupal_empresa_local,
    guardar_pago_masivo_local,
    obtener_estancias_pago_masivo_local,
    obtener_movimientos_local,
    obtener_registro_pagos_local,
    obtener_estancia_para_modificar_local,
    obtener_estado_red_cola,
    restaurar_estancia_local,
)
from sunat_wsp_service import (
    consultar_dni_local_o_reniec,
    consultar_ruc_local_o_sunat,
    ejecutar_orden_sunat_wsp,
    listar_jobs_sunat_recientes,
    obtener_estado_job_sunat,
)
from sync_engine import process_sync_queue, pull_from_sheets
from auto_updater import (
    iniciar_hilo_auto_actualizacion,
    obtener_version_actual,
    verificar_y_aplicar_actualizacion,
    reiniciar_servidor_en_caliente,
)


# Evento de despertar inmediato para el hilo de sincronización tras una escritura local
_sync_wakeup_event = threading.Event()
_sync_stop_event = threading.Event()
_sync_lock = threading.Lock()


def _is_bootstrap_completed() -> bool:
    conn = get_connection()
    try:
        r_flag = conn.execute("SELECT valor FROM estado_sistema WHERE clave = 'bootstrap_full_v2'").fetchone()
        if not r_flag or str(r_flag["valor"] or "") != "1":
            return False
        row = conn.execute("SELECT COUNT(*) AS c FROM estancias").fetchone()
        return bool(row and int(row["c"]) > 0)
    except Exception:
        return False
    finally:
        conn.close()


def _run_sync_cycle(force_full: bool = False) -> None:
    if not _sync_lock.acquire(blocking=False):
        return
    try:
        # 1. Subir primero cualquier operación pendiente en la cola local (FIFO idempotente)
        process_sync_queue()
        # 2. Descargar novedades desde Google Sheets (sin pisar filas dirty=1)
        boot_done = _is_bootstrap_completed()
        pull_from_sheets(full_bootstrap=(force_full or not boot_done))
    except Exception as exc:
        print(f"[SyncWorker] Advertencia en ciclo de sincronización: {exc}")
    finally:
        _sync_lock.release()


def _background_sync_worker() -> None:
    _run_sync_cycle(force_full=False)
    while not _sync_stop_event.is_set():
        triggered = _sync_wakeup_event.wait(timeout=SYNC_INTERVAL_SECONDS)
        if _sync_stop_event.is_set():
            break
        if triggered:
            _sync_wakeup_event.clear()
            time.sleep(0.3)
        _run_sync_cycle(force_full=False)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    worker = threading.Thread(target=_background_sync_worker, name="OvaloSyncWorker", daemon=True)
    worker.start()
    iniciar_hilo_auto_actualizacion(intervalo_segundos=20)
    yield
    _sync_stop_event.set()
    _sync_wakeup_event.set()


app = FastAPI(
    title="Hotel Terrazas de Villa — Recepción Local Offline-First",
    version="1.0.0",
    lifespan=lifespan,
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


class RpcPayload(BaseModel):
    args: List[Any] = []


WRITE_METHODS = {
    "editarEstanciaBDAdmin",
    "eliminarEstanciaBDAdmin",
    "editarGastoBDAdmin",
    "eliminarGastoBDAdmin",
    "editarVentaTiendaBDAdmin",
    "eliminarVentaTiendaBDAdmin",
    "editarPagoBDAdmin",
    "eliminarPagoBDAdmin",
    "marcarAsistenciaHorario",
    "editarCeldaHorario",
    "guardarPersonalConfig",
    "guardarLiquidacionHorario",
    "registrarVisitaPase",
    "cambiarEstadoVisitaPase",
    "eliminarVisitaPase",
    "guardarCheckInDesdeApp",
    "procesarCheckInApp",
    "guardarPagoPosteriorDesdeApp",
    "procesarPagoPosteriorApp",
    "guardarVentaTiendaDesdeApp",
    "procesarVentaTiendaApp",
    "guardarGastoDesdeApp",
    "procesarGastoApp",
    "continuarEstanciaDesdeApp",
    "continuarEstanciaRapida",
    "liberarHabitacionDesdeApp",
    "cancelarEstanciaDesdeApp",
    "restaurarEstanciaDesdeApp",
    "modificarEstanciaDesdeApp",
    "guardarModificacionEstancia",
    "actualizarEstadoComprobanteDesdePanel",
    "guardarConteoVitrina",
    "registrarCompraTienda",
    "cerrarCompraPendienteTienda",
    "registrarMovimientoTienda",
    "anularMovimientoAlmacen",
    "guardarCheckInGrupalEmpresa",
    "guardarPagoMasivo",
}


BRIDGE_BODY_LOCAL = """
<script>
  (function() {
    var hookBanner = function() {
      if (typeof window.mostrarBanner === 'function' && !window.mostrarBanner._hooked) {
        var origBanner = window.mostrarBanner;
        window.mostrarBanner = function(tipo, msg) {
          origBanner(tipo, msg);
          if (tipo === 'success' && window.parent && window.parent !== window) {
            window.parent.postMessage({ type: 'REFRESH_PANEL' }, '*');
          }
        };
        window.mostrarBanner._hooked = true;
      }
    };
    hookBanner();
    setTimeout(hookBanner, 500);

    var seleccionarEn = function(sel, coincide) {
      if (!sel || !sel.options || sel.options.length <= 1) return false;
      for (var i = 0; i < sel.options.length; i++) {
        if (coincide(sel.options[i])) {
          sel.selectedIndex = i;
          sel.dispatchEvent(new Event('change', { bubbles: true }));
          return true;
        }
      }
      return false;
    };

    window.aplicarContextoDesdePanel = function(ctx) {
      if (!ctx) return;
      if (typeof window.aplicarSeleccionDesdeContextoCheckIn === 'function' ||
          typeof window.aplicarContextoPago === 'function' ||
          typeof window.aplicarContextoTienda === 'function') {
        return; // CheckInRapido, PagosPosteriores y VentaTienda ya gestionan SET_CONTEXT directamente con UUID/fecha exacta
      }
      if (ctx.fechaPanelISO) {
        var fInIso = String(ctx.fechaPanelISO).trim();
        var dNext = new Date(fInIso + 'T12:00:00');
        dNext.setDate(dNext.getDate() + 1);
        var fOutIso = dNext.toISOString().split('T')[0];
        try { FECHA_HOY = fInIso; FECHA_MANANA = fOutIso; } catch (e) {}
        var elFIn = document.getElementById('fechaIn');
        var elFOut = document.getElementById('fechaOut');
        if (elFIn) { elFIn.value = fInIso; elFIn.dispatchEvent(new Event('change', { bubbles: true })); }
        if (elFOut) { elFOut.value = fOutIso; elFOut.dispatchEvent(new Event('change', { bubbles: true })); }
        var elGrIn = document.getElementById('grFechaIn');
        var elGrOut = document.getElementById('grFechaOut');
        if (elGrIn) elGrIn.value = fInIso;
        if (elGrOut) elGrOut.value = fOutIso;
        var elFGasto = document.getElementById('fechaGasto') || document.getElementById('fecha');
        if (elFGasto && elFGasto.type === 'date' && elFGasto.id !== 'fechaIn') elFGasto.value = fInIso;
      }
      if (typeof cargarDatosServidor === 'function') {
        try { cargarDatosServidor(); } catch (e) {}
      }
      if (ctx.tab) {
        var btn = document.getElementById(ctx.tab === 'continuar' ? 'tabBtnContinuar' : 'tabBtnCheckIn');
        if (btn) btn.click();
      }
      if (!ctx.hab) return;
      var hab = String(ctx.hab);
      var intentos = 18;
      var intentar = function() {
        var porValor = function(o) { return String(o.value).trim() === hab; };
        var porTexto = function(o) {
          var t = String(o.text).trim();
          return t.indexOf(hab + ' —') === 0 || t.indexOf(hab + ' -') === 0 || t.indexOf('Hab. ' + hab) !== -1;
        };
        var ok = false;
        if (ctx.tab === 'continuar') {
          var inpContHab = document.getElementById('contHabBuscar');
          if (inpContHab && typeof ESTANCIAS_ACTIVAS !== 'undefined' && ESTANCIAS_ACTIVAS.length > 0) {
            inpContHab.value = hab;
            inpContHab.dispatchEvent(new Event('input', { bubbles: true }));
            if (document.getElementById('estanciaActivaSelect') && document.getElementById('estanciaActivaSelect').value !== '') {
              if (ctx.fechaPanelISO) {
                var cIn = document.getElementById('contFechaIn');
                var cOut = document.getElementById('contFechaOut');
                var dN = new Date(ctx.fechaPanelISO + 'T12:00:00');
                dN.setDate(dN.getDate() + 1);
                if (cIn) cIn.value = ctx.fechaPanelISO;
                if (cOut) cOut.value = dN.toISOString().split('T')[0];
              }
              ok = true;
            }
          }
          if (!ok && seleccionarEn(document.getElementById('estanciaActivaSelect') || document.getElementById('estanciaSelect'), porTexto)) ok = true;
        } else {
          if (seleccionarEn(document.getElementById('hab'), porValor)) ok = true;
          if (seleccionarEn(document.getElementById('estanciaActivaSelect') || document.getElementById('estanciaSelect'), porTexto)) ok = true;
          if (seleccionarEn(document.getElementById('habTienda'), porValor)) ok = true;
        }
        if (!ok && intentos-- > 0) setTimeout(intentar, 200);
      };
      intentar();
    };

    window.addEventListener('message', function(e) {
      if (e.data && e.data.type === 'SET_CONTEXT') window.aplicarContextoDesdePanel(e.data.context);
    });
  })();
</script>"""


def _obtener_vista_formulario_local(nombre_vista: str) -> str:
    vistas_permitidas = {
        "CheckInRapido": "CheckInRapido.html",
        "PagosPosteriores": "PagosPosteriores.html",
        "VentaTienda": "VentaTienda.html",
        "GastosRegistro": "GastosRegistro.html",
    }
    fname = vistas_permitidas.get(str(nombre_vista or "").strip())
    if not fname:
        raise ValueError(f"Vista no reconocida: {nombre_vista}")
    fpath = STATIC_DIR / fname
    if not fpath.exists():
        raise FileNotFoundError(f"Archivo de vista no encontrado: {fname}")
    html = fpath.read_text(encoding="utf-8")
    html = html.replace("{{NOMBRE_HOTEL}}", NOMBRE_HOTEL)
    # Igual que Código.js (BRIDGE_BODY_): preselecciona habitación/pestaña enviada por el Panel
    # y avisa al Panel para refrescarse cuando el formulario guarda algo.
    if re.search(r"</body>", html, re.IGNORECASE):
        html = re.sub(r"</body>", lambda _m: BRIDGE_BODY_LOCAL + "\n</body>", html, count=1, flags=re.IGNORECASE)
    else:
        html += BRIDGE_BODY_LOCAL
    return html


@app.get("/", response_class=HTMLResponse)
def index():
    panel_path = STATIC_DIR / "PanelDisponibilidad.html"
    js_sunat_path = STATIC_DIR / "js" / "facturacion_sunat.js"
    html = panel_path.read_text(encoding="utf-8")
    mtime_ui = int(max(panel_path.stat().st_mtime, js_sunat_path.stat().st_mtime if js_sunat_path.exists() else 0))
    html = html.replace("{{NOMBRE_HOTEL}}", NOMBRE_HOTEL)
    html = html.replace('/static/js/api_bridge.js"', f'/static/js/api_bridge.js?v={mtime_ui}"')
    html = html.replace('/static/js/facturacion_sunat.js"', f'/static/js/facturacion_sunat.js?v={mtime_ui}"')
    return HTMLResponse(
        content=html,
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.get("/api/cuentas/pdf")
def api_descargar_pdf_cuenta_hab(hab: str = "", modo: str = "ingreso", fecha: str = "", estanciaUuid: str = ""):
    import pases_logic as pl
    try:
        pdf_path = pl.generar_pdf_cuenta_hab_local({"hab": hab, "modo": modo, "fecha": fecha, "estanciaUuid": estanciaUuid})
        return FileResponse(
            str(pdf_path),
            media_type="application/pdf",
            filename=pdf_path.name,
            headers={"Cache-Control": "no-store"},
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/sync/status")
def api_sync_status():
    st = obtener_estado_red_cola()
    try:
        mtime = int((STATIC_DIR / "PanelDisponibilidad.html").stat().st_mtime)
        st["uiVersion"] = f"{obtener_version_actual()}-{mtime}"
    except Exception:
        st["uiVersion"] = obtener_version_actual()
    return JSONResponse(
        content=st,
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.post("/api/system/update")
def api_system_update():
    res = verificar_y_aplicar_actualizacion()
    if res.get("actualizado"):
        reiniciar_servidor_en_caliente()
    return res


@app.post("/api/sync/trigger")
def api_sync_trigger():
    _sync_wakeup_event.set()
    return {"status": "TRIGGERED", "syncStatus": obtener_estado_red_cola()}


@app.get("/api/sunat/captura/{filename}")
def api_sunat_captura(filename: str):
    safe_name = Path(filename).name
    cap_path = Path(r"C:\Hostal_Sistema\capturas") / safe_name
    if not cap_path.is_file():
        raise HTTPException(status_code=404, detail="Captura no encontrada")
    return FileResponse(str(cap_path), media_type="image/png", headers={"Cache-Control": "no-store"})


@app.get("/api/sunat/jobs")
def api_sunat_jobs():
    return JSONResponse(content={"jobs": []}, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


@app.post("/api/sunat/orden")
def api_sunat_orden(payload: Dict[str, Any]):
    return JSONResponse(
        status_code=400,
        content={"status": "ERROR", "exito": False, "error": "Emisión automática SUNAT / WhatsApp deshabilitada en Hotel Terrazas.", "mensaje": "Emisión automática SUNAT / WhatsApp deshabilitada en Hotel Terrazas."},
    )


@app.post("/api/rpc/{method_name}")
def api_rpc_handler(method_name: str, body: RpcPayload):
    import domain_logic as dl

    args = body.args or []
    try:
        if method_name in ("obtenerDatosPanelDisponibilidad", "cambiarFechaPanelDisponibilidad"):
            fecha_iso = args[0] if len(args) > 0 and args[0] else None
            res = dl.obtener_datos_panel_local(fecha_iso)
        elif method_name == "obtenerVistaFormulario":
            res = _obtener_vista_formulario_local(args[0] if args else "")
        elif method_name == "obtenerDatosInicialesCheckIn":
            res = dl.obtener_datos_iniciales_checkin_local()
        elif method_name in ("consultarDniReniec", "consultarDocumentoPadron"):
            res = consultar_dni_local_o_reniec(args[0] if args else "")
        elif method_name in ("consultarRucSunat", "consultarRucPadron"):
            res = consultar_ruc_local_o_sunat(args[0] if args else "")
        elif method_name in ("guardarCheckInDesdeApp", "procesarCheckInApp"):
            res = dl.guardar_checkin_local(args[0] if args else {})
        elif method_name in ("obtenerDatosPagosPosteriores", "obtenerEstanciasConDeuda"):
            res = dl.obtener_datos_pagos_posteriores_local()
        elif method_name in ("guardarPagoPosteriorDesdeApp", "procesarPagoPosteriorApp"):
            res = dl.guardar_pago_posterior_local(args[0] if args else {})
        elif method_name == "obtenerDatosTienda":
            res = dl.obtener_datos_tienda_local()
        elif method_name in ("guardarVentaTiendaDesdeApp", "procesarVentaTiendaApp"):
            res = dl.guardar_venta_tienda_local(args[0] if args else {})
        elif method_name == "obtenerDatosGastos":
            res = dl.obtener_datos_gastos_local()
        elif method_name in ("guardarGastoDesdeApp", "procesarGastoApp"):
            res = dl.guardar_gasto_local(args[0] if args else {})
        elif method_name in ("continuarEstanciaDesdeApp", "continuarEstanciaRapida"):
            res = dl.continuar_estancia_local(args[0] if args else "")
        elif method_name == "liberarHabitacionDesdeApp":
            res = dl.liberar_habitacion_local(args[0] if args else {})
        elif method_name == "cancelarEstanciaDesdeApp":
            res = dl.cancelar_estancia_local(args[0] if args else {})
        elif method_name == "restaurarEstanciaDesdeApp":
            res = dl.restaurar_estancia_local(args[0] if args else {})
        elif method_name == "obtenerEstanciaParaModificar":
            hab = args[0] if len(args) > 0 else ""
            fila = args[1] if len(args) > 1 else None
            res = dl.obtener_estancia_para_modificar_local(hab, fila)
        elif method_name in ("modificarEstanciaDesdeApp", "guardarModificacionEstancia"):
            res = dl.modificar_estancia_local(args[0] if args else {})
        elif method_name == "actualizarEstadoComprobanteDesdePanel":
            res = dl.actualizar_comprobante_desde_panel_local(args[0] if args else {})
        elif method_name == "obtenerRegistroPagos":
            res = dl.obtener_registro_pagos_local(args[0] if args else {})
        elif method_name == "obtenerMovimientos":
            res = dl.obtener_movimientos_local(args[0] if args else {})
        elif method_name == "guardarColorFondoHabitacion":
            hab = args[0] if len(args) > 0 else ""
            color = args[1] if len(args) > 1 else ""
            fecha_ref = args[2] if len(args) > 2 else None
            nota = args[3] if len(args) > 3 else ""
            res = dl.guardar_color_habitacion_local(hab, color, fecha_ref, nota)
        elif method_name == "obtenerDatosVitrina":
            res = dl.obtener_datos_vitrina_local()
        elif method_name == "guardarConteoVitrina":
            res = dl.guardar_conteo_vitrina_local(args[0] if args else {})
        elif method_name == "verificarClaveGestionTienda":
            res = dl.verificar_clave_gestion_tienda_local(args[0] if args else {})
        elif method_name == "registrarCompraTienda":
            res = dl.registrar_compra_tienda_local(args[0] if args else {})
        elif method_name == "cerrarCompraPendienteTienda":
            res = dl.cerrar_compra_pendiente_tienda_local(args[0] if args else {})
        elif method_name == "registrarMovimientoTienda":
            res = dl.registrar_movimiento_tienda_local(args[0] if args else {})
        elif method_name == "anularMovimientoAlmacen":
            res = dl.anular_movimiento_almacen_local(args[0] if args else {})
        elif method_name == "obtenerRegistroEstanciasBD":
            res = dl.obtener_registro_estancias_bd_local(args[0] if args else {})
        elif method_name == "editarEstanciaBDAdmin":
            res = dl.editar_estancia_bd_admin_local(args[0] if args else {})
        elif method_name == "eliminarEstanciaBDAdmin":
            res = dl.eliminar_estancia_bd_admin_local(args[0] if args else {})
        elif method_name == "obtenerRegistroGastosBD":
            res = dl.obtener_registro_gastos_bd_local(args[0] if args else {})
        elif method_name == "editarGastoBDAdmin":
            res = dl.editar_gasto_bd_admin_local(args[0] if args else {})
        elif method_name == "eliminarGastoBDAdmin":
            res = dl.eliminar_gasto_bd_admin_local(args[0] if args else {})
        elif method_name == "obtenerRegistroVentasTiendaBD":
            res = dl.obtener_registro_ventas_tienda_bd_local(args[0] if args else {})
        elif method_name == "editarVentaTiendaBDAdmin":
            res = dl.editar_venta_tienda_bd_admin_local(args[0] if args else {})
        elif method_name == "eliminarVentaTiendaBDAdmin":
            res = dl.eliminar_venta_tienda_bd_admin_local(args[0] if args else {})
        elif method_name == "obtenerRegistroPagosBD":
            res = dl.obtener_registro_pagos_bd_local(args[0] if args else {})
        elif method_name == "editarPagoBDAdmin":
            res = dl.editar_pago_bd_admin_local(args[0] if args else {})
        elif method_name == "eliminarPagoBDAdmin":
            res = dl.eliminar_pago_bd_admin_local(args[0] if args else {})
        elif method_name == "obtenerHorario":
            import horario_logic as hl
            res = hl.obtener_horario_local(args[0] if args else {})
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
            res = dl.guardar_checkin_grupal_empresa_local(args[0] if args else {})
        elif method_name == "obtenerEstanciasPagoMasivo":
            res = dl.obtener_estancias_pago_masivo_local(args[0] if args else {})
        elif method_name == "guardarPagoMasivo":
            res = dl.guardar_pago_masivo_local(args[0] if args else {})
        elif method_name == "obtenerCuentaDetalladaHab":
            import pases_logic as pl
            res = pl.obtener_cuenta_detallada_hab_local(args[0] if args else {})
        else:
            raise HTTPException(status_code=404, detail=f"Método RPC no soportado: {method_name}")

        if method_name in WRITE_METHODS:
            _sync_wakeup_event.set()

        sync_st = dl.obtener_estado_red_cola()
        if isinstance(res, dict) and "syncStatus" not in res:
            res["syncStatus"] = sync_st

        return {"result": res, "syncStatus": sync_st}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


if __name__ == "__main__":
    import uvicorn

    if sys.stdout is None or sys.stderr is None:
        _log_f = open(Path(_APP_DIR) / "server_local.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = _log_f
        sys.stderr = _log_f
    uvicorn.run(app, host=APP_HOST, port=APP_PORT, log_config=None)
