// AgentAiNewsReddit - frontend
const $ = (id) => document.getElementById(id);
let cfg = null;
let nextRunAt = null;

const STATUS_LABEL = {
  idle: "In attesa",
  needs_config: "Configura API key",
  running: "Analisi in corso…",
  ok: "Operativo",
  error: "Errore",
};

const ARROW = { up: "▲", down: "▼", neutral: "▬" };
const DIRLABEL = { up: "RIALZO", down: "RIBASSO", neutral: "NEUTRO" };
const BIAS_LABEL = { up: "RISK-ON", down: "RISK-OFF", neutral: "MISTO" };

let intervalMin = 5;

async function fetchState() {
  try {
    const r = await fetch("/api/state");
    const s = await r.json();
    render(s);
  } catch (e) {
    $("statusText").textContent = "Server non raggiungibile";
  }
}

function render(s) {
  cfg = s.config;
  intervalMin = (cfg && cfg.interval_minutes) || 5;
  // status
  $("statusDot").className = "dot " + s.status;
  $("statusPill").className = "status-pill " + s.status;
  $("statusText").textContent = STATUS_LABEL[s.status] || s.status;
  $("lastRun").textContent = "Ultima: " + fmtTime(s.last_run);
  nextRunAt = s.next_run ? new Date(s.next_run) : null;

  // banner
  const banner = $("banner");
  if (s.status === "needs_config") {
    banner.className = "banner";
    banner.innerHTML = "🔑 Inserisci la tua <b>OpenRouter API key</b> nelle Impostazioni per iniziare.";
  } else if (s.status === "error" && s.last_error) {
    banner.className = "banner error";
    banner.textContent = "Errore: " + s.last_error;
  } else {
    banner.className = "banner hidden";
  }

  renderHero(s.consensus || [], s.last_run, s.status);
  renderConsensus(s.consensus || []);
  renderMacro(s.macro_summary, s.relevant_news || []);
  renderAgents(s.agent_reports || []);
  renderNews(s.news || []);
  $("logs").textContent = (s.logs || []).join("\n");
  $("logs").scrollTop = $("logs").scrollHeight;
}

function renderHero(items, lastRun, status) {
  const up = items.filter(c => c.direction === "up").length;
  const down = items.filter(c => c.direction === "down").length;
  const neutral = items.filter(c => c.direction === "neutral").length;
  const total = items.length || 0;
  $("heroUp").textContent = up;
  $("heroDown").textContent = down;
  $("heroNeutral").textContent = neutral;

  let bias = "neutral";
  if (up > down) bias = "up";
  else if (down > up) bias = "down";

  const heroBias = $("heroBias");
  const heroLabel = $("heroBiasLabel");
  if (total === 0) {
    heroBias.className = "hero-bias";
    heroLabel.textContent = status === "needs_config" ? "Configura API key" : "In attesa";
    $("heroMeta").textContent = "Nessuna analisi ancora";
  } else {
    heroBias.className = "hero-bias " + bias;
    heroLabel.textContent = BIAS_LABEL[bias];
    const avg = Math.round(items.reduce((a, c) => a + (c.confidence || 0), 0) / total);
    $("heroMeta").textContent =
      `${total} asset · forza media ${avg}% · aggiornato ${fmtTime(lastRun)}`;
  }

  const pct = (n) => total ? (n / total * 100) : 0;
  $("segUp").style.width = pct(up) + "%";
  $("segNeutral").style.width = pct(neutral) + "%";
  $("segDown").style.width = pct(down) + "%";
}

