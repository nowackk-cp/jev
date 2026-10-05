/* Jev ofisi istemcisi: /api/durum anlık görüntüsünü çizer, /api/olaylar (SSE) akışıyla canlı tutar.
 * Karakter duruşları office.css'teki data-* öznitelikleriyle, yürüyüşler Web Animations ile yapılır.
 * CSP gereği satır içi betik ya da on* özniteliği yoktur; ajanlardan gelen metin her zaman textContent ile yazılır.
 * Erişim anahtarı yalnızca sayfa adresindedir (?k=); API istekleri onu X-Jev-Key başlığıyla taşır.
 */
(function () {
  "use strict";

  const S = window.JevScene, C = window.JevChars;
  const doc = document, root = doc.documentElement;
  const $ = (sel, el) => (el || doc).querySelector(sel);
  const $$ = (sel, el) => Array.from((el || doc).querySelectorAll(sel));

  // --- sabitler -----------------------------------------------------------------------------

  const IDLE_PHASES = new Set(["paused", "reported", "aborted", "awaiting_approval"]);
  const RESTING = new Set(["idle", "sleeping", "done", "failed", "blocked"]);
  const FEED_TYPES = new Set(["log", "jev.dispatch", "jev.consult", "jev.decision", "guard.blocked", "quota",
    "verify", "report.ready", "run.phase", "task.update", "run.scale"]);
  const FEED_TASK_STATUSES = new Set(["done", "failed", "blocked", "skipped", "split", "needs_decision",
    "waiting_quota"]);
  const FINISHED = new Set(["done", "skipped", "failed", "blocked"]);
  const FEED_MAX = 200, DECISIONS_MAX = 50, NOTES_MAX = 30, FLY_MAX = 12;
  const MAIN_FLOW = ["planning", "awaiting_approval", "executing", "reviewing", "reported"];
  const FIX_FLOW = ["fixing", "executing", "reviewing", "reported"];
  // decomposing: eski koşulardan kalan aşama (sunucudaki WORK_PHASES ile aynı küme)
  const WORK_PHASES = new Set(["sizing", "planning", "decomposing", "executing", "reviewing", "fixing"]);
  const DESK_AGENTS = new Set(["sol", "sonnet", "luna"]);
  const COLUMN = { pending: "pending", ready: "ready", running: "running", verifying: "verifying", done: "done",
    skipped: "done", split: "done", needs_decision: "trouble", waiting_quota: "trouble", failed: "trouble",
    blocked: "trouble" };
  const STATUS_TAG = { skipped: "", split: "", needs_decision: "err", waiting_quota: "warn", failed: "err",
    blocked: "err" };
  const STATE_TR = { idle: "Boşta", sleeping: "Uyuyor", done: "Bitti", failed: "Takıldı", blocked: "İlerleyemiyor",
    planning: "Planı çiziyor", reviewing: "Son kontrolü yapıyor",
    thinking: "Düşünüyor", dispatch: "Görev dağıtıyor", verifying: "Doğruluyor", consulting: "Danışıyor" };
  const ICON = { thinking: "💭", reading: "📄", editing: "✏️", command: "⌨️", testing: "🧪", message: "💬",
    guarded: "🛡️", quota: "💤", dispatch: "📋", consulting: "📻", decision: "◆", planning: "📐",
    reviewing: "🔍", idle: "⏳", sleeping: "💤", done: "✅", failed: "⚠️", blocked: "✋",
    verifying: "✅" };
  const LOG_ICON = { info: "•", ok: "✔", warn: "⚠", error: "✖" };
  const ORIGIN_TR = { plan: "Plan", split: "Bölünme", fix: "Düzeltme" };
  const CMD_OK = { duzelt: "Düzeltme turu başlıyor.", onayla: "Plan onaylandı; görevler dağıtılıyor.",
    reddet: "Plan reddedildi; koşu kapanıyor.", devam: "Koşu kaldığı yerden sürüyor." };
  const CONN_TEXT = { live: "Canlı", connecting: "Bağlanıyor…", down: "Bağlantı yok", closed: "Ofis kapandı" };
  const THEMES = [["auto", "◐", "Tema: otomatik"], ["light", "☀", "Tema: açık"], ["dark", "☾", "Tema: koyu"]];
  const CONFETTI = ["#e07a2e", "#1fa5b8", "#b04fc9", "#e6b400", "#3d7cc9", "#3aa55d", "#d64545"];
  const WALK_SPEED = 420;  // sahne birimi / saniye
  const CHEER_MS = 3500, DWELL_MS = 1600, ACT_THROTTLE_MS = 350, AGENT_DLG_MS = 1500, PENDING_MS = 20000;
  const motion = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : null;
  const calm = () => !!(motion && motion.matches);

  // --- durum --------------------------------------------------------------------------------

  const key = new URLSearchParams(location.search).get("k") || "";
  let snap = null;             // son anlık görüntü; canlı olaylar run ve controls alanlarını yerinde günceller
  let L = {};                  // etiket sözlükleri (snapshot.labels)
  let lastSeq = 0, serverOffset = 0, booted = false;
  const tasks = new Map();     // görev kimliği → görev görünümü
  let taskOrder = [];
  const agents = new Map();    // ajan adı → kayıt (makeAgent)
  let feed = [], decisions = [];
  let consultBrain = null, consultSeq = 0;
  let es = null, closed = false, backoff = 1000, resyncTimer = 0, connErrors = 0;
  let refreshTimer = 0, refreshDue = 0, refreshing = false, refreshAgain = false;
  let pending = false, pendingPhase = null, pendingTimer = 0;
  let agentView = null, reportView = null, taskView = null, fixDraft = "";
  let flyQueue = [], tasksFrame = 0, theme = "auto";
  const serverNow = () => Date.now() + serverOffset;
  const phaseNow = () => (snap ? snap.run.phase : "planning");

  const stage = $("#stage"), feedBox = $("#feed"), decBox = $("#decs");
  const dlgAgent = $("#dlg-agent"), dlgReport = $("#dlg-report"), dlgPlan = $("#dlg-plan"), dlgTask = $("#dlg-task");
  let notesBox = null, frontLayer = null, stageEmpty = null;
  const plates = {};           // levha kimliği (opus, jev, desk-sol…) → levha
  const cardEls = new Map(), noteEls = new Map(), cardKeys = new WeakMap();

  // --- küçük yardımcılar --------------------------------------------------------------------

  function h(tag, attrs, ...kids) {
    const el = doc.createElement(tag);
    if (attrs) {
      for (const k of Object.keys(attrs)) {
        const v = attrs[k];
        if (v == null || v === false) continue;
        if (k === "class") el.className = v;
        else if (k === "html") el.innerHTML = v;  // yalnızca kendi SVG'lerimiz ve sunucunun kaçışlı md çıktısı
        else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
        else el.setAttribute(k, v === true ? "" : String(v));
      }
    }
    for (const c of kids.flat(2)) {
      if (c == null || c === false || c === "") continue;
      el.append(c instanceof Node ? c : String(c));
    }
    return el;
  }

  const setText = (el, t) => { if (el && el.textContent !== t) el.textContent = t; };
  const setData = (el, k, v) => { if (v) el.dataset[k] = v; else delete el.dataset[k]; };
  const cap = (s) => (s ? String(s).charAt(0).toLocaleUpperCase("tr") + String(s).slice(1) : "");
  const safe = (name) => String(name || "").toLowerCase().replace(/[^a-z0-9_-]/g, "");
  const pad2 = (n) => String(n).padStart(2, "0");
  const num = (n) => Number(n || 0).toLocaleString("tr-TR");
  const usd = (n) => "$" + Number(n || 0).toFixed(Number(n || 0) >= 1 ? 2 : 4);

  function shorten(text, n) {
    const t = String(text == null ? "" : text).replace(/\s+/g, " ").trim();
    return t.length <= n ? t : t.slice(0, Math.max(0, n - 1)) + "…";
  }

  function hhmm(iso) {
    const t = Date.parse(iso || "");
    if (isNaN(t)) return "?";
    const d = new Date(t);
    return pad2(d.getHours()) + ":" + pad2(d.getMinutes());
  }

  function clock(ms) {
    let s = Math.max(0, Math.floor((ms || 0) / 1000));
    const hr = Math.floor(s / 3600);
    s -= hr * 3600;
    const m = Math.floor(s / 60);
    s -= m * 60;
    return hr ? `${hr}:${pad2(m)}:${pad2(s)}` : `${m}:${pad2(s)}`;
  }

  function dur(sec) {
    const v = Number(sec);
    if (sec == null || sec === "" || !isFinite(v)) return "—";
    let s = Math.round(v);
    if (s < 60) return s + " sn";
    const m = Math.floor(s / 60);
    s %= 60;
    if (m < 60) return `${m} dk ${pad2(s)} sn`;
    return `${Math.floor(m / 60)} sa ${pad2(m % 60)} dk`;
  }

  // Türkçe ek uyumu (util.ek ile aynı kural): ek("Opus", "e") → "Opus'a", ek("08:00", "de") → "08:00'de"
  const VOWELS = "aıoueiöü";
  const ONES = ["sıfır", "bir", "iki", "üç", "dört", "beş", "altı", "yedi", "sekiz", "dokuz"];
  const TENS = ["", "on", "yirmi", "otuz", "kırk", "elli", "altmış", "yetmiş", "seksen", "doksan"];
  const HARMONY = { a: "ı", ı: "ı", o: "u", u: "u", e: "i", i: "i", ö: "ü", ü: "ü" };

  function spokenTail(word) {
    const w = String(word).trim().toLocaleLowerCase("tr");
    const m = /(?:(\d{1,2}):)?(\d+)$/.exec(w);
    if (!m) return w;
    let digits = m[2];
    if (m[1] !== undefined && /^0+$/.test(digits)) digits = m[1];  // 08:00 → saat okunur
    if (/^0+$/.test(digits)) return "sıfır";
    const n = parseInt(digits.slice(-3), 10);
    if (n % 10) return ONES[n % 10];
    if (n % 100) return TENS[Math.floor((n % 100) / 10)];
    return n % 1000 ? "yüz" : "bin";
  }

  function suffix(word, kind) {
    const t = spokenTail(word);
    const vs = [...t].filter((c) => VOWELS.includes(c));
    const last = vs.length ? vs[vs.length - 1] : "e", back = "aıou".includes(last);
    const endV = t.length > 0 && VOWELS.includes(t[t.length - 1]);
    const hard = t.length > 0 && "çfhkpsşt".includes(t[t.length - 1]);
    let s;
    if (kind === "e") s = (endV ? "y" : "") + (back ? "a" : "e");
    else if (kind === "de") s = (hard ? "t" : "d") + (back ? "a" : "e");
    else if (kind === "den") s = (hard ? "t" : "d") + (back ? "an" : "en");
    else if (kind === "in") s = (endV ? "n" : "") + HARMONY[last] + "n";
    else throw new Error("bilinmeyen ek: " + kind);
    return "'" + s;
  }

  const ek = (word, kind) => word + suffix(word, kind);

  function lbl(group, k) {
    if (k == null || k === "") return "";
    const m = L[group];
    return m && m[k] != null && m[k] !== "" ? m[k] : String(k);
  }

  function disp(name) {
    if (!name) return "";
    const a = agents.get(name);
    if (a && a.info.display) return a.info.display;
    return (L.display && L.display[name]) || cap(name);
  }

  const who = (name) => h("span", { class: "who ag-" + safe(name) }, disp(name));
  const deskOf = (name) => (DESK_AGENTS.has(name) ? name : "uzman");

  // Son raporu yazan (jev ya da denetçi ajan): rapor bitince elinde raporla durur
  const lastReportBy = () => (((snap && snap.reports) || []).slice(-1)[0] || {}).by || null;

  function roleName(role, fallback) {
    for (const a of agents.values()) if ((a.info.roles || []).includes(role)) return a.info.display;
    return fallback;
  }

  function titleOf(a) {
    const m = C.META[a.name];
    if (m) return m.title;
    return (a.info.roles_tr && a.info.roles_tr[0]) || "Uzman";
  }

  // Başarılı ve eksiksiz rapor: düzeltme turu ancak kullanıcının notuyla açılır (fix.nothing_to_fix ile aynı kural)
  function cleanReport(rep) {
    return !!rep && rep.verdict === "basarili" && !(rep.gaps || []).length;
  }

  // plan.stack {language, frameworks[], runtime_notes}: yalnız değerler, context.stack_text ile aynı sırada
  // ("Python 3.11+ · argparse, unittest · Ek paket yok."); İngilizce anahtarlar kullanıcıya gösterilmez
  const STACK_KEYS = ["language", "frameworks", "runtime_notes"];
  function stackText(s) {
    if (s == null || s === "") return "";
    if (typeof s === "string") return s;
    if (Array.isArray(s)) return s.map(stackText).filter(Boolean).join(", ");
    if (typeof s === "object") {
      const keys = STACK_KEYS.filter((k) => k in s).concat(Object.keys(s).filter((k) => !STACK_KEYS.includes(k)));
      return keys.map((k) => stackText(s[k])).filter(Boolean).join(" · ");
    }
    return String(s);
  }

  function table(head, rows, numCols) {
    const nums = new Set(numCols || []);
    return h("div", { class: "tbl-wrap" }, h("table", { class: "tbl" },
      h("thead", null, h("tr", null, head.map((x) => h("th", null, x)))),
      h("tbody", null, rows.map((r) => h("tr", { class: r.bad ? "bad" : null },
        r.cells.map((c, i) => h("td", { class: nums.has(i) ? "num" : null }, c == null || c === "" ? "—" : c)))))));
  }

  const taskLink = (id, text) => h("button", { class: "btn small", type: "button", "data-task": id }, text || id);

  function toast(text, kind, action) {
    const box = $("#toasts");
    const t = h("div", { class: "toast" + (kind ? " " + kind : ""), role: kind === "err" ? "alert" : null },
      h("span", { class: "tt" }, text));
    const drop = () => {
      if (t.classList.contains("out")) return;
      t.classList.add("out");
      setTimeout(() => t.remove(), 260);
    };
    if (action) t.append(h("button", { class: "btn small", type: "button", onclick: () => { drop(); action.run(); } }, action.label));
    t.append(h("button", { class: "x", type: "button", "aria-label": "Kapat", onclick: drop }, "×"));
    box.append(t);
    while (box.children.length > 4) box.firstElementChild.remove();
    setTimeout(drop, action ? 12000 : 6000);
  }

  function setConn(state) {
    const c = $("#conn");
    c.dataset.state = state;
    c.textContent = CONN_TEXT[state];
  }

  function stageMsg(text) {
    if (!stageEmpty) return;
    stageEmpty.textContent = text || "";
    stageEmpty.hidden = !text;
  }

  async function api(path) {
    const r = await fetch(path, { headers: { "X-Jev-Key": key }, cache: "no-store" });
    let j = null;
    try { j = await r.json(); } catch (_) { j = null; }
    if (!r.ok) {
      const e = new Error((j && j.mesaj) || "Sunucu hatası (HTTP " + r.status + ").");
      e.status = r.status;
      throw e;
    }
    return j;
  }

  // --- ajan durumu --------------------------------------------------------------------------

  /* snapshot._normalize_state ile aynı: koşu beklerken (rapor, duraklama, onay) kotası dolan uyur, herkes
   * dinlenir; eski "T04 bitti!" ya da "raporu bekliyor" yazıları kalmaz. */
  function normalizeState(s, phase, quota) {
    s = s ? Object.assign({}, s) : { state: "idle", text: "", task_id: null, until: null, seq: 0, ts: null };
    let state = s.state || "idle";
    const rp = IDLE_PHASES.has(phase);
    if (rp && state !== "sleeping") state = "idle";
    if (state === "sleeping") {
      const u = Date.parse(s.until || "");
      if (!u || u <= serverNow()) state = "idle";
    }
    if (quota && state === "idle") {
      state = "sleeping";
      s.until = quota.until;
      s.text = "Kota doldu";
    }
    if (state === "idle" && (state !== s.state || rp)) {
      s.text = "";
      s.task_id = null;
    }
    s.state = state;
    return s;
  }

  const activeQuota = (a) => (a.quota && Date.parse(a.quota.until || "") > serverNow() ? a.quota : null);
  const renorm = (a) => { a.st = normalizeState(a.raw, phaseNow(), activeQuota(a)); };

  // --- sahne --------------------------------------------------------------------------------

  function buildStage() {
    stageEmpty = $(".stage-empty", stage) || h("div", { class: "stage-empty" });
    stage.textContent = "";
    const wrap = stage.parentElement;
    for (const old of wrap.querySelectorAll(":scope > .stage-backdrop")) old.remove();
    wrap.insertAdjacentHTML("afterbegin", S.backdrop());
    stage.insertAdjacentHTML("beforeend", S.back());
    notesBox = h("div", { class: "wb-notes", id: "wb-notes", "aria-label": "Beyaz tahtadaki görev notları" });
    stage.append(notesBox);
    stage.insertAdjacentHTML("beforeend", S.glass());
    stage.insertAdjacentHTML("beforeend", S.front());
    frontLayer = stage.lastElementChild;
    for (const id of ["opus", "jev"].concat(S.DESKS.map((d) => "desk-" + d.key))) makePlate(id);
    stage.append(stageEmpty);
    tickClock();
  }

  function makePlate(id) {
    const r = S.PLATES[id];
    if (!r) return;
    const el = h("button", { class: "plate" + (r.sign ? " sign" : ""), type: "button", hidden: true,
      style: `left:${r.x / 16}%;top:${r.y / 9}%;width:${r.w / 16}%;min-height:${r.h / 9}%` });
    const nm = h("span"), small = h("small");
    el.append(h("span", { class: "pn" }, nm, small));
    const pt = r.compact ? null : h("span", { class: "pt" });
    if (pt) el.append(pt);
    const ps = h("span", { class: "ps" });
    el.append(ps);
    el.addEventListener("click", () => { if (el.dataset.agent) openAgent(el.dataset.agent); });
    stage.append(el);
    plates[id] = { id, el, nm, small, pt, ps, ag: "" };
  }

  function makeAgent(name) {
    const cls = safe(name);
    const actor = h("div", { class: "actor ag-" + cls, "data-name": name, "data-arms": "rest",
      style: `--d:${(-Math.random() * 5).toFixed(2)}s` });
    actor.innerHTML = C.build(name);
    const carry = h("div", { class: "carry", hidden: true });
    actor.append(carry);
    const body = $(".c-body", actor);
    if (body) body.addEventListener("click", () => openAgent(name));
    const bt = h("span", { class: "bt" });
    const bubble = h("div", { class: "bubble ag-" + cls, "aria-hidden": "true" }, bt);
    stage.insertBefore(actor, frontLayer);
    stage.insertBefore(bubble, stageEmpty);
    const a = {
      name, cls, info: { display: (L.display && L.display[name]) || cap(name), roles: [], roles_tr: [] },
      raw: null, st: null, act: null, usage: null, quota: null, v: null,
      actor, carry, bubble, bt, pos: null, spot: null, moving: null, anim: null, banim: null, timers: [],
      errand: null, queue: [], cheerUntil: 0, shieldUntil: 0, visTimer: 0, lastVis: 0, bubbleKey: "",
    };
    a.st = normalizeState(null, phaseNow(), null);
    agents.set(name, a);
    return a;
  }

  function tickClock() {
    const d = new Date(), s = d.getSeconds(), m = d.getMinutes() + s / 60, hr = (d.getHours() % 12) + m / 60;
    const rot = (id, deg) => {
      const el = doc.getElementById(id);
      if (el) el.setAttribute("transform", `rotate(${deg.toFixed(1)} 1330 170)`);
    };
    rot("clock-h", hr * 30);
    rot("clock-m", m * 6);
    rot("clock-s", s * 6);
  }

  // --- nerede durur? ------------------------------------------------------------------------

  function targetName(a) {
    const st = a.st, s = st.state, n = a.name;
    if (n === "jev") return jevTarget(st);
    const atDesk = st.phase === "worker" && (!RESTING.has(s) || !!st.task_id);
    if (n === "opus") return atDesk ? "desk-uzman" : "room";  // proje şefi planlama odasındadır; kod yazarken şefin masasına geçer
    if (DESK_AGENTS.has(n)) return "desk-" + n;
    return atDesk || !RESTING.has(s) ? "desk-uzman" : null;  // bilinmeyen ajan yalnızca çalışırken görünür
  }

  function jevTarget(st) {
    const s = st.state;
    if (s === "consulting") return consultSpot();
    if (s === "verifying") {
      const t = st.task_id ? tasks.get(st.task_id) : null;
      return t && t.agent ? "front-" + deskOf(t.agent) : "home";
    }
    if (s === "idle") return st.text ? "home" : "tea";
    return "home";
  }

  function consultSpot() {
    const b = agents.get(consultBrain || "opus");
    const t = b ? targetName(b) : "room";
    if (t === "room") return "consult";
    if (t && t.startsWith("desk-")) return "front-" + t.slice(5);
    return "home";
  }

  function spotOf(name) {
    const s = S.SPOTS[name];
    return s ? { name, x: s.x, y: s.y, z: s.z || 30, dir: s.dir || "", dx: s.dx || 0 } : null;
  }

  // --- görünüş ------------------------------------------------------------------------------

  function look(a) {
    const st = a.st, s = st.state, now = Date.now();
    const v = { arms: "rest", prop: "", eyes: "", mouth: "", brow: "", fx: [], icon: "", text: "", tone: "",
      plate: "", busy: !RESTING.has(s) };
    if (a.errand) {
      v.arms = a.errand.arrived ? "rest" : "hold";
      v.icon = "📋";
      v.text = a.errand.text;
      v.plate = "📋 " + a.errand.text;
      v.busy = true;
      return v;
    }
    switch (s) {
      case "idle":
        if (!st.text && !st.task_id) {
          v.arms = "hold";
          if (phaseNow() === "reported" && a.name === lastReportBy()) { v.prop = "report"; v.plate = "Rapor teslim edildi 📄"; }
          else { v.prop = "tea"; v.plate = "Çay molası ☕"; }
        } else {
          v.icon = "⏳";
          v.text = st.text || "Bekliyor";
        }
        break;
      case "sleeping":
        v.eyes = "closed";
        v.fx.push("zzz");
        v.icon = "💤";
        v.text = "Kota doldu · " + ek(hhmm(st.until), "de") + " uyanır";
        v.plate = "💤 Kota · " + clock(Date.parse(st.until || "") - serverNow());
        break;
      case "done":
        if (a.cheerUntil > now) {
          v.arms = "cheer"; v.mouth = "open"; v.eyes = "happy"; v.icon = "🎉";
          v.text = st.text || "Bitti!";
        }
        v.plate = "✅ " + (st.text || "Bitti");
        break;
      case "failed":
        v.fx.push("sweat", "alert"); v.mouth = "o"; v.brow = "w"; v.icon = "⚠️"; v.tone = "err";
        v.text = st.text || STATE_TR.failed;
        break;
      case "blocked":
        v.arms = "up"; v.mouth = "o"; v.brow = "w"; v.icon = "✋"; v.tone = "warn";
        v.text = st.text || STATE_TR.blocked;
        break;
      case "planning": v.arms = "hold"; v.prop = "drawing"; v.icon = "📐"; break;
      case "reviewing": v.arms = "up"; v.prop = "magnifier"; v.icon = "🔍"; break;
      case "consulting": v.mouth = "open"; v.icon = "📻"; break;
      case "dispatch": v.icon = "📋"; break;
      case "verifying": v.arms = "hold"; v.prop = "clipboard"; v.icon = "✅"; break;
      default: v.prop = "dots"; v.icon = "💭";
    }
    if (v.busy) {
      v.text = st.text || STATE_TR[s] || s;
      const act = a.act;
      if (act && act.kind && (act.seq || 0) > (st.seq || 0)) applyActivity(a, v, act);
      v.plate = v.icon + " " + (st.text || STATE_TR[s] || s);
    }
    if (a.shieldUntil > now) v.fx.push("shield");
    if (!v.plate) v.plate = (v.icon ? v.icon + " " : "") + (v.text || STATE_TR[s] || s);
    return v;
  }

  // Son etkinlik, durum olayından yeniyse duruşu belirler (okuyor, yazıyor, komut çalıştırıyor…)
  function applyActivity(a, v, act) {
    const atDesk = (targetName(a) || "").startsWith("desk-");
    switch (act.kind) {
      case "reading": v.arms = "hold"; v.prop = "page"; break;
      case "editing":
        if (atDesk) { v.arms = "type"; v.prop = ""; } else { v.arms = "hold"; v.prop = "keyboard"; }
        break;
      case "command": v.arms = "type"; v.prop = "term"; break;
      case "testing": v.arms = "up"; v.prop = "tube"; break;
      case "message": v.arms = "rest"; v.prop = ""; v.mouth = "open"; break;
      case "guarded": v.fx.push("shield"); v.tone = "warn"; break;
      case "thinking": v.arms = "rest"; v.prop = "dots"; break;
      default: return;
    }
    v.icon = ICON[act.kind] || v.icon;
    if (act.text) v.text = act.text;
  }

  function bump(el) {
    el.classList.remove("bump");
    void el.offsetWidth;  // animasyonu yeniden başlatır
    el.classList.add("bump");
  }

  function renderAgent(a) {
    const v = look(a), el = a.actor;
    a.v = v;
    el.dataset.arms = v.arms;
    setData(el, "prop", v.prop);
    setData(el, "eyes", v.eyes);
    setData(el, "mouth", v.mouth);
    setData(el, "brow", v.brow);
    setData(el, "fx", v.fx.join(" "));
    const card = a.errand && !a.errand.arrived ? a.errand.carry : "";
    setText(a.carry, card);
    a.carry.hidden = !card;
    const text = shorten(v.text, 60);
    const k = v.icon + "|" + text + "|" + v.tone;
    if (k !== a.bubbleKey) {
      a.bubbleKey = k;
      a.bt.textContent = "";
      if (text) {
        if (v.icon) a.bt.append(h("span", { class: "bi" }, v.icon));
        a.bt.append(text);
      }
      a.bubble.classList.toggle("warn", v.tone === "warn");
      a.bubble.classList.toggle("err", v.tone === "err");
      if (text && !calm()) bump(a.bubble);
    }
    a.bubble.classList.toggle("show", !!text && !el.classList.contains("gone"));
    renderPlates();
  }

  function plateAgent(id) {
    if (!id.startsWith("desk-")) {
      const a = agents.get(id) || null;
      // Opus kod yazarken şefin masasındadır: odanın tabelası o sırada gizlenir, iki levha görünmez
      return a && String(targetName(a) || "").startsWith("desk-") ? null : a;
    }
    const k = id.slice(5);
    if (k !== "uzman") return agents.get(k) || null;
    for (const a of agents.values()) {
      if (a.name === "jev" || DESK_AGENTS.has(a.name)) continue;
      if (targetName(a) === "desk-uzman") return a;
    }
    return null;
  }

  function renderPlates() {
    for (const id of Object.keys(plates)) {
      const p = plates[id], a = plateAgent(id);
      const desk = id.startsWith("desk-") ? doc.getElementById(id) : null;
      const ag = a ? "ag-" + a.cls : "";
      if (p.ag !== ag) {
        if (p.ag) p.el.classList.remove(p.ag);
        if (ag) p.el.classList.add(ag);
        p.ag = ag;
      }
      if (!a) {
        p.el.hidden = !desk;
        p.el.dataset.agent = "";
        p.el.dataset.busy = "0";
        delete p.el.dataset.state;
        setText(p.nm, id === "desk-uzman" ? "Şefin masası" : "Boş masa");
        setText(p.small, "");
        if (p.pt) setText(p.pt, "");
        setText(p.ps, "boş");
        p.el.title = "";
        p.el.setAttribute("aria-label", p.nm.textContent + ": boş");
        if (desk) {
          desk.dataset.busy = "0";
          delete desk.dataset.state;
          desk.dataset.empty = "1";
        }
        continue;
      }
      const v = a.v || look(a), title = titleOf(a);
      p.el.hidden = false;
      p.el.dataset.agent = a.name;
      p.el.dataset.busy = v.busy ? "1" : "0";
      p.el.dataset.state = a.st.state;
      setText(p.nm, a.info.display);
      setText(p.small, title);
      // Levha dar: Jev'in satırında beynin kısa adı, tam adı ipucunda ve ajan kartında
      if (p.pt) setText(p.pt, a.name === "jev" ? "Beyin: " + (a.info.brain_display || a.info.brain || "—")
        : (a.info.label || a.info.model || ""));
      setText(p.ps, v.plate);
      p.el.title = `${a.info.display} · ${v.plate}` + (a.name === "jev" && a.info.brain ? ` · Beyin: ${a.info.brain}` : "");
      p.el.setAttribute("aria-label", `${a.info.display} (${title}): ${v.plate}. Ayrıntılar için tıkla.`);
      if (desk) {
        desk.dataset.busy = v.busy ? "1" : "0";
        desk.dataset.state = a.st.state;
        delete desk.dataset.empty;
      }
    }
  }

  // --- yürüyüş ------------------------------------------------------------------------------

  function setXY(el, x, y) {
    el.style.left = x / 16 + "%";
    el.style.top = y / 9 + "%";
  }

  function setDir(a, dir) {
    if (dir === "l") a.actor.dataset.dir = "l";
    else delete a.actor.dataset.dir;
  }

  // Balon başın üstünde durur; kenara yakınsa içeri kayar, kuyruğu yine başı gösterir (--tail)
  function placeBubble(a, dx) {
    const x = a.pos.x, bx = Math.min(S.W - 110, Math.max(110, x + (dx || 0)));
    setXY(a.bubble, bx, a.pos.y - 235);
    a.bubble.style.setProperty("--tail", (bx - x) / 16 + "cqw");
  }

  function place(a, p) {
    setXY(a.actor, p.x, p.y);
    a.actor.style.zIndex = String(p.z);
    setDir(a, p.dir);
    a.pos = { x: p.x, y: p.y };
    a.spot = p.name || null;
    placeBubble(a, p.dx);
  }

  const isFront = (p) => p.y >= 850;
  const inRoom = (p) => p.x < S.ROOM.right && p.y < S.ROOM.front;
  const nearestGap = (x) => S.AISLE.gaps.reduce((b, g) => (Math.abs(g - x) < Math.abs(b - x) ? g : b));

  // Masaların arasından geçen yol: arka koridor, masa aralıkları ve ön koridor; planlama odasına kapıdan girilir
  function route(from, to) {
    const B = S.AISLE.back, F = S.AISLE.front, D = S.ROOM.door;
    const pts = [{ x: from.x, y: from.y }], tail = [{ x: to.x, y: to.y }];
    if (!inRoom(from) || !inRoom(to)) {
      if (inRoom(from)) {
        pts.push({ x: D, y: from.y });
        from = { x: D, y: B };
      }
      if (inRoom(to)) {
        tail.unshift({ x: D, y: to.y });
        to = { x: D, y: B };
      }
      if (isFront(from) && isFront(to)) {
        pts.push({ x: from.x, y: F }, { x: to.x, y: F });
      } else if (!isFront(from) && !isFront(to)) {
        pts.push({ x: from.x, y: B }, { x: to.x, y: B });
      } else if (!isFront(from)) {
        const g = nearestGap(to.x);
        pts.push({ x: from.x, y: B }, { x: g, y: B }, { x: g, y: F });
      } else {
        const g = nearestGap(from.x);
        pts.push({ x: g, y: F }, { x: g, y: B }, { x: to.x, y: B });
      }
      pts.push({ x: to.x, y: to.y });
    }
    pts.push(...tail);
    return pts.filter((p, i) => i === 0 || Math.abs(p.x - pts[i - 1].x) > 0.5 || Math.abs(p.y - pts[i - 1].y) > 0.5);
  }

  function clearTimers(a) {
    for (const t of a.timers) clearTimeout(t);
    a.timers = [];
  }

  // Yürüyüş yarıda kesilirse karakterin o anki yerini sahne birimiyle döndürür
  function currentXY(a) {
    const w = stage.clientWidth, ht = stage.clientHeight;
    if (!w || !ht) return a.pos;
    const cs = getComputedStyle(a.actor);
    const x = (parseFloat(cs.left) / w) * S.W, y = (parseFloat(cs.top) / ht) * S.H;
    return isFinite(x) && isFinite(y) ? { x, y } : a.pos;
  }

  function stopWalk(a) {
    if (!a.anim) return;
    const p = currentXY(a);
    clearTimers(a);
    a.anim.cancel();
    if (a.banim) a.banim.cancel();
    a.anim = a.banim = null;
    a.actor.classList.remove("walk");
    setXY(a.actor, p.x, p.y);
    a.pos = p;
    a.spot = null;
    placeBubble(a, 0);
  }

  function walk(a, to, done) {
    stopWalk(a);
    const pts = route(a.pos, to), lens = [];
    let total = 0;
    for (let i = 1; i < pts.length; i++) {
      const l = Math.hypot(pts[i].x - pts[i - 1].x, pts[i].y - pts[i - 1].y);
      lens.push(l);
      total += l;
    }
    if (calm() || doc.hidden || total < 2 || typeof a.actor.animate !== "function") {
      place(a, to);
      if (done) done();
      return;
    }
    const ms = (total / WALK_SPEED) * 1000, frames = [], bframes = [];
    let acc = 0;
    pts.forEach((p, i) => {
      if (i) acc += lens[i - 1];
      const offset = acc / total, bx = Math.min(S.W - 110, Math.max(110, p.x));
      frames.push({ left: p.x / 16 + "%", top: p.y / 9 + "%", offset });
      bframes.push({ left: bx / 16 + "%", top: (p.y - 235) / 9 + "%", offset });
    });
    // Her parçada katman (camın arkası, masaların arkası/önü) ve bakış yönü değişir
    acc = 0;
    for (let i = 1; i < pts.length; i++) {
      const p0 = pts[i - 1], p1 = pts[i], at = (acc / total) * ms;
      acc += lens[i - 1];
      const z = inRoom(p0) && inRoom(p1) ? 10
        : p0.y <= 610 && p1.y <= 610 ? 30 : (p0.y >= 850 || p1.y >= 850 ? 60 : 40);
      const dir = p1.x < p0.x - 0.5 ? "l" : (p1.x > p0.x + 0.5 ? "r" : "");
      const apply = () => {
        a.actor.style.zIndex = String(z);
        if (dir) setDir(a, dir);
      };
      if (i === 1) apply();
      else a.timers.push(setTimeout(apply, at));
    }
    a.bubble.style.setProperty("--tail", "0cqw");
    a.actor.classList.add("walk");
    const anim = a.actor.animate(frames, { duration: ms, easing: "linear", fill: "forwards" });
    const banim = a.bubble.animate(bframes, { duration: ms, easing: "linear", fill: "forwards" });
    a.anim = anim;
    a.banim = banim;
    anim.onfinish = () => {
      if (a.anim !== anim) return;
      clearTimers(a);
      a.anim = a.banim = null;
      a.actor.classList.remove("walk");
      place(a, to);
      anim.cancel();
      banim.cancel();
      if (done) done();
    };
  }

  // Ayak işi (Jev'in görev kartı götürmesi): sırayla yürünür; sıra uzarsa yalnızca sonuncusu kalır
  function errand(a, leg) {
    a.queue.push(leg);
    if (a.queue.length > 3) a.queue = a.queue.slice(-1);
    pump(a);
  }

  function pump(a) {
    if (a.errand) return;
    const leg = a.queue.shift();
    if (!leg) {
      settle(a);
      return;
    }
    a.errand = leg;
    a.moving = null;
    renderAgent(a);
    walk(a, leg.to, () => {
      leg.arrived = true;
      renderAgent(a);
      setTimeout(() => {
        a.errand = null;
        renderAgent(a);
        pump(a);
      }, leg.dwell || 0);
    });
  }

  // Karakteri durumuna uygun yere götürür; ayak işi sürerken dokunmaz (pump bitince kendisi çağırır)
  function settle(a, instant) {
    if (a.errand || a.queue.length) return;
    const name = targetName(a), el = a.actor;
    if (!name) {
      if (a.anim) stopWalk(a);
      a.moving = null;
      if (!a.pos) place(a, spotOf("home"));
      if (!el.classList.contains("gone")) {
        el.classList.add("gone");
        a.bubble.classList.remove("show");
      }
      return;
    }
    const to = spotOf(name);
    if (!to) return;
    if (!a.pos || instant || el.classList.contains("gone")) {
      if (a.anim) stopWalk(a);
      a.moving = null;
      place(a, to);
      if (el.classList.contains("gone")) {
        el.classList.remove("gone");
        renderAgent(a);
      }
      return;
    }
    if (a.moving ? a.moving === name : a.spot === name) return;
    a.moving = name;
    walk(a, to, () => {
      a.moving = null;
      renderAgent(a);
    });
  }

  // --- efektler -----------------------------------------------------------------------------

  function confetti(a) {
    if (calm() || doc.hidden) return;
    const r = a.actor.getBoundingClientRect();
    if (!r.width) return;
    const fx = $("#fx"), cx = r.left + r.width / 2, cy = r.top + r.height * 0.25;
    for (let i = 0; i < 22; i++) {
      const el = h("div", { class: "confetti", style: `background:${CONFETTI[i % CONFETTI.length]}` });
      fx.append(el);
      const ang = -Math.PI / 2 + (Math.random() - 0.5) * 2.2, sp = 60 + Math.random() * 110;
      const dx = Math.cos(ang) * sp, dy = Math.sin(ang) * sp, fall = 90 + Math.random() * 120;
      const rot = (Math.random() - 0.5) * 720;
      el.animate([
        { transform: `translate(${cx}px, ${cy}px) rotate(0deg)`, opacity: 1 },
        { transform: `translate(${cx + dx}px, ${cy + dy}px) rotate(${rot / 2}deg)`, opacity: 1, offset: 0.35 },
        { transform: `translate(${cx + dx * 1.3}px, ${cy + dy + fall}px) rotate(${rot}deg)`, opacity: 0 },
      ], { duration: 1300 + Math.random() * 500, easing: "cubic-bezier(.2,.6,.4,1)" }).onfinish = () => el.remove();
    }
  }

  const popNext = new Map();

  function pop(x, y, ok) {
    const k = Math.round(x) + ":" + Math.round(y), now = Date.now();
    const at = Math.max(now, popNext.get(k) || 0);
    popNext.set(k, at + 320);
    setTimeout(() => {
      const el = h("div", { class: "pop " + (ok ? "ok" : "fail"), style: `left:${x / 16}%;top:${y / 9}%` }, ok ? "✓" : "✗");
      stage.append(el);
      setTimeout(() => el.remove(), 1800);
    }, at - now);
  }

  function flash(el) {
    if (!el) return;
    el.classList.remove("flash-guard");
    void el.offsetWidth;
    el.classList.add("flash-guard");
    setTimeout(() => el.classList.remove("flash-guard"), 1800);
  }

  // --- görevler: kanban ve beyaz tahta ------------------------------------------------------

  function progress() {
    let done = 0, total = 0;
    for (const t of tasks.values()) {
      if (t.status === "split") continue;
      total++;
      if (FINISHED.has(t.status)) done++;
    }
    return { done, total, pct: total ? Math.round((100 * done) / total) : 0 };
  }

  function scheduleTasks() {
    if (tasksFrame) return;
    tasksFrame = requestAnimationFrame(() => {
      tasksFrame = 0;
      renderTasks(true);
    });
  }

  function capture() {
    const m = new Map();
    for (const [id, el] of cardEls) if (el.isConnected) m.set(id, el.getBoundingClientRect());
    return m;
  }

  function play(before) {
    for (const [id, el] of cardEls) {
      const a = before.get(id);
      if (!a) continue;
      const b = el.getBoundingClientRect(), dx = a.left - b.left, dy = a.top - b.top;
      if (Math.abs(dx) < 1 && Math.abs(dy) < 1) continue;
      el.style.zIndex = "2";
      el.animate([{ transform: `translate(${dx}px, ${dy}px)` }, { transform: "translate(0, 0)" }],
        { duration: 480, easing: "cubic-bezier(.2,.8,.2,1)" }).onfinish = () => { el.style.zIndex = ""; };
    }
  }

  function renderTasks(animate) {
    const before = animate && !calm() && !doc.hidden ? capture() : null;
    const cols = {};
    for (const c of $$("#kanban .col")) cols[c.dataset.col] = { box: $(".cards", c), n: $(".n", c), items: [] };
    for (const id of taskOrder) {
      const t = tasks.get(id);
      if (t) (cols[COLUMN[t.status]] || cols.pending).items.push(t);
    }
    const seen = new Set();
    for (const col of Object.values(cols)) {
      col.n.textContent = String(col.items.length);
      col.items.forEach((t, i) => {
        let el = cardEls.get(t.task_id);
        if (!el) {
          el = h("button", { class: "card", type: "button", "data-task": t.task_id });
          cardEls.set(t.task_id, el);
        }
        fillCard(el, t);
        if (col.box.children[i] !== el) col.box.insertBefore(el, col.box.children[i] || null);
        seen.add(t.task_id);
      });
    }
    for (const [id, el] of cardEls) {
      if (!seen.has(id)) {
        el.remove();
        cardEls.delete(id);
      }
    }
    if (before) play(before);
    renderNotes();
    renderProgress();
    if (flyQueue.length) flyIn(flyQueue.splice(0));
  }

  function fillCard(el, t) {
    const k = [t.status, t.agent, t.attempt, t.title, t.complexity, t.round, t.suggested_agent, t.parallel].join("|");
    if (cardKeys.get(el) === k) return;
    cardKeys.set(el, k);
    el.className = "card" + (t.agent ? " ag-" + safe(t.agent) : "") + (t.status === "running" ? " hot" : "")
      + (t.status === "skipped" || t.status === "split" ? " dim" : "");
    const tags = h("span", { class: "tags" });
    if (t.complexity) tags.append(h("span", { class: "tag", title: "Zorluk" }, t.complexity));
    if ((t.attempt || 0) > 1) tags.append(h("span", { class: "tag warn" }, t.attempt + ". deneme"));
    if (t.status in STATUS_TAG) {
      tags.append(h("span", { class: "tag" + (STATUS_TAG[t.status] ? " " + STATUS_TAG[t.status] : "") }, lbl("status", t.status)));
    }
    if ((t.round || 1) > 1) tags.append(h("span", { class: "tag info" }, "Tur " + t.round));
    if (t.parallel) {
      tags.append(h("span", { class: "tag info", title: "Kendi çalışma kopyasında, başka görevlerle aynı anda" }, "Paralel"));
    }
    const av = t.agent ? h("span", { class: "av", html: C.head(t.agent) }) : h("span", { class: "av none" });
    const whoText = t.agent ? disp(t.agent) : (t.suggested_agent ? "Öneri: " + disp(t.suggested_agent) : "Atanmadı");
    el.textContent = "";
    el.append(h("span", { class: "r1" }, av, h("span", { class: "tid" }, t.task_id), tags),
      h("span", { class: "ti" }, t.title || ""), h("span", { class: "who" }, whoText));
    el.title = `${t.task_id} · ${lbl("status", t.status)}${t.title ? " — " + t.title : ""}`;
  }

  // Not eğimi görev kimliğinden gelir; her çizimde aynı kalır
  function noteTilt(id) {
    let x = 0;
    for (const ch of String(id)) x = (x * 31 + ch.codePointAt(0)) >>> 0;
    return ((x % 11) - 5) * 0.9;
  }

  function renderNotes() {
    const ids = taskOrder.filter((id) => tasks.has(id));
    const over = ids.length > NOTES_MAX;
    const shown = over ? ids.slice(0, NOTES_MAX - 1) : ids;
    const keep = new Set(shown);
    for (const [id, el] of noteEls) {
      if (!keep.has(id)) {
        el.remove();
        noteEls.delete(id);
      }
    }
    shown.forEach((id, i) => {
      const t = tasks.get(id);
      let el = noteEls.get(id);
      if (!el) {
        el = h("button", { class: "wb-note", type: "button", "data-task": id, style: `--r:${noteTilt(id)}deg` },
          id.length > 5 ? id.slice(0, 5) : id);
        noteEls.set(id, el);
      }
      el.dataset.s = t.status || "pending";
      el.title = `${id} · ${lbl("status", t.status)}${t.title ? " — " + t.title : ""}`;
      if (notesBox.children[i] !== el) notesBox.insertBefore(el, notesBox.children[i] || null);
    });
    let more = $(".wb-note.more", notesBox);
    if (over) {
      if (!more) more = h("span", { class: "wb-note more" });
      more.textContent = "+" + (ids.length - shown.length);
      notesBox.append(more);
    } else if (more) {
      more.remove();
    }
  }

  // Yeni görevler tahtadan panoya uçan notlarla gelir
  function flyIn(ids) {
    if (calm() || doc.hidden || !ids.length) return;
    const fx = $("#fx");
    ids.slice(0, FLY_MAX).forEach((id, i) => {
      setTimeout(() => {
        const from = noteEls.get(id) || notesBox, to = cardEls.get(id);
        if (!to || !to.isConnected) return;
        const a = from.getBoundingClientRect(), b = to.getBoundingClientRect();
        if (!b.width || !a.width) return;
        const el = h("div", { class: "fly" }, id);
        fx.append(el);
        const x0 = a.left + a.width / 2 - 19, y0 = a.top + a.height / 2 - 13, x1 = b.left + 14, y1 = b.top + 6;
        const mx = (x0 + x1) / 2, my = Math.min(y0, y1) - 60;
        el.animate([
          { transform: `translate(${x0}px, ${y0}px) rotate(-8deg) scale(.6)`, opacity: 0 },
          { transform: `translate(${mx}px, ${my}px) rotate(6deg) scale(1.1)`, opacity: 1, offset: 0.45 },
          { transform: `translate(${x1}px, ${y1}px) rotate(0deg) scale(.9)`, opacity: 0.9 },
        ], { duration: 900, easing: "cubic-bezier(.3,.7,.3,1)" }).onfinish = () => el.remove();
      }, i * 80);
    });
  }

  // --- üst çubuk ----------------------------------------------------------------------------

  function renderHeader() {
    const r = snap.run, plan = snap.plan;
    const name = r.project_name || (plan && plan.project_name) || "Yeni proje";
    const pn = $("#proj-name"), pr = $("#proj-req");
    setText(pn, name);
    pn.title = name;
    setText(pr, r.request || "");
    pr.title = r.request || "";
    doc.title = name + " · Jev Ofis";
    renderPhases();
    renderPills();
    renderProgress();
    renderElapsed();
    $("#btn-plan").disabled = !plan;
    $("#btn-report").disabled = !(snap.reports && snap.reports.length);
  }

  function renderPhases() {
    const r = snap.run, ph = r.phase, box = $("#phases");
    const paused = ph === "paused", round = r.round || 1;
    const eff = paused ? r.resume_phase || "" : ph;
    const fix = round > 1 || eff === "fixing";
    // Akış ölçeğe göre sunucudan gelir (mini ve küçükte plan yok); eski koşu tam hattı çizer
    const base = Array.isArray(r.flow) && r.flow.length ? r.flow.slice() : MAIN_FLOW.slice();
    if (eff === "awaiting_approval" && !base.includes(eff)) base.splice(base.indexOf("planning") + 1, 0, eff);
    const flow = fix ? FIX_FLOW
      : base.filter((p) => p !== "awaiting_approval" || r.plan_approval || eff === "awaiting_approval");
    const idx = flow.indexOf(eff);
    box.textContent = "";
    if (fix) box.append(h("li", { class: "ph-sep" }, "Tur " + round + ":"));
    flow.forEach((p, i) => {
      if (i) box.append(h("li", { class: "ph-sep", "aria-hidden": "true" }, "›"));
      let cls = "ph" + (fix ? " fixr" : "");
      if (idx >= 0 && i < idx) cls += " done";
      else if (i === idx) cls += " cur" + (paused || p === "awaiting_approval" ? " wait" : "");
      // geçilmiş onay adımı "✓ Onay bekliyor" diye kalmasın
      const text = p === "awaiting_approval" && idx >= 0 && i < idx ? "Onaylandı" : lbl("phase", p);
      const li = h("li", { class: cls }, text);
      if (i === idx) {
        li.setAttribute("aria-current", "step");
        if (paused) li.title = "Duraklatıldı; Devam ile buradan sürer.";
      }
      box.append(li);
    });
    if (paused && idx < 0) box.append(h("li", { class: "ph cur wait" }, lbl("phase", "paused")));
    if (ph === "aborted") box.append(h("li", { class: "ph cur wait" }, lbl("phase", "aborted")));
  }

  function renderPills() {
    const r = snap.run, box = $("#run-pills");
    box.textContent = "";
    const sc = r.scale;
    if (sc && sc.level) {
      const pct = sc.p != null ? ` %${Math.round(Number(sc.p) * 100)}` : "";
      box.append(h("span", { class: "chip info", title: `İş boyu ${sc.level_tr} · zorluk ${sc.difficulty_tr} · ` +
        `kaynak: ${sc.source_tr || "-"}${pct}` + (sc.flow ? "\n" + sc.flow : "") }, `📏 ${sc.level_tr} · ${sc.difficulty_tr}`));
    }
    if (r.dry_run) box.append(h("span", { class: "chip info", title: "Kuru çalışma: gerçek model çağrısı yapılmaz" }, "kuru"));
    if (r.dry_run && r.speed && Number(r.speed) !== 1) box.append(h("span", { class: "chip", title: "Kuru çalışma hızı" }, "hız ×" + r.speed));
    if (r.branch) box.append(h("span", { class: "chip", title: "Git dalı: " + r.branch }, "⎇ " + shorten(r.branch, 32)));
    if ((r.round || 1) > 1) box.append(h("span", { class: "chip warn", title: "Düzeltme turu" }, "Tur " + r.round));
  }

  function renderProgress() {
    const p = progress();
    setText($("#prog-text"), p.total ? `${p.done}/${p.total} görev` : "Görev yok");
    $("#prog-bar").style.width = p.pct + "%";
    $(".prog").title = p.total ? `Biten görevler: ${p.done}/${p.total} (%${p.pct})` : "Henüz görev yok";
  }

  function renderElapsed() {
    if (!snap) return;
    // Yalnız çalışma süresi: onay, rapor ve duraklama beklemesi sayılmaz (sunucudaki work_s ile aynı kural)
    const r = snap.run, since = Date.parse(r.phase_since || "");
    let ms = (Number(r.work_s) || 0) * 1000;
    if (WORK_PHASES.has(r.phase) && !isNaN(since)) ms += Math.max(0, serverNow() - since);
    setText($("#elapsed"), clock(ms));
  }

  // --- kontrol çubuğu ve komutlar -----------------------------------------------------------

  function btn(label, kind, onClick, disabled, title) {
    return h("button", { class: "btn" + (kind ? " " + kind : ""), type: "button", disabled: !!disabled,
      title: title || null, onclick: onClick }, label);
  }

  function workingText() {
    const p = progress(), ph = phaseNow();
    const r = snap.run, sc = r.scale || {};
    const planner = roleName("planlayici", "Opus"), reviewer = disp(r.reviewer) || roleName("denetci", "Sol");
    const single = Array.isArray(r.flow) && r.flow.length > 0 && !r.flow.includes("planning");
    const base = {
      sizing: "Jev işin boyunu ve zorluğunu ölçüyor.",
      planning: `${planner} isteği inceliyor; planı ve görev kartlarını yazıyor.`,
      fixing: single ? "Jev düzeltme notunu tek göreve çeviriyor." : `${planner} düzeltme kartlarını yazıyor.`,
      executing: single ? "Jev işi tek ustaya verdi; plan yok." : "Jev görev kartlarını ustalara dağıtıyor.",
      reviewing: sc.jev_review ? "Jev doğrulama komutlarını çalıştırıyor; raporu kendisi yazacak."
        : `Jev son kontrol komutlarını çalıştırıyor; raporu ${reviewer} yazacak.`,
    }[ph] || "";
    return (base + (p.total ? ` ${p.done}/${p.total} görev bitti.` : "")).trim();
  }

  function renderControls() {
    if (!snap) return;
    const r = snap.run, c = snap.controls || {}, ph = r.phase, box = $("#controls");
    const inter = c.interactive !== false, busy = pending || !!c.pending, off = !inter || busy;
    const offTitle = !inter ? "Bu koşu bitince kapanacak şekilde başlatıldı." : (busy ? "Komut işleniyor…" : null);
    const msg = h("div", { class: "msg" }), acts = h("div", { class: "acts" });
    let kind = "working", hint = "";
    if (ph === "awaiting_approval") {
      kind = ph;
      msg.append(h("b", null, "Plan onayını bekliyor"),
        h("span", { class: "sub" }, `${roleName("planlayici", "Opus")} planı ve görev kartlarını yazdı. İncele; onaylarsan ustalar çalışmaya başlar.`));
      acts.append(btn("Planı incele", "", () => openPlan()), btn("Planı onayla", "ok", () => command("onayla"), off, offTitle),
        btn("Reddet ve çık", "danger", () => rejectPlan(), off, offTitle));
    } else if (ph === "paused") {
      kind = ph;
      const sub = h("span", { class: "sub" }, r.paused_reason || "Koşu duraklatıldı.");
      if (r.pause_kind === "kota" && r.quota_until) {
        sub.append(" · Kota " + ek(hhmm(r.quota_until), "de") + " açılır, ",
          h("span", { class: "countdown", "data-until": r.quota_until }, "kalan " + clock(Date.parse(r.quota_until) - serverNow())));
      }
      msg.append(h("b", null, "Koşu duraklatıldı"), sub);
      acts.append(btn("Devam", "primary", () => command("devam"), off, offTitle));
      hint = "Devam edince Jev kaldığı yerden sürdürür" + (r.resume_phase_tr ? ` (${r.resume_phase_tr})` : "") + ".";
    } else if (ph === "reported") {
      kind = ph;
      const rep = snap.report || (snap.reports || []).slice(-1)[0] || {};
      if (rep.verdict) box.dataset.verdict = rep.verdict;
      else delete box.dataset.verdict;
      msg.append(h("b", null, "Rapor hazır · ", h("span", { class: "verdict " + safe(rep.verdict) }, rep.verdict_tr || lbl("verdict", rep.verdict))),
        h("span", { class: "sub" }, shorten(rep.summary || "", 240)));
      acts.append(btn("Raporu aç", "", () => openReport()), btn("Düzelt…", "primary", () => openReport(null, true), off, offTitle));
      hint = cleanReport(rep) ? "Akış burada durdu. Eksik yok; yine de değişiklik istersen Düzelt'e bas ve ne istediğini yaz."
        : "Akış burada durdu. Düzeltme turunu yalnızca sen başlatırsın; istersen bir not ekle.";
    } else if (ph === "aborted") {
      kind = ph;
      msg.append(h("b", null, "Koşu iptal edildi"), h("span", { class: "sub" }, r.paused_reason || "Jev koşuyu durdurdu."));
    } else {
      msg.append(h("b", null, lbl("phase", ph)), h("span", { class: "sub" }, workingText()));
    }
    if (kind !== "working" && kind !== "aborted") {
      if (!inter) hint = "Bu koşu bitince kapanacak şekilde başlatıldı; komutları terminalden ver (jev duzelt, jev devam).";
      else if (busy) hint = "Komut işleniyor…";
    }
    if (kind !== "reported") delete box.dataset.verdict;
    box.dataset.kind = kind;
    box.textContent = "";
    box.append(msg);
    if (acts.childElementCount) box.append(acts);
    if (hint) box.append(h("div", { class: "hint" }, hint));
  }

  function clearPending() {
    pending = false;
    clearTimeout(pendingTimer);
  }

  async function command(cmd, body) {
    if (pending || !snap) return false;
    pending = true;
    pendingPhase = snap.run.phase;
    clearTimeout(pendingTimer);
    pendingTimer = setTimeout(() => { pending = false; renderControls(); syncDialogs(); }, PENDING_MS);
    renderControls();
    syncDialogs();
    let ok = false;
    try {
      const r = await fetch("/api/komut/" + cmd, {
        method: "POST", cache: "no-store",
        headers: { "X-Jev-Key": key, "Content-Type": "application/json" },
        body: JSON.stringify(body || {}),
      });
      let j = {};
      try { j = (await r.json()) || {}; } catch (_) { j = {}; }
      ok = r.ok && !!j.ok;
      if (ok) toast(CMD_OK[cmd] || j.mesaj || "Tamam.", "ok");
      else toast(j.mesaj || `Komut gönderilemedi (HTTP ${r.status}).`, r.status === 409 ? "warn" : "err");
    } catch (_) {
      toast("Komut gönderilemedi: ofise ulaşılamıyor.", "err");
    }
    if (!ok) clearPending();
    renderControls();
    syncDialogs();
    refreshSoon(300);
    return ok;
  }

  async function rejectPlan() {
    if (!window.confirm("Plan reddedilsin ve koşu kapansın mı?")) return false;
    return command("reddet");
  }

  // --- akış ve kararlar ---------------------------------------------------------------------

  function feedWorthy(ev) {
    if (!FEED_TYPES.has(ev.type)) return false;
    const d = ev.data || {};
    if (ev.type === "task.update") return FEED_TASK_STATUSES.has(d.status);
    if (ev.type === "log") return d.level !== "debug";
    return true;
  }

  function feedItem(ev, fresh) {
    const d = ev.data || {}, tx = h("span", { class: "tx" });
    let cls = "", task = d.task_id || null;
    switch (ev.type) {
      case "log": {
        const lv = d.level || "info";
        cls = lv === "warn" ? "lv-warn" : lv === "error" ? "lv-error" : lv === "ok" ? "ok" : "";
        tx.append((LOG_ICON[lv] || "•") + " " + (d.text || ""));
        task = null;
        break;
      }
      case "jev.dispatch": {
        tx.append(`📋 ${d.task_id || "?"} → `, who(d.agent));
        if (d.reason) tx.append(h("span", { class: "dim" }, " (" + shorten(d.reason, 120) + ")"));
        const bits = [];
        if (d.effort) bits.push(d.effort);
        if ((d.attempt || 0) > 1) bits.push(d.attempt + ". deneme");
        if (d.escalation) bits.push("⬆ eskalasyon");
        if (bits.length) tx.append(h("span", { class: "dim" }, " · " + bits.join(" · ")));
        break;
      }
      case "jev.consult":
        tx.append(`📻 ${d.task_id ? d.task_id + ": " : ""}` + (d.brain === "jev" ? "Jev düşünüyor (Jev modeli)"
          : `Jev, ${ek(disp(d.brain) || "beyin", "e")} danışıyor`));
        if (d.problem) tx.append(h("span", { class: "dim" }, " — " + lbl("outcome", d.problem)));
        break;
      case "jev.decision":
        tx.append(`◆ ${d.task_id ? d.task_id + " kararı" : "Karar"}: ${lbl("decision", d.decision)}`);
        if (d.agent) tx.append(" → ", who(d.agent));
        if (d.rationale) tx.append(h("span", { class: "dim" }, " — " + shorten(d.rationale, 160)));
        break;
      case "guard.blocked":
        cls = "bad";
        tx.append(`🛡 Koruma: ${disp(d.agent) || "?"} · ${d.category_tr || d.category || "engellendi"}`);
        if (d.reason) tx.append(h("span", { class: "dim" }, " — " + shorten(d.reason, 140)));
        break;
      case "quota":
        cls = "lv-warn";
        tx.append(`💤 ${ek(disp(d.agent) || "?", "in")} kotası doldu · ${ek(hhmm(d.until), "de")} açılır`);
        break;
      case "verify": {
        cls = d.passed ? "ok" : "bad";
        let src = d.task_id || "";
        if (d.source === "son_kontrol") { src = "Son kontrol"; task = null; }
        else if (d.source === "tam_test") src = d.task_id ? d.task_id + " · tam test" : "Tam test";
        tx.append(`${d.passed ? "✔" : "✖"} ${src ? src + " · " : ""}`, h("code", null, shorten(d.command || "", 90)));
        if (!d.passed && d.exit_code != null) tx.append(h("span", { class: "dim" }, ` (çıkış ${d.exit_code})`));
        break;
      }
      case "report.ready":
        cls = "phase";
        tx.append(`📄 Rapor hazır (Tur ${d.round || 1}): ${lbl("verdict", d.verdict)} · ${d.gaps ? d.gaps + " eksik" : "eksik yok"}`);
        if (d.by === "jev") tx.append(h("span", { class: "dim" }, " · raporu Jev yazdı"));
        break;
      case "run.scale": {
        cls = "phase";
        const pct = d.p != null ? ` %${Math.round(Number(d.p) * 100)}` : "";
        tx.append(`📏 İş boyu: ${d.level_tr || d.level || "?"} · zorluk ${d.difficulty_tr || d.difficulty || "?"} (${d.source_tr || "-"}${pct})`);
        if (d.flow) tx.append(h("span", { class: "dim" }, " — " + shorten(d.flow, 160)));
        break;
      }
      case "run.phase":
        cls = "phase";
        tx.append(`▸ Aşama: ${lbl("phase", d.phase)}${(d.round || 1) > 1 ? ` (Tur ${d.round})` : ""}`);
        if ((d.phase === "paused" || d.phase === "aborted") && d.reason) tx.append(h("span", { class: "dim" }, " — " + shorten(d.reason, 140)));
        break;
      case "task.update": {
        const s = d.status;
        cls = s === "done" ? "ok" : (s === "failed" || s === "blocked") ? "bad"
          : (s === "needs_decision" || s === "waiting_quota") ? "lv-warn" : "";
        tx.append(`${d.task_id} · ${lbl("status", s)}`);
        if (d.title) tx.append(h("span", { class: "dim" }, " — " + shorten(d.title, 100)));
        break;
      }
      default:
        return null;
    }
    const el = h("div", { class: "fi" + (cls ? " " + cls : "") + (fresh ? " new" : "") },
      h("time", { datetime: ev.ts || null }, hhmm(ev.ts)), tx);
    if (task) {
      el.dataset.task = task;
      el.tabIndex = 0;
      el.setAttribute("role", "button");
    }
    return el;
  }

  function renderFeedAll() {
    const keep = feedBox.scrollTop;
    feedBox.textContent = "";
    for (let i = feed.length - 1; i >= 0; i--) {
      const el = feedItem(feed[i], false);
      if (el) feedBox.append(el);
    }
    if (!feedBox.childElementCount) feedBox.append(h("div", { class: "pane-empty" }, "Henüz olay yok."));
    feedBox.scrollTop = keep;
  }

  function feedPush(ev) {
    feed.push(ev);
    if (feed.length > FEED_MAX) feed = feed.slice(-FEED_MAX);
    const el = feedItem(ev, true);
    if (!el) return;
    const empty = $(".pane-empty", feedBox);
    if (empty) empty.remove();
    const scrolled = feedBox.scrollTop > 4, h0 = feedBox.scrollHeight;
    feedBox.prepend(el);
    while (feedBox.children.length > FEED_MAX) feedBox.lastElementChild.remove();
    if (scrolled) feedBox.scrollTop += feedBox.scrollHeight - h0;  // okuyan kullanıcının yeri kaymasın
  }

  const devKind = (v) => (v === true ? "minor" : v === "minor" || v === "major" ? v : "none");

  // Karar kartı: hem canlı jev.decision olayını hem de /api/ajan/jev/log kayıtlarını gösterir
  function decCard(d, ts, fresh) {
    const dev = devKind(d.deviation);
    const head = h("h4", null);
    if (d.task_id) head.append(h("span", { class: "tag" }, d.task_id));
    head.append(h("span", null, cap(d.decision_tr || lbl("decision", d.decision) || "karar")));
    if (d.agent) head.append("→", who(d.agent));
    if (dev !== "none") head.append(h("span", { class: "chip " + (dev === "major" ? "err" : "warn") }, d.deviation_tr || lbl("deviation", dev)));
    const meta = [];
    if (ts) meta.push(hhmm(ts));
    const brain = d.brain === "kural" ? "basit kural" : (d.brain ? disp(d.brain) : "");
    if (brain) meta.push("karar veren: " + brain);
    if (d.problem) meta.push("sorun: " + (d.problem_tr || lbl("outcome", d.problem)));
    if (d.effort) meta.push("efor: " + d.effort);
    const applied = d.applied_tr || (d.applied ? lbl("applied", d.applied) : "");
    if (applied) meta.push(applied);
    const box = h("div", { class: "dc" + (fresh ? " new" : "") }, head);
    if (meta.length) box.append(h("div", { class: "meta" }, meta.join(" · ")));
    if (d.problem_text) box.append(h("p", null, h("b", null, "Sorun: "), shorten(d.problem_text, 300)));
    if (d.rationale) box.append(h("p", null, shorten(d.rationale, 500)));
    if (d.guidance) box.append(h("p", null, h("b", null, "Yönlendirme: "), shorten(d.guidance, 400)));
    const notes = (Array.isArray(d.notes) ? d.notes : []).filter(Boolean).slice(0, 5);
    if (notes.length) box.append(h("ul", null, notes.map((n) => h("li", null, shorten(String(n), 200)))));
    return box;
  }

  function renderDecAll() {
    decBox.textContent = "";
    for (let i = decisions.length - 1; i >= 0; i--) decBox.append(decCard(decisions[i].data || {}, decisions[i].ts, false));
    if (!decisions.length) decBox.append(h("div", { class: "pane-empty" }, "Jev henüz bir karar vermedi."));
    setText($("#dec-count"), decisions.length ? String(decisions.length) : "");
  }

  function decPush(ev) {
    decisions.push(ev);
    if (decisions.length > DECISIONS_MAX) decisions = decisions.slice(-DECISIONS_MAX);
    const empty = $(".pane-empty", decBox);
    if (empty) empty.remove();
    decBox.prepend(decCard(ev.data || {}, ev.ts, true));
    while (decBox.children.length > DECISIONS_MAX) decBox.lastElementChild.remove();
    setText($("#dec-count"), String(decisions.length));
  }

  const sig = (arr) => (arr.length ? `${arr.length}:${arr[0].seq}:${arr[arr.length - 1].seq}` : "0");

  function mergeSeq(arr, add, max) {
    if (!add || !add.length) return arr;
    const m = new Map();
    for (const e of arr) m.set(e.seq, e);
    for (const e of add) if (e && typeof e.seq === "number") m.set(e.seq, e);
    return Array.from(m.values()).sort((x, y) => x.seq - y.seq).slice(-max);
  }

  // --- diyaloglar ---------------------------------------------------------------------------

  function show(d) {
    if (d.open) return;
    if (typeof d.showModal === "function") d.showModal();
    else d.setAttribute("open", "");
  }

  function syncDialogs() {
    const c = (snap && snap.controls) || {}, inter = c.interactive !== false, busy = pending || !!c.pending;
    const go = $("#fix-go");
    if (go) go.disabled = !(c.duzelt && inter && !busy) || (!!go.dataset.needNote && !fixDraft.trim());
    const ap = $("#plan-approve"), rj = $("#plan-reject");
    ap.hidden = rj.hidden = !c.onayla;
    ap.disabled = rj.disabled = !inter || busy;
  }

  // Ajan kartı: açıkken ilgili olaylarla en çok 1,5 saniyede bir yenilenir
  function openAgent(name) {
    if (agentView && agentView.timer) clearTimeout(agentView.timer);
    agentView = { name, timer: 0, last: 0, busy: false, again: false };
    const a = agents.get(name), hd = $("#dlg-agent-h");
    hd.textContent = "";
    hd.append(disp(name), h("small", null, a ? titleOf(a) : ""));
    $(".dlg-body", dlgAgent).textContent = "Yükleniyor…";
    show(dlgAgent);
    loadAgent();
  }

  async function loadAgent() {
    const v = agentView;
    if (!v || !dlgAgent.open) return;
    if (v.busy) {
      v.again = true;
      return;
    }
    v.busy = true;
    v.last = Date.now();
    try {
      const j = await api(`/api/ajan/${encodeURIComponent(v.name)}/log?tail=60`);
      if (agentView === v && dlgAgent.open) renderAgentDlg(j);
    } catch (e) {
      if (agentView === v) setText($(".dlg-body", dlgAgent), e.message || "Ajan bilgisi alınamadı.");
    } finally {
      v.busy = false;
      if (v.again) {
        v.again = false;
        agentSoon();
      }
    }
  }

  function agentSoon() {
    const v = agentView;
    if (!v || !dlgAgent.open || v.timer) return;
    v.timer = setTimeout(() => {
      v.timer = 0;
      loadAgent();
    }, Math.max(0, v.last + AGENT_DLG_MS - Date.now()));
  }

  function renderAgentDlg(j) {
    const name = j.name, a = agents.get(name), body = $(".dlg-body", dlgAgent), hd = $("#dlg-agent-h");
    const info = a ? a.info : { display: j.display, roles_tr: [] }, v = a ? a.v || look(a) : null;
    const keep = body.scrollTop;
    hd.textContent = "";
    hd.append(info.display || j.display || name,
      h("small", null, [a ? titleOf(a) : "", name === "jev" ? info.label : info.label || info.model].filter(Boolean).join(" · ")));
    body.textContent = "";

    const fig = h("div", { class: "fig ag-" + safe(name), html: C.build(name) });
    const rest = $(".c-arms-rest", fig);
    if (rest) rest.style.display = "inline";  // kollar yalnızca .actor içinde CSS ile açılır
    const sub = [];
    if (info.roles_tr && info.roles_tr.length) sub.push(info.roles_tr.join(", "));
    if (info.provider) sub.push(info.provider === "yerel" ? "yerel orkestratör" : info.provider);
    const quota = j.quota
      ? h("div", { class: "sub" }, `💤 Kota ${ek(j.quota.hhmm || hhmm(j.quota.until), "e")} kadar dolu${j.quota.reason ? " — " + j.quota.reason : ""}`)
      : null;
    body.append(h("div", { class: "agent-hero" }, fig,
      h("div", null, h("div", { class: "now" }, v ? v.plate : "—"), h("div", { class: "sub" }, sub.join(" · ")), quota)));

    const u = j.usage || {}, stats = h("div", { class: "stats" });
    const stat = (b, s) => stats.append(h("div", { class: "stat" }, h("b", null, b), h("span", null, s)));
    if (name === "jev") {
      const lim = snap && snap.run.brain_limit;
      stat(num(u.calls) + (lim ? " / " + lim : ""), "beyin çağrısı");
      stat(info.brain || "—", "beyin");
      stat(info.fallback || "—", "yedek beyin");
    } else {
      stat(num(u.calls), "çağrı");
      stat(num(u.input_tokens), "girdi token");
      stat(num(u.output_tokens), "çıktı token");
      stat(usd(u.cost_usd), "maliyet");
      stat(dur(u.duration_s), "toplam süre");
    }
    body.append(stats);

    if (j.current) {
      const t = j.current;
      body.append(h("h3", null, "Şu anki görev"),
        h("p", null, taskLink(t.task_id, `${t.task_id} · ${shorten(t.title || "", 60)}`), " ",
          h("span", { class: "chip info" }, t.status_tr || lbl("status", t.status))));
    }

    const lines = (j.lines || []).slice().reverse();
    body.append(h("h3", null, "Etkinlik ", h("span", { class: "n" }, String(lines.length))));
    if (lines.length) {
      body.append(h("ul", { class: "loglist" }, lines.map((l) => h("li", null,
        h("time", { datetime: l.ts || null }, hhmm(l.ts)),
        h("span", { class: "k", title: l.kind || "" }, ICON[l.kind] || "•"),
        h("span", { class: "t" }, (l.task_id ? l.task_id + " · " : "") + (l.text || STATE_TR[l.kind] || l.kind || ""))))));
    } else {
      body.append(h("p", null, "Henüz etkinlik yok."));
    }

    const att = (j.attempts || []).slice().reverse();
    if (att.length) {
      body.append(h("h3", null, "Denemeler ", h("span", { class: "n" }, String(att.length))),
        table(["Görev", "#", "Sonuç", "Doğrulama", "Süre", "Not"], att.map((x) => ({
          bad: !!x.outcome && x.outcome !== "done",
          cells: [taskLink(x.task_id), x.n, x.outcome_tr || x.outcome || "sürüyor",
            x.verify_total ? `${x.verify_pass}/${x.verify_total}` : "—", dur(x.duration_s),
            shorten(x.summary || x.reason || "", 160)],
        })), [1, 3, 4]));
    }

    const calls = (j.calls || []).slice().reverse();
    if (calls.length) {
      body.append(h("h3", null, "Çağrılar ", h("span", { class: "n" }, String(calls.length))),
        table(["#", "Aşama", "Görev", "Sonuç", "Süre", "Token (girdi / çıktı)", "Maliyet"], calls.map((c) => ({
          bad: !c.ok,
          cells: [c.seq, c.phase_tr || c.phase, c.task || "—",
            h("span", { title: c.error_text || null }, c.ok ? "✔" : (c.error_tr || c.error_kind || "hata") + (c.quota_hit ? " 💤" : "")),
            dur(c.duration_s), `${num(c.input_tokens)} / ${num(c.output_tokens)}`, usd(c.cost_usd)],
        })), [0, 4, 5, 6]));
    }

    if (name === "jev" && Array.isArray(j.decisions)) {
      const ds = j.decisions.slice().reverse();
      body.append(h("h3", null, "Kararlar ", h("span", { class: "n" }, String(ds.length))));
      if (!ds.length) body.append(h("p", null, "Jev henüz bir karar vermedi."));
      for (const d of ds) body.append(decCard(d, d.ts, false));
    }
    body.scrollTop = keep;
  }

  function openTask(id) {
    const t = tasks.get(id);
    if (!t) {
      toast(`${id} görevi bulunamadı.`, "warn");
      return;
    }
    taskView = id;
    renderTaskDlg(t);
    show(dlgTask);
    refreshSoon(0);  // denemeler ve commit için taze görüntü
  }

  function renderTaskDlg(t) {
    const hd = $("#dlg-task-h"), body = $(".dlg-body", dlgTask), keep = body.scrollTop;
    hd.textContent = "";
    hd.append(`${t.task_id} · ${t.title || "Görev"}`, h("small", null, lbl("status", t.status)));
    body.textContent = "";
    const kv = h("dl", { class: "kv" });
    const row = (k, v) => {
      if (v == null || v === "" || (Array.isArray(v) && !v.length)) return;
      kv.append(h("dt", null, k), h("dd", null, v));
    };
    row("Durum", lbl("status", t.status));
    row("Ajan", t.agent ? who(t.agent) : (t.suggested_agent ? "Öneri: " + disp(t.suggested_agent) : "Atanmadı"));
    row("Zorluk", t.complexity);
    row("Tür", lbl("type", t.type));
    row("Modül", t.module);
    row("Bağımlılıklar", (t.depends_on || []).join(", "));
    row("Köken", ORIGIN_TR[t.origin] || t.origin);
    row("Tur", t.round);
    row("Karşıladığı ölçütler", (t.covers || []).join(", "));
    row("Deneme sayısı", t.attempt || 0);
    row("Commit", t.commit ? h("code", null, t.commit) : null);
    row("Bekleme", t.waiting_until ? ek(hhmm(t.waiting_until), "e") + " kadar" : null);
    body.append(kv);
    if (t.summary) body.append(h("h3", null, "Özet"), h("p", null, t.summary));
    if (t.description) body.append(h("h3", null, "Açıklama"), h("p", null, t.description));
    if (t.acceptance && t.acceptance.length) {
      body.append(h("h3", null, "Kabul ölçütleri"), h("ul", { class: "plain" }, t.acceptance.map((x) => h("li", null, x))));
    }
    const att = t.attempts || [];
    if (att.length) {
      body.append(h("h3", null, "Denemeler ", h("span", { class: "n" }, String(att.length))),
        table(["#", "Ajan", "Sonuç", "Doğrulama", "Süre", "Not"], att.map((x) => ({
          bad: !!x.outcome && x.outcome !== "done",
          cells: [x.n, x.agent ? who(x.agent) : "—", x.outcome_tr || x.outcome || "sürüyor",
            x.verify_total ? `${x.verify_pass}/${x.verify_total}` : "—", dur(x.duration_s),
            [x.effort ? "efor: " + x.effort : "", x.summary || x.reason || ""].filter(Boolean).join(" · ")],
        })), [0, 3, 4]));
    }
    const decs = t.decisions || [];
    if (decs.length) {
      body.append(h("h3", null, "Jev'in kararları ", h("span", { class: "n" }, String(decs.length))));
      for (const d of decs) body.append(decCard(Object.assign({ task_id: t.task_id }, d), null, false));
    }
    if (t.files && t.files.length) {
      body.append(h("h3", null, "Dosyalar"), h("ul", { class: "plain" }, t.files.map((f) => h("li", null, h("code", null, f)))));
    }
    body.scrollTop = keep;
  }

  function openReport(round, focusFix) {
    reportView = { round: round || null, focusFix: !!focusFix };
    $(".dlg-body", dlgReport).textContent = "Yükleniyor…";
    show(dlgReport);
    loadReport();
  }

  async function loadReport() {
    const v = reportView;
    if (!v) return;
    try {
      const j = await api("/api/rapor" + (v.round ? "?tur=" + encodeURIComponent(v.round) : ""));
      if (reportView === v && dlgReport.open) renderReportDlg(j);
    } catch (e) {
      if (reportView === v) setText($(".dlg-body", dlgReport), e.message || "Rapor alınamadı.");
    }
  }

  function renderReportDlg(j) {
    const body = $(".dlg-body", dlgReport), hd = $("#dlg-report-h"), sel = $("#report-round");
    hd.textContent = "";
    body.textContent = "";
    if (!j || !j.ok) {
      hd.append("Rapor");
      sel.hidden = true;
      body.append(h("p", null, (j && j.mesaj) || "Henüz rapor yok."));
      return;
    }
    hd.append("Rapor · Tur " + j.round, h("small", null, (disp(j.by) || roleName("denetci", "Sol")) + " tarafından yazıldı"));
    const reps = j.reports || [];
    sel.textContent = "";
    for (const r of reps) {
      sel.append(h("option", { value: r.round, selected: r.round === j.round }, `Tur ${r.round} · ${r.verdict_tr || r.verdict || ""}`));
    }
    sel.hidden = reps.length < 2;
    body.append(h("p", null, h("span", { class: "verdict " + safe(j.verdict) }, j.verdict_tr || lbl("verdict", j.verdict)),
      j.summary ? " — " + j.summary : ""));

    const crit = j.criteria || [];
    if (crit.length) {
      body.append(h("h3", null, "Başarı ölçütleri ", h("span", { class: "n" }, String(crit.length))),
        table(["Ölçüt", "Durum", "Kanıt"], crit.map((c) => ({
          bad: c.status === "unmet",
          cells: [c.id, h("span", { class: "st-" + safe(c.status) }, c.status_tr || c.status || ""),
            h("span", null, c.evidence || "", c.gap ? h("div", null, h("b", null, "Eksik: "), c.gap) : null)],
        }))));
    }
    const gaps = j.gaps || [];
    body.append(h("h3", null, "Eksikler ", h("span", { class: "n" }, String(gaps.length))));
    if (gaps.length) {
      body.append(h("ul", { class: "gap-list" }, gaps.map((g) => h("li", null,
        h("b", null, g.id || "?"), " ", (g.criteria || []).map((c) => [h("span", { class: "tag" }, c), " "]), g.description || "",
        g.suggested_fix ? h("span", { class: "fix" }, "Öneri: " + g.suggested_fix) : null))));
    } else {
      body.append(h("p", null, "Eksik yok. 🎉"));
    }
    if ((j.risks || []).length) body.append(h("h3", null, "Riskler"), h("ul", { class: "plain" }, j.risks.map((x) => h("li", null, x))));
    if ((j.next_steps || []).length) {
      body.append(h("h3", null, "Sonraki adımlar"), h("ul", { class: "plain" }, j.next_steps.map((x) => h("li", null, x))));
    }
    if (j.html) body.append(h("details", { class: "md-box" }, h("summary", null, "Raporun tamamı"), h("div", { class: "md", html: j.html })));
    if (phaseNow() === "reported") body.append(fixBox(j));
    syncDialogs();
    if (reportView && reportView.focusFix) {
      reportView.focusFix = false;
      const ta = $("#fix-note");
      if (ta) {
        ta.focus();
        ta.scrollIntoView({ block: "nearest" });
      }
    }
  }

  function fixBox(j) {
    const c = snap.controls || {}, inter = c.interactive !== false;
    // Son raporda eksik yoksa tur ancak kullanıcının notuyla açılır (sunucu da boş notu reddeder).
    // Eski bir tur görüntülense de kural son rapora bakar: düzeltme hep son raporun üstüne açılır.
    const clean = cleanReport(snap.report || j);
    const ta = h("textarea", { id: "fix-note", maxlength: "4000", rows: "4",
      placeholder: "Örn. temizle alt komutunu da yaz; README'ye kullanım örneği ekle." });
    ta.value = fixDraft;
    const count = h("small", null, `${fixDraft.length}/4000`);
    ta.addEventListener("input", () => {
      fixDraft = ta.value;
      count.textContent = `${fixDraft.length}/4000`;
      syncDialogs();
    });
    const go = btn("Düzelt", "primary", async () => {
      if (await command("duzelt", { not: ta.value.trim() })) {
        fixDraft = "";
        dlgReport.close();
      }
    });
    go.id = "fix-go";
    if (clean) go.dataset.needNote = "1";
    const note = !inter ? "Bu koşu bitince kapanacak şekilde başlatıldı; düzeltmeyi terminalden başlat: jev duzelt"
      : clean ? "Raporda eksik yok; tur, yazdığın nota göre açılır."
        : "Boş bırakırsan rapordaki eksiklerden düzeltme görevleri çıkarılır.";
    const box = h("div", { class: "fixbox" },
      h("label", { for: "fix-note" },
        `Düzeltme turu (Tur ${(snap.run.round || 1) + 1}) · notun (${clean ? "gerekli" : "isteğe bağlı"})`), ta,
      h("div", { class: "row" }, h("small", null, note), count, go));
    if (j.fix_note) box.append(h("small", null, "Önceki turun notu: " + shorten(j.fix_note, 300)));
    return box;
  }

  async function openPlan() {
    const body = $(".dlg-body", dlgPlan);
    body.textContent = "Yükleniyor…";
    syncDialogs();
    show(dlgPlan);
    try {
      renderPlanDlg(await api("/api/plan"));
    } catch (e) {
      setText(body, e.message || "Plan alınamadı.");
    }
  }

  function renderPlanDlg(j) {
    const body = $(".dlg-body", dlgPlan), hd = $("#dlg-plan-h");
    hd.textContent = "";
    body.textContent = "";
    if (!j || !j.ok) {
      hd.append("Plan");
      body.append(h("p", null, (j && j.mesaj) || "Plan henüz hazır değil."));
      return;
    }
    hd.append("Plan · " + (j.project_name || "proje"), h("small", null, roleName("planlayici", "Opus") + " tarafından yazıldı"));
    if (j.summary) body.append(h("p", null, j.summary));
    const stack = stackText(j.stack);
    if (stack) body.append(h("p", null, h("b", null, "Teknoloji: "), stack));
    const mods = j.modules || [];
    if (mods.length) {
      body.append(h("h3", null, "Modüller ", h("span", { class: "n" }, String(mods.length))),
        table(["Kimlik", "Modül", "Sorumluluk", "Bağımlı"], mods.map((m) => ({
          cells: [m.id, m.name, m.responsibility, (m.depends_on || []).join(", ")],
        }))));
    }
    const crit = j.success_criteria || [];
    if (crit.length) {
      body.append(h("h3", null, "Başarı ölçütleri ", h("span", { class: "n" }, String(crit.length))),
        table(["Kimlik", "Ölçüt", "Nasıl doğrulanır"], crit.map((c) => ({ cells: [c.id, c.statement, c.verification] }))));
    }
    const cmds = Object.entries(j.commands || {});
    if (cmds.length) {
      body.append(h("h3", null, "Komutlar"),
        h("dl", { class: "kv" }, cmds.map(([k, v]) => [h("dt", null, k), h("dd", null, h("code", null, v))])));
    }
    for (const [title, list] of [["Varsayımlar", j.assumptions], ["Kapsam dışı", j.out_of_scope], ["Riskler", j.risks]]) {
      if (list && list.length) body.append(h("h3", null, title), h("ul", { class: "plain" }, list.map((x) => h("li", null, x))));
    }
    if (j.html) body.append(h("details", { class: "md-box" }, h("summary", null, "Planın tamamı"), h("div", { class: "md", html: j.html })));
    syncDialogs();
  }

  // --- olaylar ------------------------------------------------------------------------------

  function concerns(ev, name) {
    const d = ev.data || {};
    if (d.agent === name || d.brain === name) return true;
    return name === "jev" && String(ev.type || "").startsWith("jev.");
  }

  function lateAgent(name) {
    const a = makeAgent(name);
    renorm(a);
    renderAgent(a);
    settle(a, true);
    refreshSoon();
    return a;
  }

  function onAgentState(ev, d) {
    if (!d.agent) return;
    const a = agents.get(d.agent) || lateAgent(d.agent);
    const raw = Object.assign({}, d, { seq: ev.seq, ts: ev.ts });
    if (a.raw && (a.raw.seq || 0) > raw.seq) return;
    const prev = a.st.state;
    a.raw = raw;
    renorm(a);
    if (a.st.state === "done" && prev !== "done") {
      a.cheerUntil = Date.now() + CHEER_MS;
      confetti(a);
      setTimeout(() => renderAgent(a), CHEER_MS + 50);
    }
    if (a.visTimer) {
      clearTimeout(a.visTimer);
      a.visTimer = 0;
    }
    renderAgent(a);
    settle(a);
    const jev = agents.get("jev");
    if (jev && jev !== a && jev.st.state === "consulting") settle(jev);  // beyin yer değiştirince Jev de gider
  }

  function onActivity(ev, d) {
    const a = agents.get(d.agent);
    if (!a) return;
    a.act = Object.assign({}, d, { seq: ev.seq, ts: ev.ts });
    const wait = a.lastVis + ACT_THROTTLE_MS - Date.now();
    if (wait <= 0) {
      a.lastVis = Date.now();
      renderAgent(a);
    } else if (!a.visTimer) {
      a.visTimer = setTimeout(() => {
        a.visTimer = 0;
        a.lastVis = Date.now();
        renderAgent(a);
      }, wait);
    }
  }

  function onUsage(d) {
    const a = agents.get(d.agent);
    if (a) a.usage = Object.assign({}, a.usage || {}, d);
  }

  function onDispatch(d) {
    const jev = agents.get("jev");
    if (!jev || !d.agent || calm() || doc.hidden) return;
    const to = spotOf("front-" + deskOf(d.agent));
    if (!to) return;
    errand(jev, { to, dwell: DWELL_MS, carry: d.task_id || "", text: `${d.task_id || "?"} → ${disp(d.agent)}` });
  }

  function onTaskUpdate(ev, d) {
    const id = d.task_id;
    if (!id) return;
    let t = tasks.get(id);
    if (!t) {
      t = { task_id: id };
      tasks.set(id, t);
      taskOrder.push(id);
      flyQueue.push(id);
    }
    Object.assign(t, d);
    t.status_tr = lbl("status", d.status);
    t._seq = ev.seq;
    scheduleTasks();
    if (phaseNow() !== "reported" && phaseNow() !== "paused" && phaseNow() !== "awaiting_approval") renderControls();
    if (taskView === id && dlgTask.open) renderTaskDlg(t);
    const jev = agents.get("jev");
    if (jev) settle(jev);  // Jev'in doğrulama yeri görevin ajanına bağlı
    if (FEED_TASK_STATUSES.has(d.status)) refreshSoon(1500);
  }

  function onVerify(d) {
    let x, y;
    const t = d.task_id ? tasks.get(d.task_id) : null;
    if (d.source === "son_kontrol" || !t) {
      const jev = agents.get("jev"), p = (jev && jev.pos) || S.SPOTS.home;
      x = p.x + 40;
      y = p.y - 225;
    } else {
      const desk = S.DESKS.find((k) => k.key === deskOf(t.agent));
      if (!desk) return;
      x = desk.cx - 105;
      y = 596;
    }
    pop(x, y, !!d.passed);
  }

  function onQuota(d) {
    const a = agents.get(d.agent);
    if (!a) return;
    a.quota = { until: d.until, hhmm: hhmm(d.until), reason: d.text || "" };
    renorm(a);
    renderAgent(a);
    settle(a);
  }

  function onGuard(d) {
    flash(stage);
    const a = agents.get(d.agent);
    if (a) {
      flash(a.actor);
      const pid = Object.keys(plates).find((id) => plates[id].el.dataset.agent === a.name);
      if (pid) flash(plates[pid].el);
      a.shieldUntil = Date.now() + 1500;
      renderAgent(a);
      setTimeout(() => renderAgent(a), 1550);
    }
    if (d.serious) toast(`🛡 Koruma, ${ek(disp(d.agent) || "ajan", "in")} işlemini durdurdu: ${shorten(d.reason || d.category_tr || "", 140)}`, "warn");
  }

  function onReport(d) {
    const kind = d.verdict === "basarili" ? "ok" : d.verdict === "basarisiz" ? "err" : "warn";
    toast(`📄 Rapor hazır (Tur ${d.round || 1}): ${lbl("verdict", d.verdict)}`, kind, { label: "Raporu aç", run: () => openReport() });
    refreshSoon(200);
    if (reportView && dlgReport.open && !reportView.round) setTimeout(loadReport, 300);
  }

  function onPhase(ev, d) {
    const r = snap.run, ph = d.phase;
    if (!ph) return;
    r.phase = ph;
    r.phase_tr = lbl("phase", ph);
    if (d.round) r.round = d.round;
    if (ph === "paused") {
      r.paused_reason = d.reason || r.paused_reason;
      r.quota_until = d.until || null;
      r.pause_kind = d.kind || null;
    }
    if (ph === "aborted" && d.reason) r.paused_reason = d.reason;
    if (typeof d.work_s === "number") r.work_s = d.work_s;
    if (ev.ts) r.updated = r.phase_since = ev.ts;
    const c = snap.controls || (snap.controls = {});
    c.duzelt = ph === "reported";
    c.onayla = c.reddet = ph === "awaiting_approval";
    c.devam = ph === "paused";
    c.pending = false;
    if (pending && ph !== pendingPhase) clearPending();
    for (const a of agents.values()) renorm(a);
    for (const a of agents.values()) renderAgent(a);
    for (const a of agents.values()) settle(a);
    renderHeader();
    renderControls();
    syncDialogs();
    refreshSoon();
  }

  function onRunInfo(d) {
    const r = snap.run;
    for (const k of ["project_name", "request", "branch", "dry_run", "reviewer"]) if (d[k] != null) r[k] = d[k];
    renderHeader();
    refreshSoon();
  }

  function onScale(d) {
    const r = snap.run;
    if (!d.level) return;
    r.scale = Object.assign({}, r.scale || {}, { level: d.level, level_tr: d.level_tr, difficulty: d.difficulty,
      difficulty_tr: d.difficulty_tr, source: d.source, source_tr: d.source_tr, p: d.p, from: d.from, flow: d.flow });
    if (Array.isArray(d.phases) && d.phases.length) r.flow = d.phases;
    renderHeader();
    renderControls();
    refreshSoon();  // denetim kimde (jev_review) gibi ayrıntıyı anlık görüntü tamamlar
  }

  function handleEvent(ev) {
    if (!ev || typeof ev.seq !== "number" || closed || !snap) return;
    if (ev.seq <= lastSeq) return;
    if (ev.seq > lastSeq + 1) refreshSoon();  // arada olay kaçtıysa anlık görüntü tamamlar
    lastSeq = ev.seq;
    const d = ev.data || {};
    try {
      switch (ev.type) {
        case "agent.state": onAgentState(ev, d); break;
        case "agent.activity": onActivity(ev, d); break;
        case "agent.usage": onUsage(d); break;
        case "jev.dispatch": onDispatch(d); break;
        case "jev.consult":
          consultSeq = ev.seq;
          consultBrain = d.brain || consultBrain;
          break;
        case "jev.decision": decPush(ev); break;
        case "task.update": onTaskUpdate(ev, d); break;
        case "verify": onVerify(d); break;
        case "quota": onQuota(d); break;
        case "guard.blocked": onGuard(d); break;
        case "report.ready": onReport(d); break;
        case "run.phase": onPhase(ev, d); break;
        case "run.info": onRunInfo(d); break;
        case "run.scale": onScale(d); break;
        default: break;
      }
      if (feedWorthy(ev)) feedPush(ev);
    } catch (err) {
      console.error("Jev ofisi: olay işlenemedi", ev.type, err);
    }
    if (agentView && concerns(ev, agentView.name)) agentSoon();
  }

  // --- anlık görüntü ------------------------------------------------------------------------

  function syncTasks(list, seq, first) {
    const fresh = [], next = new Map();
    for (const t of list) {
      if (!t || !t.task_id) continue;
      const old = tasks.get(t.task_id), v = Object.assign({}, t);
      if (old && (old._seq || 0) > seq) {  // canlı olay anlık görüntüden yeni
        v.status = old.status;
        v.agent = old.agent;
        v.attempt = old.attempt;
        v._seq = old._seq;
      } else if (!old && !first) {
        fresh.push(t.task_id);
      }
      next.set(t.task_id, v);
    }
    for (const [id, t] of tasks) if (!next.has(id) && (t._seq || 0) > seq) next.set(id, t);
    tasks.clear();
    for (const [id, t] of next) tasks.set(id, t);
    taskOrder = Array.from(next.keys());
    return fresh;
  }

  function syncAgents(list) {
    for (const s of list) {
      if (!s || !s.name) continue;
      const a = agents.get(s.name) || makeAgent(s.name);
      a.info = { display: s.display || cap(s.name), label: s.label, model: s.model, provider: s.provider,
        roles: s.roles || [], roles_tr: s.roles_tr || [], brain: s.brain, brain_display: s.brain_display,
        fallback: s.fallback };
      a.usage = s.usage || a.usage;
      a.quota = s.quota || null;
      if (s.state && (!a.raw || (s.state.seq || 0) >= (a.raw.seq || 0))) a.raw = s.state;
      if (s.activity && (!a.act || (s.activity.seq || 0) >= (a.act.seq || 0))) a.act = s.activity;
      renorm(a);
    }
  }

  function renderAll(s) {
    const first = !booted;
    const now = Date.parse(s.server_now || "");
    if (!isNaN(now)) serverOffset = now - Date.now();
    snap = s;
    L = s.labels || L;
    if ((s.last_seq || 0) > lastSeq) lastSeq = s.last_seq || 0;
    const fresh = syncTasks(s.tasks || [], s.last_seq || 0, first);
    syncAgents(s.agents || []);
    const fs = sig(feed), ds = sig(decisions);
    feed = mergeSeq(feed, s.feed, FEED_MAX);
    decisions = mergeSeq(decisions, s.decisions, DECISIONS_MAX);
    for (let i = feed.length - 1; i >= 0; i--) {
      if (feed[i].type !== "jev.consult") continue;
      if (feed[i].seq > consultSeq) {
        consultSeq = feed[i].seq;
        consultBrain = (feed[i].data || {}).brain || consultBrain;
      }
      break;
    }
    if (pending && s.run.phase !== pendingPhase && !(s.controls && s.controls.pending)) clearPending();
    renderHeader();
    renderControls();
    flyQueue.push(...fresh);
    renderTasks(!first);
    if (first || sig(feed) !== fs) renderFeedAll();
    if (first || sig(decisions) !== ds) renderDecAll();
    for (const a of agents.values()) renderAgent(a);
    for (const a of agents.values()) settle(a, first);
    if (first) {
      booted = true;
      stageMsg("");
    }
    syncDialogs();
    if (taskView && dlgTask.open) {
      const t = tasks.get(taskView);
      if (t) renderTaskDlg(t);
    }
  }

  function refreshSoon(ms) {
    const wait = ms == null ? 400 : ms, due = Date.now() + wait;
    if (refreshTimer && refreshDue <= due) return;  // daha erken bir yenileme zaten bekliyor
    clearTimeout(refreshTimer);
    refreshDue = due;
    refreshTimer = setTimeout(() => {
      refreshTimer = 0;
      refresh();
    }, wait);
  }

  async function refresh() {
    if (closed || !booted) return;
    if (refreshing) {
      refreshAgain = true;
      return;
    }
    refreshing = true;
    try {
      renderAll(await api("/api/durum"));
    } catch (e) {
      if (e.status === 403) keyGone();
      else if (!e.status) console.error("Jev ofisi: yenilenemedi", e);
    } finally {
      refreshing = false;
      if (refreshAgain) {
        refreshAgain = false;
        refreshSoon(300);
      }
    }
  }

  // --- canlı akış ---------------------------------------------------------------------------

  function connect() {
    if (closed) return;
    if (es) es.close();
    setConn("connecting");
    const src = new EventSource("/api/olaylar?k=" + encodeURIComponent(key) + "&from=" + lastSeq);
    es = src;
    src.onopen = () => {
      if (es !== src) return;
      setConn("live");
      backoff = 1000;
      connErrors = 0;
    };
    src.onmessage = (m) => {
      let ev;
      try { ev = JSON.parse(m.data); } catch (_) { return; }
      handleEvent(ev);
    };
    src.addEventListener("kapandi", () => {
      if (es !== src) return;
      closed = true;
      src.close();
      es = null;
      setConn("closed");
      stageMsg("Ofis kapandı. Jev yeniden başlarsa terminaldeki yeni ofis adresini aç.");
      toast("Ofis kapandı.", "warn");
    });
    src.onerror = () => {
      if (es !== src || closed) return;
      if (src.readyState === EventSource.CLOSED) {  // tarayıcı vazgeçti (örn. 403): anlık görüntüyle yeniden dene
        es = null;
        src.close();
        setConn("down");
        resyncLater();
      } else {
        connErrors++;
        setConn(connErrors > 3 ? "down" : "connecting");  // tarayıcı kendisi yeniden bağlanır (Last-Event-ID)
      }
    };
  }

  function resyncLater() {
    clearTimeout(resyncTimer);
    resyncTimer = setTimeout(resync, backoff);
    backoff = Math.min(backoff * 2, 15000);
  }

  async function resync() {
    if (closed) return;
    let s;
    try {
      s = await api("/api/durum");
    } catch (e) {
      if (e.status === 403) keyGone();
      else resyncLater();
      return;
    }
    renderAll(s);
    connect();
  }

  function keyGone() {
    if (closed) return;
    closed = true;
    if (es) {
      es.close();
      es = null;
    }
    setConn("down");
    stageMsg("Ofis kapandı ya da anahtar değişti. Terminaldeki yeni ofis adresini aç.");
    toast("Ofis kapandı ya da anahtar değişti. Terminaldeki yeni ofis adresini aç.", "err");
  }

  // --- saniyelik işler ----------------------------------------------------------------------

  function tick() {
    tickClock();
    if (!snap) return;
    renderElapsed();
    const now = serverNow();
    for (const a of agents.values()) {
      const expired = !!a.quota && Date.parse(a.quota.until || "") <= now;
      if (expired) a.quota = null;
      if (expired || a.st.state === "sleeping") {
        const before = a.st.state;
        renorm(a);
        renderAgent(a);  // levhadaki geri sayım
        if (a.st.state !== before) settle(a);
      }
    }
    for (const el of $$(".countdown[data-until]")) el.textContent = "kalan " + clock(Date.parse(el.dataset.until) - now);
  }

  // --- tema, sekmeler, düğmeler -------------------------------------------------------------

  function applyTheme(name) {
    const t = THEMES.find((x) => x[0] === name) || THEMES[0], b = $("#btn-theme");
    if (t[0] === "auto") delete root.dataset.theme;
    else root.dataset.theme = t[0];
    b.textContent = t[1];
    b.title = t[2];
    b.setAttribute("aria-label", t[2]);
    return t[0];
  }

  function initTheme() {
    let saved = null;
    try { saved = localStorage.getItem("jev-tema"); } catch (_) { saved = null; }
    theme = applyTheme(saved || "auto");
    $("#btn-theme").addEventListener("click", () => {
      const i = THEMES.findIndex((x) => x[0] === theme);
      theme = applyTheme(THEMES[(i + 1) % THEMES.length][0]);
      try { localStorage.setItem("jev-tema", theme); } catch (_) { /* depolama kapalı olabilir */ }
    });
  }

  function selectTab(which, silent) {
    const on = which === "feed", tf = $("#tab-feed"), td = $("#tab-dec");
    tf.setAttribute("aria-selected", String(on));
    td.setAttribute("aria-selected", String(!on));
    tf.tabIndex = on ? 0 : -1;
    td.tabIndex = on ? -1 : 0;
    feedBox.hidden = !on;
    decBox.hidden = on;
    if (!silent) {
      try { localStorage.setItem("jev-sekme", which); } catch (_) { /* depolama kapalı olabilir */ }
    }
  }

  function initTabs() {
    const tf = $("#tab-feed"), td = $("#tab-dec");
    tf.addEventListener("click", () => selectTab("feed"));
    td.addEventListener("click", () => selectTab("decs"));
    for (const t of [tf, td]) {
      t.addEventListener("keydown", (e) => {
        if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
        e.preventDefault();
        const next = t === tf ? "decs" : "feed";
        selectTab(next);
        (next === "feed" ? tf : td).focus();
      });
    }
    let saved = null;
    try { saved = localStorage.getItem("jev-sekme"); } catch (_) { saved = null; }
    selectTab(saved === "decs" ? "decs" : "feed", true);
  }

  function wire() {
    $("#btn-plan").addEventListener("click", () => openPlan());
    $("#btn-report").addEventListener("click", () => openReport());
    $("#plan-approve").addEventListener("click", async () => { if (await command("onayla")) dlgPlan.close(); });
    $("#plan-reject").addEventListener("click", async () => { if (await rejectPlan()) dlgPlan.close(); });
    $("#report-round").addEventListener("change", (e) => {
      if (!reportView) return;
      reportView.round = parseInt(e.target.value, 10) || null;
      loadReport();
    });
    for (const d of [dlgAgent, dlgReport, dlgPlan, dlgTask]) {
      d.addEventListener("click", (e) => {
        if (e.target === d || (e.target.closest && e.target.closest("[data-close]"))) d.close();
      });
    }
    dlgAgent.addEventListener("close", () => {
      if (agentView && agentView.timer) clearTimeout(agentView.timer);
      agentView = null;
    });
    dlgReport.addEventListener("close", () => { reportView = null; });
    dlgTask.addEventListener("close", () => { taskView = null; });
    doc.addEventListener("click", (e) => {
      const t = e.target.closest ? e.target.closest("[data-task]") : null;
      if (t && t.dataset.task) openTask(t.dataset.task);
    });
    doc.addEventListener("keydown", (e) => {
      const t = e.target;
      if ((e.key === "Enter" || e.key === " ") && t.matches && t.matches(".fi[data-task]")) {
        e.preventDefault();
        openTask(t.dataset.task);
      }
    });
    doc.addEventListener("visibilitychange", () => { if (!doc.hidden && booted && !closed) refreshSoon(100); });
    const top = $("#top"), setTop = () => root.style.setProperty("--top-h", top.offsetHeight + "px");
    setTop();
    if (window.ResizeObserver) new ResizeObserver(setTop).observe(top);
    else window.addEventListener("resize", setTop);
  }

  function noKey(invalid) {
    $("#app").hidden = true;
    $(".acts-top").hidden = true;
    $(".prog").hidden = true;
    $("#elapsed").hidden = true;
    setText($("#proj-name"), "Jev ofisi");
    setText($("#proj-req"), "");
    if (invalid) {
      $("#nokey-h").textContent = "Erişim anahtarı geçersiz";
      $("#nokey-p").textContent = "Bu adresteki anahtar artık geçerli değil. Jev her başladığında yeni bir anahtar üretir.";
    }
    $("#nokey").hidden = false;
    setConn("down");
  }

  async function boot() {
    let s;
    try {
      s = await api("/api/durum");
    } catch (e) {
      if (e.status === 403) {
        noKey(true);
        return;
      }
      setConn("down");
      stageMsg("Ofise ulaşılamıyor. Jev çalışıyor mu? Yeniden deneniyor…");
      setTimeout(boot, backoff);
      backoff = Math.min(backoff * 2, 15000);
      return;
    }
    backoff = 1000;
    try {
      renderAll(s);
    } catch (err) {
      console.error("Jev ofisi: çizilemedi", err);
      stageMsg("Ofis çizilemedi: " + ((err && err.message) || err));
      return;
    }
    connect();
  }

  function start() {
    initTheme();
    initTabs();
    wire();
    if (!key) {
      noKey(false);
      return;
    }
    if (!S || !C) {
      stageMsg("Sahne dosyaları yüklenemedi; sayfayı yenile.");
      return;
    }
    buildStage();
    setInterval(tick, 1000);
    boot();
  }

  start();
})();
