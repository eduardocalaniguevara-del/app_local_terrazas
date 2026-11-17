# -*- coding: utf-8 -*-
"""
Script constructor de las vistas HTML locales (Offline-First) a partir de apps_script_ovalo/
Conserva 100% la estética y flujos que Recepción ya conoce, inyectando:
1. Tailwind CSS local (/static/js/tailwind_local.js) para funcionar sin internet tras corte de luz.
2. Puente Universal RPC (/static/js/api_bridge.js) con indicador de red en vivo.
3. Constructor Multi-Ítem IGV 10.5% + Emitir SUNAT + Enviar a WSP (/static/js/facturacion_sunat.js).
4. Accesibilidad en contenedor de tabla (tabindex="0" role="region" aria-label=...).
5. Corrección de raíz en navegación de fechas (0 ms) y filtro de botón 👤 1.
"""

from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "apps_script_ovalo"
DST_DIR = Path(__file__).resolve().parent / "static"


BRIDGE_OVERRIDE_SCRIPT = """<script>
  window.addEventListener('message', function(e) {
    if (!e.data || e.data.type !== 'SET_CONTEXT' || !e.data.context) return;
    if (typeof window.aplicarSeleccionDesdeContextoCheckIn === 'function') return;
    var ctx = e.data.context;
    if (ctx.fechaPanelISO) {
      var fInIso = String(ctx.fechaPanelISO).trim();
      var dNext = new Date(fInIso + 'T12:00:00');
      dNext.setDate(dNext.getDate() + 1);
      var fOutIso = dNext.toISOString().split('T')[0];
      try { FECHA_HOY = fInIso; FECHA_MANANA = fOutIso; } catch (err) {}
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
      try { cargarDatosServidor(); } catch (err) {}
    }
    if (ctx.tab === 'continuar' && ctx.hab) {
      var hab = String(ctx.hab);
      var tries = 18;
      var pickCont = function() {
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
            return;
          }
        }
        if (tries-- > 0) setTimeout(pickCont, 200);
      };
      pickCont();
    }
  });
</script>"""


def build_drawer_forms():
    forms = ["CheckInRapido.html", "PagosPosteriores.html", "VentaTienda.html", "GastosRegistro.html"]
    for fname in forms:
        src_path = SRC_DIR / fname
        dst_path = DST_DIR / fname
        html = src_path.read_text(encoding="utf-8")
        if "https://www.gstatic.com/antigravity/web/dev/tailwindcss.min.js" in html:
            html = html.replace(
                '<script src="https://www.gstatic.com/antigravity/web/dev/tailwindcss.min.js"></script>',
                '<script src="/static/js/tailwind_local.js"></script>\n  <script src="/static/js/api_bridge.js"></script>',
            )
        elif "/static/js/api_bridge.js" not in html:
            html = html.replace(
                "</head>",
                '  <script src="/static/js/api_bridge.js"></script>\n</head>',
                1,
            )
        if "ctx.fechaPanelISO" not in html and "</body>" in html:
            html = html.replace("</body>", BRIDGE_OVERRIDE_SCRIPT + "\n</body>", 1)
        dst_path.write_text(html, encoding="utf-8")
        print(f"[OK] Generado {dst_path.name} ({len(html)} bytes)")