function renderConsensus(items) {
  const grid = $("consensusGrid");
  $("consensusEmpty").classList.toggle("hidden", items.length > 0);
  $("assetCount").textContent = items.length ? items.length + " asset" : "";
  grid.innerHTML = items.map((c, i) => {
    const d = c.direction;
    const bd = (c.breakdown || []).map(b =>
      `<div class="bd"><b>${esc(b.agent)}</b> · <span class="dirlabel ${b.direction}">${DIRLABEL[b.direction]}</span> (${b.confidence}%)<br><span class="muted">${esc(b.rationale)}</span></div>`
    ).join("");
    return `<div class="card ${d}" id="card${i}">
      <div class="card-top">
        <span class="asset">${esc(c.asset)}</span>
        <span class="arrow ${d}">${ARROW[d]}</span>
      </div>
      <span class="dirlabel ${d}">${DIRLABEL[d]}</span>
      <div class="confbar ${d}"><span style="width:${c.confidence}%"></span></div>
      <div class="conftxt">Forza consenso: ${c.confidence}%</div>
      <div class="votes">
        <span class="vu">▲ ${c.votes_up||0}</span>
        <span class="vd">▼ ${c.votes_down||0}</span>
        <span class="vn">▬ ${c.votes_neutral||0}</span>
      </div>
      ${bd ? `<span class="toggle-bd" data-i="${i}">▸ dettaglio agenti</span><div class="breakdown">${bd}</div>` : ""}
    </div>`;
  }).join("");

  grid.querySelectorAll(".toggle-bd").forEach(el => {
    el.addEventListener("click", () => {
      const card = $("card" + el.dataset.i);
      card.classList.toggle("open");
      el.textContent = card.classList.contains("open") ? "▾ nascondi dettaglio" : "▸ dettaglio agenti";
    });
  });
}

function renderMacro(summary, relevant) {
  $("macroSummary").textContent = summary || "—";
  $("relevantNews").innerHTML = (relevant || []).map(r =>
    `<li><span class="tag ${r.impact||'medium'}">${r.impact||'?'}</span>${esc(r.title)}
     ${r.why ? `<span class="rel-why">${esc(r.why)}</span>` : ""}</li>`
  ).join("");
}

function renderAgents(reports) {
  $("agentReports").innerHTML = reports.map(rep => {
    const sent = rep.overall_sentiment || "mixed";
    return `<div class="agent">
      <div class="agent-head">
        <div><div class="nm">${esc(rep.name)}</div><div class="md">${esc(rep.model)}</div></div>
        <span class="sentiment ${sent}">${esc(sent)}</span>
      </div>
      <div class="cm">${esc(rep.comment || "")}</div>
    </div>`;
  }).join("") || '<p class="muted">Nessun parere ancora.</p>';
}

function renderNews(news) {
  const srcCount = new Set(news.map(n => n.source)).size;
  $("newsCount").textContent = news.length ? `${news.length} titoli · ${srcCount} fonti` : "";
  $("newsList").innerHTML = news.map(n =>
    `<li><span class="src-badge">${esc(n.source || "")}</span>` +
    `<a href="${n.url}" target="_blank" rel="noopener">${esc(n.title)}</a></li>`
  ).join("");
}

function tickCountdown() {
  const el = $("nextRun");
  const bar = $("headProgress");
  if (!nextRunAt) { el.textContent = "Prossima: —"; bar.style.width = "0%"; return; }
  const diff = Math.max(0, Math.floor((nextRunAt - new Date()) / 1000));
  const m = String(Math.floor(diff / 60)).padStart(2, "0");
  const s = String(diff % 60).padStart(2, "0");
  el.textContent = `Prossima: tra ${m}:${s}`;
  const totalSec = intervalMin * 60;
  const elapsed = Math.max(0, Math.min(1, 1 - diff / totalSec));
  bar.style.width = (elapsed * 100).toFixed(1) + "%";
}

function fmtTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString("it-IT", { dateStyle: "short", timeStyle: "medium" });
}
function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"]/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

// ---- Settings modal ----
let editSources = [];

function renderSources() {
  const box = $("sourcesList");
  box.innerHTML = editSources.map((s, i) =>
    `<div class="src-row ${s.enabled ? "on" : ""}">
      <input type="checkbox" data-i="${i}" ${s.enabled ? "checked" : ""}>
      <div class="si">
        <div class="sname">${esc(s.name || "(senza nome)")}</div>
        <div class="surl">${esc(s.url || "")}</div>
      </div>
      <button type="button" class="srm" data-rm="${i}" title="Rimuovi">✕</button>
    </div>`
  ).join("") || '<small style="color:var(--faint)">Nessuna fonte. Aggiungine una qui sotto.</small>';

  box.querySelectorAll('input[type=checkbox]').forEach(cb => {
    cb.addEventListener("change", () => {
      editSources[+cb.dataset.i].enabled = cb.checked;
      renderSources();
    });
  });
  box.querySelectorAll('.srm').forEach(b => {
    b.addEventListener("click", () => { editSources.splice(+b.dataset.rm, 1); renderSources(); });
  });
}

