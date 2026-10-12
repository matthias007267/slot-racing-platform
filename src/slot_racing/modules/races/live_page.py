"""Browser HUD. It paints one HUD scene and does not compute a race.

The page is static. It has no CDN, no external font, and no race command.
A viewport message asks the PC for a scene of that size. ``planned_go_ns`` is
not read. While a start sequence is active the lamps stay dark and the page
shows that synchronization is not available yet.

Small screens keep the same grid as the PC. The scene already says when that
grid scrolls or overflows. Text may shrink to the scene's fit floor and can
still clip. There is no separate phone layout. The best-lap column follows the
scene's ``visible`` flag; the PC may still hide it after measuring its own font.
"""

from __future__ import annotations

# The en dash is the wording requested for the unsynchronized start sequence.
START_HINT = "Startsequenz – Synchronisation noch nicht verfügbar"  # noqa: RUF001

_HUD_HTML = """<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Slot-Racing Platform</title>
<style>
  :root {
    --background: #121417; --surface: #1A1E24; --elevated: #222831;
    --border: #2E3540; --text: #E8EDF2; --text_secondary: #A8B0BA;
    --text_muted: #8B949E; --accent: #2FBF71; --warning: #E2B340;
    --error: #E15B5B; --info: #7EB6D9;
  }
  html, body { margin: 0; height: 100%; background: var(--background); color: var(--text); }
  body { font-family: sans-serif; }
  #fit { position: fixed; inset: 0; overflow: hidden; }
  #stage { position: relative; }
  .panel, .card, .housing, .lamp { box-sizing: border-box; }
  .panel {
    position: absolute; overflow: hidden; background: var(--surface);
    border: 1px solid var(--border); color: var(--text);
  }
  .panel[data-tone="ok"] { border-color: var(--accent); }
  .panel[data-tone="warn"] { border-color: var(--warning); }
  .readout, .stack { height: 100%; display: flex; flex-direction: column; min-height: 0; }
  .caption { color: var(--text_muted); font-weight: 700; padding: 4px 8px 0; }
  .value {
    flex: 1; display: flex; align-items: center; justify-content: center;
    font-weight: 700; min-height: 0; overflow: hidden; padding: 0 8px;
  }
  .card {
    position: absolute; overflow: hidden; background: var(--elevated);
    border: 1px solid var(--border); display: flex; flex-direction: column;
    justify-content: center;
  }
  .field { flex: 0 0 auto; overflow: hidden; }
  .field .cap { color: var(--text_muted); line-height: 1.1; }
  .field .val {
    font-weight: 700; line-height: 1.05; overflow: hidden;
    text-overflow: ellipsis; white-space: nowrap;
  }
  table { width: 100%; border-collapse: collapse; table-layout: fixed; }
  th, td {
    padding: 0 8px; overflow: hidden; text-overflow: ellipsis;
    white-space: nowrap; text-align: left; border-bottom: 1px solid var(--border);
  }
  th { color: var(--text_muted); font-weight: 700; }
  tr.leader td { background: var(--elevated); }
  .message, .warning { margin: 0; padding: 4px 8px; white-space: pre-wrap; overflow: hidden; }
  .warning { color: var(--error); }
  .controls { height: 100%; display: flex; flex-direction: column; gap: 8px; padding: 8px; }
  .control-row { display: flex; flex: 1; gap: 8px; min-height: 0; }
  button.control {
    flex: 1; min-width: 0; border: 1px solid var(--border); border-radius: 6px;
    background: var(--elevated); color: var(--text); font: inherit;
  }
  button.control[data-tone="primary"] { background: var(--accent); color: var(--background); }
  button.control[data-tone="danger"] { background: var(--error); color: var(--background); }
  button.control[data-tone="secondary"] { background: var(--surface); }
  button.control[data-tone="ghost"] { background: transparent; }
  button.control:disabled { opacity: 1; cursor: default; }
  #lights { position: absolute; z-index: 5; pointer-events: none; }
  .housing { position: absolute; background: var(--background); border: 1px solid var(--border); }
  .lamp {
    position: absolute; border-radius: 50%; background: #5a2424;
    border: 1px solid var(--border);
  }
  .lamp.on { background: var(--error); }
  #start-note {
    position: absolute; z-index: 6; margin: 0; text-align: center;
    background: var(--elevated); color: var(--text); padding: 4px;
  }
  #banner {
    position: fixed; inset: 0; z-index: 20; display: flex;
    align-items: center; justify-content: center; text-align: center;
    background: rgba(18, 20, 23, 0.94); padding: 1rem;
  }
  #banner[hidden] { display: none; }
  #banner p { margin: 0.3rem 0; font-size: 1.4rem; }
  #fullscreen {
    position: fixed; z-index: 30; top: 8px; right: 8px; min-width: 44px;
    min-height: 44px; border: 1px solid var(--border); border-radius: 6px;
    background: var(--elevated); color: var(--text); font: inherit;
  }
  .section { padding: 4px 8px; }
  .split { display: flex; gap: 8px; min-height: 0; }
  .split > div { flex: 1; min-width: 0; }
  .attempts { display: flex; gap: 8px; overflow: hidden; }
  select:disabled { width: 100%; color: var(--text); background: var(--surface); }
</style>
</head>
<body>
<div id="fit"><div id="stage"><p id="start-note" hidden></p></div></div>
<div id="banner" role="status">
  <div><p id="banner-title"></p><p id="banner-detail"></p></div>
</div>
<button id="fullscreen" type="button">Vollbild</button>
<script>
(function () {
  var HINT = "__START_HINT__";
  var startSynchronized = false;
  var limits = {min: 1, max: 8192};
  var fit = document.getElementById("fit");
  var stage = document.getElementById("stage");
  var banner = document.getElementById("banner");
  var bannerTitle = document.getElementById("banner-title");
  var bannerDetail = document.getElementById("banner-detail");
  var startNote = document.getElementById("start-note");
  var socket = null;
  var connection = "connecting";
  var queue = [];
  var opened = false;
  var requested = null;
  var fitted = false;
  var mismatchSeq = null;
  var lastSceneSeq = -1;
  var lastScheduleSeq = -1;
  var scheduleActive = false;
  var lastLights = null;
  var resizeTimer = 0;

  function setText(node, value) {
    var text = value == null ? "" : String(value);
    if (node.textContent !== text) node.textContent = text;
  }
  function px(value) {
    var number = Number(value);
    if (!isFinite(number)) number = 0;
    return number + "px";
  }
  function clamp(value) {
    var number = Math.round(Number(value));
    if (!isFinite(number)) number = limits.min;
    if (number < limits.min) return limits.min;
    if (number > limits.max) return limits.max;
    return number;
  }
  function ensure(parent, key, tag) {
    var children = parent.children;
    var index;
    for (index = 0; index < children.length; index += 1) {
      if (children[index].getAttribute("data-key") === key) return children[index];
    }
    var node = document.createElement(tag || "div");
    node.setAttribute("data-key", key);
    parent.appendChild(node);
    return node;
  }
  function dropMissing(parent, prefix, keep) {
    var children = [];
    var index;
    for (index = 0; index < parent.children.length; index += 1) {
      children.push(parent.children[index]);
    }
    for (index = 0; index < children.length; index += 1) {
      var key = children[index].getAttribute("data-key") || "";
      if (key.indexOf(prefix) === 0 && !keep[key]) parent.removeChild(children[index]);
    }
  }
  function roleColor(role) {
    if (role && /^[a-z0-9_]+$/.test(role)) return "var(--" + role + ")";
    return "var(--text)";
  }
  function alignOf(value) {
    if (value === "left" || value === "right" || value === "center") return value;
    return "center";
  }
  function showBanner() {
    var title = "";
    var detail = "";
    var cover = false;
    if (connection === "disconnected") {
      title = "Verbindung unterbrochen";
      cover = true;
    } else if (connection === "reconnecting") {
      title = "Verbindung unterbrochen";
      detail = "Wiederverbindung läuft";
      cover = true;
    } else if (connection === "connecting") {
      title = "Verbindung wird hergestellt";
      cover = true;
    } else if (connection === "no-race") {
      title = "Kein Rennen aktiv";
      cover = true;
    }
    banner.hidden = !cover;
    setText(bannerTitle, title);
    setText(bannerDetail, detail);
    stage.setAttribute("aria-hidden", cover ? "true" : "false");
  }
  function currentSize() {
    var view = window.visualViewport;
    return {
      width: clamp(view ? view.width : window.innerWidth),
      height: clamp(view ? view.height : window.innerHeight)
    };
  }
  function sendViewport() {
    if (!socket || socket.readyState !== 1) return;
    var size = currentSize();
    if (requested && requested.width === size.width && requested.height === size.height) return;
    requested = size;
    fitted = false;
    mismatchSeq = null;
    socket.send(JSON.stringify({
      type: "viewport", width: size.width, height: size.height
    }));
  }
  function scheduleViewport() {
    if (resizeTimer) clearTimeout(resizeTimer);
    resizeTimer = setTimeout(function () {
      resizeTimer = 0;
      sendViewport();
    }, 150);
  }
  function phaseLit(lights, index) {
    if (!startSynchronized || !lights || lights.go) return false;
    return index < (lights.lit_lights || 0);
  }
  function paintLights(lights) {
    lastLights = lights || null;
    var host = ensure(stage, "lights", "div");
    host.id = "lights";
    var visible = !!(lights && lights.visible);
    host.hidden = !visible;
    if (visible) {
      host.style.left = px(lights.x);
      host.style.top = px(lights.y);
      host.style.width = px(lights.width);
      host.style.height = px(lights.height);
      var housing = lights.housing || [0, 0, 0, 0];
      var shell = ensure(host, "housing", "div");
      shell.className = "housing";
      shell.style.left = px(housing[0]);
      shell.style.top = px(housing[1]);
      shell.style.width = px(housing[2]);
      shell.style.height = px(housing[3]);
      shell.style.borderRadius = px((housing[3] || 0) * 0.22);
      var lamps = lights.lamps || [];
      var keep = {"housing": true};
      var index;
      for (index = 0; index < lamps.length; index += 1) {
        var lamp = lamps[index];
        var key = "lamp-" + String(lamp.index);
        keep[key] = true;
        var face = ensure(host, key, "div");
        face.className = phaseLit(lights, lamp.index) ? "lamp on" : "lamp";
        face.style.left = px(lamp.x);
        face.style.top = px(lamp.y);
        face.style.width = px(lamp.size);
        face.style.height = px(lamp.size);
      }
      dropMissing(host, "lamp-", keep);
    }
    var sequence = scheduleActive || !!(lights && lights.active);
    startNote.hidden = !sequence;
    if (!sequence) {
      setText(startNote, "");
      return;
    }
    setText(startNote, HINT);
    if (visible) {
      startNote.style.left = px(lights.x);
      startNote.style.top = px((lights.y || 0) + (lights.height || 0));
      startNote.style.width = px(lights.width);
      startNote.style.right = "auto";
    } else {
      startNote.style.left = "0";
      startNote.style.right = "0";
      startNote.style.top = "0";
      startNote.style.width = "auto";
    }
  }
  function applyColors(scene) {
    var rows = scene.colors || [];
    var index;
    for (index = 0; index < rows.length; index += 1) {
      var row = rows[index];
      if (!row || row.length < 2) continue;
      var name = String(row[0]);
      var color = String(row[1]);
      if (!/^[a-z0-9_]+$/.test(name) || !/^#[0-9A-Fa-f]{3,8}$/.test(color)) continue;
      document.documentElement.style.setProperty("--" + name, color);
    }
  }
  function panelMap(scene) {
    var found = {};
    var panels = scene.panels || [];
    var index;
    for (index = 0; index < panels.length; index += 1) found[panels[index].id] = panels[index];
    return found;
  }
  function place(node, box) {
    node.hidden = !box || box.visible === false;
    if (!box || box.visible === false) return;
    node.className = "panel";
    node.style.left = px(box.x);
    node.style.top = px(box.y);
    node.style.width = px(box.width);
    node.style.height = px(box.height);
  }
  function fitBox(node, minPx, maxPx) {
    var value = node.querySelector(".value");
    if (!value) return;
    var size = maxPx || minPx || 12;
    var floor = minPx || 12;
    var guard = 0;
    value.style.fontSize = px(size);
    while (guard < 24 && size > floor &&
        (value.scrollWidth > node.clientWidth || value.scrollHeight > node.clientHeight)) {
      size -= 1;
      value.style.fontSize = px(size);
      guard += 1;
    }
  }
  function reserveFullscreen(panel) {
    var button = document.getElementById("fullscreen");
    panel.style.paddingRight = "0px";
    if (!button || panel.hidden) return;
    var buttonBox = button.getBoundingClientRect();
    var panelBox = panel.getBoundingClientRect();
    var across = Math.min(buttonBox.right, panelBox.right) -
      Math.max(buttonBox.left, panelBox.left);
    var down = Math.min(buttonBox.bottom, panelBox.bottom) -
      Math.max(buttonBox.top, panelBox.top);
    if (across > 0 && down > 0) panel.style.paddingRight = px(Math.ceil(across) + 8);
  }
  function renderReadout(panel, readout) {
    var host = ensure(panel, "readout", "div");
    host.className = "readout";
    var caption = ensure(host, "caption", "div");
    caption.className = "caption";
    var value = ensure(host, "value", "div");
    value.className = "value";
    var cap = readout && readout.caption ? readout.caption : {};
    var val = readout && readout.value ? readout.value : {};
    caption.hidden = cap.visible === false;
    setText(caption, cap.text || "");
    caption.style.fontSize = px(cap.font_px || 11);
    caption.style.textAlign = alignOf(cap.align);
    setText(value, val.text || "");
    value.style.color = roleColor(val.color_role);
    value.style.textAlign = alignOf(val.align);
    value.style.justifyContent = val.align === "left" ? "flex-start" : "center";
    fitBox(host, val.font_min_px || 12, val.font_max_px || 64);
  }
  function renderFields(card, spec) {
    var fields = spec.fields || [];
    var keep = {};
    var index;
    for (index = 0; index < fields.length; index += 1) {
      var field = fields[index];
      if (!field.visible) continue;
      var key = "field-" + String(field.id);
      keep[key] = true;
      var node = ensure(card, key, "div");
      node.className = "field";
      node.style.textAlign = alignOf(field.align);
      var cap = ensure(node, "cap", "div");
      cap.className = "cap";
      cap.hidden = !field.caption;
      setText(cap, field.caption || "");
      cap.setAttribute("data-px", String(field.caption_px || 0));
      var val = ensure(node, "val", "div");
      val.className = "val";
      setText(val, field.text || "");
      val.style.color = roleColor(field.color_role);
      val.setAttribute("data-px", String(field.font_px || 12));
    }
    dropMissing(card, "field-", keep);
    var floor = typeof spec.fit_floor === "number" ? spec.fit_floor : 0.45;
    if (floor < 0.45) floor = 0.45;
    var fontFloor = typeof spec.font_floor === "number" ? spec.font_floor : 8;
    function apply(next) {
      var nodes = card.querySelectorAll("[data-px]");
      var cursor;
      for (cursor = 0; cursor < nodes.length; cursor += 1) {
        var size = Math.round(Number(nodes[cursor].getAttribute("data-px")) * next);
        if (size < fontFloor) size = fontFloor;
        nodes[cursor].style.fontSize = px(size);
      }
    }
    card.style.gap = px(spec.line_gap || 0);
    apply(1);
    var factor = 1;
    var pass;
    for (pass = 0; pass < 3; pass += 1) {
      var needed = card.scrollHeight;
      var available = card.clientHeight;
      if (!(needed > available + 1) || available <= 0 || factor <= floor) break;
      var next = factor * (available / needed);
      if (next < floor) next = floor;
      if (!(next < factor)) break;
      factor = next;
      apply(factor);
    }
  }
  function renderLanes(panel, scene) {
    var lanes = panel && !panel.hidden ? (scene.lanes || []) : [];
    var keep = {};
    var index;
    for (index = 0; index < lanes.length; index += 1) {
      var lane = lanes[index];
      var key = "lane-" + String(lane.lane);
      keep[key] = true;
      var card = ensure(panel, key, "section");
      card.className = "card";
      card.style.left = px((lane.x || 0) - (panel._boxX || 0));
      card.style.top = px((lane.y || 0) - (panel._boxY || 0));
      card.style.width = px(lane.width);
      card.style.height = px(lane.height);
      card.style.padding = px(lane.margin || 0);
      renderFields(card, lane);
    }
    dropMissing(panel, "lane-", keep);
  }
  function renderRanking(panel, ranking) {
    if (!ranking) return;
    var columns = [];
    var all = ranking.columns || [];
    var index;
    for (index = 0; index < all.length; index += 1) {
      if (all[index].visible !== false) columns.push({column: all[index], index: index});
    }
    var signature = "";
    for (index = 0; index < columns.length; index += 1) {
      signature += columns[index].column.id + "|";
    }
    var table = ensure(panel, "table", "table");
    if (table.getAttribute("data-cols") !== signature) {
      while (table.firstChild) table.removeChild(table.firstChild);
      var head = document.createElement("thead");
      var headRow = document.createElement("tr");
      for (index = 0; index < columns.length; index += 1) {
        var th = document.createElement("th");
        setText(th, columns[index].column.header || "");
        headRow.appendChild(th);
      }
      head.appendChild(headRow);
      table.appendChild(head);
      table.appendChild(document.createElement("tbody"));
      table.setAttribute("data-cols", signature);
    }
    var body = table.querySelector("tbody");
    var rows = ranking.rows || [];
    var keep = {};
    var ordered = [];
    for (index = 0; index < rows.length; index += 1) {
      var row = rows[index];
      var key = "row-" + String(row.lane);
      keep[key] = true;
      var tr = ensure(body, key, "tr");
      tr.className = row.leader ? "leader" : "";
      tr.style.height = px(ranking.row_px || 32);
      var cells = row.cells || [];
      var column;
      for (column = 0; column < columns.length; column += 1) {
        var spec = columns[column];
        var td = ensure(tr, "cell-" + spec.column.id, "td");
        setText(td, cells[spec.index] == null ? "" : String(cells[spec.index]));
        td.style.color = roleColor(spec.column.color_role);
        var bold = spec.column.bold === "always" || (spec.column.bold === "leader" && row.leader);
        td.style.fontWeight = bold ? "700" : "400";
      }
      dropMissing(tr, "cell-", keepCells(columns));
      ordered.push(tr);
    }
    dropMissing(body, "row-", keep);
    for (index = 0; index < ordered.length; index += 1) {
      if (body.children[index] !== ordered[index]) {
        body.insertBefore(ordered[index], body.children[index] || null);
      }
    }
  }
  function keepCells(columns) {
    var keep = {};
    var index;
    for (index = 0; index < columns.length; index += 1) {
      keep["cell-" + columns[index].column.id] = true;
    }
    return keep;
  }
  function renderControls(parent, controls) {
    var host = ensure(parent, "controls", "div");
    host.className = "controls";
    var groups = [];
    var index;
    for (index = 0; index < (controls || []).length; index += 1) {
      var control = controls[index];
      var row = control.row || 0;
      while (groups.length <= row) groups.push([]);
      groups[row].push(control);
    }
    var keep = {};
    for (index = 0; index < groups.length; index += 1) {
      if (!groups[index].length) continue;
      var key = "row-" + String(index);
      keep[key] = true;
      var line = ensure(host, key, "div");
      line.className = "control-row";
      var inner = {};
      var item;
      for (item = 0; item < groups[index].length; item += 1) {
        var buttonKey = "button-" + String(groups[index][item].id);
        inner[buttonKey] = true;
        var button = ensure(line, buttonKey, "button");
        button.type = "button";
        button.className = "control";
        button.disabled = true;
        button.setAttribute("aria-disabled", "true");
        button.tabIndex = -1;
        button.setAttribute("data-tone", groups[index][item].tone || "");
        setText(button, groups[index][item].label || "");
      }
      dropMissing(line, "button-", inner);
    }
    dropMissing(host, "row-", keep);
    var size = 16;
    var guard = 0;
    host.style.fontSize = px(size);
    while (guard < 10 && size > 6 && host.scrollWidth > host.clientWidth + 1) {
      size -= 1;
      host.style.fontSize = px(size);
      guard += 1;
    }
  }
  function renderTable(parent, key, title, columns, rows, muted, pxSize) {
    var host = ensure(parent, key, "div");
    host.className = "section";
    var heading = ensure(host, "title", "div");
    heading.className = "caption";
    setText(heading, title || "");
    heading.style.fontSize = px(pxSize || 13);
    var table = ensure(host, "table", "table");
    var signature = (columns || []).join("|");
    if (table.getAttribute("data-cols") !== signature) {
      while (table.firstChild) table.removeChild(table.firstChild);
      var head = document.createElement("thead");
      var headRow = document.createElement("tr");
      var index;
      for (index = 0; index < (columns || []).length; index += 1) {
        var th = document.createElement("th");
        setText(th, columns[index]);
        headRow.appendChild(th);
      }
      head.appendChild(headRow);
      table.appendChild(head);
      table.appendChild(document.createElement("tbody"));
      table.setAttribute("data-cols", signature);
    }
    var body = table.querySelector("tbody");
    var keep = {};
    var mutedSet = {};
    var cursor;
    for (cursor = 0; cursor < (muted || []).length; cursor += 1) mutedSet[muted[cursor]] = true;
    for (cursor = 0; cursor < (rows || []).length; cursor += 1) {
      var rowKey = "row-" + String(cursor);
      keep[rowKey] = true;
      var tr = ensure(body, rowKey, "tr");
      tr.style.color = mutedSet[cursor] ? "var(--text_muted)" : "var(--text)";
      var values = rows[cursor] || [];
      var cell;
      for (cell = 0; cell < (columns || []).length; cell += 1) {
        var td = ensure(tr, "cell-" + String(cell), "td");
        setText(td, values[cell] == null ? "" : String(values[cell]));
      }
    }
    dropMissing(body, "row-", keep);
  }
  function renderRecords(panel, records) {
    if (!records) return;
    var head = ensure(panel, "head", "div");
    head.className = "split";
    var name = ensure(head, "name", "div");
    setText(name, records.name || "");
    name.style.fontSize = px(records.header_px || 28);
    var status = ensure(head, "status", "div");
    setText(status, records.status_text || "");
    status.style.color = roleColor(records.status_tone === "ok" ? "accent" : "text");
    var elapsed = ensure(head, "elapsed", "div");
    setText(elapsed, records.elapsed_text || "");
    var warning = ensure(panel, "warning", "p");
    warning.className = "warning";
    warning.hidden = !records.warning;
    setText(warning, records.warning || "");
    var split = ensure(panel, "split", "div");
    split.className = "split";
    renderTable(
      split, "records", records.records_title, records.records_columns,
      records.records_rows, [], records.section_px
    );
    renderTable(
      split, "active", records.active_title, records.active_columns,
      records.active_rows, records.active_muted_rows, records.section_px
    );
    var attempts = ensure(panel, "attempts", "div");
    attempts.className = "attempts";
    var keep = {};
    var index;
    for (index = 0; index < (records.attempts || []).length; index += 1) {
      var attempt = records.attempts[index];
      var key = "attempt-" + String(attempt.lane);
      keep[key] = true;
      var card = ensure(attempts, key, "div");
      var badge = ensure(card, "badge", "div");
      setText(badge, attempt.badge || "");
      badge.style.fontSize = px(attempt.badge_px || records.badge_px || 36);
      var title = ensure(card, "title", "div");
      setText(title, attempt.title || "");
      title.style.fontSize = px(records.body_px || 15);
      renderTable(card, "laps", "", attempt.columns, attempt.rows, [], records.body_px);
      var best = ensure(card, "best", "div");
      setText(best, attempt.session_best || "");
      var laneBest = ensure(card, "lane", "div");
      setText(laneBest, attempt.lane_record || "");
    }
    dropMissing(attempts, "attempt-", keep);
    renderControls(panel, records.controls || []);
  }
  function renderBriefing(panel, briefing) {
    if (!briefing) return;
    var title = ensure(panel, "title", "div");
    title.className = "caption";
    setText(title, briefing.title || "");
    title.style.fontSize = px(briefing.title_px || 34);
    var seats = ensure(panel, "seats", "div");
    seats.style.display = "grid";
    seats.style.gridTemplateColumns = "repeat(" + String(briefing.seat_columns || 1) + ", 1fr)";
    var keep = {};
    var index;
    for (index = 0; index < (briefing.seats || []).length; index += 1) {
      var seat = briefing.seats[index];
      var key = "seat-" + String(seat.lane);
      keep[key] = true;
      var node = ensure(seats, key, "div");
      var lane = ensure(node, "lane", "div");
      setText(lane, seat.lane_text || "");
      lane.style.fontSize = px(seat.lane_px || 16);
      var driver = ensure(node, "driver", "div");
      setText(driver, seat.driver || "");
      driver.style.fontSize = px(seat.driver_px || 28);
    }
    dropMissing(seats, "seat-", keep);
    var changes = ensure(panel, "changes", "div");
    setText(changes, (briefing.changes || []).join("\\n"));
    changes.style.fontSize = px(briefing.change_px || 20);
    changes.hidden = !(briefing.changes && briefing.changes.length);
    var hint = ensure(panel, "hint", "div");
    setText(hint, briefing.hint || "");
    hint.style.fontSize = px(briefing.hint_px || 16);
    var select = ensure(panel, "drivers", "select");
    select.disabled = true;
    select.setAttribute("aria-disabled", "true");
    var names = briefing.drivers || [];
    var signature = names.join("\\n");
    if (select.getAttribute("data-sig") !== signature) {
      while (select.firstChild) select.removeChild(select.firstChild);
      for (index = 0; index < names.length; index += 1) {
        var option = document.createElement("option");
        setText(option, names[index]);
        select.appendChild(option);
      }
      select.setAttribute("data-sig", signature);
    }
    renderControls(panel, briefing.controls || []);
  }
  function renderMessages(panel, scene) {
    var caption = ensure(panel, "caption", "div");
    caption.className = "caption";
    var cap = scene.message_caption || {};
    caption.hidden = cap.visible === false;
    setText(caption, cap.text || "");
    var message = ensure(panel, "message", "p");
    message.className = "message";
    var body = scene.message || {};
    message.hidden = body.visible === false;
    setText(message, body.text || "");
    var warning = ensure(panel, "warning", "p");
    warning.className = "warning";
    var warn = scene.warning || {};
    warning.hidden = warn.visible === false || !warn.text;
    setText(warning, warn.text || "");
  }
  function renderScene(scene) {
    if (!scene || scene.present === false) {
      connection = "no-race";
      showBanner();
      scheduleActive = false;
      paintLights(null);
      return;
    }
    if (connection !== "disconnected" && connection !== "reconnecting") {
      connection = "connected";
      showBanner();
    }
    applyColors(scene);
    if (scene.race_name) document.title = String(scene.race_name);
    var width = (scene.viewport && scene.viewport.width) || 0;
    var height = (scene.viewport && scene.viewport.height) || 0;
    var panels = panelMap(scene);
    var ids = [
      "clock", "status", "lanes", "ranking", "messages", "controls", "records", "briefing"
    ];
    var index;
    for (index = 0; index < ids.length; index += 1) {
      var box = panels[ids[index]];
      if (box) {
        width = Math.max(width, (box.x || 0) + (box.width || 0));
        height = Math.max(height, (box.y || 0) + (box.height || 0));
      }
    }
    stage.style.width = px(width);
    stage.style.height = px(height);
    fit.style.overflowX = scene.horizontal_overflow ? "auto" : "hidden";
    fit.style.overflowY = scene.scrolls ? "auto" : "hidden";
    for (index = 0; index < ids.length; index += 1) {
      var name = ids[index];
      var panel = ensure(stage, "panel-" + name, "section");
      place(panel, panels[name]);
      if (panels[name]) {
        panel._boxX = panels[name].x || 0;
        panel._boxY = panels[name].y || 0;
      }
    }
    var clock = stage.querySelector("[data-key='panel-clock']");
    var status = stage.querySelector("[data-key='panel-status']");
    if (clock) reserveFullscreen(clock);
    if (status) reserveFullscreen(status);
    if (clock && !clock.hidden) renderReadout(clock, scene.clock);
    if (status && !status.hidden) {
      var tone = (scene.status_line && scene.status_line.panel_tone) || "";
      if (tone === "ok" || tone === "warn") status.setAttribute("data-tone", tone);
      else status.removeAttribute("data-tone");
      renderReadout(status, scene.status_line);
    }
    var lanes = stage.querySelector("[data-key='panel-lanes']");
    if (lanes) renderLanes(lanes, scene);
    var ranking = stage.querySelector("[data-key='panel-ranking']");
    if (ranking && !ranking.hidden) renderRanking(ranking, scene.ranking);
    var messages = stage.querySelector("[data-key='panel-messages']");
    if (messages && !messages.hidden) renderMessages(messages, scene);
    var controls = stage.querySelector("[data-key='panel-controls']");
    if (controls && !controls.hidden) renderControls(controls, scene.controls || []);
    var records = stage.querySelector("[data-key='panel-records']");
    if (records && !records.hidden) renderRecords(records, scene.records);
    var briefing = stage.querySelector("[data-key='panel-briefing']");
    if (briefing && !briefing.hidden) renderBriefing(briefing, scene.briefing);
    paintLights(scene.lights);
  }
  function sameViewport(scene) {
    var box = scene.viewport || {};
    return !!requested && box.width === requested.width && box.height === requested.height;
  }
  function onScene(msg) {
    var seq = typeof msg.seq === "number" ? msg.seq : 0;
    if (seq < lastSceneSeq) return;
    var scene = msg.payload || {};
    var matches = !requested || sameViewport(scene);
    if (!matches) {
      if (!fitted && mismatchSeq === seq) {
        lastSceneSeq = seq;
        renderScene(scene);
        return;
      }
      mismatchSeq = seq;
      return;
    }
    fitted = true;
    mismatchSeq = null;
    lastSceneSeq = seq;
    renderScene(scene);
  }
  function onSchedule(msg, active) {
    var seq = typeof msg.seq === "number" ? msg.seq : 0;
    if (seq < lastScheduleSeq) return;
    lastScheduleSeq = seq;
    scheduleActive = active;
    paintLights(lastLights);
  }
  function apply(raw) {
    var msg;
    try { msg = JSON.parse(raw); } catch (error) { return; }
    if (!msg || typeof msg.type !== "string") return;
    if (msg.type === "hello") {
      var payload = msg.payload || {};
      var box = payload.viewport_limits;
      if (box && typeof box.min === "number" && typeof box.max === "number") {
        limits = {min: box.min, max: box.max};
      }
      if (payload.receiving_hud === false && lastSceneSeq < 0) {
        connection = "no-race";
        showBanner();
      }
      return;
    }
    if (msg.type === "scene") onScene(msg);
    else if (msg.type === "start_schedule") onSchedule(msg, true);
    else if (msg.type === "start_schedule_revoked") onSchedule(msg, false);
  }
  function connect() {
    if (connection !== "reconnecting") connection = "connecting";
    showBanner();
    opened = false;
    queue = [];
    lastSceneSeq = -1;
    lastScheduleSeq = -1;
    fitted = false;
    mismatchSeq = null;
    scheduleActive = false;
    var openedSocket = new WebSocket("ws://" + location.host + "/live" + location.search);
    socket = openedSocket;
    openedSocket.onopen = function () {
      if (socket !== openedSocket) return;
      opened = true;
      connection = "connected";
      sendViewport();
      var pending = queue;
      queue = [];
      var index;
      for (index = 0; index < pending.length; index += 1) apply(pending[index]);
    };
    openedSocket.onmessage = function (event) {
      if (socket !== openedSocket) return;
      if (!opened) {
        if (queue.length > 8) queue.shift();
        queue.push(event.data);
        return;
      }
      apply(event.data);
    };
    openedSocket.onclose = function () {
      if (socket !== openedSocket) return;
      socket = null;
      connection = "disconnected";
      scheduleActive = false;
      paintLights(lastLights);
      showBanner();
      setTimeout(function () {
        connection = "reconnecting";
        showBanner();
        connect();
      }, 1000);
    };
  }
  document.getElementById("fullscreen").addEventListener("click", function () {
    var root = document.documentElement;
    var active = document.fullscreenElement || document.webkitFullscreenElement;
    if (active) {
      var exit = document.exitFullscreen || document.webkitExitFullscreen;
      if (exit) exit.call(document);
      return;
    }
    var request = root.requestFullscreen || root.webkitRequestFullscreen;
    if (!request) return;
    var pending = request.call(root);
    if (pending && pending.catch) pending.catch(function () { return null; });
  });
  window.addEventListener("resize", scheduleViewport);
  window.addEventListener("orientationchange", scheduleViewport);
  document.addEventListener("fullscreenchange", scheduleViewport);
  document.addEventListener("webkitfullscreenchange", scheduleViewport);
  if (window.visualViewport) window.visualViewport.addEventListener("resize", scheduleViewport);
  showBanner();
  connect();
})();
</script>
</body>
</html>
"""


def hud_page() -> bytes:
    """The live HUD. The session token is not part of this document."""
    return _HUD_HTML.replace("__START_HINT__", START_HINT).encode("utf-8")