def build_panel_disponibilidad():
    src_path = SRC_DIR / "PanelDisponibilidad.html"
    dst_path = DST_DIR / "PanelDisponibilidad.html"
    html = src_path.read_text(encoding="utf-8")

    # 1. Reemplazar CDN de Tailwind por motor local + puentes JS
    html = html.replace(
        '<script src="https://www.gstatic.com/antigravity/web/dev/tailwindcss.min.js"></script>',
        '<script src="/static/js/tailwind_local.js"></script>\n'
        '  <script src="/static/js/api_bridge.js"></script>\n'
        '  <script src="/static/js/facturacion_sunat.js"></script>',
    )

    # 2. Modern Web Guidance (responsive-table): región accesible y enfocable por teclado
    old_table_wrapper = (
        '  <div id="vistaPanelTabla" class="rounded-lg shadow-xs border overflow-x-hidden flex-1 min-h-0 overflow-y-auto"\n'
        '       style="background-color: #FAF6F3; border-color: #DFD2C5;">'
    )
    new_table_wrapper = (
        '  <div id="vistaPanelTabla" class="rounded-lg shadow-xs border overflow-x-auto flex-1 min-h-0 overflow-y-auto"\n'
        '       tabindex="0" role="region" aria-label="Panel de disponibilidad de habitaciones"\n'
        '       style="background-color: #FAF6F3; border-color: #DFD2C5;">'
    )
    assert old_table_wrapper in html, "No se encontró el wrapper de tabla"
    html = html.replace(old_table_wrapper, new_table_wrapper, 1)

    # 2b. Formato de fecha peruano fijo DD/MM/YYYY (24/10/2026) independiente del idioma de Chrome
    old_date_input = (
        '        <input type="date" id="inputFecha" onchange="alCambiarFechaInput(event)"\n'
        '               class="px-2 py-0.5 rounded border border-[#D8C7B8] font-black text-slate-900 text-xs sm:text-sm bg-white focus:outline-hidden focus:ring-2 focus:ring-blue-600 cursor-pointer">'
    )
    new_date_input = (
        '        <div class="relative inline-flex items-center bg-white rounded border border-[#D8C7B8] px-2.5 py-0.5 cursor-pointer hover:bg-amber-50/40 focus-within:ring-2 focus-within:ring-blue-600"\n'
        '             onclick="const inp=document.getElementById(\'inputFecha\'); if(inp && inp.showPicker) inp.showPicker();"\n'
        '             title="Clic para elegir fecha en el calendario (Formato Día/Mes/Año)">\n'
        '          <span id="lblFechaVisibleDMY" class="font-black text-slate-900 text-xs sm:text-sm tracking-tight select-none">24/10/2026</span>\n'
        '          <span class="ml-1.5 text-[11px] text-slate-500 select-none">📆</span>\n'
        '          <input type="date" id="inputFecha" onchange="alCambiarFechaInput(event)"\n'
        '                 class="absolute inset-0 opacity-0 w-full h-full cursor-pointer">\n'
        '        </div>'
    )
    assert old_date_input in html, "No se encontró old_date_input"
    html = html.replace(old_date_input, new_date_input, 1)

    old_sync_fn = """    // Sincronizar input type="date" con formato dd/MM/yyyy
    function sincronizarInputFecha(fechaDMY) {
      if (!inputFecha || !fechaDMY) return;
      const partes = String(fechaDMY).trim().split("/");
      if (partes.length === 3) {
        const dia = partes[0].padStart(2, '0');
        const mes = partes[1].padStart(2, '0');
        const anio = partes[2];
        inputFecha.value = `${anio}-${mes}-${dia}`;
      }
    }"""
    new_sync_fn = """    function actualizarEtiquetaFechaDMY(isoOrDmy) {
      const lbl = document.getElementById("lblFechaVisibleDMY");
      if (!lbl || !isoOrDmy) return;
      const s = String(isoOrDmy).trim();
      if (s.includes("-")) {
        const p = s.split("-");
        if (p.length === 3) lbl.textContent = `${p[2].padStart(2, '0')}/${p[1].padStart(2, '0')}/${p[0]}`;
      } else if (s.includes("/")) {
        const p = s.split("/");
        if (p.length === 3) lbl.textContent = `${p[0].padStart(2, '0')}/${p[1].padStart(2, '0')}/${p[2]}`;
      }
      if (typeof actualizarResaltadoFechaPanel === "function") {
        actualizarResaltadoFechaPanel(isoOrDmy);
      }
    }

    // Sincronizar input type="date" con formato dd/MM/yyyy
    function sincronizarInputFecha(fechaDMY) {
      if (!inputFecha || !fechaDMY) return;
      const partes = String(fechaDMY).trim().split("/");
      if (partes.length === 3) {
        const dia = partes[0].padStart(2, '0');
        const mes = partes[1].padStart(2, '0');
        const anio = partes[2];
        inputFecha.value = `${anio}-${mes}-${dia}`;
        actualizarEtiquetaFechaDMY(`${dia}/${mes}/${anio}`);
      }
    }"""
    assert old_sync_fn in html, "No se encontró old_sync_fn"
    html = html.replace(old_sync_fn, new_sync_fn, 1)

    old_nav_fns = """    function navegarFecha(dias) {
      const d = obtenerFechaInputActual();
      d.setDate(d.getDate() + dias);
      const iso = d.toISOString().split("T")[0];
      inputFecha.value = iso;
      solicitarCambioFecha(iso);
    }

    function irAHoy() {
      const iso = fechaHoyServidorISO || new Date().toISOString().split("T")[0];
      inputFecha.value = iso;
      solicitarCambioFecha(iso);
    }

    function alCambiarFechaInput(e) {
      if (e.target && e.target.value) {
        solicitarCambioFecha(e.target.value);
      }
    }"""
    new_nav_fns = """    function navegarFecha(dias) {
      const d = obtenerFechaInputActual();
      d.setDate(d.getDate() + dias);
      const iso = d.toISOString().split("T")[0];
      inputFecha.value = iso;
      actualizarEtiquetaFechaDMY(iso);
      solicitarCambioFecha(iso);
    }

    function irAHoy() {
      const iso = fechaHoyServidorISO || new Date().toISOString().split("T")[0];
      inputFecha.value = iso;
      actualizarEtiquetaFechaDMY(iso);
      solicitarCambioFecha(iso);
    }

    function alCambiarFechaInput(e) {
      if (e.target && e.target.value) {
        actualizarEtiquetaFechaDMY(e.target.value);
        solicitarCambioFecha(e.target.value);
      }
    }"""
    assert old_nav_fns in html, "No se encontró old_nav_fns"
    html = html.replace(old_nav_fns, new_nav_fns, 1)

    # 3. Mejorar recalcularHabitacionesEnCliente para incluir historialDia filtrado (sin falso positivo 👤 1) y estancias históricas
    old_recalc_loop = """      // Buscar la primera estancia activa por habitación para esa fecha (estanciasCacheRAM ya viene de más reciente a más antigua)
      const mapaPorHab = {};
      for (let i = 0; i < estanciasCacheRAM.length; i++) {
        const st = estanciasCacheRAM[i];
        const hab = String(st.hab || "").trim();
        if (!hab || mapaPorHab[hab]) continue;
        const activa = (st.ymdIn > 0 && st.ymdIn <= ymdTarget && st.ymdOut > ymdTarget) || (st.checkIn === fechaDMY);
        if (activa) {
          mapaPorHab[hab] = st;
        }
      }"""

    new_recalc_loop = """      // Buscar estancia activa e historial del mismo día (excluyendo continuaciones y mismo huésped)
      const mapaPorHab = {};
      const mapaAyerPorHab = {};
      const historialPorHab = {};
      const hoyYMD = isoAYMDNum(fechaHoyServidorISO || new Date().toISOString().split("T")[0]);
      const esVisualizacionPasada = hoyYMD > 0 && ymdTarget < hoyYMD;
      const dAyerRef = new Date(isoFecha + "T12:00:00");
      dAyerRef.setDate(dAyerRef.getDate() - 1);
      const isoAyerRef = dAyerRef.toISOString().split("T")[0];
      const ymdAyerRef = isoAYMDNum(isoAyerRef);
      const dmyAyerRef = isoADMY(isoAyerRef);

      for (let i = 0; i < estanciasCacheRAM.length; i++) {
        const st = estanciasCacheRAM[i];
        const hab = String(st.hab || "").trim();
        if (!hab) continue;
        const estLow = String(st.estadoEstancia || "").toLowerCase();
        const esContinuaCerrada = estLow.includes("continúa") || estLow.includes("continua");
        const esLibOCanc = estLow === "liberado" || estLow === "cancelado" || estLow.includes("cancel");

        if (!esLibOCanc && !esContinuaCerrada && !mapaAyerPorHab[hab]) {
          const activaAyer = (st.ymdIn > 0 && st.ymdIn <= ymdAyerRef && st.ymdOut > ymdAyerRef) || (st.checkIn === dmyAyerRef);
          if (activaAyer) mapaAyerPorHab[hab] = st;
        }

        if (esContinuaCerrada && !esVisualizacionPasada) continue;

        const activa = (st.ymdIn > 0 && st.ymdIn <= ymdTarget && st.ymdOut > ymdTarget) || (st.checkIn === fechaDMY);
        if (!activa) continue;

        if (esLibOCanc) {
          if (st.checkIn === fechaDMY && !st.continuoEstancia && !esContinuaCerrada) {
            if (!historialPorHab[hab]) historialPorHab[hab] = [];
            historialPorHab[hab].push(st);
          }
          continue;
        }

        if (!mapaPorHab[hab]) {
          mapaPorHab[hab] = st;
        } else if (st.checkIn === fechaDMY && !st.continuoEstancia && !esContinuaCerrada) {
          const act = mapaPorHab[hab];
          const mismoDni = st.dni && act.dni && String(st.dni).trim() === String(act.dni).trim();
          const mismoNom = st.huesped && act.huesped && String(st.huesped).trim().toUpperCase() === String(act.huesped).trim().toUpperCase();
          if (!mismoDni && !mismoNom) {
            if (!historialPorHab[hab]) historialPorHab[hab] = [];
            historialPorHab[hab].push(st);
          }
        }
      }"""

    assert old_recalc_loop in html, "No se encontró old_recalc_loop"
    html = html.replace(old_recalc_loop, new_recalc_loop, 1)

    # 4. Incluir historialDia, dni, celular, estanciaUuid en el mapeo de recalcularHabitacionesEnCliente
    old_free_ret = """            adicDetalle: "",
            saldo: 0,
            facturacion: null
          };"""
    new_free_ret = """            adicDetalle: "",
            saldo: 0,
            facturacion: null,
            ocupadaAyer: Boolean(mapaAyerPorHab[String(base.hab)]),
            huespedAyer: mapaAyerPorHab[String(base.hab)] ? (mapaAyerPorHab[String(base.hab)].huesped || "") : "",
            filaAyer: mapaAyerPorHab[String(base.hab)] ? (mapaAyerPorHab[String(base.hab)].fila || mapaAyerPorHab[String(base.hab)].filaNum || 0) : 0,
            idAyer: mapaAyerPorHab[String(base.hab)] ? (mapaAyerPorHab[String(base.hab)].id || "") : "",
            estanciaUuidAyer: mapaAyerPorHab[String(base.hab)] ? (mapaAyerPorHab[String(base.hab)].estanciaUuid || "") : "",
            historialDia: (historialPorHab[String(base.hab)] || [])
          };"""
    assert old_free_ret in html, "No se encontró old_free_ret"
    html = html.replace(old_free_ret, new_free_ret, 1)

    old_occ_ret = """          saldo: saldo,
          filaNum: st.fila || st.filaNum || 0,
          facturacion: st.facturacion || null
        };"""
    new_occ_ret = """          saldo: saldo,
          filaNum: st.fila || st.filaNum || 0,
          estanciaUuid: st.estanciaUuid || "",
          id: st.id || "",
          dni: st.dni || "",
          celular: st.celular || "",
          empresa: st.empresa || "",
          proyecto: st.proyecto || "",
          nAcomp: st.nAcomp || "",
          triple1: st.triple1 || "",
          tipoAdicional: st.tipoAdicional || "",
          facturacion: st.facturacion || null,
          historialDia: (historialPorHab[String(base.hab)] || []).filter(item => {
            const mDni = item.dni && st.dni && String(item.dni).trim() === String(st.dni).trim();
            const mNom = item.huesped && st.huesped && String(item.huesped).trim().toUpperCase() === String(st.huesped).trim().toUpperCase();
            return !mDni && !mNom && !item.continuoEstancia;
          })
        };"""
    assert old_occ_ret in html, "No se encontró old_occ_ret"
    html = html.replace(old_occ_ret, new_occ_ret, 1)

    # 5. Actualizar badgeSync en solicitarCambioFecha y recargarDatos respetando el estado real de red/cola SQLite
    old_badge_cambio = """            if (badgeSync) {
              badgeSync.className = "text-[10px] font-bold text-emerald-700 bg-emerald-100/80 px-2 py-0.5 rounded";
              badgeSync.textContent = "🟢 En vivo";
            }"""
    new_badge_cambio = """            if (res && res.syncStatus && typeof actualizarBadgeEstadoRed === "function") {
              actualizarBadgeEstadoRed(res.syncStatus);
            } else if (badgeSync) {
              badgeSync.className = "text-[10px] font-bold text-emerald-700 bg-emerald-100/80 px-2 py-0.5 rounded";
              badgeSync.textContent = "🟢 Sincronizado";
            }"""
    assert old_badge_cambio in html, "No se encontró old_badge_cambio"
    html = html.replace(old_badge_cambio, new_badge_cambio)

    # Cuando el usuario pulsa el botón manual "🔄 Actualizar", disparar también sincronización con Google Sheets en segundo plano
    old_recargar_start = """    function recargarDatos(e) {
      if (e) e.preventDefault();"""
    new_recargar_start = """    function recargarDatos(e) {
      if (e) {
        e.preventDefault();
        fetch("/api/sync/trigger", { method: "POST" }).catch(() => {});
      }"""
    assert old_recargar_start in html, "No se encontró old_recargar_start"
    html = html.replace(old_recargar_start, new_recargar_start, 1)

    # 6. En verDetalleFacturacion: agregar constructor Multi-Ítem IGV 10.5% (+ Adicionar) y botones Emitir en SUNAT y Enviar a WSP
    old_fact_buttons = """            <div class="flex gap-1.5 pt-0.5">
              <button type="button" id="btnGuardarFactModal"
                      onclick="guardarEstadoComprobanteModal('${h.hab}', false)"
                      class="flex-1 bg-slate-800 hover:bg-slate-900 text-white font-extrabold py-1.5 px-3 rounded-lg text-[11px] shadow-2xs cursor-pointer transition-colors">
                💾 Guardar Cambios
              </button>
              ${f.estado === 'Pendiente' ? `
              <button type="button" id="btnEmitirRapidoModal"
                      onclick="guardarEstadoComprobanteModal('${h.hab}', true)"
                      class="flex-1 bg-emerald-600 hover:bg-emerald-700 text-white font-black py-1.5 px-3 rounded-lg text-[11px] shadow-2xs cursor-pointer transition-colors">
                ✅ Ya lo emití — Marcar Emitido
              </button>` : ''}
            </div>"""

    new_fact_buttons = """            <!-- CONSTRUCTOR MULTI-ÍTEM SUNAT (IGV 10.5% LEY N° 31556) -->
            <div class="bg-white border border-slate-300 rounded-lg p-2 space-y-1.5 shadow-2xs">
              <div class="flex items-center justify-between">
                <div>
                  <span class="text-[10px] font-black text-slate-800 uppercase tracking-tight">🧾 Ítems SUNAT (IGV 10.5% Ley 31556)</span>
                  <span class="block text-[9px] text-slate-500">V. Unit = P. Unit ÷ 1.105 (automático a 4 decimales)</span>
                </div>
                <button type="button"
                        onclick="agregarFilaItemSunat(1, 'DIA DE ALOJAMIENTO HAB N ' + '${h.hab}', ${tarifaNoche > 0 ? tarifaNoche : 60})"
                        class="px-2 py-1 rounded-md bg-blue-600 hover:bg-blue-700 text-white font-black text-[10px] shadow-2xs cursor-pointer flex items-center gap-1 transition-colors">
                  ➕ Adicionar Hab/Día
                </button>
              </div>
              <div class="overflow-x-auto">
                <table class="w-full text-left border-collapse">
                  <thead>
                    <tr class="border-b border-slate-200 text-[9px] font-black text-slate-500 uppercase">
                      <th class="p-1 w-12 text-center">Cant.</th>
                      <th class="p-1">Descripción SUNAT</th>
                      <th class="p-1 w-16 text-right">P.Unit (S/)</th>
                      <th class="p-1 w-14 text-right" title="Valor Unitario sin IGV 10.5%">V.Unit</th>
                      <th class="p-1 w-16 text-right">Subtotal</th>
                      <th class="p-1 w-6"></th>
                    </tr>
                  </thead>
                  <tbody id="tbodyItemsFacturacion" class="divide-y divide-slate-100"></tbody>
                </table>
              </div>
              <div class="flex items-center justify-between pt-1 border-t border-slate-200 text-[10px] font-mono">
                <span class="text-slate-600">Op. Gravada: <strong id="lblTotalValorGravado" class="text-slate-900">S/ 0.00</strong></span>
                <span class="text-slate-600">IGV (10.5%): <strong id="lblTotalIgv105" class="text-slate-900">S/ 0.00</strong></span>
                <span class="text-slate-900 font-black">Total: <strong id="lblTotalItemsSunat" class="text-emerald-700">S/ 0.00</strong></span>
              </div>
            </div>

            <div class="flex gap-1.5 pt-0.5">
              <button type="button" id="btnGuardarFactModal"
                      onclick="guardarEstadoComprobanteModal('${h.hab}', false)"
                      class="flex-1 bg-slate-800 hover:bg-slate-900 text-white font-extrabold py-1.5 px-2.5 rounded-lg text-[10.5px] shadow-2xs cursor-pointer transition-colors">
                💾 Guardar Datos
              </button>
              ${f.estado === 'Pendiente' ? `
              <button type="button" id="btnEmitirRapidoModal"
                      onclick="guardarEstadoComprobanteModal('${h.hab}', true)"
                      class="flex-1 bg-slate-600 hover:bg-slate-700 text-white font-black py-1.5 px-2.5 rounded-lg text-[10.5px] shadow-2xs cursor-pointer transition-colors"
                      title="Marcar manualmente como Emitido si ya lo hiciste en el portal SUNAT">
                ✅ Marcar Emitido
              </button>` : ''}
            </div>

            <!-- BOTONES DE EMISIÓN AUTOMÁTICA SUNAT Y ENVÍO POR WHATSAPP -->
            <div class="grid grid-cols-2 gap-1.5 pt-1 border-t border-slate-200/80">
              <button type="button" id="btnOrdenEmitirSunat"
                      onclick="ejecutarOrdenSunatModal('${h.hab}', 'EMITIR')"
                      class="bg-indigo-700 hover:bg-indigo-800 text-white font-black py-2 px-2.5 rounded-lg text-[11px] shadow-xs cursor-pointer flex items-center justify-center gap-1 transition-colors">
                ⚡ Emitir en SUNAT
              </button>
              <button type="button" id="btnOrdenEmitirWsp"
                      onclick="ejecutarOrdenSunatModal('${h.hab}', 'EMITIR_Y_WSP')"
                      class="bg-emerald-600 hover:bg-emerald-700 text-white font-black py-2 px-2.5 rounded-lg text-[11px] shadow-xs cursor-pointer flex items-center justify-center gap-1 transition-colors">
                ⚡📲 Emitir y Enviar WSP
              </button>
              ${f.estado === 'Emitido' ? `
              <button type="button" id="btnOrdenSoloWsp"
                      onclick="ejecutarOrdenSunatModal('${h.hab}', 'SOLO_WSP')"
                      class="col-span-2 bg-teal-700 hover:bg-teal-800 text-white font-black py-1.5 px-3 rounded-lg text-[11px] shadow-2xs cursor-pointer flex items-center justify-center gap-1.5 transition-colors">
                📲 Reenviar Comprobante por WhatsApp
              </button>` : ''}
            </div>"""

    assert old_fact_buttons in html, "No se encontró old_fact_buttons"
    html = html.replace(old_fact_buttons, new_fact_buttons, 1)

    # Inicializar las filas del constructor Multi-Ítem justo al abrir el modal en verDetalleFacturacion
    old_open_pop = """      popModal.classList.remove("hidden");
      popModal.classList.add("flex");
    }"""
    new_open_pop = """      popModal.classList.remove("hidden");
      popModal.classList.add("flex");
      if (typeof inicializarItemsFacturacionModal === "function") {
        inicializarItemsFacturacionModal(h, montoSolicitado, subtotalAloj, tarifaNoche);
      }
    }"""
    assert old_open_pop in html, "No se encontró old_open_pop"
    html = html.replace(old_open_pop, new_open_pop, 1)

    dst_path.write_text(html, encoding="utf-8")
    print(f"[OK] Generado {dst_path.name} ({len(html)} bytes)")


if __name__ == "__main__":
    build_drawer_forms()
    build_panel_disponibilidad()
