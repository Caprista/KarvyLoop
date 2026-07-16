var KarvyModalBundle = (function(exports) {
  "use strict";
  function dom() {
    return window.KarvyDom;
  }
  let _setupLocked = false;
  let _backdropClose = true;
  let _escClose = false;
  function openMgmtModal(title, opts) {
    var _a;
    _backdropClose = !opts || opts.backdropClose !== false;
    _escClose = !!(opts && opts.escClose);
    const ttl = document.getElementById("mgmt-title");
    if (ttl) ttl.textContent = title;
    (_a = document.getElementById("mgmt-modal")) == null ? void 0 : _a.classList.remove("hidden");
  }
  function closeMgmtModal() {
    var _a;
    if (_setupLocked) return;
    (_a = document.getElementById("mgmt-modal")) == null ? void 0 : _a.classList.add("hidden");
  }
  function mgmtBody() {
    return document.getElementById("mgmt-body");
  }
  function setSetupLocked(locked) {
    _setupLocked = locked;
  }
  function backdropCloseEnabled() {
    return _backdropClose;
  }
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape" || e.defaultPrevented || !_escClose) return;
    const m = document.getElementById("mgmt-modal");
    if (!m || m.classList.contains("hidden")) return;
    e.preventDefault();
    closeMgmtModal();
  });
  function formMsg() {
    return dom().el("div", { class: "mgmt-msg" });
  }
  function setMsg(msg, ok, text) {
    msg.className = "mgmt-msg " + (ok ? "ok" : "err");
    msg.textContent = text;
  }
  const KarvyModal = {
    openMgmtModal,
    closeMgmtModal,
    mgmtBody,
    setSetupLocked,
    backdropCloseEnabled,
    formMsg,
    setMsg
  };
  window.KarvyModal = KarvyModal;
  exports.KarvyModal = KarvyModal;
  Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
  return exports;
})({});
