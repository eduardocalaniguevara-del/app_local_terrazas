/**
 * Puente RPC Local en 0 ms (Sustituye google.script.run por FastAPI + SQLite Local)
 * Incluye monitor en tiempo real de Estado de Red, Cola de Sincronización y Auto-Recarga UI (<30s).
 */
(function () {
  function createGoogleScriptRunner(successHandler, failureHandler, userObject) {
    return new Proxy(
      {},
      {
        get(_target, prop) {
          if (prop === "withSuccessHandler") {
            return function (fn) {
              return createGoogleScriptRunner(fn, failureHandler, userObject);
            };
          }
          if (prop === "withFailureHandler") {
            return function (fn) {
              return createGoogleScriptRunner(successHandler, fn, userObject);
            };
          }
          if (prop === "withUserObject") {
            return function (obj) {
              return createGoogleScriptRunner(successHandler, failureHandler, obj);
            };
          }
          return function (...args) {
            fetch(`/api/rpc/${String(prop)}`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ args: args }),
            })
              .then(async (resp) => {
                const data = await resp.json().catch(() => ({}));
                if (!resp.ok || data.rpcError) {
                  throw new Error(data.error || data.detail || `Error HTTP ${resp.status}`);
                }
                if (data.syncStatus && typeof window.actualizarBadgeEstadoRed === "function") {
                  window.actualizarBadgeEstadoRed(data.syncStatus);
                }
                if (typeof successHandler === "function") {
                  successHandler(data.result, userObject);
                }
              })
              .catch((err) => {
                console.error(`[RPC Local Error - ${String(prop)}]:`, err);
                if (typeof failureHandler === "function") {
                  failureHandler(err, userObject);
                }
              });
          };
        },
      }
    );
  }

  window.google = window.google || {};
  window.google.script = {
    get run() {
      return createGoogleScriptRunner(null, null, null);
    },
    host: {
      close: function () {
        try {
          if (window.parent && window.parent !== window && typeof window.parent.cerrarFormularioDrawer === "function") {
            window.parent.cerrarFormularioDrawer();
          }
        } catch (e) {}
      },
    },
  };

  function getBadgeEl() {
    return document.getElementById("badgeEstadoSync") || document.getElementById("badgeSync");
  }

  function hayFormularioAbierto() {
    const drawer = document.getElementById("appDrawerOverlay") || document.getElementById("drawerBackdrop");
    if (drawer && !drawer.classList.contains("hidden")) return true;
    const modEst = document.getElementById("modalModificarEstancia");
    if (modEst && !modEst.classList.contains("hidden")) return true;
    return false;
  }

  window.actualizarBadgeEstadoRed = function (syncStatus) {
    if (syncStatus && syncStatus.nombreHotel && window === window.top) {
      document.title = "Panel de Disponibilidad — " + syncStatus.nombreHotel;
    }
    const badge = getBadgeEl();
    if (!badge || !syncStatus) return;

    const browserOnline = navigator.onLine !== false;
    const isOnline = Boolean(syncStatus.online && browserOnline);
    const pending = Number(syncStatus.pendingCount || 0);
    const lastSync = syncStatus.lastSync || "";
    const minOffline = Number(syncStatus.minutosOffline || 0);
    const minSync = Number(syncStatus.minutosDesdeSync !== undefined ? syncStatus.minutosDesdeSync : -1);

    badge.style.cursor = "pointer";
    badge.onclick = function () {
      window.forzarSincronizacionManual();
    };

    if (!isOnline) {
      badge.className =
        "text-[11px] font-black text-red-950 bg-red-200 border-2 border-red-500 px-2.5 py-0.5 rounded-md shadow-xs transition-all hover:bg-red-300";

      let textoOff = "🔴 Offline";
      if (minOffline > 0) {
        textoOff += ` hace ${minOffline} min`;
      }
      if (lastSync) {
        textoOff += ` (últ. sync: ${lastSync})`;
      }
      if (pending > 0) {
        textoOff += ` [${pending} pend.]`;
      }
      badge.textContent = textoOff;

      let tooltip = `⚠️ MODO OFFLINE: Sin conexión a Google Sheets.`;
      if (minOffline > 0) tooltip += `\n• Desconectado desde hace: ${minOffline} minuto(s).`;
      if (lastSync) tooltip += `\n• Última sincronización con Sheets: ${lastSync}.`;
      if (pending > 0) tooltip += `\n• Cambios pendientes de subir a Sheets: ${pending}.`;
      tooltip += `\n• Los datos están 100% seguros en SQLite local (0 ms).\n👉 Clic para forzar reintento de conexión.`;
      badge.title = tooltip;
    } else if (pending > 0) {
      badge.className =
        "text-[11px] font-black text-blue-950 bg-blue-100 border border-blue-400 px-2.5 py-0.5 rounded-md shadow-2xs transition-all hover:bg-blue-200";
      badge.textContent = `🔄 Subiendo a Sheets (${pending})...`;
      badge.title = `Subiendo ${pending} operación(es) a Google Sheets en segundo plano.\nÚltima sinc.: ${lastSync || "reciente"}.\nClic para forzar.`;
    } else {
      badge.className =
        "text-[11px] font-extrabold text-emerald-900 bg-emerald-100 px-2.5 py-0.5 rounded-md border border-emerald-300 transition-all hover:bg-emerald-200";

      let textoOn = "🟢 En vivo";
      if (minSync === 0) {
        textoOn += ` (hace instantes · ${lastSync})`;
      } else if (minSync > 0 && minSync < 60) {
        textoOn += ` (hace ${minSync}m · ${lastSync})`;
      } else if (lastSync) {
        textoOn += ` (${lastSync})`;
      }
      badge.textContent = textoOn;
      badge.title = `Conectado y sincronizado con Google Sheets.\n• Última sincronización exitosa: ${lastSync || "hace instantes"}.\n👉 Clic para forzar sincronización ahora.`;
    }
  };

  window.forzarSincronizacionManual = function () {
    const badge = getBadgeEl();
    if (badge) {
      badge.textContent = "🔄 Sincronizando con Sheets...";
    }
    fetch("/api/sync/trigger", { method: "POST" })
      .then((r) => r.json())
      .then((data) => {
        if (data && data.syncStatus) {
          window.actualizarBadgeEstadoRed(data.syncStatus);
        }
        if (typeof window.recargarDatos === "function") {
          window.recargarDatos();
        }
      })
      .catch(() => {
        window.actualizarBadgeEstadoRed({ online: false, pendingCount: 0 });
      });
  };

  function chequearEstadoYVersion() {
    if (window !== window.top) return;
    fetch("/api/sync/status?_t=" + Date.now(), { cache: "no-store" })
      .then((r) => r.json())
      .then((st) => {
        if (st && st.uiVersion) {
          if (!window._uiVersionLoaded) {
            window._uiVersionLoaded = st.uiVersion;
          } else if (window._uiVersionLoaded !== st.uiVersion) {
            if (!hayFormularioAbierto()) {
              window.location.reload();
              return;
            }
          }
        }
        window.actualizarBadgeEstadoRed(st);
        if (st && st.totalCaja !== undefined) {
          const lblCaja = document.getElementById("lblCaja") || document.getElementById("lblTotalCaja");
          if (lblCaja) lblCaja.textContent = st.totalCaja;
        }
      })
      .catch(() => {
        window.actualizarBadgeEstadoRed({ online: false, pendingCount: 0 });
      });
  }

  // Fijar versión inicial apenas carga y monitorear cambios cada 5 segundos
  setTimeout(chequearEstadoYVersion, 1200);
  setInterval(chequearEstadoYVersion, 5000);
})();
