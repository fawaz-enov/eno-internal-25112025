/** @odoo-module **/
import { patch } from "@web/core/utils/patch";
import { onMounted, onWillUnmount } from "@odoo/owl";
import { ReceiptScreen } from "@point_of_sale/app/screens/receipt_screen/receipt_screen";

const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const isInvoiced = (o) =>
  o && (typeof o.isToInvoice === "function" ? !!o.isToInvoice() : !!o.to_invoice);

// Small helper: call UI methods only if provided
const safeUI = {
  block:   (ui) => { try { ui && ui.block && ui.block(); } catch(_) {} },
  unblock: (ui) => { try { ui && ui.unblock && ui.unblock(); } catch(_) {} },
  notify:  (ui, opts) => { try { ui && ui.showNotification && ui.showNotification(opts); } catch(_) {} },
};

// Core: open FRCS pdf (component-agnostic)
async function openFrcsPdf(ormService, uiServiceOrNull, order) {
  console.log("[frcs] openFrcsPdf() called");
  if (!isInvoiced(order)) {
    console.warn("[frcs] not invoiced → abort");
    safeUI.notify(uiServiceOrNull, {
      title: "Not invoiced",
      message: "Enable Invoice on this order.",
      type: "warning",
    });
    return false;
  }

  // resolve key
  const idOf = (o) => o && (o.backendId || o.server_id || o.orderId || null);
  let key = idOf(order);
  const t0 = Date.now();
  while (!key && Date.now() - t0 < 500) { await wait(100); key = idOf(order); }
  key ||= order?.name;
  if (!key) {
    console.warn("[frcs] no identifier yet → abort");
    safeUI.notify(uiServiceOrNull, {
      title: "Not Ready",
      message: "No identifier for this order yet.",
      type: "warning",
    });
    return false;
  }

  safeUI.block(uiServiceOrNull);
  try {
    const tStart = performance.now();
    const url = await ormService.call("pos.order", "pos_get_frcs_invoice_pdf", [key], {});
    const ms = (performance.now() - tStart).toFixed(1);
    console.log(`[frcs] RPC pos.order.pos_get_frcs_invoice_pdf: ${ms} ms`);
    console.log("[frcs] RPC url:", url);

    if (!url) {
      console.warn("[frcs] RPC returned no URL");
      safeUI.notify(uiServiceOrNull, { title: "No PDF", message: "Invoice not available.", type: "warning" });
      return false;
    }

    console.log("[frcs] window.open:", url);
    window.open(url, "_blank");
    return true;
  } catch (e) {
    console.error("[frcs] RPC error:", e);
    safeUI.notify(uiServiceOrNull, { title: "Error", message: e.message || "FRCS PDF failed.", type: "danger" });
    return false;
  } finally {
    safeUI.unblock(uiServiceOrNull);
  }
}

// Match your button
function looksLikePrintFullReceipt(btn) {
  const label = ((btn.innerText || btn.textContent || "")).trim().replace(/\s+/g, " ").toLowerCase();
  return /\bprint\s+full\s+receipt\b/i.test(label) || (btn.classList.contains("print") && /\breceipt\b/i.test(label));
}

patch(ReceiptScreen.prototype, {
  setup() {
    super.setup(...arguments);

    // IMPORTANT: use raw services from env, not useService() proxies
    this.ormSvc = this.env.services.orm;
    this.uiSvc  = this.env.services.ui;

    // prevent auto-print behaviors
    try {
      this.env.pos.config.iface_print_auto = false;
      this.env.pos.config.iface_skip_receipt_screen = false;
      console.log("[frcs] forced iface_print_auto=false, iface_skip_receipt_screen=false");
    } catch(_) {}

    console.log("[frcs] ReceiptScreen.setup() patch ACTIVE");
    this.__alive = true;
    onWillUnmount(() => { this.__alive = false; });

    // Console helpers
    window.__frcs = () => openFrcsPdf(this.ormSvc, this.__alive ? this.uiSvc : null, this.currentOrder);

    // Scoped click interceptor
    this.__clickHandler = (ev) => {
      const root = this.el;
      const btn  = ev.target?.closest?.("button, a");
      if (!root || !btn || !root.contains(btn)) return;

      const label = ((btn.innerText || btn.textContent || "")).trim().replace(/\s+/g, " ");
      console.log("[frcs] Receipt click:", label, "| classes:", btn.className);

      if (isInvoiced(this.currentOrder) && looksLikePrintFullReceipt(btn)) {
        console.log("[frcs] → intercept Receipt click: Print Full Receipt");
        ev.preventDefault();
        ev.stopPropagation();
        // pass ui only if still alive
        openFrcsPdf(this.ormSvc, this.__alive ? this.uiSvc : null, this.currentOrder);
      }
    };

    onMounted(() => {
      console.log("[frcs] ReceiptScreen onMounted → adding receipt click handler");
      this.el?.addEventListener("click", this.__clickHandler, true);
    });

    onWillUnmount(() => {
      console.log("[frcs] ReceiptScreen onWillUnmount → removing receipt click handler");
      this.el?.removeEventListener("click", this.__clickHandler, true);
    });

    // Global fallback (active only while screen lives)
    this.__globalClick = (ev) => {
      if (!this.__alive) return;
      const btn = ev.target?.closest?.("button, a");
      if (!btn) return;
      const label = ((btn.innerText || btn.textContent || "")).trim().replace(/\s+/g, " ");
      if (looksLikePrintFullReceipt(btn)) {
        console.log("[frcs] GLOBAL click matched:", label, "| classes:", btn.className);
        openFrcsPdf(this.ormSvc, this.__alive ? this.uiSvc : null, this.currentOrder);
        ev.preventDefault();
        ev.stopPropagation();
      }
    };
    document.addEventListener("click", this.__globalClick, true);
    onWillUnmount(() => document.removeEventListener("click", this.__globalClick, true));
  },

  async downloadInvoice(...args) {
    console.log("[frcs] downloadInvoice() override called");
    if (isInvoiced(this.currentOrder)) {
      await openFrcsPdf(this.ormSvc, this.__alive ? this.uiSvc : null, this.currentOrder);
      return;
    }
    return super.downloadInvoice ? super.downloadInvoice(...args) : undefined;
  },

  async printReceipt(...args) {
    console.log("[frcs] printReceipt() override called");
    if (isInvoiced(this.currentOrder)) {
      await openFrcsPdf(this.ormSvc, this.__alive ? this.uiSvc : null, this.currentOrder);
      return;
    }
    return super.printReceipt ? super.printReceipt(...args) : undefined;
  },
});
