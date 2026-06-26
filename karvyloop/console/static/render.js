/* render.js — 模型输出渲染层(M3+ 拍 9.4-显示层)
 * 把模型的结构化事件流(text/tool_call/tool_result/terminal)按类型渲染:
 *   text        → markdown(markdown-it)→ DOMPurify 消毒 → HTML
 *   tool_call   → 折叠卡(图标 + 工具名 + 输入摘要;<details> 原生折叠)
 *   tool_result → 输出面板(折叠 + 截断标记)
 *   terminal    → status 行
 * 借 openclaw 渲染模式(MIT);clean-room w.r.t. Claude Code(只取原则)。
 *
 * 安全:模型文本半可信 —— markdown-it 关 html(不吃裸 HTML)+ DOMPurify 兜底消毒,绝不裸 innerHTML。
 * 库缺失(vendor 没加载)→ renderMarkdown 返 null,调用方回退 textContent(裸文本,0 崩)。
 */
(function () {
  "use strict";

  var md = (typeof window.markdownit === "function")
    ? window.markdownit({ html: false, linkify: true, breaks: false })
    : null;

  function _sanitize(html) {
    if (window.DOMPurify && typeof window.DOMPurify.sanitize === "function") {
      return window.DOMPurify.sanitize(html, { ADD_ATTR: ["target", "rel"] });
    }
    return null; // 没消毒库 → 不出 HTML(调用方回退裸文本)
  }

  // 渲染 markdown → 消毒后的 HTML 字符串;任一库缺失 → null(回退裸文本)
  function renderMarkdown(text) {
    if (md === null) return null;
    var html = md.render(text || "");
    return _sanitize(html); // null 若无 DOMPurify
  }

  // 代码高亮:在**已消毒的 DOM** 上跑 highlight.js(只加 hljs 样式 span,不注入脚本 → 安全)。
  function _highlight(div) {
    if (!window.hljs || typeof window.hljs.highlightElement !== "function") return;
    var blocks = div.querySelectorAll("pre code");
    for (var i = 0; i < blocks.length; i++) {
      try { window.hljs.highlightElement(blocks[i]); } catch (e) { /* 单块失败不影响其余 */ }
    }
  }
  // 把文本以 markdown 渲染进容器;失败安全回退裸文本节点(永不裸 innerHTML 未消毒内容)
  function appendMarkdown(container, text, cls) {
    var html = renderMarkdown(text);
    var div = document.createElement("div");
    div.className = cls || "md";
    if (html === null) {
      div.textContent = text || "";        // 回退:裸文本(转义,安全)
    } else {
      div.innerHTML = html;                 // 已 DOMPurify 消毒
      _highlight(div);                      // P4:代码块语法高亮(消毒后再跑,安全)
      var pres = div.querySelectorAll("pre");
      for (var i = 0; i < pres.length; i++) _wrapWithCopy(pres[i]);   // 代码块加复制按钮
    }
    container.appendChild(div);
    return div;
  }

  var _ICONS = {
    read_file: "📖", list_dir: "📂", search_code: "🔎", glob: "🔎", grep: "🔎",
    write_file: "✏️", edit_file: "✏️", run_command: "$", bash: "$",
    web_search: "🌐", network: "🌐",
  };
  function toolIcon(name) { return _ICONS[name] || "🔧"; }

  // 工具输入摘要:挑常见键(path/file/command/...)的值,否则截断的 JSON
  function _inputSummary(input) {
    if (!input || typeof input !== "object") return "";
    var keys = ["path", "file", "file_path", "command", "cmd", "pattern", "query", "url"];
    for (var i = 0; i < keys.length; i++) {
      if (input[keys[i]] != null) return String(input[keys[i]]);
    }
    try { return JSON.stringify(input); } catch (e) { return ""; }
  }
  function _truncate(s, n) { s = s || ""; return s.length > n ? s.slice(0, n) + "…" : s; }

  function _el(tag, cls) { var e = document.createElement(tag); if (cls) e.className = cls; return e; }

  // 复制按钮(代码/指令框右上角)。LAN(非 https/localhost)下 navigator.clipboard 可能不可用 →
  // execCommand 兜底,保证局域网真机也能复制。
  function _fallbackCopy(txt) {
    try {
      var ta = document.createElement("textarea");
      ta.value = txt; ta.style.position = "fixed"; ta.style.left = "-9999px";
      document.body.appendChild(ta); ta.focus(); ta.select();
      document.execCommand("copy"); document.body.removeChild(ta);
    } catch (e) { /* 复制失败不致命 */ }
  }
  function _copyBtn(getText) {
    var T = window.KarvyI18n;
    var label = function () { return T ? T.t("render.copy") : "Copy"; };
    var btn = _el("button", "copy-btn"); btn.type = "button"; btn.textContent = label();
    btn.addEventListener("click", function (e) {
      e.preventDefault(); e.stopPropagation();
      var txt = getText() || "";
      var done = function () {
        btn.classList.add("copied");
        btn.textContent = T ? T.t("render.copied") : "Copied";
        setTimeout(function () { btn.classList.remove("copied"); btn.textContent = label(); }, 1500);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(txt).then(done, function () { _fallbackCopy(txt); done(); });
      } else { _fallbackCopy(txt); done(); }
    });
    return btn;
  }
  // 把一个 <pre> 包进带复制按钮的容器(代码块 / 工具输入 / 工具输出都用)
  function _wrapWithCopy(pre) {
    if (!pre || !pre.parentNode) return;
    if (pre.parentNode.classList && pre.parentNode.classList.contains("code-wrap")) return;
    var wrap = _el("div", "code-wrap");
    pre.parentNode.insertBefore(wrap, pre);
    wrap.appendChild(pre);
    wrap.appendChild(_copyBtn(function () { return pre.innerText || pre.textContent || ""; }));
  }

  // 渲染单条 render-event 进容器
  function renderEvent(container, ev) {
    if (!ev || !ev.type) return;
    if (ev.type === "text") {
      if ((ev.text || "").trim()) appendMarkdown(container, ev.text, "md chat-md");
    } else if (ev.type === "thinking") {
      // P4:推理过程 → 默认折叠(不污染答案;想看再展开)
      if ((ev.text || "").trim()) {
        var T0 = window.KarvyI18n;
        var det0 = _el("details", "thinking-card");
        var sum0 = _el("summary", "thinking-head");
        sum0.textContent = "💭 " + (T0 ? T0.t("render.thinking") : "思考过程");
        det0.appendChild(sum0);
        var body0 = _el("div", "thinking-body");
        appendMarkdown(body0, ev.text);
        det0.appendChild(body0);
        container.appendChild(det0);
      }
    } else if (ev.type === "tool_call") {
      var card = _el("details", "tool-card");
      var sum = _el("summary", "tool-card-head");
      sum.textContent = toolIcon(ev.name) + " " + (ev.name || "tool") + "  " + _truncate(_inputSummary(ev.input), 80);
      card.appendChild(sum);
      var body = _el("pre", "tool-card-body");
      try { body.textContent = JSON.stringify(ev.input, null, 2); } catch (e) { body.textContent = String(ev.input); }
      card.appendChild(body);
      _wrapWithCopy(body);   // 指令/输入框加复制按钮
      container.appendChild(card);
    } else if (ev.type === "tool_result") {
      var det = _el("details", "tool-result" + (ev.is_error ? " tool-result-error" : ""));
      var rs = _el("summary", "tool-result-head");
      var T = window.KarvyI18n;
      var lbl = T ? T.t("render.result") : "result";
      var tr = ev.truncated ? " (" + (T ? T.t("render.truncated") : "truncated") + ")" : "";
      rs.textContent = (ev.is_error ? "⚠ " : "↳ ") + lbl + tr;
      det.appendChild(rs);
      var out = _el("pre", "tool-result-body");
      out.textContent = ev.output || "";
      det.appendChild(out);
      _wrapWithCopy(out);   // 输出框加复制按钮
      container.appendChild(det);
    } else if (ev.type === "terminal") {
      var st = _el("div", "terminal-status" + (ev.ok ? "" : " terminal-error"));
      st.textContent = (ev.ok ? "✓ " : "✗ ") + (ev.reason || "");
      container.appendChild(st);
    }
  }

  // 9.5 P2:有工具调用时 → "过程"默认折叠,只突出最后的"结果"(用户:别给我一堆过程)。
  // 纯对话(无工具)→ 照常直接渲染。
  function renderEvents(container, events) {
    events = events || [];
    var hasTools = events.some(function (e) {
      return e.type === "tool_call" || e.type === "tool_result";
    });
    if (!hasTools) { events.forEach(function (ev) { renderEvent(container, ev); }); return; }
    // 最后一段非空 text = 最终结果;其余(工具卡 + 中间文字 + terminal)= 过程
    var lastTextIdx = -1;
    for (var i = events.length - 1; i >= 0; i--) {
      if (events[i].type === "text" && (events[i].text || "").trim()) { lastTextIdx = i; break; }
    }
    var processEvents = [], finalText = null;
    events.forEach(function (ev, idx) {
      if (idx === lastTextIdx) finalText = ev; else processEvents.push(ev);
    });
    if (processEvents.length) {
      var steps = processEvents.filter(function (e) { return e.type === "tool_call"; }).length;
      var fold = _el("details", "process-fold");
      var head = _el("summary", "process-fold-head");
      var T = window.KarvyI18n;
      head.textContent = T ? T.t("render.process", { n: steps }) : ("过程(" + steps + " 步)");
      fold.appendChild(head);
      var inner = _el("div", "process-fold-body");
      processEvents.forEach(function (ev) { renderEvent(inner, ev); });
      fold.appendChild(inner);
      container.appendChild(fold);
    }
    if (finalText) appendMarkdown(container, finalText.text, "md chat-md final-answer");
  }

  window.KarvyRender = {
    renderMarkdown: renderMarkdown,
    appendMarkdown: appendMarkdown,
    renderEvents: renderEvents,
    renderEvent: renderEvent,
    toolIcon: toolIcon,
  };
})();
