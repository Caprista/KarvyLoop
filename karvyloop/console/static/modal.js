var KarvyModalBundle = (function(exports) {
  "use strict";
  function dom() {
    return window.KarvyDom;
  }
  let _setupLocked = false;
  function openMgmtModal(title) {
    var _a;
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
  function formMsg() {
    return dom().el("div", { class: "mgmt-msg" });
  }
  function setMsg(msg, ok, text) {
    msg.className = "mgmt-msg " + (ok ? "ok" : "err");
    msg.textContent = text;
  }
  const KarvyModal = { openMgmtModal, closeMgmtModal, mgmtBody, setSetupLocked, formMsg, setMsg };
  window.KarvyModal = KarvyModal;
  exports.KarvyModal = KarvyModal;
  Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
  return exports;
})({});
