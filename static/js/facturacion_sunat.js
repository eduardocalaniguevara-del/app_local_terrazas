/**
 * Módulo Asíncrono de Facturación Electrónica SUNAT (IGV 10.5% Ley 31556) + Notas de Crédito + WhatsApp
 * - Constructor Multi-Ítem sincronizado con Vista Grande Formato SUNAT (Lupa 🔍)
 * - Botón 1-clic de Identidad del Huésped ("IDENTIFICADO CON DNI ... Y NOMBRE ...")
 * - Totales explícitos: Op. Gravada, IGV (10.5%) e Importe Total
 * - Ejecución 100% en segundo plano: cierra el modal al instante, muestra señal flotante en vivo
 *   y cambia a ✅ Emitido recién cuando termina todo el proceso (SUNAT + WhatsApp).
 */
(function () {
  const FACTOR_IGV_HOTEL = 1.105;

  let _itemsActuales = [];
  let _habActivaModal = "";
  let _tarifaRefModal = 60;
  let _focusedItemIdx = 0;

  // Registro vivo de trabajos en segundo plano: hab -> job
  window.sunatJobsPorHab = window.sunatJobsPorHab || {};
  const _notifiedDoneJobs = new Set();
  let _pollTimer = null;

  function _escHtml(str) {
    return String(str || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function _obtenerHabObj(habNum) {
    const lista = window.habitacionesData || [];
    return lista.find((x) => String(x.hab) === String(habNum)) || null;
  }

  function _obtenerFactEfectiva(h) {
    if (!h) return {};
    if (typeof window.obtenerFacturacionEfectiva === "function") {
      return window.obtenerFacturacionEfectiva(h) || {};
    }
    return h.facturacion || {};
  }

  function _cerrarModalDetallePanel() {
    const dlgBig = document.getElementById("dialogFormatoSunatGrande");
    if (dlgBig && dlgBig.open) {
      try { dlgBig.close(); } catch (e) {}
    }
    const popModal = document.getElementById("popoverModal");
    if (popModal) {
      popModal.classList.add("hidden");
      popModal.classList.remove("flex");
    }
  }

  function _instalarLightDismissDialog(dlg) {
    if (!dlg || dlg._lightDismissInstalled) return;
    dlg._lightDismissInstalled = true;
    if (!("closedBy" in HTMLDialogElement.prototype)) {
      dlg.addEventListener("click", (event) => {
        if (event.target !== dlg) return;
        const rect = dlg.getBoundingClientRect();
        const inside =
          rect.top <= event.clientY &&
          event.clientY <= rect.top + rect.height &&
          rect.left <= event.clientX &&
          event.clientX <= rect.left + rect.width;
        if (!inside) dlg.close();
      });
    }
  }

  // =========================================================================
  // 1. GESTIÓN DE ÍTEMS Y DESGLOSE OP. GRAVADA + IGV 10.5% + IMPORTE TOTAL
  // =========================================================================

  const MESES_ES_SUNAT = [
    "ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO",
    "JULIO", "AGOSTO", "SEPTIEMBRE", "OCTUBRE", "NOVIEMBRE", "DICIEMBRE"
  ];
  let _timerDniModalLookup = null;

  function _formatearFechaAlojamientoTexto(fechaRaw) {
    const s = String(fechaRaw || "").trim();
    if (!s || s === "-") return "HOY";
    let m = s.match(/^(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{2,4})/);
    if (m) {
      const dia = parseInt(m[1], 10);
      const mesIdx = parseInt(m[2], 10) - 1;
      if (dia >= 1 && dia <= 31 && mesIdx >= 0 && mesIdx < 12) {
        return `${dia} DE ${MESES_ES_SUNAT[mesIdx]}`;
      }
    }
    m = s.match(/^(\d{4})-(\d{1,2})-(\d{1,2})/);
    if (m) {
      const dia = parseInt(m[3], 10);
      const mesIdx = parseInt(m[2], 10) - 1;
      if (dia >= 1 && dia <= 31 && mesIdx >= 0 && mesIdx < 12) {
        return `${dia} DE ${MESES_ES_SUNAT[mesIdx]}`;
      }
    }
    return s.toUpperCase();
  }

  /**
   * Valida y normaliza DNI (7 dígitos -> 8 dígitos con '0' adelante):
   * - Si ingresó algún DNI con longitud inválida (ej. 4 dígitos), bloquea.
   * - Si tiene número de celular registrado o solicita envío WSP, es OBLIGATORIO tener DNI válido (7 u 8 dígitos).
   */
  window.validarDniConCelularModal = function (rawDni, rawCelular, esFact, ruc, exigirPorWsp) {
    const cel = String(rawCelular || "").replace(/\D/g, "").trim();
    const token = String(rawDni || "").split(/[\/,;]+/)[0].trim();
    const esVacio = !token || token === "-" || token.toUpperCase() === "SIN DOCUMENTO";
    let dniDig = esVacio ? "" : token.replace(/\D/g, "");
    if (dniDig.length === 7) {
      dniDig = "0" + dniDig;
    }
    const rucClean = String(ruc || "").replace(/\D/g, "");
    if (esFact && !dniDig && /^10\d{9}$/.test(rucClean)) {
      dniDig = rucClean.slice(2, 10);
    }

    if (!esVacio && dniDig.length !== 8) {
      return {
        ok: false,
        dniNormalizado: "",
        error:
          `⚠️ El DNI registrado ('${token}' — ${token.replace(/\D/g, "").length} dígitos) es incorrecto.\n\n` +
          `Debe tener 7 u 8 dígitos numéricos válidos antes de emitir o enviar.`,
      };
    }

    if ((cel.length > 0 || exigirPorWsp) && dniDig.length !== 8) {
      return {
        ok: false,
        dniNormalizado: "",
        error:
          `⚠️ El huésped tiene número de celular (${cel || "WhatsApp"}), por lo que es OBLIGATORIO ` +
          `tener su DNI correcto (7 u 8 dígitos) antes de emitir o enviar la ${esFact ? "Factura" : "Boleta"}.\n\n` +
          `Corrija el campo '🪪 DNI Huésped' en esta ventana.`,
      };
    }

    return { ok: true, dniNormalizado: dniDig, error: "" };
  };

  function _refrescarEstadoVisualDniModal(consultarReniec) {
    const inpDni = document.getElementById("modalFact_dni");
    const inpCel = document.getElementById("modalFact_celular");
    const lbl = document.getElementById("lblDniModalStatus");
    if (!inpDni) return;

    const val = String(inpDni.value || "").replace(/\D/g, "").slice(0, 8);
    if (inpDni.value !== val) inpDni.value = val;
    const cel = String(inpCel?.value || "").replace(/\D/g, "");
    const esFact = String(document.getElementById("modalFact_tipo")?.value || "").toUpperCase() === "FACTURA";
    const ruc = String(document.getElementById("modalFact_ruc")?.value || "").replace(/\D/g, "");
    const chk = window.validarDniConCelularModal(val, cel, esFact, ruc, false);

    inpDni.classList.remove("border-slate-300", "border-red-500", "bg-red-50", "border-emerald-500");
    if (!chk.ok) {
      inpDni.classList.add("border-red-500", "bg-red-50");
      if (lbl) {
        lbl.textContent = val.length > 0 ? `⚠️ ${val.length} díg.` : "⚠️ Req. DNI";
        lbl.className = "text-[8.5px] font-black text-red-600 truncate max-w-[75px]";
      }
      return;
    }

    if (val.length === 7 || val.length === 8) {
      inpDni.classList.add("border-emerald-500");
      const dni8 = val.length === 7 ? ("0" + val) : val;
      if (lbl) {
        lbl.textContent = val.length === 7 ? `✅ ${dni8}` : "✅ DNI OK";
        lbl.className = "text-[8.5px] font-bold text-emerald-700 truncate max-w-[75px]";
      }
      if (consultarReniec && typeof google !== "undefined" && google.script && google.script.run) {
        clearTimeout(_timerDniModalLookup);
        _timerDniModalLookup = setTimeout(() => {
          google.script.run
            .withSuccessHandler((res) => {
              if (res && res.ok && res.nombre && lbl) {
                lbl.textContent = "✅ " + res.nombre.split(" ")[0];
                lbl.title = res.nombre;
                lbl.className = "text-[8.5px] font-bold text-emerald-700 truncate max-w-[75px]";
              }
            })
            .consultarDniReniec(dni8);
        }, 320);
      }
    } else {
      inpDni.classList.add("border-slate-300");
      if (lbl) {
        lbl.textContent = "";
        lbl.className = "text-[8.5px] font-bold text-slate-400 truncate max-w-[75px]";
      }
    }
  }

  window.onInputDniModalFact = function () {
    _refrescarEstadoVisualDniModal(true);
  };

  window.onInputCelularModalFact = function (inp) {
    if (inp) {
      const limpio = String(inp.value || "").replace(/[^\d+\s-]/g, "");
      if (inp.value !== limpio) inp.value = limpio;
    }
    _refrescarEstadoVisualDniModal(false);
  };

  window.inicializarItemsFacturacionModal = function (h, montoSolicitado, subtotalAloj, tarifaNoche) {
    _habActivaModal = String(h.hab || "");
    _tarifaRefModal = Number(tarifaNoche) > 0 ? Number(tarifaNoche) : 60;
    _focusedItemIdx = 0;
    const fechaTxt = _formatearFechaAlojamientoTexto(h.checkIn);
    const defaultDesc = `DIA DE ALOJAMIENTO DEL ${fechaTxt}, HAB N ${h.hab}`;
    const defaultPrecio = Number(montoSolicitado) > 0
      ? Number(montoSolicitado)
      : (Number(subtotalAloj) || Number(tarifaNoche) || 0);
    _itemsActuales = [
      {
        cant: 1,
        desc: defaultDesc.toUpperCase(),
        precioUnit: defaultPrecio > 0 ? Number(defaultPrecio.toFixed(2)) : 0,
      },
    ];
    _renderizarTablasItems();
    _refrescarEstadoVisualDniModal(false);
  };

  window.agregarFilaItemSunat = function (cant, desc, precioUnit) {
    _sincronizarDesdeInputsActivos();
    const c = Math.max(1, parseInt(cant, 10) || 1);
    const d = String(desc || `DIA DE ALOJAMIENTO HAB N ${_habActivaModal}`).trim().toUpperCase();
    const p = Number(precioUnit) > 0 ? Number(Number(precioUnit).toFixed(2)) : _tarifaRefModal;
    _itemsActuales.push({ cant: c, desc: d, precioUnit: p });
    _focusedItemIdx = _itemsActuales.length - 1;
    _renderizarTablasItems();
  };

  window.eliminarFilaItemSunatIdx = function (idx) {
    _sincronizarDesdeInputsActivos();
    if (_itemsActuales.length <= 1) {
      alert("⚠️ Debe existir al menos un ítem para emitir el comprobante.");
      return;
    }
    _itemsActuales.splice(idx, 1);
    if (_focusedItemIdx >= _itemsActuales.length) {
      _focusedItemIdx = Math.max(0, _itemsActuales.length - 1);
    }
    _renderizarTablasItems();
  };

  // Compatibilidad por si algún botón antiguo llama eliminarFilaItemSunat(btn)
  window.eliminarFilaItemSunat = function (btn) {
    const tr = btn && btn.closest ? btn.closest("tr") : null;
    const idx = tr ? Number(tr.dataset.idx || 0) : 0;
    window.eliminarFilaItemSunatIdx(idx);
  };

  function _construirTextoIdentidadHuesped(h, f) {
    const inpDniModal = document.getElementById("modalFact_dni");
    const modalDniVal = inpDniModal ? String(inpDniModal.value || "").trim() : "";
    const rawDni = modalDniVal || String((f && f.dni) || (h && h.dni) || "").trim();
    const rawNom = String((h && h.huesped) || "").trim().toUpperCase();
    if ((!rawDni || rawDni === "-") && (!rawNom || rawNom === "-")) {
      return { ok: true, texto: "", error: "" };
    }
    const dnisBrutos = rawDni
      .split(/[\/,;]+/)
      .map((s) => s.replace(/\D/g, "").trim())
      .filter(Boolean);

    for (let i = 0; i < dnisBrutos.length; i++) {
      const tok = dnisBrutos[i];
      if (tok.length !== 7 && tok.length !== 8) {
        return {
          ok: false,
          texto: "",
          error: `⚠️ El DNI '${tok}' (${tok.length} dígitos) es inválido. Corríjalo a 7 u 8 dígitos en el campo '🪪 DNI Huésped' antes de anexarlo.`,
        };
      }
    }

    const dnis = dnisBrutos.map((d) => (d.length === 7 ? "0" + d : d));
    const noms = rawNom.split(/[\/;]+/).map((s) => s.trim()).filter((s) => s && s !== "-");

    if (dnis.length > 1 && noms.length > 1) {
      const pares = [];
      const maxLen = Math.max(dnis.length, noms.length);
      for (let i = 0; i < maxLen; i++) {
        const d = dnis[i] || "";
        const n = noms[i] || "";
        if (d && n) pares.push(`IDENTIFICADO CON DNI ${d} Y NOMBRE ${n}`);
        else if (n) pares.push(`NOMBRE ${n}`);
      }
      return { ok: true, texto: pares.join("; "), error: "" };
    }

    const dniUnico = dnis[0] || "";
    const nomUnico = noms.join(" ") || "";
    if (dniUnico && nomUnico) {
      return { ok: true, texto: `IDENTIFICADO CON DNI ${dniUnico} Y NOMBRE ${nomUnico}`, error: "" };
    }
    if (dniUnico) {
      return { ok: true, texto: `IDENTIFICADO CON DNI ${dniUnico}`, error: "" };
    }
    return { ok: true, texto: `IDENTIFICADO CON NOMBRE ${nomUnico}`, error: "" };
  }

  window.adicionarIdentidadHuespedItem = function (habNum) {
    _sincronizarDesdeInputsActivos();
    const habKey = String(habNum || _habActivaModal || "");
    const h = _obtenerHabObj(habKey);
    const f = _obtenerFactEfectiva(h);
    if (_itemsActuales.length === 0) return;
    const idx = (_focusedItemIdx >= 0 && _focusedItemIdx < _itemsActuales.length)
      ? _focusedItemIdx
      : (_itemsActuales.length - 1);
    const item = _itemsActuales[idx];
    const actual = String(item.desc || "").trim().toUpperCase();
    const marcador = ", IDENTIFICADO CON ";

    if (actual.includes("IDENTIFICADO CON ")) {
      // Si ya estaba agregado, al volver a pulsar lo quita limpiamente (toggle sin duplicar)
      const pos = actual.indexOf(marcador);
      if (pos !== -1) {
        item.desc = actual.slice(0, pos).trim();
      } else {
        item.desc = actual.replace(/\s*,?\s*IDENTIFICADO CON .*$/i, "").trim();
      }
      _renderizarTablasItems();
      return;
    }

    const resId = _construirTextoIdentidadHuesped(h, f);
    if (!resId.ok) {
      alert(resId.error);
      document.getElementById("modalFact_dni")?.focus();
      return;
    }
    if (!resId.texto) {
      alert("⚠️ Esta habitación no tiene DNI ni nombre de huésped registrado.");
      return;
    }
    item.desc = actual ? `${actual}, ${resId.texto}` : resId.texto;
    _renderizarTablasItems();
  };

  function _sincronizarDesdeInputsActivos() {
    const dlgBig = document.getElementById("dialogFormatoSunatGrande");
    const usarGrande = dlgBig && dlgBig.open;
    const tbody = document.getElementById(usarGrande ? "tbodyItemsSunatGrande" : "tbodyItemsFacturacion");
    if (!tbody) return;
    const trs = tbody.querySelectorAll("tr[data-idx]");
    if (!trs.length) return;
    const nuevos = [];
    trs.forEach((tr) => {
      const cant = Math.max(1, parseInt(tr.querySelector(".it-cant")?.value, 10) || 1);
      const desc = String(tr.querySelector(".it-desc")?.value || "").replace(/\s+/g, " ").trim().toUpperCase();
      const precioUnit = parseFloat(tr.querySelector(".it-precio")?.value) || 0;
      nuevos.push({ cant, desc, precioUnit });
    });
    _itemsActuales = nuevos;
  }

  window.onEditarItemSunatLive = function (idx, campo, valor, origenGrande) {
    if (!_itemsActuales[idx]) return;
    _focusedItemIdx = idx;
    if (campo === "cant") {
      _itemsActuales[idx].cant = Math.max(1, parseInt(valor, 10) || 1);
    } else if (campo === "desc") {
      _itemsActuales[idx].desc = String(valor || "").toUpperCase();
    } else if (campo === "precio") {
      _itemsActuales[idx].precioUnit = parseFloat(valor) || 0;
    }
    // Actualizar cálculos en la fila activa sin perder el foco del cursor y sincronizar la otra tabla
    _actualizarCalculosYEspejo(idx, Boolean(origenGrande));
  };

  function _actualizarCalculosYEspejo(changedIdx, origenGrande) {
    let totalGeneral = 0;
    _itemsActuales.forEach((it) => {
      totalGeneral += (Math.max(1, it.cant) * (Number(it.precioUnit) || 0));
    });

    const actualizarFilaDOM = (tbodyId, idx, syncInputs) => {
      const tb = document.getElementById(tbodyId);
      if (!tb) return;
      const tr = tb.querySelector(`tr[data-idx="${idx}"]`);
      if (!tr || !_itemsActuales[idx]) return;
      const it = _itemsActuales[idx];
      const vUnit = ((Number(it.precioUnit) || 0) / FACTOR_IGV_HOTEL).toFixed(4);
      const sub = (Math.max(1, it.cant) * (Number(it.precioUnit) || 0)).toFixed(2);
      if (syncInputs) {
        const ic = tr.querySelector(".it-cant");
        const id = tr.querySelector(".it-desc");
        const ip = tr.querySelector(".it-precio");
        if (ic) ic.value = it.cant;
        if (id) id.value = it.desc;
        if (ip) ip.value = it.precioUnit > 0 ? it.precioUnit : "";
      }
      const iv = tr.querySelector(".it-valor-unit");
      const isub = tr.querySelector(".it-subtotal");
      if (iv) iv.value = vUnit;
      if (isub) isub.textContent = `S/ ${sub}`;
    };

    if (origenGrande) {
      actualizarFilaDOM("tbodyItemsSunatGrande", changedIdx, false);
      actualizarFilaDOM("tbodyItemsFacturacion", changedIdx, true);
    } else {
      actualizarFilaDOM("tbodyItemsFacturacion", changedIdx, false);
      actualizarFilaDOM("tbodyItemsSunatGrande", changedIdx, true);
    }
    _pintarTotalesGlobales(totalGeneral);
  }

  function _pintarTotalesGlobales(totalGeneral, skipMontoInput) {
    const totalGravado = totalGeneral / FACTOR_IGV_HOTEL;
    const totalIgv = totalGeneral - totalGravado;

    const idsGrav = ["lblTotalValorGravado", "lblBigOpGravada"];
    const idsIgv = ["lblTotalIgv105", "lblBigIgv105"];
    const idsTot = ["lblTotalItemsSunat", "lblBigImporteTotal"];

    idsGrav.forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.textContent = `S/ ${totalGravado.toFixed(2)}`;
    });
    idsIgv.forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.textContent = `S/ ${totalIgv.toFixed(2)}`;
    });
    idsTot.forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.textContent = `S/ ${totalGeneral.toFixed(2)}`;
    });

    if (!skipMontoInput) {
      const inpMontoModal = document.getElementById("modalFact_monto");
      if (inpMontoModal && totalGeneral > 0 && document.activeElement !== inpMontoModal) {
        inpMontoModal.value = totalGeneral.toFixed(2);
      }
    }
  }

  window.onCambiarMontoCompModal = function (valor) {
    if (_itemsActuales.length !== 1) return;
    const nuevoPrecio = parseFloat(valor) || 0;
    const cant = Math.max(1, parseInt(_itemsActuales[0].cant, 10) || 1);
    _itemsActuales[0].precioUnit = cant > 1 ? Number((nuevoPrecio / cant).toFixed(2)) : nuevoPrecio;

    const actualizarFilaPrecio = (tbodyId) => {
      const tb = document.getElementById(tbodyId);
      if (!tb) return;
      const tr = tb.querySelector('tr[data-idx="0"]');
      if (!tr) return;
      const it = _itemsActuales[0];
      const vUnit = ((Number(it.precioUnit) || 0) / FACTOR_IGV_HOTEL).toFixed(4);
      const sub = (Math.max(1, it.cant) * (Number(it.precioUnit) || 0)).toFixed(2);
      const ip = tr.querySelector(".it-precio");
      const iv = tr.querySelector(".it-valor-unit");
      const isub = tr.querySelector(".it-subtotal");
      if (ip) ip.value = it.precioUnit > 0 ? it.precioUnit : "";
      if (iv) iv.value = vUnit;
      if (isub) isub.textContent = `S/ ${sub}`;
    };

    actualizarFilaPrecio("tbodyItemsFacturacion");
    actualizarFilaPrecio("tbodyItemsSunatGrande");
    _pintarTotalesGlobales(nuevoPrecio, true);
  };

  function _renderizarTablasItems() {
    let totalGeneral = 0;

    // 1. Tabla compacta del modal lateral
    const tbodyComp = document.getElementById("tbodyItemsFacturacion");
    if (tbodyComp) {
      tbodyComp.innerHTML = _itemsActuales
        .map((it, idx) => {
          const vUnit = ((Number(it.precioUnit) || 0) / FACTOR_IGV_HOTEL).toFixed(4);
          const sub = (Math.max(1, it.cant) * (Number(it.precioUnit) || 0));
          totalGeneral += sub;
          return `
            <tr data-idx="${idx}" class="border-b border-slate-100 last:border-b-0">
              <td class="py-1 pr-1">
                <input type="number" min="1" value="${it.cant}"
                       onfocus="_setFocusItemSunat(${idx})"
                       oninput="onEditarItemSunatLive(${idx}, 'cant', this.value, false)"
                       class="it-cant w-11 border border-slate-300 rounded px-1 py-0.5 font-bold text-center text-slate-900 bg-white text-[11px]" />
              </td>
              <td class="py-1 px-1">
                <input type="text" value="${_escHtml(it.desc)}" placeholder="DIA DE ALOJAMIENTO HAB N..."
                       title="${_escHtml(it.desc)}"
                       onfocus="_setFocusItemSunat(${idx})"
                       oninput="onEditarItemSunatLive(${idx}, 'desc', this.value, false)"
                       class="it-desc w-full border border-slate-300 rounded px-1.5 py-0.5 text-slate-900 uppercase font-semibold bg-white text-[10.5px]" />
              </td>
              <td class="py-1 px-1">
                <input type="number" step="0.01" min="0" value="${it.precioUnit > 0 ? it.precioUnit : ""}" placeholder="0.00"
                       onfocus="_setFocusItemSunat(${idx})"
                       oninput="onEditarItemSunatLive(${idx}, 'precio', this.value, false)"
                       class="it-precio w-16 border border-slate-300 rounded px-1 py-0.5 font-black text-slate-900 bg-white text-right text-[11px]" />
              </td>
              <td class="py-1 px-1">
                <input type="text" readonly value="${vUnit}"
                       class="it-valor-unit w-14 border border-slate-200 rounded px-1 py-0.5 font-mono text-[9.5px] text-slate-500 bg-slate-100 text-right" />
              </td>
              <td class="py-1 px-1 text-right font-black text-slate-900 font-mono text-[10.5px] it-subtotal">
                S/ ${sub.toFixed(2)}
              </td>
              <td class="py-1 pl-0.5 text-center">
                <button type="button" onclick="eliminarFilaItemSunatIdx(${idx})"
                        title="Eliminar este ítem"
                        class="text-red-500 hover:text-red-700 font-black px-1 text-xs cursor-pointer">✕</button>
              </td>
            </tr>
          `;
        })
        .join("");
    } else {
      _itemsActuales.forEach((it) => {
        totalGeneral += Math.max(1, it.cant) * (Number(it.precioUnit) || 0);
      });
    }

    // 2. Tabla ampliada estilo SUNAT Preliminar (si el diálogo grande existe)
    const tbodyBig = document.getElementById("tbodyItemsSunatGrande");
    if (tbodyBig) {
      tbodyBig.innerHTML = _itemsActuales
        .map((it, idx) => {
          const vUnit = ((Number(it.precioUnit) || 0) / FACTOR_IGV_HOTEL).toFixed(4);
          const sub = Math.max(1, it.cant) * (Number(it.precioUnit) || 0);
          return `
            <tr data-idx="${idx}" class="border-b border-slate-200 hover:bg-slate-50/70 align-top">
              <td class="p-2 text-center w-20">
                <input type="number" min="1" value="${it.cant}"
                       onfocus="_setFocusItemSunat(${idx})"
                       oninput="onEditarItemSunatLive(${idx}, 'cant', this.value, true)"
                       class="it-cant w-16 border border-slate-300 rounded-md px-2 py-1 font-black text-center text-slate-900 bg-white text-xs" />
              </td>
              <td class="p-2 text-center w-24 font-mono text-[11px] font-bold text-slate-600 pt-3">
                UNIDAD
              </td>
              <td class="p-2">
                <textarea rows="2"
                          onfocus="_setFocusItemSunat(${idx})"
                          oninput="onEditarItemSunatLive(${idx}, 'desc', this.value, true)"
                          placeholder="DIA DE ALOJAMIENTO DEL ..., HAB N ..., IDENTIFICADO CON DNI ... Y NOMBRE ..."
                          class="it-desc w-full border border-slate-300 focus:border-indigo-600 rounded-md px-2.5 py-1.5 text-slate-900 uppercase font-bold bg-white text-xs leading-snug resize-y">${_escHtml(it.desc)}</textarea>
              </td>
              <td class="p-2 text-right w-32">
                <input type="number" step="0.01" min="0" value="${it.precioUnit > 0 ? it.precioUnit : ""}" placeholder="0.00"
                       onfocus="_setFocusItemSunat(${idx})"
                       oninput="onEditarItemSunatLive(${idx}, 'precio', this.value, true)"
                       class="it-precio w-28 border border-slate-300 rounded-md px-2 py-1 font-black text-slate-900 bg-white text-right text-xs" />
              </td>
              <td class="p-2 text-right w-32">
                <input type="text" readonly value="${vUnit}"
                       class="it-valor-unit w-28 border border-slate-200 rounded-md px-2 py-1 font-mono text-xs text-slate-600 bg-slate-100 text-right" />
              </td>
              <td class="p-2 text-right w-32 font-black text-slate-900 font-mono text-xs pt-3 it-subtotal">
                S/ ${sub.toFixed(2)}
              </td>
              <td class="p-2 text-center w-10 pt-2.5">
                <button type="button" onclick="eliminarFilaItemSunatIdx(${idx})"
                        title="Eliminar fila"
                        class="text-red-500 hover:text-red-700 font-black px-1.5 py-0.5 rounded hover:bg-red-50 text-sm cursor-pointer">✕</button>
              </td>
            </tr>
          `;
        })
        .join("");
    }

    _pintarTotalesGlobales(totalGeneral);
  }

  window._setFocusItemSunat = function (idx) {
    _focusedItemIdx = idx;
  };

  window.recalcularItemsFacturacionModal = function () {
    _sincronizarDesdeInputsActivos();
    _renderizarTablasItems();
  };

  window.recolectarItemsFacturacionModal = function () {
    _sincronizarDesdeInputsActivos();
    const out = [];
    _itemsActuales.forEach((it) => {
      const cant = Math.max(1, parseInt(it.cant, 10) || 1);
      const desc = String(it.desc || "").replace(/\s+/g, " ").trim().toUpperCase();
      const precioUnit = Number(it.precioUnit) || 0;
      const valorUnit = parseFloat((precioUnit / FACTOR_IGV_HOTEL).toFixed(4));
      if (desc && precioUnit > 0) {
        out.push({
          cantidad: cant,
          descripcion: desc,
          precio_unitario: precioUnit,
          valor_unitario: valorUnit,
        });
      }
    });
    return out;
  };

  // =========================================================================
  // 2. VISTA GRANDE ESTILO PRELIMINAR SUNAT (BOTÓN LUPA 🔍)
  // =========================================================================

  function _asegurarDialogoFormatoSunatGrande() {
    let dlg = document.getElementById("dialogFormatoSunatGrande");
    if (dlg) return dlg;
    dlg = document.createElement("dialog");
    dlg.id = "dialogFormatoSunatGrande";
    dlg.setAttribute("closedby", "any");
    dlg.setAttribute("aria-labelledby", "tituloDialogFormatoSunat");
    dlg.className = "rounded-2xl shadow-2xl border-2 border-slate-400 p-0 w-[96vw] max-w-5xl bg-white text-slate-900 m-auto";
    document.body.appendChild(dlg);
    _instalarLightDismissDialog(dlg);
    return dlg;
  }

  window.abrirVistaGrandeFormatoSunat = function (habNum) {
    _sincronizarDesdeInputsActivos();
    const habKey = String(habNum || _habActivaModal || "");
    const h = _obtenerHabObj(habKey);
    if (!h) return;
    const f = _obtenerFactEfectiva(h);

    const tipoSel = String(document.getElementById("modalFact_tipo")?.value || (f.esFactura ? "Factura" : "Boleta")).toUpperCase();
    const esFact = tipoSel === "FACTURA";
    const ruc = String(document.getElementById("modalFact_ruc")?.value || f.ruc || "").replace(/\D/g, "");
    const empresa = String(document.getElementById("modalFact_empresa")?.value || f.empresa || "").trim().toUpperCase();
    const dniModalRaw = String(document.getElementById("modalFact_dni")?.value || f.dni || h.dni || "")
      .split(/[\/,;]+/)[0]
      .replace(/\D/g, "");
    const dniNorm = dniModalRaw.length === 7 ? ("0" + dniModalRaw) : dniModalRaw;
    const receptorNombre = esFact ? (empresa || "PENDIENTE RAZÓN SOCIAL") : (h.huesped || "CLIENTES VARIOS");
    const receptorDoc = esFact ? (ruc || "SIN RUC") : (dniNorm || "-");
    const hoyStr = new Date().toLocaleDateString("es-PE", { day: "2-digit", month: "2-digit", year: "numeric" });

    const dlg = _asegurarDialogoFormatoSunatGrande();
    dlg.innerHTML = `
      <div class="p-4 sm:p-5 space-y-3.5 max-h-[92vh] overflow-y-auto">
        <!-- Barra superior de cierre -->
        <div class="flex items-center justify-between border-b border-slate-200 pb-2.5">
          <div class="flex items-center gap-2">
            <span class="px-2.5 py-1 rounded-md bg-indigo-900 text-white font-black text-xs uppercase tracking-wider">🔍 Vista Ampliada Formato SUNAT</span>
            <span class="text-xs font-bold text-slate-500">Habitación ${h.hab} • Edita cómodamente descripciones largas e ítems</span>
          </div>
          <form method="dialog">
            <button type="submit" class="px-3 py-1 rounded-lg bg-slate-100 hover:bg-slate-200 text-slate-700 font-black text-xs cursor-pointer">✕ Cerrar</button>
          </form>
        </div>

        <!-- Cabecera idéntica a Preliminar SUNAT SEE-SOL -->
        <div class="border-2 border-slate-400 rounded-xl p-3.5 bg-slate-50/60 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
          <div class="space-y-0.5">
            <div id="tituloDialogFormatoSunat" class="font-black text-sm text-slate-900 tracking-tight">EL OVALO MARIATEGUI</div>
            <div class="font-extrabold text-xs text-slate-700">MAELEN HOGAR EMPRESA INDIVIDUAL DE RESPONSABILIDAD LIMITADA</div>
            <div class="text-[11px] text-slate-500">AV. PAZ 507 INT. A URB. VILLA MILITAR MARISCAL NIETO - MOQUEGUA</div>
          </div>
          <div class="border-2 border-sky-700 bg-white rounded-lg px-5 py-2.5 text-center shrink-0 shadow-2xs">
            <div class="font-black text-xs sm:text-sm text-slate-900 uppercase tracking-wide">
              ${esFact ? "FACTURA ELECTRÓNICA" : "BOLETA DE VENTA ELECTRÓNICA"}
            </div>
            <div class="font-mono font-black text-xs sm:text-sm text-sky-900 mt-0.5">RUC: 20611986484</div>
            <div class="text-[10px] font-bold text-amber-700 uppercase mt-0.5">Pre-visualización Editable (IGV 10.5%)</div>
          </div>
        </div>

        <!-- Datos del Receptor estilo SUNAT -->
        <div class="border border-slate-300 rounded-xl p-3 bg-white grid grid-cols-1 sm:grid-cols-12 gap-2 text-xs">
          <div class="sm:col-span-7 flex flex-col gap-1">
            <div><span class="font-bold text-slate-500 inline-block w-28">Señor(es):</span> <strong class="font-black text-slate-900 uppercase">${_escHtml(receptorNombre)}</strong></div>
            <div><span class="font-bold text-slate-500 inline-block w-28">${esFact ? "RUC:" : "DNI:"}</span> <strong class="font-mono font-black text-slate-900">${_escHtml(receptorDoc)}</strong></div>
          </div>
          <div class="sm:col-span-5 flex flex-col gap-1">
            <div><span class="font-bold text-slate-500 inline-block w-32">Fecha de Emisión:</span> <strong class="font-mono font-bold text-slate-900">${hoyStr}</strong></div>
            <div><span class="font-bold text-slate-500 inline-block w-32">Tipo de Moneda:</span> <strong class="font-bold text-slate-900">SOLES (PEN)</strong></div>
          </div>
        </div>

        <!-- Barra de herramientas rápidas sobre los ítems -->
        <div class="flex flex-wrap items-center justify-between gap-2 bg-indigo-50/70 border border-indigo-200 rounded-xl px-3 py-2">
          <div class="text-[11px] font-bold text-indigo-950">
            💡 Haz clic en una fila y usa <strong>🪪 + Identidad Huésped</strong> para anexar el DNI y nombre del alojado en la Factura/Boleta.
          </div>
          <div class="flex flex-wrap items-center gap-2">
            <button type="button"
                    onclick="adicionarIdentidadHuespedItem('${h.hab}')"
                    class="px-3 py-1.5 rounded-lg bg-amber-600 hover:bg-amber-700 text-white font-black text-xs shadow-2xs cursor-pointer flex items-center gap-1.5 transition-colors">
              🪪 + Identidad Huésped (DNI y Nombre)
            </button>
            <button type="button"
                    onclick="agregarFilaItemSunat(1, 'DIA DE ALOJAMIENTO HAB N ${h.hab}', ${_tarifaRefModal})"
                    class="px-3 py-1.5 rounded-lg bg-blue-600 hover:bg-blue-700 text-white font-black text-xs shadow-2xs cursor-pointer flex items-center gap-1.5 transition-colors">
              ➕ Adicionar Hab/Día
            </button>
          </div>
        </div>

        <!-- Tabla ancha estilo SUNAT sin truncamiento -->
        <div class="border border-slate-300 rounded-xl overflow-hidden bg-white">
          <table class="tabla-sunat-grande w-full text-left border-collapse">
            <thead>
              <tr class="bg-slate-800 text-white text-[11px] font-extrabold uppercase">
                <th class="p-2.5 text-center w-20">Cantidad</th>
                <th class="p-2.5 text-center w-24">Unidad</th>
                <th class="p-2.5">Descripción Completa en Comprobante SUNAT</th>
                <th class="p-2.5 text-right w-32">P. Unit (Inc. IGV)</th>
                <th class="p-2.5 text-right w-32">Valor Unit.(*)</th>
                <th class="p-2.5 text-right w-32">Importe(**)</th>
                <th class="p-2.5 text-center w-10"></th>
              </tr>
            </thead>
            <tbody id="tbodyItemsSunatGrande"></tbody>
          </table>
        </div>

        <!-- Cuadro Oficial de 3 Totales SUNAT: Op. Gravada, IGV (10.5%) e Importe Total -->
        <div class="flex flex-col sm:flex-row items-stretch sm:items-end justify-between gap-3 pt-1">
          <div class="text-[11px] text-slate-500 space-y-0.5">
            <div>(*) Sin impuestos (Valor Unitario = Precio Unitario ÷ 1.105 a 4 decimales).</div>
            <div>(**) Incluye IGV Especial de Hotelería y Turismo 10.5% (Ley N° 31556).</div>
          </div>
          <div class="w-full sm:w-80 border-2 border-slate-300 rounded-xl p-3 bg-slate-50 space-y-1.5 font-mono text-xs">
            <div class="flex justify-between items-center text-slate-700">
              <span class="font-bold">Op. Gravada :</span>
              <strong id="lblBigOpGravada" class="font-black text-slate-900 text-sm">S/ 0.00</strong>
            </div>
            <div class="flex justify-between items-center text-slate-700">
              <span class="font-bold">IGV (10.5%) :</span>
              <strong id="lblBigIgv105" class="font-black text-slate-900 text-sm">S/ 0.00</strong>
            </div>
            <div class="flex justify-between items-center border-t-2 border-slate-300 pt-1.5 text-slate-900">
              <span class="font-black uppercase">Importe Total :</span>
              <strong id="lblBigImporteTotal" class="font-black text-emerald-700 text-base">S/ 0.00</strong>
            </div>
          </div>
        </div>

        <!-- Botonera inferior directa -->
        <div class="flex flex-wrap items-center justify-between gap-2 pt-2 border-t border-slate-200">
          <button type="button"
                  onclick="ejecutarOrdenSunatModal('${h.hab}', 'BORRADOR')"
                  class="px-3.5 py-2 rounded-xl bg-amber-100 hover:bg-amber-200 text-amber-950 border border-amber-400 font-black text-xs cursor-pointer flex items-center gap-1.5 transition-colors">
            🧪 Ver Captura Oficial SUNAT (Borrador sin emitir)
          </button>
          <div class="flex flex-wrap items-center gap-2">
            <button type="button"
                    onclick="ejecutarOrdenSunatModal('${h.hab}', 'EMITIR')"
                    class="px-4 py-2 rounded-xl bg-indigo-700 hover:bg-indigo-800 text-white font-black text-xs cursor-pointer flex items-center gap-1.5 shadow-xs transition-colors">
              ⚡ Emitir en SUNAT
            </button>
            <button type="button"
                    onclick="ejecutarOrdenSunatModal('${h.hab}', 'EMITIR_Y_WSP')"
                    class="px-4 py-2 rounded-xl bg-emerald-600 hover:bg-emerald-700 text-white font-black text-xs cursor-pointer flex items-center gap-1.5 shadow-xs transition-colors">
              ⚡📲 Emitir y Enviar WSP
            </button>
            <form method="dialog" class="inline">
              <button type="submit" class="px-4 py-2 rounded-xl bg-slate-800 hover:bg-slate-900 text-white font-black text-xs cursor-pointer">
                ✅ Listo (Volver)
              </button>
            </form>
          </div>
        </div>
      </div>
    `;

    _renderizarTablasItems();
    dlg.showModal();
  };

  // =========================================================================
  // 3. VISOR DE CAPTURA PRELIMINAR OFICIAL DE SUNAT (BORRADOR / NC)
  // =========================================================================

  window.mostrarCapturaBorradorSunat = function (nombreCaptura, titulo, habNum) {
    if (!nombreCaptura) return;
    let dlg = document.getElementById("dialogCapturaSunat");
    if (!dlg) {
      dlg = document.createElement("dialog");
      dlg.id = "dialogCapturaSunat";
      dlg.setAttribute("closedby", "any");
      dlg.setAttribute("aria-label", "Captura Vista Preliminar SUNAT");
      dlg.className = "rounded-2xl shadow-2xl border-2 border-amber-400 p-0 w-[96vw] max-w-5xl bg-white text-slate-900 m-auto";
      document.body.appendChild(dlg);
      _instalarLightDismissDialog(dlg);
    }
    const urlImg = `/api/sunat/captura/${encodeURIComponent(nombreCaptura)}?t=${Date.now()}`;
    dlg.innerHTML = `
      <div class="p-4 space-y-3 max-h-[92vh] flex flex-col">
        <div class="flex items-center justify-between border-b border-amber-200 pb-2 shrink-0">
          <div>
            <span class="px-2 py-0.5 rounded bg-amber-200 text-amber-950 font-black text-xs uppercase">🧪 Captura Oficial Portal SUNAT (Sin Emitir)</span>
            <span class="ml-2 text-xs font-bold text-slate-700">${_escHtml(titulo || `Habitación ${habNum || ""}`)}</span>
          </div>
          <div class="flex items-center gap-2">
            <a href="${urlImg}" target="_blank" class="px-2.5 py-1 rounded-lg bg-indigo-50 hover:bg-indigo-100 text-indigo-800 border border-indigo-200 font-bold text-xs">🔍 Abrir tamaño real</a>
            <form method="dialog" class="inline">
              <button type="submit" class="px-3 py-1 rounded-lg bg-slate-800 text-white font-black text-xs cursor-pointer">✕ Cerrar</button>
            </form>
          </div>
        </div>
        <div class="overflow-auto flex-1 border border-slate-300 rounded-xl bg-slate-100 p-2">
          <img src="${urlImg}" alt="Vista Preliminar SUNAT" class="w-full h-auto rounded shadow-xs" />
        </div>
      </div>
    `;
    if (!dlg.open) dlg.showModal();
  };

  // =========================================================================
  // 4. NOTA DE CRÉDITO ELECTRÓNICA (PARA BOLETAS / FACTURAS EMITIDAS)
  // =========================================================================

  window.togglePanelNotaCreditoModal = function (habNum) {
    const box = document.getElementById("boxNotaCreditoModal");
    if (!box) return;
    box.classList.toggle("hidden");
    if (!box.classList.contains("hidden")) {
      const inpNro = document.getElementById("nc_nroOrigen");
      if (inpNro && !inpNro.value.trim()) {
        const nroModal = document.getElementById("modalFact_nro")?.value || "";
        inpNro.value = nroModal.trim();
      }
      inpNro?.focus();
    }
  };

  window.ejecutarNotaCreditoModal = async function (habNum, modoBorrador) {
    const h = _obtenerHabObj(habNum);
    if (!h) return;
    const f = _obtenerFactEfectiva(h);

    const nroOrigen = String(
      document.getElementById("nc_nroOrigen")?.value ||
      document.getElementById("modalFact_nro")?.value ||
      f.comprobantesStr ||
      ""
    ).trim().toUpperCase();

    const motivo = String(
      document.getElementById("nc_motivo")?.value || "ANULACION DE LA OPERACION"
    ).trim().toUpperCase();

    if (!/(\d+)$/.test(nroOrigen)) {
      alert("⚠️ Ingrese el número del comprobante emitido que desea anular (ejemplo: EB01-2069 o E001-355).");
      document.getElementById("nc_nroOrigen")?.focus();
      return;
    }

    if (!modoBorrador) {
      const ok = confirm(
        `⚠️ CONFIRMAR EMISIÓN DE NOTA DE CRÉDITO EN SUNAT\n\n` +
        `• Habitación: ${h.hab}\n` +
        `• Comprobante a anular: ${nroOrigen}\n` +
        `• Motivo: ${motivo}\n\n` +
        `Al terminar en SUNAT, la habitación volverá a estado PENDIENTE para que puedas emitir el nuevo comprobante.\n¿Proceder?`
      );
      if (!ok) return;
    }

    const tipoSel = nroOrigen.startsWith("E001") || nroOrigen.startsWith("F")
      ? "FACTURA"
      : (f.esFactura ? "FACTURA" : "BOLETA");

    await _enviarOrdenAlServidor({
      accion: "NOTA_CREDITO",
      hab: h.hab,
      estanciaUuid: h.estanciaUuid || f.estanciaUuid || "",
      id_estancia: f.filaNum || h.filaNum || 0,
      fila_calzado: f.filaCalzado || null,
      tipo: tipoSel,
      nro_comprobante: nroOrigen,
      motivo: motivo,
      cliente: f.empresa || h.huesped || "",
      modoBorrador: Boolean(modoBorrador),
    });
  };

  // =========================================================================
  // 5. DESPACHO ASÍNCRONO (NO BLOQUEA EL PANEL) Y RASTREADOR FLOTANTE VIVO
  // =========================================================================

  window.ejecutarOrdenSunatModal = async function (habNum, accion, forzarAuditoria = false) {
    if (accion === "SOLO_WSP") {
      return window.reenviarComprobanteWspModal(habNum);
    }
    const modoBorrador = accion === "BORRADOR";
    const accionReal = modoBorrador ? "EMITIR" : accion;
    return window.ejecutarEmisionDesdeApp(accionReal, habNum, forzarAuditoria, modoBorrador);
  };

  window.ejecutarEmisionDesdeApp = async function (accion, habNum, forzarAuditoria = false, modoBorrador = false) {
    const h = _obtenerHabObj(habNum);
    if (!h) return;
    const f = _obtenerFactEfectiva(h);

    const tipoSel = String(document.getElementById("modalFact_tipo")?.value || (f.esFactura ? "Factura" : "Boleta")).toUpperCase();
    const esFact = tipoSel === "FACTURA";
    const ruc = String(document.getElementById("modalFact_ruc")?.value || f.ruc || "").replace(/\D/g, "");
    const empresa = String(document.getElementById("modalFact_empresa")?.value || f.empresa || "").trim().toUpperCase();
    const celular = String(document.getElementById("modalFact_celular")?.value || f.celular || h.celular || "").replace(/\D/g, "");
    const inpDniEl = document.getElementById("modalFact_dni");
    const rawDniModal = inpDniEl ? inpDniEl.value : String(f.dni || h.dni || "");
    const items = window.recolectarItemsFacturacionModal();

    if (esFact) {
      if (ruc.length !== 11 || !/^(10|15|17|20)/.test(ruc)) {
        alert("⚠️ Para emitir FACTURA se requiere un RUC válido de 11 dígitos (10 o 20).");
        document.getElementById("modalFact_ruc")?.focus();
        return;
      }
      if (empresa.length < 3) {
        alert("⚠️ Ingrese la Razón Social de la empresa para la Factura.");
        document.getElementById("modalFact_empresa")?.focus();
        return;
      }
    }

    const exigirWsp = Boolean(accion === "EMITIR_Y_WSP" && !modoBorrador);
    if (exigirWsp && !celular) {
      alert("⚠️ Ingrese el número de Celular / WhatsApp del cliente para 'Emitir y Enviar WSP'.");
      document.getElementById("modalFact_celular")?.focus();
      return;
    }

    const chkDni = window.validarDniConCelularModal(rawDniModal, celular, esFact, ruc, exigirWsp);
    if (!chkDni.ok) {
      alert(chkDni.error);
      inpDniEl?.focus();
      return;
    }

    if (items.length === 0) {
      alert("⚠️ Verifique que todos los ítems tengan descripción y Precio Unitario mayor a S/ 0.00.");
      return;
    }

    if (chkDni.dniNormalizado) {
      h.dni = chkDni.dniNormalizado;
      if (h.facturacion) h.facturacion.dni = chkDni.dniNormalizado;
      if (inpDniEl && inpDniEl.value !== chkDni.dniNormalizado) {
        inpDniEl.value = chkDni.dniNormalizado;
        _refrescarEstadoVisualDniModal(false);
      }
    }

    await _enviarOrdenAlServidor(
      {
        accion: accion,
        hab: h.hab,
        estanciaUuid: h.estanciaUuid || f.estanciaUuid || "",
        id_estancia: f.filaNum || h.filaNum || 0,
        fila_calzado: f.filaCalzado || null,
        tipo: esFact ? "FACTURA" : "BOLETA",
        documento: esFact ? ruc : chkDni.dniNormalizado,
        dni_huesped: chkDni.dniNormalizado,
        cliente: esFact ? empresa : (h.huesped || "CLIENTES VARIOS"),
        telefono: celular,
        items: items,
        forzar: Boolean(forzarAuditoria),
        modoBorrador: Boolean(modoBorrador),
      },
      { accionOriginal: modoBorrador ? "BORRADOR" : accion, habNum: h.hab }
    );
  };

  window.reenviarComprobanteWspModal = async function (habNum) {
    const h = _obtenerHabObj(habNum);
    if (!h) return;
    const f = _obtenerFactEfectiva(h);
    const tipoSel = String(document.getElementById("modalFact_tipo")?.value || (f.esFactura ? "Factura" : "Boleta")).toUpperCase();
    const esFact = tipoSel === "FACTURA";
    const ruc = String(document.getElementById("modalFact_ruc")?.value || f.ruc || "").replace(/\D/g, "");
    const celular = String(document.getElementById("modalFact_celular")?.value || f.celular || h.celular || "").replace(/\D/g, "");
    const nroCp = String(document.getElementById("modalFact_nro")?.value || f.comprobantesStr || "").trim();
    const inpDniEl = document.getElementById("modalFact_dni");
    const rawDniModal = inpDniEl ? inpDniEl.value : String(f.dni || h.dni || "");

    if (!celular) {
      alert("⚠️ Ingrese el número de WhatsApp del cliente para reenviar el comprobante.");
      document.getElementById("modalFact_celular")?.focus();
      return;
    }

    const chkDni = window.validarDniConCelularModal(rawDniModal, celular, esFact, ruc, true);
    if (!chkDni.ok) {
      alert(chkDni.error);
      inpDniEl?.focus();
      return;
    }

    await _enviarOrdenAlServidor({
      accion: "SOLO_WSP",
      hab: h.hab,
      tipo: esFact ? "FACTURA" : "BOLETA",
      telefono: celular,
      nro_comprobante: nroCp,
      documento: esFact ? (ruc || chkDni.dniNormalizado) : chkDni.dniNormalizado,
      dni_huesped: chkDni.dniNormalizado,
      cliente: (esFact ? f.empresa : "") || h.huesped || "",
    });
  };

  async function _enviarOrdenAlServidor(payload, metaRetry) {
    try {
      const resp = await fetch("/api/sunat/orden", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const res = await resp.json();

      if (res.status === "CONFIRM_REQUIRED" && metaRetry) {
        const ok = confirm(
          `⚠️ ALERTA DE AUDITORÍA PREVENTIVA\n\n${res.mensajeAuditoria}\n\n¿Confirmas que el monto S/ ${Number(res.monto).toFixed(2)} es correcto y deseas continuar?`
        );
        if (ok) {
          payload.forzar = true;
          return _enviarOrdenAlServidor(payload, null);
        }
        return;
      }

      if (res.status === "ERROR") {
        alert(`❌ No se pudo iniciar la orden:\n${res.error || res.mensaje || "Error desconocido"}`);
        return;
      }

      if (res.status === "PROCESSING" && res.job_id) {
        // Registrar señal viva en la habitación (mantiene estado Pendiente hasta que termine todo)
        if (res.hab) {
          window.sunatJobsPorHab[String(res.hab)] = {
            job_id: res.job_id,
            hab: res.hab,
            accion: res.accion,
            tipo: res.tipo,
            modoBorrador: res.modoBorrador,
            state: "RUNNING",
            etapa: res.etapa,
          };
          if (typeof window.renderTabla === "function") {
            window.renderTabla();
          }
        }
        // Cerrar inmediatamente el modal para que Recepción siga operando sin esperar
        _cerrarModalDetallePanel();
        _iniciarPollingJobsSunat();
        _actualizarTrackerFlotante([window.sunatJobsPorHab[String(res.hab)]].filter(Boolean));
      }
    } catch (err) {
      alert(`❌ Error de red con el servidor local: ${err.message}`);
    }
  }

  function _asegurarContenedorFlotante() {
    let box = document.getElementById("sunatFloatingTracker");
    if (box) return box;
    box = document.createElement("div");
    box.id = "sunatFloatingTracker";
    box.className = "fixed bottom-3 right-3 z-50 flex flex-col gap-2 max-w-sm w-[350px] pointer-events-none";
    document.body.appendChild(box);
    return box;
  }

  window.descartarAvisoJobSunat = function (jobId) {
    _notifiedDoneJobs.add(jobId + "_dismissed");
    const card = document.getElementById(`sunatJobCard_${jobId}`);
    if (card) card.remove();
  };

  function _actualizarTrackerFlotante(jobs) {
    const box = _asegurarContenedorFlotante();
    const ahora = Date.now() / 1000;
    const visibles = (jobs || []).filter((j) => {
      if (!j || _notifiedDoneJobs.has(j.job_id + "_dismissed")) return false;
      if (j.state === "QUEUED" || j.state === "RUNNING") return true;
      const fin = Number(j.finished_ts || ahora);
      // Mostrar tarjetas finalizadas por 14s (o 45s si es borrador o error)
      const ttl = (j.state === "ERROR" || (j.result && j.result.modo === "borrador")) ? 45 : 14;
      return (ahora - fin) <= ttl;
    });

    if (visibles.length === 0) {
      box.innerHTML = "";
      return;
    }

    box.innerHTML = visibles
      .map((j) => {
        const activo = j.state === "QUEUED" || j.state === "RUNNING";
        const esErr = j.state === "ERROR";
        const res = j.result || {};
        const esBorradorListo = !activo && !esErr && res.modo === "borrador" && res.nombre_captura;
        const wspWarn = !activo && !esErr && res.wsp_status === "ERROR_WSP";

        let bg = "bg-indigo-950 text-white border-indigo-500";
        if (esErr) bg = "bg-red-950 text-white border-red-500";
        else if (wspWarn) bg = "bg-amber-950 text-white border-amber-400";
        else if (!activo) bg = "bg-emerald-950 text-white border-emerald-400";

        return `
          <div id="sunatJobCard_${j.job_id}" class="pointer-events-auto rounded-xl border-2 px-3.5 py-2.5 shadow-2xl ${bg} text-xs space-y-1.5 transition-all">
            <div class="flex items-start justify-between gap-2">
              <div class="font-black flex items-center gap-1.5">
                <span>${activo ? "⏳⚙️" : (esErr ? "❌" : (wspWarn ? "⚠️" : "✅"))}</span>
                <span>Hab. ${_escHtml(j.hab || "-")} • ${_escHtml(j.accion === "NOTA_CREDITO" ? "Nota de Crédito" : (j.tipo || "SUNAT"))}</span>
              </div>
              ${!activo ? `<button type="button" onclick="descartarAvisoJobSunat('${j.job_id}')" class="opacity-75 hover:opacity-100 font-black px-1 cursor-pointer">✕</button>` : ""}
            </div>
            <div class="text-[11px] font-medium leading-snug opacity-95">
              ${_escHtml(j.etapa || "")}
            </div>
            ${esBorradorListo ? `
              <button type="button"
                      onclick="mostrarCapturaBorradorSunat('${_escHtml(res.nombre_captura)}', 'Hab. ${_escHtml(j.hab)} (${_escHtml(j.tipo)})', '${_escHtml(j.hab)}')"
                      class="w-full mt-1 py-1.5 px-2.5 rounded-lg bg-amber-400 hover:bg-amber-300 text-slate-950 font-black text-[11px] cursor-pointer">
                🖼️ Ver Captura Preliminar SUNAT
              </button>
            ` : ""}
          </div>
        `;
      })
      .join("");
  }

  async function _tickPollingJobsSunat() {
    try {
      const resp = await fetch("/api/sunat/jobs", { cache: "no-store" });
      if (!resp.ok) return;
      const data = await resp.json();
      const jobs = data.jobs || [];

      const nuevoMapaHab = {};
      let algunoActivo = false;
      let requiereRecargaPanel = false;

      jobs.forEach((j) => {
        if (j.state === "QUEUED" || j.state === "RUNNING") {
          algunoActivo = true;
          if (j.hab) nuevoMapaHab[String(j.hab)] = j;
        } else if (!_notifiedDoneJobs.has(j.job_id)) {
          _notifiedDoneJobs.add(j.job_id);
          const res = j.result || {};
          if (j.state === "DONE") {
            if (res.modo === "borrador" && res.nombre_captura) {
              // Abrir automáticamente el visor de la captura preliminar cuando llega de SUNAT
              window.mostrarCapturaBorradorSunat(
                res.nombre_captura,
                `Vista Preliminar ${j.accion === "NOTA_CREDITO" ? "Nota de Crédito" : j.tipo} • Hab. ${j.hab}`,
                j.hab
              );
            } else {
              // Emisión oficial o Nota de Crédito terminada -> refrescar Panel para pasar a ✅ Emitido (o ⏳ Pendiente en NC)
              requiereRecargaPanel = true;
            }
          } else {
            requiereRecargaPanel = true;
          }
        }
      });

      const cambioMapa = JSON.stringify(Object.keys(window.sunatJobsPorHab)) !== JSON.stringify(Object.keys(nuevoMapaHab));
      window.sunatJobsPorHab = nuevoMapaHab;

      if (requiereRecargaPanel && typeof window.recargarDatos === "function") {
        window.recargarDatos();
      } else if (cambioMapa && typeof window.renderTabla === "function") {
        window.renderTabla();
      }

      _actualizarTrackerFlotante(jobs);

      if (!algunoActivo && jobs.every((j) => (Date.now() / 1000 - Number(j.finished_ts || 0)) > 46)) {
        clearInterval(_pollTimer);
        _pollTimer = null;
      }
    } catch (e) {
      // Silencioso ante microcortes locales
    }
  }

  function _iniciarPollingJobsSunat() {
    if (_pollTimer) return;
    _pollTimer = setInterval(_tickPollingJobsSunat, 1800);
    setTimeout(_tickPollingJobsSunat, 400);
  }

  // Al cargar la página, verificar una vez si había una emisión en curso (ej. tras F5)
  document.addEventListener("DOMContentLoaded", () => {
    setTimeout(async () => {
      try {
        const r = await fetch("/api/sunat/jobs", { cache: "no-store" });
        if (!r.ok) return;
        const d = await r.json();
        if (d.jobs && d.jobs.some((j) => j.state === "QUEUED" || j.state === "RUNNING")) {
          _iniciarPollingJobsSunat();
        }
      } catch (e) {}
    }, 800);
  });
})();