function openSettings() {
  $("apiKey").value = "";
  $("apiKey").placeholder = cfg.api_key_set ? "(chiave salvata — lascia vuoto per non cambiarla)" : "sk-or-v1-...";
  $("defaultModel").value = cfg.default_model || "";
  // Se il modello è fissato da una variabile d'ambiente, non è modificabile.
  const modelLocked = !!cfg.model_locked;
  $("defaultModel").disabled = modelLocked;
  $("modelLockNote").classList.toggle("hidden", !modelLocked);
  $("interval").value = cfg.interval_minutes || 5;
  $("newsLimit").value = cfg.news_limit || 12;
  $("autoRun").checked = !!cfg.auto_run;
  $("assets").value = (cfg.assets || []).join("\n");
  $("agents").value = JSON.stringify(cfg.agents || [], null, 2);
  editSources = JSON.parse(JSON.stringify(cfg.sources || []));
  renderSources();
  $("settingsError").classList.add("hidden");
  $("settingsModal").classList.remove("hidden");
}

function addSource() {
  const name = $("newSrcName").value.trim();
  const url = $("newSrcUrl").value.trim();
  if (!url) { $("newSrcUrl").focus(); return; }
  editSources.push({ name: name || url.replace(/^https?:\/\//, "").split("/")[0], url, enabled: true });
  $("newSrcName").value = ""; $("newSrcUrl").value = "";
  renderSources();
}
function closeSettings() { $("settingsModal").classList.add("hidden"); }

async function saveSettings() {
  const err = $("settingsError");
  let agents;
  try {
    agents = JSON.parse($("agents").value);
    if (!Array.isArray(agents)) throw new Error("Gli agenti devono essere una lista JSON []");
  } catch (e) {
    err.textContent = "JSON agenti non valido: " + e.message;
    err.classList.remove("hidden");
    return;
  }
  const payload = {
    interval_minutes: parseInt($("interval").value) || 5,
    news_limit: parseInt($("newsLimit").value) || 12,
    auto_run: $("autoRun").checked,
    assets: $("assets").value.split("\n").map(s => s.trim()).filter(Boolean),
    agents: agents,
    sources: editSources,
  };
  // Il modello si invia solo se non è bloccato da una variabile d'ambiente.
  if (!cfg.model_locked) payload.default_model = $("defaultModel").value.trim();
  const k = $("apiKey").value.trim();
  if (k) payload.openrouter_api_key = k;

  const r = await fetch("/api/config", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (r.ok) { closeSettings(); fetchState(); }
  else { err.textContent = "Errore nel salvataggio."; err.classList.remove("hidden"); }
}

async function runNow() {
  const btn = $("runNowBtn");
  btn.disabled = true; btn.textContent = "⏳ Analisi…";
  try {
    // Su serverless (Vercel) l'analisi gira in modo sincrono e torna nel body:
    // la usiamo subito, cosi' la dashboard si aggiorna anche senza stato condiviso.
    const r = await fetch("/api/run-now", { method: "POST" });
    const data = await r.json().catch(() => null);
    if (data && data.state) render(data.state);
  } catch (e) {
    /* il polling periodico recuperera' lo stato */
  } finally {
    btn.disabled = false; btn.textContent = "▶ Analizza ora";
    fetchState();
  }
}

$("settingsBtn").addEventListener("click", openSettings);
$("closeSettings").addEventListener("click", closeSettings);
$("cancelSettings").addEventListener("click", closeSettings);
$("saveSettings").addEventListener("click", saveSettings);
$("addSrcBtn").addEventListener("click", addSource);
$("newSrcUrl").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); addSource(); } });
$("runNowBtn").addEventListener("click", runNow);
$("settingsModal").addEventListener("click", (e) => { if (e.target.id === "settingsModal") closeSettings(); });

fetchState();
setInterval(fetchState, 4000);
setInterval(tickCountdown, 1000);
