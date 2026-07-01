var KarvyWorkflowCanvasBundle = (function(exports) {
  "use strict";
  function getDefaultExportFromCjs(x) {
    return x && x.__esModule && Object.prototype.hasOwnProperty.call(x, "default") ? x["default"] : x;
  }
  var drawflow_min$1 = { exports: {} };
  var drawflow_min = drawflow_min$1.exports;
  var hasRequiredDrawflow_min;
  function requireDrawflow_min() {
    if (hasRequiredDrawflow_min) return drawflow_min$1.exports;
    hasRequiredDrawflow_min = 1;
    (function(module, exports2) {
      !(function(e, t) {
        module.exports = t();
      })("undefined" != typeof self ? self : drawflow_min, (function() {
        return (function(e) {
          var t = {};
          function n(i) {
            if (t[i]) return t[i].exports;
            var s = t[i] = { i, l: false, exports: {} };
            return e[i].call(s.exports, s, s.exports, n), s.l = true, s.exports;
          }
          return n.m = e, n.c = t, n.d = function(e2, t2, i) {
            n.o(e2, t2) || Object.defineProperty(e2, t2, { enumerable: true, get: i });
          }, n.r = function(e2) {
            "undefined" != typeof Symbol && Symbol.toStringTag && Object.defineProperty(e2, Symbol.toStringTag, { value: "Module" }), Object.defineProperty(e2, "__esModule", { value: true });
          }, n.t = function(e2, t2) {
            if (1 & t2 && (e2 = n(e2)), 8 & t2) return e2;
            if (4 & t2 && "object" == typeof e2 && e2 && e2.__esModule) return e2;
            var i = /* @__PURE__ */ Object.create(null);
            if (n.r(i), Object.defineProperty(i, "default", { enumerable: true, value: e2 }), 2 & t2 && "string" != typeof e2) for (var s in e2) n.d(i, s, (function(t3) {
              return e2[t3];
            }).bind(null, s));
            return i;
          }, n.n = function(e2) {
            var t2 = e2 && e2.__esModule ? function() {
              return e2.default;
            } : function() {
              return e2;
            };
            return n.d(t2, "a", t2), t2;
          }, n.o = function(e2, t2) {
            return Object.prototype.hasOwnProperty.call(e2, t2);
          }, n.p = "", n(n.s = 0);
        })([function(e, t, n) {
          n.r(t), n.d(t, "default", (function() {
            return i;
          }));
          class i {
            constructor(e2, t2 = null, n2 = null) {
              this.events = {}, this.container = e2, this.precanvas = null, this.nodeId = 1, this.ele_selected = null, this.node_selected = null, this.drag = false, this.reroute = false, this.reroute_fix_curvature = false, this.curvature = 0.5, this.reroute_curvature_start_end = 0.5, this.reroute_curvature = 0.5, this.reroute_width = 6, this.drag_point = false, this.editor_selected = false, this.connection = false, this.connection_ele = null, this.connection_selected = null, this.canvas_x = 0, this.canvas_y = 0, this.pos_x = 0, this.pos_x_start = 0, this.pos_y = 0, this.pos_y_start = 0, this.mouse_x = 0, this.mouse_y = 0, this.line_path = 5, this.first_click = null, this.force_first_input = false, this.draggable_inputs = true, this.useuuid = false, this.parent = n2, this.noderegister = {}, this.render = t2, this.drawflow = { drawflow: { Home: { data: {} } } }, this.module = "Home", this.editor_mode = "edit", this.zoom = 1, this.zoom_max = 1.6, this.zoom_min = 0.5, this.zoom_value = 0.1, this.zoom_last_value = 1, this.evCache = new Array(), this.prevDiff = -1;
            }
            start() {
              this.container.classList.add("parent-drawflow"), this.container.tabIndex = 0, this.precanvas = document.createElement("div"), this.precanvas.classList.add("drawflow"), this.container.appendChild(this.precanvas), this.container.addEventListener("mouseup", this.dragEnd.bind(this)), this.container.addEventListener("mousemove", this.position.bind(this)), this.container.addEventListener("mousedown", this.click.bind(this)), this.container.addEventListener("touchend", this.dragEnd.bind(this)), this.container.addEventListener("touchmove", this.position.bind(this)), this.container.addEventListener("touchstart", this.click.bind(this)), this.container.addEventListener("contextmenu", this.contextmenu.bind(this)), this.container.addEventListener("keydown", this.key.bind(this)), this.container.addEventListener("wheel", this.zoom_enter.bind(this)), this.container.addEventListener("input", this.updateNodeValue.bind(this)), this.container.addEventListener("dblclick", this.dblclick.bind(this)), this.container.onpointerdown = this.pointerdown_handler.bind(this), this.container.onpointermove = this.pointermove_handler.bind(this), this.container.onpointerup = this.pointerup_handler.bind(this), this.container.onpointercancel = this.pointerup_handler.bind(this), this.container.onpointerout = this.pointerup_handler.bind(this), this.container.onpointerleave = this.pointerup_handler.bind(this), this.load();
            }
            pointerdown_handler(e2) {
              this.evCache.push(e2);
            }
            pointermove_handler(e2) {
              for (var t2 = 0; t2 < this.evCache.length; t2++) if (e2.pointerId == this.evCache[t2].pointerId) {
                this.evCache[t2] = e2;
                break;
              }
              if (2 == this.evCache.length) {
                var n2 = Math.abs(this.evCache[0].clientX - this.evCache[1].clientX);
                this.prevDiff > 100 && (n2 > this.prevDiff && this.zoom_in(), n2 < this.prevDiff && this.zoom_out()), this.prevDiff = n2;
              }
            }
            pointerup_handler(e2) {
              this.remove_event(e2), this.evCache.length < 2 && (this.prevDiff = -1);
            }
            remove_event(e2) {
              for (var t2 = 0; t2 < this.evCache.length; t2++) if (this.evCache[t2].pointerId == e2.pointerId) {
                this.evCache.splice(t2, 1);
                break;
              }
            }
            load() {
              for (var e2 in this.drawflow.drawflow[this.module].data) this.addNodeImport(this.drawflow.drawflow[this.module].data[e2], this.precanvas);
              if (this.reroute) for (var e2 in this.drawflow.drawflow[this.module].data) this.addRerouteImport(this.drawflow.drawflow[this.module].data[e2]);
              for (var e2 in this.drawflow.drawflow[this.module].data) this.updateConnectionNodes("node-" + e2);
              const t2 = this.drawflow.drawflow;
              let n2 = 1;
              Object.keys(t2).map((function(e3, i2) {
                Object.keys(t2[e3].data).map((function(e4, t3) {
                  parseInt(e4) >= n2 && (n2 = parseInt(e4) + 1);
                }));
              })), this.nodeId = n2;
            }
            removeReouteConnectionSelected() {
              this.dispatch("connectionUnselected", true), this.reroute_fix_curvature && this.connection_selected.parentElement.querySelectorAll(".main-path").forEach((e2, t2) => {
                e2.classList.remove("selected");
              });
            }
            click(e2) {
              if (this.dispatch("click", e2), "fixed" === this.editor_mode) {
                if ("parent-drawflow" !== e2.target.classList[0] && "drawflow" !== e2.target.classList[0]) return false;
                this.ele_selected = e2.target.closest(".parent-drawflow"), e2.preventDefault();
              } else "view" === this.editor_mode ? (null != e2.target.closest(".drawflow") || e2.target.matches(".parent-drawflow")) && (this.ele_selected = e2.target.closest(".parent-drawflow"), e2.preventDefault()) : (this.first_click = e2.target, this.ele_selected = e2.target, 0 === e2.button && this.contextmenuDel(), null != e2.target.closest(".drawflow_content_node") && (this.ele_selected = e2.target.closest(".drawflow_content_node").parentElement));
              switch (this.ele_selected.classList[0]) {
                case "drawflow-node":
                  null != this.node_selected && (this.node_selected.classList.remove("selected"), this.node_selected != this.ele_selected && this.dispatch("nodeUnselected", true)), null != this.connection_selected && (this.connection_selected.classList.remove("selected"), this.removeReouteConnectionSelected(), this.connection_selected = null), this.node_selected != this.ele_selected && this.dispatch("nodeSelected", this.ele_selected.id.slice(5)), this.node_selected = this.ele_selected, this.node_selected.classList.add("selected"), this.draggable_inputs ? "SELECT" !== e2.target.tagName && (this.drag = true) : "INPUT" !== e2.target.tagName && "TEXTAREA" !== e2.target.tagName && "SELECT" !== e2.target.tagName && true !== e2.target.hasAttribute("contenteditable") && (this.drag = true);
                  break;
                case "output":
                  this.connection = true, null != this.node_selected && (this.node_selected.classList.remove("selected"), this.node_selected = null, this.dispatch("nodeUnselected", true)), null != this.connection_selected && (this.connection_selected.classList.remove("selected"), this.removeReouteConnectionSelected(), this.connection_selected = null), this.drawConnection(e2.target);
                  break;
                case "parent-drawflow":
                case "drawflow":
                  null != this.node_selected && (this.node_selected.classList.remove("selected"), this.node_selected = null, this.dispatch("nodeUnselected", true)), null != this.connection_selected && (this.connection_selected.classList.remove("selected"), this.removeReouteConnectionSelected(), this.connection_selected = null), this.editor_selected = true;
                  break;
                case "main-path":
                  null != this.node_selected && (this.node_selected.classList.remove("selected"), this.node_selected = null, this.dispatch("nodeUnselected", true)), null != this.connection_selected && (this.connection_selected.classList.remove("selected"), this.removeReouteConnectionSelected(), this.connection_selected = null), this.connection_selected = this.ele_selected, this.connection_selected.classList.add("selected");
                  const t2 = this.connection_selected.parentElement.classList;
                  t2.length > 1 && (this.dispatch("connectionSelected", { output_id: t2[2].slice(14), input_id: t2[1].slice(13), output_class: t2[3], input_class: t2[4] }), this.reroute_fix_curvature && this.connection_selected.parentElement.querySelectorAll(".main-path").forEach((e3, t3) => {
                    e3.classList.add("selected");
                  }));
                  break;
                case "point":
                  this.drag_point = true, this.ele_selected.classList.add("selected");
                  break;
                case "drawflow-delete":
                  this.node_selected && this.removeNodeId(this.node_selected.id), this.connection_selected && this.removeConnection(), null != this.node_selected && (this.node_selected.classList.remove("selected"), this.node_selected = null, this.dispatch("nodeUnselected", true)), null != this.connection_selected && (this.connection_selected.classList.remove("selected"), this.removeReouteConnectionSelected(), this.connection_selected = null);
              }
              "touchstart" === e2.type ? (this.pos_x = e2.touches[0].clientX, this.pos_x_start = e2.touches[0].clientX, this.pos_y = e2.touches[0].clientY, this.pos_y_start = e2.touches[0].clientY, this.mouse_x = e2.touches[0].clientX, this.mouse_y = e2.touches[0].clientY) : (this.pos_x = e2.clientX, this.pos_x_start = e2.clientX, this.pos_y = e2.clientY, this.pos_y_start = e2.clientY), ["input", "output", "main-path"].includes(this.ele_selected.classList[0]) && e2.preventDefault(), this.dispatch("clickEnd", e2);
            }
            position(e2) {
              if ("touchmove" === e2.type) var t2 = e2.touches[0].clientX, n2 = e2.touches[0].clientY;
              else t2 = e2.clientX, n2 = e2.clientY;
              if (this.connection && this.updateConnection(t2, n2), this.editor_selected && (i2 = this.canvas_x + -(this.pos_x - t2), s = this.canvas_y + -(this.pos_y - n2), this.dispatch("translate", { x: i2, y: s }), this.precanvas.style.transform = "translate(" + i2 + "px, " + s + "px) scale(" + this.zoom + ")"), this.drag) {
                e2.preventDefault();
                var i2 = (this.pos_x - t2) * this.precanvas.clientWidth / (this.precanvas.clientWidth * this.zoom), s = (this.pos_y - n2) * this.precanvas.clientHeight / (this.precanvas.clientHeight * this.zoom);
                this.pos_x = t2, this.pos_y = n2, this.ele_selected.style.top = this.ele_selected.offsetTop - s + "px", this.ele_selected.style.left = this.ele_selected.offsetLeft - i2 + "px", this.drawflow.drawflow[this.module].data[this.ele_selected.id.slice(5)].pos_x = this.ele_selected.offsetLeft - i2, this.drawflow.drawflow[this.module].data[this.ele_selected.id.slice(5)].pos_y = this.ele_selected.offsetTop - s, this.updateConnectionNodes(this.ele_selected.id);
              }
              if (this.drag_point) {
                i2 = (this.pos_x - t2) * this.precanvas.clientWidth / (this.precanvas.clientWidth * this.zoom), s = (this.pos_y - n2) * this.precanvas.clientHeight / (this.precanvas.clientHeight * this.zoom);
                this.pos_x = t2, this.pos_y = n2;
                var o = this.pos_x * (this.precanvas.clientWidth / (this.precanvas.clientWidth * this.zoom)) - this.precanvas.getBoundingClientRect().x * (this.precanvas.clientWidth / (this.precanvas.clientWidth * this.zoom)), l = this.pos_y * (this.precanvas.clientHeight / (this.precanvas.clientHeight * this.zoom)) - this.precanvas.getBoundingClientRect().y * (this.precanvas.clientHeight / (this.precanvas.clientHeight * this.zoom));
                this.ele_selected.setAttributeNS(null, "cx", o), this.ele_selected.setAttributeNS(null, "cy", l);
                const e3 = this.ele_selected.parentElement.classList[2].slice(9), c = this.ele_selected.parentElement.classList[1].slice(13), d = this.ele_selected.parentElement.classList[3], a = this.ele_selected.parentElement.classList[4];
                let r = Array.from(this.ele_selected.parentElement.children).indexOf(this.ele_selected) - 1;
                if (this.reroute_fix_curvature) {
                  r -= this.ele_selected.parentElement.querySelectorAll(".main-path").length - 1, r < 0 && (r = 0);
                }
                const h = e3.slice(5), u = this.drawflow.drawflow[this.module].data[h].outputs[d].connections.findIndex((function(e4, t3) {
                  return e4.node === c && e4.output === a;
                }));
                this.drawflow.drawflow[this.module].data[h].outputs[d].connections[u].points[r] = { pos_x: o, pos_y: l };
                const p = this.ele_selected.parentElement.classList[2].slice(9);
                this.updateConnectionNodes(p);
              }
              "touchmove" === e2.type && (this.mouse_x = t2, this.mouse_y = n2), this.dispatch("mouseMove", { x: t2, y: n2 });
            }
            dragEnd(e2) {
              if ("touchend" === e2.type) var t2 = this.mouse_x, n2 = this.mouse_y, i2 = document.elementFromPoint(t2, n2);
              else t2 = e2.clientX, n2 = e2.clientY, i2 = e2.target;
              if (this.drag && (this.pos_x_start == t2 && this.pos_y_start == n2 || this.dispatch("nodeMoved", this.ele_selected.id.slice(5))), this.drag_point && (this.ele_selected.classList.remove("selected"), this.pos_x_start == t2 && this.pos_y_start == n2 || this.dispatch("rerouteMoved", this.ele_selected.parentElement.classList[2].slice(14))), this.editor_selected && (this.canvas_x = this.canvas_x + -(this.pos_x - t2), this.canvas_y = this.canvas_y + -(this.pos_y - n2), this.editor_selected = false), true === this.connection) if ("input" === i2.classList[0] || this.force_first_input && (null != i2.closest(".drawflow_content_node") || "drawflow-node" === i2.classList[0])) {
                if (!this.force_first_input || null == i2.closest(".drawflow_content_node") && "drawflow-node" !== i2.classList[0]) s = i2.parentElement.parentElement.id, o = i2.classList[1];
                else {
                  if (null != i2.closest(".drawflow_content_node")) var s = i2.closest(".drawflow_content_node").parentElement.id;
                  else var s = i2.id;
                  if (0 === Object.keys(this.getNodeFromId(s.slice(5)).inputs).length) var o = false;
                  else var o = "input_1";
                }
                var l = this.ele_selected.parentElement.parentElement.id, c = this.ele_selected.classList[1];
                if (l !== s && false !== o) {
                  if (0 === this.container.querySelectorAll(".connection.node_in_" + s + ".node_out_" + l + "." + c + "." + o).length) {
                    this.connection_ele.classList.add("node_in_" + s), this.connection_ele.classList.add("node_out_" + l), this.connection_ele.classList.add(c), this.connection_ele.classList.add(o);
                    var d = s.slice(5), a = l.slice(5);
                    this.drawflow.drawflow[this.module].data[a].outputs[c].connections.push({ node: d, output: o }), this.drawflow.drawflow[this.module].data[d].inputs[o].connections.push({ node: a, input: c }), this.updateConnectionNodes("node-" + a), this.updateConnectionNodes("node-" + d), this.dispatch("connectionCreated", { output_id: a, input_id: d, output_class: c, input_class: o });
                  } else this.dispatch("connectionCancel", true), this.connection_ele.remove();
                  this.connection_ele = null;
                } else this.dispatch("connectionCancel", true), this.connection_ele.remove(), this.connection_ele = null;
              } else this.dispatch("connectionCancel", true), this.connection_ele.remove(), this.connection_ele = null;
              this.drag = false, this.drag_point = false, this.connection = false, this.ele_selected = null, this.editor_selected = false, this.dispatch("mouseUp", e2);
            }
            contextmenu(e2) {
              if (this.dispatch("contextmenu", e2), e2.preventDefault(), "fixed" === this.editor_mode || "view" === this.editor_mode) return false;
              if (this.precanvas.getElementsByClassName("drawflow-delete").length && this.precanvas.getElementsByClassName("drawflow-delete")[0].remove(), this.node_selected || this.connection_selected) {
                var t2 = document.createElement("div");
                t2.classList.add("drawflow-delete"), t2.innerHTML = "x", this.node_selected && this.node_selected.appendChild(t2), this.connection_selected && this.connection_selected.parentElement.classList.length > 1 && (t2.style.top = e2.clientY * (this.precanvas.clientHeight / (this.precanvas.clientHeight * this.zoom)) - this.precanvas.getBoundingClientRect().y * (this.precanvas.clientHeight / (this.precanvas.clientHeight * this.zoom)) + "px", t2.style.left = e2.clientX * (this.precanvas.clientWidth / (this.precanvas.clientWidth * this.zoom)) - this.precanvas.getBoundingClientRect().x * (this.precanvas.clientWidth / (this.precanvas.clientWidth * this.zoom)) + "px", this.precanvas.appendChild(t2));
              }
            }
            contextmenuDel() {
              this.precanvas.getElementsByClassName("drawflow-delete").length && this.precanvas.getElementsByClassName("drawflow-delete")[0].remove();
            }
            key(e2) {
              if (this.dispatch("keydown", e2), "fixed" === this.editor_mode || "view" === this.editor_mode) return false;
              ("Delete" === e2.key || "Backspace" === e2.key && e2.metaKey) && (null != this.node_selected && "INPUT" !== this.first_click.tagName && "TEXTAREA" !== this.first_click.tagName && true !== this.first_click.hasAttribute("contenteditable") && this.removeNodeId(this.node_selected.id), null != this.connection_selected && this.removeConnection());
            }
            zoom_enter(e2, t2) {
              e2.ctrlKey && (e2.preventDefault(), e2.deltaY > 0 ? this.zoom_out() : this.zoom_in());
            }
            zoom_refresh() {
              this.dispatch("zoom", this.zoom), this.canvas_x = this.canvas_x / this.zoom_last_value * this.zoom, this.canvas_y = this.canvas_y / this.zoom_last_value * this.zoom, this.zoom_last_value = this.zoom, this.precanvas.style.transform = "translate(" + this.canvas_x + "px, " + this.canvas_y + "px) scale(" + this.zoom + ")";
            }
            zoom_in() {
              this.zoom < this.zoom_max && (this.zoom += this.zoom_value, this.zoom_refresh());
            }
            zoom_out() {
              this.zoom > this.zoom_min && (this.zoom -= this.zoom_value, this.zoom_refresh());
            }
            zoom_reset() {
              1 != this.zoom && (this.zoom = 1, this.zoom_refresh());
            }
            createCurvature(e2, t2, n2, i2, s, o) {
              var l = e2, c = t2, d = n2, a = i2, r = s;
              switch (o) {
                case "open":
                  if (e2 >= n2) var h = l + Math.abs(d - l) * r, u = d - Math.abs(d - l) * (-1 * r);
                  else h = l + Math.abs(d - l) * r, u = d - Math.abs(d - l) * r;
                  return " M " + l + " " + c + " C " + h + " " + c + " " + u + " " + a + " " + d + "  " + a;
                case "close":
                  if (e2 >= n2) h = l + Math.abs(d - l) * (-1 * r), u = d - Math.abs(d - l) * r;
                  else h = l + Math.abs(d - l) * r, u = d - Math.abs(d - l) * r;
                  return " M " + l + " " + c + " C " + h + " " + c + " " + u + " " + a + " " + d + "  " + a;
                case "other":
                  if (e2 >= n2) h = l + Math.abs(d - l) * (-1 * r), u = d - Math.abs(d - l) * (-1 * r);
                  else h = l + Math.abs(d - l) * r, u = d - Math.abs(d - l) * r;
                  return " M " + l + " " + c + " C " + h + " " + c + " " + u + " " + a + " " + d + "  " + a;
                default:
                  return " M " + l + " " + c + " C " + (h = l + Math.abs(d - l) * r) + " " + c + " " + (u = d - Math.abs(d - l) * r) + " " + a + " " + d + "  " + a;
              }
            }
            drawConnection(e2) {
              var t2 = document.createElementNS("http://www.w3.org/2000/svg", "svg");
              this.connection_ele = t2;
              var n2 = document.createElementNS("http://www.w3.org/2000/svg", "path");
              n2.classList.add("main-path"), n2.setAttributeNS(null, "d", ""), t2.classList.add("connection"), t2.appendChild(n2), this.precanvas.appendChild(t2);
              var i2 = e2.parentElement.parentElement.id.slice(5), s = e2.classList[1];
              this.dispatch("connectionStart", { output_id: i2, output_class: s });
            }
            updateConnection(e2, t2) {
              const n2 = this.precanvas, i2 = this.zoom;
              let s = n2.clientWidth / (n2.clientWidth * i2);
              s = s || 0;
              let o = n2.clientHeight / (n2.clientHeight * i2);
              o = o || 0;
              var l = this.connection_ele.children[0], c = this.ele_selected.offsetWidth / 2 + (this.ele_selected.getBoundingClientRect().x - n2.getBoundingClientRect().x) * s, d = this.ele_selected.offsetHeight / 2 + (this.ele_selected.getBoundingClientRect().y - n2.getBoundingClientRect().y) * o, a = e2 * (this.precanvas.clientWidth / (this.precanvas.clientWidth * this.zoom)) - this.precanvas.getBoundingClientRect().x * (this.precanvas.clientWidth / (this.precanvas.clientWidth * this.zoom)), r = t2 * (this.precanvas.clientHeight / (this.precanvas.clientHeight * this.zoom)) - this.precanvas.getBoundingClientRect().y * (this.precanvas.clientHeight / (this.precanvas.clientHeight * this.zoom)), h = this.curvature, u = this.createCurvature(c, d, a, r, h, "openclose");
              l.setAttributeNS(null, "d", u);
            }
            addConnection(e2, t2, n2, i2) {
              var s = this.getModuleFromNodeId(e2);
              if (s === this.getModuleFromNodeId(t2)) {
                var o = this.getNodeFromId(e2), l = false;
                for (var c in o.outputs[n2].connections) {
                  var d = o.outputs[n2].connections[c];
                  d.node == t2 && d.output == i2 && (l = true);
                }
                if (false === l) {
                  if (this.drawflow.drawflow[s].data[e2].outputs[n2].connections.push({ node: t2.toString(), output: i2 }), this.drawflow.drawflow[s].data[t2].inputs[i2].connections.push({ node: e2.toString(), input: n2 }), this.module === s) {
                    var a = document.createElementNS("http://www.w3.org/2000/svg", "svg"), r = document.createElementNS("http://www.w3.org/2000/svg", "path");
                    r.classList.add("main-path"), r.setAttributeNS(null, "d", ""), a.classList.add("connection"), a.classList.add("node_in_node-" + t2), a.classList.add("node_out_node-" + e2), a.classList.add(n2), a.classList.add(i2), a.appendChild(r), this.precanvas.appendChild(a), this.updateConnectionNodes("node-" + e2), this.updateConnectionNodes("node-" + t2);
                  }
                  this.dispatch("connectionCreated", { output_id: e2, input_id: t2, output_class: n2, input_class: i2 });
                }
              }
            }
            updateConnectionNodes(e2) {
              const t2 = "node_in_" + e2, n2 = "node_out_" + e2;
              this.line_path;
              const i2 = this.container, s = this.precanvas, o = this.curvature, l = this.createCurvature, c = this.reroute_curvature, d = this.reroute_curvature_start_end, a = this.reroute_fix_curvature, r = this.reroute_width, h = this.zoom;
              let u = s.clientWidth / (s.clientWidth * h);
              u = u || 0;
              let p = s.clientHeight / (s.clientHeight * h);
              p = p || 0;
              const f = i2.querySelectorAll("." + n2);
              Object.keys(f).map((function(t3, n3) {
                if (null === f[t3].querySelector(".point")) {
                  var m2 = i2.querySelector("#" + e2), g = f[t3].classList[1].replace("node_in_", ""), _ = i2.querySelector("#" + g).querySelectorAll("." + f[t3].classList[4])[0], w = _.offsetWidth / 2 + (_.getBoundingClientRect().x - s.getBoundingClientRect().x) * u, v = _.offsetHeight / 2 + (_.getBoundingClientRect().y - s.getBoundingClientRect().y) * p, y = m2.querySelectorAll("." + f[t3].classList[3])[0], C = y.offsetWidth / 2 + (y.getBoundingClientRect().x - s.getBoundingClientRect().x) * u, x = y.offsetHeight / 2 + (y.getBoundingClientRect().y - s.getBoundingClientRect().y) * p;
                  const n4 = l(C, x, w, v, o, "openclose");
                  f[t3].children[0].setAttributeNS(null, "d", n4);
                } else {
                  const n4 = f[t3].querySelectorAll(".point");
                  let o2 = "";
                  const m3 = [];
                  n4.forEach((t4, a2) => {
                    if (0 === a2 && n4.length - 1 == 0) {
                      var f2 = i2.querySelector("#" + e2), g2 = ((x2 = t4).getBoundingClientRect().x - s.getBoundingClientRect().x) * u + r, _2 = (x2.getBoundingClientRect().y - s.getBoundingClientRect().y) * p + r, w2 = (L = f2.querySelectorAll("." + t4.parentElement.classList[3])[0]).offsetWidth / 2 + (L.getBoundingClientRect().x - s.getBoundingClientRect().x) * u, v2 = L.offsetHeight / 2 + (L.getBoundingClientRect().y - s.getBoundingClientRect().y) * p, y2 = l(w2, v2, g2, _2, d, "open");
                      o2 += y2, m3.push(y2);
                      f2 = t4;
                      var C2 = t4.parentElement.classList[1].replace("node_in_", ""), x2 = (E = i2.querySelector("#" + C2)).querySelectorAll("." + t4.parentElement.classList[4])[0];
                      g2 = (R = E.querySelectorAll("." + t4.parentElement.classList[4])[0]).offsetWidth / 2 + (R.getBoundingClientRect().x - s.getBoundingClientRect().x) * u, _2 = R.offsetHeight / 2 + (R.getBoundingClientRect().y - s.getBoundingClientRect().y) * p, w2 = (f2.getBoundingClientRect().x - s.getBoundingClientRect().x) * u + r, v2 = (f2.getBoundingClientRect().y - s.getBoundingClientRect().y) * p + r, y2 = l(w2, v2, g2, _2, d, "close");
                      o2 += y2, m3.push(y2);
                    } else if (0 === a2) {
                      var L;
                      f2 = i2.querySelector("#" + e2), g2 = ((x2 = t4).getBoundingClientRect().x - s.getBoundingClientRect().x) * u + r, _2 = (x2.getBoundingClientRect().y - s.getBoundingClientRect().y) * p + r, w2 = (L = f2.querySelectorAll("." + t4.parentElement.classList[3])[0]).offsetWidth / 2 + (L.getBoundingClientRect().x - s.getBoundingClientRect().x) * u, v2 = L.offsetHeight / 2 + (L.getBoundingClientRect().y - s.getBoundingClientRect().y) * p, y2 = l(w2, v2, g2, _2, d, "open");
                      o2 += y2, m3.push(y2);
                      f2 = t4, g2 = ((x2 = n4[a2 + 1]).getBoundingClientRect().x - s.getBoundingClientRect().x) * u + r, _2 = (x2.getBoundingClientRect().y - s.getBoundingClientRect().y) * p + r, w2 = (f2.getBoundingClientRect().x - s.getBoundingClientRect().x) * u + r, v2 = (f2.getBoundingClientRect().y - s.getBoundingClientRect().y) * p + r, y2 = l(w2, v2, g2, _2, c, "other");
                      o2 += y2, m3.push(y2);
                    } else if (a2 === n4.length - 1) {
                      var E, R;
                      f2 = t4, C2 = t4.parentElement.classList[1].replace("node_in_", ""), x2 = (E = i2.querySelector("#" + C2)).querySelectorAll("." + t4.parentElement.classList[4])[0], g2 = (R = E.querySelectorAll("." + t4.parentElement.classList[4])[0]).offsetWidth / 2 + (R.getBoundingClientRect().x - s.getBoundingClientRect().x) * u, _2 = R.offsetHeight / 2 + (R.getBoundingClientRect().y - s.getBoundingClientRect().y) * p, w2 = (f2.getBoundingClientRect().x - s.getBoundingClientRect().x) * (s.clientWidth / (s.clientWidth * h)) + r, v2 = (f2.getBoundingClientRect().y - s.getBoundingClientRect().y) * (s.clientHeight / (s.clientHeight * h)) + r, y2 = l(w2, v2, g2, _2, d, "close");
                      o2 += y2, m3.push(y2);
                    } else {
                      f2 = t4, g2 = ((x2 = n4[a2 + 1]).getBoundingClientRect().x - s.getBoundingClientRect().x) * (s.clientWidth / (s.clientWidth * h)) + r, _2 = (x2.getBoundingClientRect().y - s.getBoundingClientRect().y) * (s.clientHeight / (s.clientHeight * h)) + r, w2 = (f2.getBoundingClientRect().x - s.getBoundingClientRect().x) * (s.clientWidth / (s.clientWidth * h)) + r, v2 = (f2.getBoundingClientRect().y - s.getBoundingClientRect().y) * (s.clientHeight / (s.clientHeight * h)) + r, y2 = l(w2, v2, g2, _2, c, "other");
                      o2 += y2, m3.push(y2);
                    }
                  }), a ? m3.forEach((e3, n5) => {
                    f[t3].children[n5].setAttributeNS(null, "d", e3);
                  }) : f[t3].children[0].setAttributeNS(null, "d", o2);
                }
              }));
              const m = i2.querySelectorAll("." + t2);
              Object.keys(m).map((function(t3, n3) {
                if (null === m[t3].querySelector(".point")) {
                  var h2 = i2.querySelector("#" + e2), f2 = m[t3].classList[2].replace("node_out_", ""), g = i2.querySelector("#" + f2).querySelectorAll("." + m[t3].classList[3])[0], _ = g.offsetWidth / 2 + (g.getBoundingClientRect().x - s.getBoundingClientRect().x) * u, w = g.offsetHeight / 2 + (g.getBoundingClientRect().y - s.getBoundingClientRect().y) * p, v = (h2 = h2.querySelectorAll("." + m[t3].classList[4])[0]).offsetWidth / 2 + (h2.getBoundingClientRect().x - s.getBoundingClientRect().x) * u, y = h2.offsetHeight / 2 + (h2.getBoundingClientRect().y - s.getBoundingClientRect().y) * p;
                  const n4 = l(_, w, v, y, o, "openclose");
                  m[t3].children[0].setAttributeNS(null, "d", n4);
                } else {
                  const n4 = m[t3].querySelectorAll(".point");
                  let o2 = "";
                  const h3 = [];
                  n4.forEach((t4, a2) => {
                    if (0 === a2 && n4.length - 1 == 0) {
                      var f3 = i2.querySelector("#" + e2), m2 = ((C = t4).getBoundingClientRect().x - s.getBoundingClientRect().x) * u + r, g2 = (C.getBoundingClientRect().y - s.getBoundingClientRect().y) * p + r, _2 = (E = f3.querySelectorAll("." + t4.parentElement.classList[4])[0]).offsetWidth / 2 + (E.getBoundingClientRect().x - s.getBoundingClientRect().x) * u, w2 = E.offsetHeight / 2 + (E.getBoundingClientRect().y - s.getBoundingClientRect().y) * p, v2 = l(m2, g2, _2, w2, d, "close");
                      o2 += v2, h3.push(v2);
                      f3 = t4;
                      var y2 = t4.parentElement.classList[2].replace("node_out_", ""), C = (L = i2.querySelector("#" + y2)).querySelectorAll("." + t4.parentElement.classList[3])[0];
                      m2 = (x = L.querySelectorAll("." + t4.parentElement.classList[3])[0]).offsetWidth / 2 + (x.getBoundingClientRect().x - s.getBoundingClientRect().x) * u, g2 = x.offsetHeight / 2 + (x.getBoundingClientRect().y - s.getBoundingClientRect().y) * p, _2 = (f3.getBoundingClientRect().x - s.getBoundingClientRect().x) * u + r, w2 = (f3.getBoundingClientRect().y - s.getBoundingClientRect().y) * p + r, v2 = l(m2, g2, _2, w2, d, "open");
                      o2 += v2, h3.push(v2);
                    } else if (0 === a2) {
                      var x;
                      f3 = t4, y2 = t4.parentElement.classList[2].replace("node_out_", ""), C = (L = i2.querySelector("#" + y2)).querySelectorAll("." + t4.parentElement.classList[3])[0], m2 = (x = L.querySelectorAll("." + t4.parentElement.classList[3])[0]).offsetWidth / 2 + (x.getBoundingClientRect().x - s.getBoundingClientRect().x) * u, g2 = x.offsetHeight / 2 + (x.getBoundingClientRect().y - s.getBoundingClientRect().y) * p, _2 = (f3.getBoundingClientRect().x - s.getBoundingClientRect().x) * u + r, w2 = (f3.getBoundingClientRect().y - s.getBoundingClientRect().y) * p + r, v2 = l(m2, g2, _2, w2, d, "open");
                      o2 += v2, h3.push(v2);
                      f3 = t4, _2 = ((C = n4[a2 + 1]).getBoundingClientRect().x - s.getBoundingClientRect().x) * u + r, w2 = (C.getBoundingClientRect().y - s.getBoundingClientRect().y) * p + r, m2 = (f3.getBoundingClientRect().x - s.getBoundingClientRect().x) * u + r, g2 = (f3.getBoundingClientRect().y - s.getBoundingClientRect().y) * p + r, v2 = l(m2, g2, _2, w2, c, "other");
                      o2 += v2, h3.push(v2);
                    } else if (a2 === n4.length - 1) {
                      var L, E;
                      f3 = t4, y2 = t4.parentElement.classList[1].replace("node_in_", ""), C = (L = i2.querySelector("#" + y2)).querySelectorAll("." + t4.parentElement.classList[4])[0], _2 = (E = L.querySelectorAll("." + t4.parentElement.classList[4])[0]).offsetWidth / 2 + (E.getBoundingClientRect().x - s.getBoundingClientRect().x) * u, w2 = E.offsetHeight / 2 + (E.getBoundingClientRect().y - s.getBoundingClientRect().y) * p, m2 = (f3.getBoundingClientRect().x - s.getBoundingClientRect().x) * u + r, g2 = (f3.getBoundingClientRect().y - s.getBoundingClientRect().y) * p + r, v2 = l(m2, g2, _2, w2, d, "close");
                      o2 += v2, h3.push(v2);
                    } else {
                      f3 = t4, _2 = ((C = n4[a2 + 1]).getBoundingClientRect().x - s.getBoundingClientRect().x) * u + r, w2 = (C.getBoundingClientRect().y - s.getBoundingClientRect().y) * p + r, m2 = (f3.getBoundingClientRect().x - s.getBoundingClientRect().x) * u + r, g2 = (f3.getBoundingClientRect().y - s.getBoundingClientRect().y) * p + r, v2 = l(m2, g2, _2, w2, c, "other");
                      o2 += v2, h3.push(v2);
                    }
                  }), a ? h3.forEach((e3, n5) => {
                    m[t3].children[n5].setAttributeNS(null, "d", e3);
                  }) : m[t3].children[0].setAttributeNS(null, "d", o2);
                }
              }));
            }
            dblclick(e2) {
              null != this.connection_selected && this.reroute && this.createReroutePoint(this.connection_selected), "point" === e2.target.classList[0] && this.removeReroutePoint(e2.target);
            }
            createReroutePoint(e2) {
              this.connection_selected.classList.remove("selected");
              const t2 = this.connection_selected.parentElement.classList[2].slice(9), n2 = this.connection_selected.parentElement.classList[1].slice(13), i2 = this.connection_selected.parentElement.classList[3], s = this.connection_selected.parentElement.classList[4];
              this.connection_selected = null;
              const o = document.createElementNS("http://www.w3.org/2000/svg", "circle");
              o.classList.add("point");
              var l = this.pos_x * (this.precanvas.clientWidth / (this.precanvas.clientWidth * this.zoom)) - this.precanvas.getBoundingClientRect().x * (this.precanvas.clientWidth / (this.precanvas.clientWidth * this.zoom)), c = this.pos_y * (this.precanvas.clientHeight / (this.precanvas.clientHeight * this.zoom)) - this.precanvas.getBoundingClientRect().y * (this.precanvas.clientHeight / (this.precanvas.clientHeight * this.zoom));
              o.setAttributeNS(null, "cx", l), o.setAttributeNS(null, "cy", c), o.setAttributeNS(null, "r", this.reroute_width);
              let d = 0;
              if (this.reroute_fix_curvature) {
                const t3 = e2.parentElement.querySelectorAll(".main-path").length;
                var a = document.createElementNS("http://www.w3.org/2000/svg", "path");
                if (a.classList.add("main-path"), a.setAttributeNS(null, "d", ""), e2.parentElement.insertBefore(a, e2.parentElement.children[t3]), 1 === t3) e2.parentElement.appendChild(o);
                else {
                  const n3 = Array.from(e2.parentElement.children).indexOf(e2);
                  d = n3, e2.parentElement.insertBefore(o, e2.parentElement.children[n3 + t3 + 1]);
                }
              } else e2.parentElement.appendChild(o);
              const r = t2.slice(5), h = this.drawflow.drawflow[this.module].data[r].outputs[i2].connections.findIndex((function(e3, t3) {
                return e3.node === n2 && e3.output === s;
              }));
              void 0 === this.drawflow.drawflow[this.module].data[r].outputs[i2].connections[h].points && (this.drawflow.drawflow[this.module].data[r].outputs[i2].connections[h].points = []), this.reroute_fix_curvature ? (d > 0 || this.drawflow.drawflow[this.module].data[r].outputs[i2].connections[h].points !== [] ? this.drawflow.drawflow[this.module].data[r].outputs[i2].connections[h].points.splice(d, 0, { pos_x: l, pos_y: c }) : this.drawflow.drawflow[this.module].data[r].outputs[i2].connections[h].points.push({ pos_x: l, pos_y: c }), e2.parentElement.querySelectorAll(".main-path").forEach((e3, t3) => {
                e3.classList.remove("selected");
              })) : this.drawflow.drawflow[this.module].data[r].outputs[i2].connections[h].points.push({ pos_x: l, pos_y: c }), this.dispatch("addReroute", r), this.updateConnectionNodes(t2);
            }
            removeReroutePoint(e2) {
              const t2 = e2.parentElement.classList[2].slice(9), n2 = e2.parentElement.classList[1].slice(13), i2 = e2.parentElement.classList[3], s = e2.parentElement.classList[4];
              let o = Array.from(e2.parentElement.children).indexOf(e2);
              const l = t2.slice(5), c = this.drawflow.drawflow[this.module].data[l].outputs[i2].connections.findIndex((function(e3, t3) {
                return e3.node === n2 && e3.output === s;
              }));
              if (this.reroute_fix_curvature) {
                const t3 = e2.parentElement.querySelectorAll(".main-path").length;
                e2.parentElement.children[t3 - 1].remove(), o -= t3, o < 0 && (o = 0);
              } else o--;
              this.drawflow.drawflow[this.module].data[l].outputs[i2].connections[c].points.splice(o, 1), e2.remove(), this.dispatch("removeReroute", l), this.updateConnectionNodes(t2);
            }
            registerNode(e2, t2, n2 = null, i2 = null) {
              this.noderegister[e2] = { html: t2, props: n2, options: i2 };
            }
            getNodeFromId(e2) {
              var t2 = this.getModuleFromNodeId(e2);
              return JSON.parse(JSON.stringify(this.drawflow.drawflow[t2].data[e2]));
            }
            getNodesFromName(e2) {
              var t2 = [];
              const n2 = this.drawflow.drawflow;
              return Object.keys(n2).map((function(i2, s) {
                for (var o in n2[i2].data) n2[i2].data[o].name == e2 && t2.push(n2[i2].data[o].id);
              })), t2;
            }
            addNode(e2, t2, n2, i2, s, o, l, c, d = false) {
              if (this.useuuid) var a = this.getUuid();
              else a = this.nodeId;
              const r = document.createElement("div");
              r.classList.add("parent-node");
              const h = document.createElement("div");
              h.innerHTML = "", h.setAttribute("id", "node-" + a), h.classList.add("drawflow-node"), "" != o && h.classList.add(...o.split(" "));
              const u = document.createElement("div");
              u.classList.add("inputs");
              const p = document.createElement("div");
              p.classList.add("outputs");
              const f = {};
              for (var m = 0; m < t2; m++) {
                const e3 = document.createElement("div");
                e3.classList.add("input"), e3.classList.add("input_" + (m + 1)), f["input_" + (m + 1)] = { connections: [] }, u.appendChild(e3);
              }
              const g = {};
              for (m = 0; m < n2; m++) {
                const e3 = document.createElement("div");
                e3.classList.add("output"), e3.classList.add("output_" + (m + 1)), g["output_" + (m + 1)] = { connections: [] }, p.appendChild(e3);
              }
              const _ = document.createElement("div");
              if (_.classList.add("drawflow_content_node"), false === d) _.innerHTML = c;
              else if (true === d) _.appendChild(this.noderegister[c].html.cloneNode(true));
              else if (3 === parseInt(this.render.version)) {
                let e3 = this.render.h(this.noderegister[c].html, this.noderegister[c].props, this.noderegister[c].options);
                e3.appContext = this.parent, this.render.render(e3, _);
              } else {
                let e3 = new this.render({ parent: this.parent, render: (e4) => e4(this.noderegister[c].html, { props: this.noderegister[c].props }), ...this.noderegister[c].options }).$mount();
                _.appendChild(e3.$el);
              }
              Object.entries(l).forEach((function(e3, t3) {
                if ("object" == typeof e3[1]) !(function e4(t4, n4, i4) {
                  if (null === t4) t4 = l[n4];
                  else t4 = t4[n4];
                  null !== t4 && Object.entries(t4).forEach((function(n5, s2) {
                    if ("object" == typeof n5[1]) e4(t4, n5[0], i4 + "-" + n5[0]);
                    else for (var o2 = _.querySelectorAll("[df-" + i4 + "-" + n5[0] + "]"), l2 = 0; l2 < o2.length; l2++) o2[l2].value = n5[1], o2[l2].isContentEditable && (o2[l2].innerText = n5[1]);
                  }));
                })(null, e3[0], e3[0]);
                else for (var n3 = _.querySelectorAll("[df-" + e3[0] + "]"), i3 = 0; i3 < n3.length; i3++) n3[i3].value = e3[1], n3[i3].isContentEditable && (n3[i3].innerText = e3[1]);
              })), h.appendChild(u), h.appendChild(_), h.appendChild(p), h.style.top = s + "px", h.style.left = i2 + "px", r.appendChild(h), this.precanvas.appendChild(r);
              var w = { id: a, name: e2, data: l, class: o, html: c, typenode: d, inputs: f, outputs: g, pos_x: i2, pos_y: s };
              return this.drawflow.drawflow[this.module].data[a] = w, this.dispatch("nodeCreated", a), this.useuuid || this.nodeId++, a;
            }
            addNodeImport(e2, t2) {
              const n2 = document.createElement("div");
              n2.classList.add("parent-node");
              const i2 = document.createElement("div");
              i2.innerHTML = "", i2.setAttribute("id", "node-" + e2.id), i2.classList.add("drawflow-node"), "" != e2.class && i2.classList.add(...e2.class.split(" "));
              const s = document.createElement("div");
              s.classList.add("inputs");
              const o = document.createElement("div");
              o.classList.add("outputs"), Object.keys(e2.inputs).map((function(n3, i3) {
                const o2 = document.createElement("div");
                o2.classList.add("input"), o2.classList.add(n3), s.appendChild(o2), Object.keys(e2.inputs[n3].connections).map((function(i4, s2) {
                  var o3 = document.createElementNS("http://www.w3.org/2000/svg", "svg"), l2 = document.createElementNS("http://www.w3.org/2000/svg", "path");
                  l2.classList.add("main-path"), l2.setAttributeNS(null, "d", ""), o3.classList.add("connection"), o3.classList.add("node_in_node-" + e2.id), o3.classList.add("node_out_node-" + e2.inputs[n3].connections[i4].node), o3.classList.add(e2.inputs[n3].connections[i4].input), o3.classList.add(n3), o3.appendChild(l2), t2.appendChild(o3);
                }));
              }));
              for (var l = 0; l < Object.keys(e2.outputs).length; l++) {
                const e3 = document.createElement("div");
                e3.classList.add("output"), e3.classList.add("output_" + (l + 1)), o.appendChild(e3);
              }
              const c = document.createElement("div");
              if (c.classList.add("drawflow_content_node"), false === e2.typenode) c.innerHTML = e2.html;
              else if (true === e2.typenode) c.appendChild(this.noderegister[e2.html].html.cloneNode(true));
              else if (3 === parseInt(this.render.version)) {
                let t3 = this.render.h(this.noderegister[e2.html].html, this.noderegister[e2.html].props, this.noderegister[e2.html].options);
                t3.appContext = this.parent, this.render.render(t3, c);
              } else {
                let t3 = new this.render({ parent: this.parent, render: (t4) => t4(this.noderegister[e2.html].html, { props: this.noderegister[e2.html].props }), ...this.noderegister[e2.html].options }).$mount();
                c.appendChild(t3.$el);
              }
              Object.entries(e2.data).forEach((function(t3, n3) {
                if ("object" == typeof t3[1]) !(function t4(n4, i4, s3) {
                  if (null === n4) n4 = e2.data[i4];
                  else n4 = n4[i4];
                  null !== n4 && Object.entries(n4).forEach((function(e3, i5) {
                    if ("object" == typeof e3[1]) t4(n4, e3[0], s3 + "-" + e3[0]);
                    else for (var o2 = c.querySelectorAll("[df-" + s3 + "-" + e3[0] + "]"), l2 = 0; l2 < o2.length; l2++) o2[l2].value = e3[1], o2[l2].isContentEditable && (o2[l2].innerText = e3[1]);
                  }));
                })(null, t3[0], t3[0]);
                else for (var i3 = c.querySelectorAll("[df-" + t3[0] + "]"), s2 = 0; s2 < i3.length; s2++) i3[s2].value = t3[1], i3[s2].isContentEditable && (i3[s2].innerText = t3[1]);
              })), i2.appendChild(s), i2.appendChild(c), i2.appendChild(o), i2.style.top = e2.pos_y + "px", i2.style.left = e2.pos_x + "px", n2.appendChild(i2), this.precanvas.appendChild(n2);
            }
            addRerouteImport(e2) {
              const t2 = this.reroute_width, n2 = this.reroute_fix_curvature, i2 = this.container;
              Object.keys(e2.outputs).map((function(s, o) {
                Object.keys(e2.outputs[s].connections).map((function(o2, l) {
                  const c = e2.outputs[s].connections[o2].points;
                  void 0 !== c && c.forEach((l2, d) => {
                    const a = e2.outputs[s].connections[o2].node, r = e2.outputs[s].connections[o2].output, h = i2.querySelector(".connection.node_in_node-" + a + ".node_out_node-" + e2.id + "." + s + "." + r);
                    if (n2 && 0 === d) for (var u = 0; u < c.length; u++) {
                      var p = document.createElementNS("http://www.w3.org/2000/svg", "path");
                      p.classList.add("main-path"), p.setAttributeNS(null, "d", ""), h.appendChild(p);
                    }
                    const f = document.createElementNS("http://www.w3.org/2000/svg", "circle");
                    f.classList.add("point");
                    var m = l2.pos_x, g = l2.pos_y;
                    f.setAttributeNS(null, "cx", m), f.setAttributeNS(null, "cy", g), f.setAttributeNS(null, "r", t2), h.appendChild(f);
                  });
                }));
              }));
            }
            updateNodeValue(e2) {
              for (var t2 = e2.target.attributes, n2 = 0; n2 < t2.length; n2++) if (t2[n2].nodeName.startsWith("df-")) {
                for (var i2 = t2[n2].nodeName.slice(3).split("-"), s = this.drawflow.drawflow[this.module].data[e2.target.closest(".drawflow_content_node").parentElement.id.slice(5)].data, o = 0; o < i2.length - 1; o += 1) null == s[i2[o]] && (s[i2[o]] = {}), s = s[i2[o]];
                s[i2[i2.length - 1]] = e2.target.value, e2.target.isContentEditable && (s[i2[i2.length - 1]] = e2.target.innerText), this.dispatch("nodeDataChanged", e2.target.closest(".drawflow_content_node").parentElement.id.slice(5));
              }
            }
            updateNodeDataFromId(e2, t2) {
              var n2 = this.getModuleFromNodeId(e2);
              if (this.drawflow.drawflow[n2].data[e2].data = t2, this.module === n2) {
                const n3 = this.container.querySelector("#node-" + e2);
                Object.entries(t2).forEach((function(e3, i2) {
                  if ("object" == typeof e3[1]) !(function e4(i3, s2, o2) {
                    if (null === i3) i3 = t2[s2];
                    else i3 = i3[s2];
                    null !== i3 && Object.entries(i3).forEach((function(t3, s3) {
                      if ("object" == typeof t3[1]) e4(i3, t3[0], o2 + "-" + t3[0]);
                      else for (var l = n3.querySelectorAll("[df-" + o2 + "-" + t3[0] + "]"), c = 0; c < l.length; c++) l[c].value = t3[1], l[c].isContentEditable && (l[c].innerText = t3[1]);
                    }));
                  })(null, e3[0], e3[0]);
                  else for (var s = n3.querySelectorAll("[df-" + e3[0] + "]"), o = 0; o < s.length; o++) s[o].value = e3[1], s[o].isContentEditable && (s[o].innerText = e3[1]);
                }));
              }
            }
            addNodeInput(e2) {
              var t2 = this.getModuleFromNodeId(e2);
              const n2 = this.getNodeFromId(e2), i2 = Object.keys(n2.inputs).length;
              if (this.module === t2) {
                const t3 = document.createElement("div");
                t3.classList.add("input"), t3.classList.add("input_" + (i2 + 1));
                this.container.querySelector("#node-" + e2 + " .inputs").appendChild(t3), this.updateConnectionNodes("node-" + e2);
              }
              this.drawflow.drawflow[t2].data[e2].inputs["input_" + (i2 + 1)] = { connections: [] };
            }
            addNodeOutput(e2) {
              var t2 = this.getModuleFromNodeId(e2);
              const n2 = this.getNodeFromId(e2), i2 = Object.keys(n2.outputs).length;
              if (this.module === t2) {
                const t3 = document.createElement("div");
                t3.classList.add("output"), t3.classList.add("output_" + (i2 + 1));
                this.container.querySelector("#node-" + e2 + " .outputs").appendChild(t3), this.updateConnectionNodes("node-" + e2);
              }
              this.drawflow.drawflow[t2].data[e2].outputs["output_" + (i2 + 1)] = { connections: [] };
            }
            removeNodeInput(e2, t2) {
              var n2 = this.getModuleFromNodeId(e2);
              const i2 = this.getNodeFromId(e2);
              this.module === n2 && this.container.querySelector("#node-" + e2 + " .inputs .input." + t2).remove();
              const s = [];
              Object.keys(i2.inputs[t2].connections).map((function(n3, o2) {
                const l2 = i2.inputs[t2].connections[o2].node, c2 = i2.inputs[t2].connections[o2].input;
                s.push({ id_output: l2, id: e2, output_class: c2, input_class: t2 });
              })), s.forEach((e3, t3) => {
                this.removeSingleConnection(e3.id_output, e3.id, e3.output_class, e3.input_class);
              }), delete this.drawflow.drawflow[n2].data[e2].inputs[t2];
              const o = [], l = this.drawflow.drawflow[n2].data[e2].inputs;
              Object.keys(l).map((function(e3, t3) {
                o.push(l[e3]);
              })), this.drawflow.drawflow[n2].data[e2].inputs = {};
              const c = t2.slice(6);
              let d = [];
              if (o.forEach((t3, i3) => {
                t3.connections.forEach((e3, t4) => {
                  d.push(e3);
                }), this.drawflow.drawflow[n2].data[e2].inputs["input_" + (i3 + 1)] = t3;
              }), d = new Set(d.map((e3) => JSON.stringify(e3))), d = Array.from(d).map((e3) => JSON.parse(e3)), this.module === n2) {
                this.container.querySelectorAll("#node-" + e2 + " .inputs .input").forEach((e3, t3) => {
                  const n3 = e3.classList[1].slice(6);
                  parseInt(c) < parseInt(n3) && (e3.classList.remove("input_" + n3), e3.classList.add("input_" + (n3 - 1)));
                });
              }
              d.forEach((t3, i3) => {
                this.drawflow.drawflow[n2].data[t3.node].outputs[t3.input].connections.forEach((i4, s2) => {
                  if (i4.node == e2) {
                    const o2 = i4.output.slice(6);
                    if (parseInt(c) < parseInt(o2)) {
                      if (this.module === n2) {
                        const n3 = this.container.querySelector(".connection.node_in_node-" + e2 + ".node_out_node-" + t3.node + "." + t3.input + ".input_" + o2);
                        n3.classList.remove("input_" + o2), n3.classList.add("input_" + (o2 - 1));
                      }
                      i4.points ? this.drawflow.drawflow[n2].data[t3.node].outputs[t3.input].connections[s2] = { node: i4.node, output: "input_" + (o2 - 1), points: i4.points } : this.drawflow.drawflow[n2].data[t3.node].outputs[t3.input].connections[s2] = { node: i4.node, output: "input_" + (o2 - 1) };
                    }
                  }
                });
              }), this.updateConnectionNodes("node-" + e2);
            }
            removeNodeOutput(e2, t2) {
              var n2 = this.getModuleFromNodeId(e2);
              const i2 = this.getNodeFromId(e2);
              this.module === n2 && this.container.querySelector("#node-" + e2 + " .outputs .output." + t2).remove();
              const s = [];
              Object.keys(i2.outputs[t2].connections).map((function(n3, o2) {
                const l2 = i2.outputs[t2].connections[o2].node, c2 = i2.outputs[t2].connections[o2].output;
                s.push({ id: e2, id_input: l2, output_class: t2, input_class: c2 });
              })), s.forEach((e3, t3) => {
                this.removeSingleConnection(e3.id, e3.id_input, e3.output_class, e3.input_class);
              }), delete this.drawflow.drawflow[n2].data[e2].outputs[t2];
              const o = [], l = this.drawflow.drawflow[n2].data[e2].outputs;
              Object.keys(l).map((function(e3, t3) {
                o.push(l[e3]);
              })), this.drawflow.drawflow[n2].data[e2].outputs = {};
              const c = t2.slice(7);
              let d = [];
              if (o.forEach((t3, i3) => {
                t3.connections.forEach((e3, t4) => {
                  d.push({ node: e3.node, output: e3.output });
                }), this.drawflow.drawflow[n2].data[e2].outputs["output_" + (i3 + 1)] = t3;
              }), d = new Set(d.map((e3) => JSON.stringify(e3))), d = Array.from(d).map((e3) => JSON.parse(e3)), this.module === n2) {
                this.container.querySelectorAll("#node-" + e2 + " .outputs .output").forEach((e3, t3) => {
                  const n3 = e3.classList[1].slice(7);
                  parseInt(c) < parseInt(n3) && (e3.classList.remove("output_" + n3), e3.classList.add("output_" + (n3 - 1)));
                });
              }
              d.forEach((t3, i3) => {
                this.drawflow.drawflow[n2].data[t3.node].inputs[t3.output].connections.forEach((i4, s2) => {
                  if (i4.node == e2) {
                    const o2 = i4.input.slice(7);
                    if (parseInt(c) < parseInt(o2)) {
                      if (this.module === n2) {
                        const n3 = this.container.querySelector(".connection.node_in_node-" + t3.node + ".node_out_node-" + e2 + ".output_" + o2 + "." + t3.output);
                        n3.classList.remove("output_" + o2), n3.classList.remove(t3.output), n3.classList.add("output_" + (o2 - 1)), n3.classList.add(t3.output);
                      }
                      i4.points ? this.drawflow.drawflow[n2].data[t3.node].inputs[t3.output].connections[s2] = { node: i4.node, input: "output_" + (o2 - 1), points: i4.points } : this.drawflow.drawflow[n2].data[t3.node].inputs[t3.output].connections[s2] = { node: i4.node, input: "output_" + (o2 - 1) };
                    }
                  }
                });
              }), this.updateConnectionNodes("node-" + e2);
            }
            removeNodeId(e2) {
              this.removeConnectionNodeId(e2);
              var t2 = this.getModuleFromNodeId(e2.slice(5));
              this.module === t2 && this.container.querySelector("#" + e2).remove(), delete this.drawflow.drawflow[t2].data[e2.slice(5)], this.dispatch("nodeRemoved", e2.slice(5));
            }
            removeConnection() {
              if (null != this.connection_selected) {
                var e2 = this.connection_selected.parentElement.classList;
                this.connection_selected.parentElement.remove();
                var t2 = this.drawflow.drawflow[this.module].data[e2[2].slice(14)].outputs[e2[3]].connections.findIndex((function(t3, n3) {
                  return t3.node === e2[1].slice(13) && t3.output === e2[4];
                }));
                this.drawflow.drawflow[this.module].data[e2[2].slice(14)].outputs[e2[3]].connections.splice(t2, 1);
                var n2 = this.drawflow.drawflow[this.module].data[e2[1].slice(13)].inputs[e2[4]].connections.findIndex((function(t3, n3) {
                  return t3.node === e2[2].slice(14) && t3.input === e2[3];
                }));
                this.drawflow.drawflow[this.module].data[e2[1].slice(13)].inputs[e2[4]].connections.splice(n2, 1), this.dispatch("connectionRemoved", { output_id: e2[2].slice(14), input_id: e2[1].slice(13), output_class: e2[3], input_class: e2[4] }), this.connection_selected = null;
              }
            }
            removeSingleConnection(e2, t2, n2, i2) {
              var s = this.getModuleFromNodeId(e2);
              if (s === this.getModuleFromNodeId(t2)) {
                if (this.drawflow.drawflow[s].data[e2].outputs[n2].connections.findIndex((function(e3, n3) {
                  return e3.node == t2 && e3.output === i2;
                })) > -1) {
                  this.module === s && this.container.querySelector(".connection.node_in_node-" + t2 + ".node_out_node-" + e2 + "." + n2 + "." + i2).remove();
                  var o = this.drawflow.drawflow[s].data[e2].outputs[n2].connections.findIndex((function(e3, n3) {
                    return e3.node == t2 && e3.output === i2;
                  }));
                  this.drawflow.drawflow[s].data[e2].outputs[n2].connections.splice(o, 1);
                  var l = this.drawflow.drawflow[s].data[t2].inputs[i2].connections.findIndex((function(t3, i3) {
                    return t3.node == e2 && t3.input === n2;
                  }));
                  return this.drawflow.drawflow[s].data[t2].inputs[i2].connections.splice(l, 1), this.dispatch("connectionRemoved", { output_id: e2, input_id: t2, output_class: n2, input_class: i2 }), true;
                }
                return false;
              }
              return false;
            }
            removeConnectionNodeId(e2) {
              const t2 = "node_in_" + e2, n2 = "node_out_" + e2, i2 = this.container.querySelectorAll("." + n2);
              for (var s = i2.length - 1; s >= 0; s--) {
                var o = i2[s].classList, l = this.drawflow.drawflow[this.module].data[o[1].slice(13)].inputs[o[4]].connections.findIndex((function(e3, t3) {
                  return e3.node === o[2].slice(14) && e3.input === o[3];
                }));
                this.drawflow.drawflow[this.module].data[o[1].slice(13)].inputs[o[4]].connections.splice(l, 1);
                var c = this.drawflow.drawflow[this.module].data[o[2].slice(14)].outputs[o[3]].connections.findIndex((function(e3, t3) {
                  return e3.node === o[1].slice(13) && e3.output === o[4];
                }));
                this.drawflow.drawflow[this.module].data[o[2].slice(14)].outputs[o[3]].connections.splice(c, 1), i2[s].remove(), this.dispatch("connectionRemoved", { output_id: o[2].slice(14), input_id: o[1].slice(13), output_class: o[3], input_class: o[4] });
              }
              const d = this.container.querySelectorAll("." + t2);
              for (s = d.length - 1; s >= 0; s--) {
                o = d[s].classList, c = this.drawflow.drawflow[this.module].data[o[2].slice(14)].outputs[o[3]].connections.findIndex((function(e3, t3) {
                  return e3.node === o[1].slice(13) && e3.output === o[4];
                }));
                this.drawflow.drawflow[this.module].data[o[2].slice(14)].outputs[o[3]].connections.splice(c, 1);
                l = this.drawflow.drawflow[this.module].data[o[1].slice(13)].inputs[o[4]].connections.findIndex((function(e3, t3) {
                  return e3.node === o[2].slice(14) && e3.input === o[3];
                }));
                this.drawflow.drawflow[this.module].data[o[1].slice(13)].inputs[o[4]].connections.splice(l, 1), d[s].remove(), this.dispatch("connectionRemoved", { output_id: o[2].slice(14), input_id: o[1].slice(13), output_class: o[3], input_class: o[4] });
              }
            }
            getModuleFromNodeId(e2) {
              var t2;
              const n2 = this.drawflow.drawflow;
              return Object.keys(n2).map((function(i2, s) {
                Object.keys(n2[i2].data).map((function(n3, s2) {
                  n3 == e2 && (t2 = i2);
                }));
              })), t2;
            }
            addModule(e2) {
              this.drawflow.drawflow[e2] = { data: {} }, this.dispatch("moduleCreated", e2);
            }
            changeModule(e2) {
              this.dispatch("moduleChanged", e2), this.module = e2, this.precanvas.innerHTML = "", this.canvas_x = 0, this.canvas_y = 0, this.pos_x = 0, this.pos_y = 0, this.mouse_x = 0, this.mouse_y = 0, this.zoom = 1, this.zoom_last_value = 1, this.precanvas.style.transform = "", this.import(this.drawflow, false);
            }
            removeModule(e2) {
              this.module === e2 && this.changeModule("Home"), delete this.drawflow.drawflow[e2], this.dispatch("moduleRemoved", e2);
            }
            clearModuleSelected() {
              this.precanvas.innerHTML = "", this.drawflow.drawflow[this.module] = { data: {} };
            }
            clear() {
              this.precanvas.innerHTML = "", this.drawflow = { drawflow: { Home: { data: {} } } };
            }
            export() {
              const e2 = JSON.parse(JSON.stringify(this.drawflow));
              return this.dispatch("export", e2), e2;
            }
            import(e2, t2 = true) {
              this.clear(), this.drawflow = JSON.parse(JSON.stringify(e2)), this.load(), t2 && this.dispatch("import", "import");
            }
            on(e2, t2) {
              return "function" != typeof t2 ? (console.error("The listener callback must be a function, the given type is " + typeof t2), false) : "string" != typeof e2 ? (console.error("The event name must be a string, the given type is " + typeof e2), false) : (void 0 === this.events[e2] && (this.events[e2] = { listeners: [] }), void this.events[e2].listeners.push(t2));
            }
            removeListener(e2, t2) {
              if (!this.events[e2]) return false;
              const n2 = this.events[e2].listeners, i2 = n2.indexOf(t2);
              i2 > -1 && n2.splice(i2, 1);
            }
            dispatch(e2, t2) {
              if (void 0 === this.events[e2]) return false;
              this.events[e2].listeners.forEach((e3) => {
                e3(t2);
              });
            }
            getUuid() {
              for (var e2 = [], t2 = 0; t2 < 36; t2++) e2[t2] = "0123456789abcdef".substr(Math.floor(16 * Math.random()), 1);
              return e2[14] = "4", e2[19] = "0123456789abcdef".substr(3 & e2[19] | 8, 1), e2[8] = e2[13] = e2[18] = e2[23] = "-", e2.join("");
            }
          }
        }]).default;
      }));
    })(drawflow_min$1);
    return drawflow_min$1.exports;
  }
  var drawflow_minExports = requireDrawflow_min();
  const Drawflow = /* @__PURE__ */ getDefaultExportFromCjs(drawflow_minExports);
  function _levels(steps) {
    const byId = {};
    steps.forEach((s) => {
      byId[s.id] = s;
    });
    const lv = {};
    const calc = (id, seen) => {
      var _a;
      if (lv[id] != null) return lv[id];
      if (seen.has(id)) return 0;
      seen.add(id);
      const deps = (((_a = byId[id]) == null ? void 0 : _a.depends_on) || []).filter((d) => byId[d]);
      const v = deps.length ? Math.max(...deps.map((d) => calc(d, seen))) + 1 : 0;
      lv[id] = v;
      return v;
    };
    steps.forEach((s) => calc(s.id, /* @__PURE__ */ new Set()));
    return lv;
  }
  function planToExport(plan, roles) {
    const steps = plan.steps || [];
    const nodeOf = {};
    steps.forEach((s, i) => {
      nodeOf[s.id] = String(i + 1);
    });
    const lv = _levels(steps);
    const perLevel = {};
    const data = {};
    steps.forEach((s) => {
      const nid = nodeOf[s.id];
      const level = lv[s.id] || 0;
      const row = perLevel[level] || 0;
      perLevel[level] = row + 1;
      const inputs = {
        input_1: {
          connections: (s.depends_on || []).filter((d) => nodeOf[d]).map((d) => ({ node: nodeOf[d], input: "output_1" }))
        }
      };
      const outputs = {
        output_1: {
          connections: steps.filter((t) => (t.depends_on || []).includes(s.id)).map((t) => ({ node: nodeOf[t.id], output: "input_1" }))
        }
      };
      data[nid] = {
        id: Number(nid),
        name: "wfstep",
        class: "wf-node",
        typenode: false,
        data: {
          step_id: s.id,
          agent_id: s.agent_id || "",
          domain_id: s.domain_id || "",
          display: s.display || s.agent_id || "",
          task: s.task || "",
          on_fail: s.on_fail || "",
          when_json: s.when ? JSON.stringify(s.when) : ""
        },
        html: _nodeHtml(s),
        inputs,
        outputs,
        pos_x: 60 + level * 320,
        pos_y: 50 + row * 170
      };
    });
    return { drawflow: { Home: { data } } };
  }
  function graphToPlan(exp, goal) {
    const home = ((exp || {}).drawflow || {}).Home || {};
    const nodes = home.data || {};
    const idToStep = {};
    Object.values(nodes).forEach((n) => {
      idToStep[String(n.id)] = n.data && n.data.step_id || "s" + n.id;
    });
    const steps = [];
    const ordered = Object.values(nodes).sort((a, b) => a.pos_x - b.pos_x || a.pos_y - b.pos_y);
    ordered.forEach((n) => {
      const d = n.data || {};
      const depConns = ((n.inputs || {}).input_1 || {}).connections || [];
      const depends_on = depConns.map((c) => idToStep[String(c.node)]).filter(Boolean);
      let when = void 0;
      if (d.when_json) {
        try {
          when = JSON.parse(d.when_json);
        } catch {
          when = void 0;
        }
      }
      const step = {
        id: idToStep[String(n.id)],
        agent_id: d.agent_id || "",
        domain_id: d.domain_id || "",
        display: d.display || d.agent_id || "",
        task: d.task || "",
        depends_on
      };
      if (d.on_fail) step.on_fail = d.on_fail;
      if (when !== void 0) step.when = when;
      steps.push(step);
    });
    return { goal: goal || "", steps };
  }
  function _roleKey(r) {
    return (r.domain_id || "") + "|" + (r.agent_id || "");
  }
  function _roleOptions(roles) {
    return (roles || []).map((r) => `<option value="${_roleKey(r).replace(/"/g, "&quot;")}">${(r.display || r.agent_id || "").replace(/</g, "&lt;")}</option>`).join("");
  }
  function _nodeHtml(s, _roles) {
    const disp = (s.display || s.agent_id || "").replace(/</g, "&lt;");
    const task = (s.task || "").replace(/</g, "&lt;");
    const of = s.on_fail || "";
    const opt = (v, label) => `<option value="${v}"${of === v ? " selected" : ""}>${label}</option>`;
    return `<div class="wfn"><div class="wfn-role">${disp}</div><textarea class="wfn-task" df-task rows="2" placeholder="这一步做什么">${task}</textarea><select class="wfn-onfail" df-on_fail>` + opt("", "失败:跳过") + opt("retry", "失败:重试") + opt("abort", "失败:中止") + `</select></div>`;
  }
  function open(plan, roles, onSave) {
    const overlay = document.createElement("div");
    overlay.className = "wf-canvas-overlay";
    const bar = document.createElement("div");
    bar.className = "wf-canvas-bar";
    bar.innerHTML = `<span class="wf-canvas-title">🎨 工作流画布 · 拖节点连依赖</span><span class="wf-canvas-hint">左圈=入口(空心)· 右圈=出口(实心)· 从上一步的右出口拖到下一步的左入口 = 后者等前者做完;不连=起步并行 · <b>滚轮缩放</b>(节点多时缩小看全局)</span><span class="wf-canvas-actions"><span class="wf-cv-zoombar" title="滚轮也能缩放"><button class="wf-cv-zoom" data-z="out">－</button><button class="wf-cv-zoom" data-z="reset">⟲</button><button class="wf-cv-zoom" data-z="in">＋</button></span><select class="wf-cv-role" title="新一步派给哪个角色">${_roleOptions(roles)}</select><button class="wf-cv-add">+ 加一步</button><button class="wf-cv-cancel">取消</button><button class="wf-cv-save">✅ 保存</button></span>`;
    const canvas = document.createElement("div");
    canvas.className = "wf-canvas-area";
    overlay.appendChild(bar);
    overlay.appendChild(canvas);
    document.body.appendChild(overlay);
    const editor = new Drawflow(canvas);
    editor.reroute = true;
    editor.start();
    try {
      editor.import(planToExport(plan, roles));
    } catch {
    }
    canvas.addEventListener("wheel", (e) => {
      if (e.ctrlKey) return;
      e.preventDefault();
      if (e.deltaY > 0) editor.zoom_out();
      else editor.zoom_in();
    }, { passive: false });
    bar.querySelectorAll(".wf-cv-zoom").forEach((b) => {
      b.onclick = () => {
        const z = b.getAttribute("data-z");
        if (z === "in") editor.zoom_in();
        else if (z === "out") editor.zoom_out();
        else editor.zoom_reset();
      };
    });
    let _addN = (plan.steps || []).reduce(
      (m, s) => Math.max(m, parseInt((s.id || "s0").slice(1), 10) || 0),
      0
    );
    const close = () => {
      try {
        editor.clear();
      } catch {
      }
      overlay.remove();
    };
    bar.querySelector(".wf-cv-add").onclick = () => {
      _addN += 1;
      const roleSel = bar.querySelector(".wf-cv-role");
      const key = roleSel && roleSel.value || "";
      const r = roles.find((x) => _roleKey(x) === key) || roles[0] || { agent_id: "", domain_id: "", display: "角色" };
      const s = { id: "s" + _addN, agent_id: r.agent_id, domain_id: r.domain_id, display: r.display, task: "" };
      editor.addNode(
        "wfstep",
        1,
        1,
        80,
        80,
        "wf-node",
        { step_id: s.id, agent_id: r.agent_id, domain_id: r.domain_id, display: r.display, task: "", on_fail: "", when_json: "" },
        _nodeHtml(s),
        false
      );
    };
    bar.querySelector(".wf-cv-cancel").onclick = close;
    bar.querySelector(".wf-cv-save").onclick = () => {
      const newPlan = graphToPlan(editor.export(), plan.goal);
      close();
      onSave(newPlan);
    };
  }
  const KarvyWorkflowCanvas = { open, planToExport, graphToPlan };
  window.KarvyWorkflowCanvas = KarvyWorkflowCanvas;
  exports.KarvyWorkflowCanvas = KarvyWorkflowCanvas;
  exports.graphToPlan = graphToPlan;
  exports.planToExport = planToExport;
  Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
  return exports;
})({});
