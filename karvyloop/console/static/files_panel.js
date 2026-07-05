var KarvyFilesPanelBundle = (function(exports) {
  "use strict";
  const _KD = window.KarvyDom;
  const _KM = window.KarvyModal;
  const el = _KD.el, _getJSON = _KD.getJSON, _postJSON = _KD.postJSON;
  const openMgmtModal = _KM.openMgmtModal, closeMgmtModal = _KM.closeMgmtModal, mgmtBody = _KM.mgmtBody;
  const t = (k, vars) => window.KarvyI18n.t(k, vars);
  let _filesPath = "";
  function _analyzeInChat(rel) {
    const msg = t("files.analyze_intent", { path: rel });
    const ce = document.getElementById("chat-input");
    if (!ce) return;
    ce.textContent = msg;
    ce.classList.remove("is-empty");
    closeMgmtModal();
    ce.focus();
    try {
      const r = document.createRange();
      r.selectNodeContents(ce);
      r.collapse(false);
      const sel = window.getSelection();
      if (sel) {
        sel.removeAllRanges();
        sel.addRange(r);
      }
    } catch {
    }
  }
  function _fmtSize(n) {
    if (n < 1024) return n + " B";
    if (n < 1048576) return (n / 1024).toFixed(1) + " KB";
    return (n / 1048576).toFixed(1) + " MB";
  }
  async function renderFilesPanel() {
    const body = mgmtBody();
    if (!body) return;
    body.innerHTML = "";
    body.appendChild(el("div", { class: "mgmt-section-title", text: t("files.title") }));
    const data = await _getJSON("/api/files/list?path=" + encodeURIComponent(_filesPath));
    if (!data || !data.ok) {
      body.appendChild(el("div", {
        class: "mgmt-empty",
        text: data && data.reason === "no_workspace" ? t("files.no_workspace") : t("files.bad_path")
      }));
      return;
    }
    const crumb = el("div", { class: "files-crumb" });
    const go = (target) => {
      _filesPath = target;
      renderFilesPanel();
    };
    crumb.appendChild(el("button", { class: "files-crumb-link", text: "🗂 workspace", onClick: () => go("") }));
    let acc = "";
    for (const part of (data.path || "").split("/").filter(Boolean)) {
      acc = acc ? acc + "/" + part : part;
      crumb.appendChild(el("span", { class: "files-crumb-sep", text: " / " }));
      const tgt = acc;
      crumb.appendChild(el("button", { class: "files-crumb-link", text: part, onClick: () => go(tgt) }));
    }
    body.appendChild(crumb);
    const upRow = el("div", { class: "files-toolbar" });
    const fileInput = el("input", { type: "file" });
    fileInput.style.display = "none";
    fileInput.addEventListener("change", async () => {
      const f = fileInput.files && fileInput.files[0];
      if (!f) return;
      const url = "/api/files/upload?dir=" + encodeURIComponent(_filesPath) + "&name=" + encodeURIComponent(f.name);
      try {
        const r = await fetch(url, { method: "POST", body: f });
        const d = await r.json();
        if (d && d.ok) {
          renderFilesPanel();
        } else {
          alert(t("files.upload_fail"));
        }
      } catch {
        alert(t("files.upload_fail"));
      }
      fileInput.value = "";
    });
    upRow.appendChild(el("button", {
      class: "files-act files-upbtn",
      text: t("files.upload"),
      onClick: () => fileInput.click()
    }));
    upRow.appendChild(fileInput);
    body.appendChild(upRow);
    body.appendChild(el("div", { class: "files-hint", text: t("files.lan_hint") }));
    const list = el("div", { class: "files-list" });
    if (!data.entries.length) list.appendChild(el("div", { class: "mgmt-empty", text: t("files.empty") }));
    for (const e of data.entries) {
      const rel = (_filesPath ? _filesPath + "/" : "") + e.name;
      const row = el("div", { class: "files-row" });
      if (e.is_dir) {
        row.appendChild(el("button", { class: "files-name files-dir", text: "📁 " + e.name, onClick: () => go(rel) }));
      } else {
        row.appendChild(el("span", { class: "files-name", text: "📄 " + e.name }));
        row.appendChild(el("span", { class: "files-size", text: _fmtSize(e.size || 0) }));
        row.appendChild(el("button", { class: "files-act", text: t("files.view"), onClick: () => _viewFile(rel) }));
        row.appendChild(el("button", {
          class: "files-act files-analyze",
          text: "📊 " + t("files.analyze"),
          onClick: () => _analyzeInChat(rel)
        }));
        const dl = el("a", { class: "files-act files-dl", text: t("files.download") });
        dl.href = "/api/files/download?path=" + encodeURIComponent(rel);
        dl.setAttribute("download", e.name);
        row.appendChild(dl);
      }
      row.appendChild(el("button", {
        class: "files-act files-del",
        text: t("files.delete"),
        onClick: async () => {
          if (!window.confirm(t("files.delete_confirm", { name: e.name }))) return;
          const r = await _postJSON("/api/files/delete?path=" + encodeURIComponent(rel), {});
          if (r.ok && r.data && r.data.ok) {
            renderFilesPanel();
          } else alert(r.data && r.data.reason === "not_empty" ? t("files.del_not_empty") : t("files.del_fail"));
        }
      }));
      list.appendChild(row);
    }
    body.appendChild(list);
  }
  async function _viewFile(rel) {
    const d = await _getJSON("/api/files/view?path=" + encodeURIComponent(rel));
    const body = mgmtBody();
    if (!body) return;
    const old = body.querySelector(".files-preview-wrap");
    if (old) old.remove();
    const pre = el("pre", { class: "files-preview" });
    const notes = [];
    let unlockBtn = null;
    if (!d || !d.ok) pre.textContent = t("files.bad_path");
    else if (d.too_big) pre.textContent = t("files.too_big");
    else if (d.extract_error === "missing_dependency") {
      pre.textContent = t(d.extract === "audio" ? "files.extract_missing_dep_asr" : "files.extract_missing_dep");
      const unlock = window.KarvyUnlockPanel;
      if (unlock) unlockBtn = el("button", {
        class: "mgmt-inline-link",
        text: t("unlock.open_from_here"),
        onClick: () => unlock.open()
      });
    } else if (d.extract_error) pre.textContent = t("files.extract_bad_file");
    else if (d.binary) pre.textContent = t("files.binary");
    else if (d.extract && !d.text) pre.textContent = t("files.extract_empty");
    else {
      pre.textContent = d.text || "";
      if (d.extract) notes.push(t("files.extract_note", { kind: String(d.extract).toUpperCase() }));
      if (d.truncated) notes.push(t("files.extract_truncated", { n: d.limit || 1e5 }));
    }
    const wrap = el(
      "div",
      { class: "files-preview-wrap" },
      el(
        "div",
        { class: "files-preview-head" },
        el("span", { text: "📄 " + rel }),
        el("button", { class: "files-preview-close", text: "✕", onClick: () => wrap.remove() })
      ),
      notes.length ? el("div", { class: "files-preview-note files-hint", text: notes.join(" ") }) : null,
      pre,
      unlockBtn
    );
    body.appendChild(wrap);
    wrap.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
  async function open() {
    openMgmtModal(t("files.title"));
    _filesPath = "";
    await renderFilesPanel();
  }
  const KarvyFilesPanel = { open };
  window.KarvyFilesPanel = KarvyFilesPanel;
  exports.KarvyFilesPanel = KarvyFilesPanel;
  Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
  return exports;
})({});
