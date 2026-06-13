#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AgentAiNewsReddit
=================
Legge le news di r/worldnews ogni N minuti e le passa a un panel di agenti
OpenRouter che esprimono un giudizio (rialzo/ribasso) su indici e asset di borsa.

Solo libreria standard di Python: nessun pip install necessario.
Avvio:  python server.py    ->  http://localhost:8765

ATTENZIONE: strumento a scopo di studio/ricerca. NON e' consulenza finanziaria.
"""

import json
import os
import ssl
import sys
import threading
import time
import traceback
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Su Windows la console usa cp1252: forza UTF-8 cosi' emoji/accenti non fanno crashare.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PUBLIC_DIR = os.path.join(BASE_DIR, "public")
DATA_DIR = os.path.join(BASE_DIR, "data")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
STATE_PATH = os.path.join(DATA_DIR, "latest.json")
HISTORY_PATH = os.path.join(DATA_DIR, "history.jsonl")

os.makedirs(DATA_DIR, exist_ok=True)

PORT = int(os.environ.get("PORT", "8765"))
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
# Reddit blocca gli User-Agent generici: usiamo uno UA browser realistico.
REDDIT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

DEFAULT_CONFIG = {
    "openrouter_api_key": os.environ.get("OPENROUTER_API_KEY", ""),
    "default_model": "openai/gpt-4o-mini",
    "interval_minutes": 5,
    "news_limit": 40,
    "auto_run": True,
    "subreddit_url": "https://www.reddit.com/r/worldnews.json",
    "assets": [
        "S&P 500", "Nasdaq 100", "Dow Jones", "FTSE MIB", "EURO STOXX 50",
        "DAX", "Brent Crude Oil", "Gold (XAU/USD)", "Bitcoin", "EUR/USD",
    ],
    "agents": [
        {"name": "Macro Strategist", "model": "",
         "persona": "Sei un analista macroeconomico. Pesi tassi d'interesse, inflazione, "
                    "decisioni delle banche centrali, crescita e dati occupazionali."},
        {"name": "Geopolitics & Risk", "model": "",
         "persona": "Sei un esperto di rischio geopolitico. Valuti conflitti, sanzioni, "
                    "sicurezza energetica, catene di fornitura e shock di materie prime."},
        {"name": "Contrarian Trader", "model": "",
         "persona": "Sei un trader contrarian. Cerchi reazioni eccessive del mercato e "
                    "sentiment estremo, ragionando su mean-reversion e posizionamento."},
    ],
}

# ---------------------------------------------------------------------------
# Stato condiviso
# ---------------------------------------------------------------------------
_lock = threading.Lock()
_run_lock = threading.Lock()

state = {
    "status": "idle",          # idle | needs_config | running | ok | error
    "last_run": None,          # iso timestamp
    "next_run": None,          # iso timestamp
    "last_error": None,
    "logs": [],                # ultimi messaggi
    "news": [],                # headline usate nell'ultima analisi
    "macro_summary": "",
    "relevant_news": [],
    "consensus": [],           # per-asset consensus
    "agent_reports": [],       # output grezzo di ogni agente
    "run_count": 0,
}


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with _lock:
        state["logs"].append(line)
        state["logs"] = state["logs"][-60:]


def now_iso():
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                saved = json.load(f)
            cfg.update(saved)
        except Exception as e:
            log(f"Config non leggibile, uso default: {e}")
    return cfg


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


config = load_config()


def public_config():
    """Config sicura da mandare al browser (chiave mascherata)."""
    c = dict(config)
    key = c.get("openrouter_api_key", "") or ""
    c["openrouter_api_key"] = ("•••• " + key[-4:]) if len(key) >= 4 else ""
    c["api_key_set"] = bool(key)
    return c


# ---------------------------------------------------------------------------
# Fetch Reddit
# ---------------------------------------------------------------------------
def _http_get(url, accept, timeout=30):
    headers = {
        "User-Agent": REDDIT_UA,
        "Accept": accept,
        "Accept-Language": "en-US,en;q=0.9,it;q=0.8",
        "Accept-Encoding": "identity",
        "Connection": "close",
    }
    req = urllib.request.Request(url, headers=headers)
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return resp.read().decode("utf-8", "replace")


def _domain_of(url):
    try:
        return url.split("//", 1)[-1].split("/", 1)[0]
    except Exception:
        return ""


def _rss_url_from(url):
    base = url.split("?", 1)[0]
    if base.endswith(".json"):
        base = base[:-5]
    base = base.rstrip("/")
    return base + "/.rss"


def _fetch_json(url, limit):
    sep = "&" if "?" in url else "?"
    raw = _http_get(f"{url}{sep}limit={limit}", "application/json")
    data = json.loads(raw)
    posts = []
    for child in data.get("data", {}).get("children", []):
        d = child.get("data", {})
        if d.get("stickied"):
            continue
        posts.append({
            "title": d.get("title", "").strip(),
            "score": d.get("score", 0),
            "comments": d.get("num_comments", 0),
            "url": "https://www.reddit.com" + d.get("permalink", ""),
            "created_utc": d.get("created_utc"),
            "domain": d.get("domain", ""),
            "source": "json",
        })
    return posts


def _fetch_rss(url, limit):
    """Fallback: il feed Atom di Reddit, parsato con la stdlib."""
    sep = "&" if "?" in url else "?"
    raw = _http_get(f"{url}{sep}limit={limit}",
                    "application/atom+xml, application/xml;q=0.9, */*;q=0.8")
    ns = {"a": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(raw)
    posts = []
    for entry in root.findall("a:entry", ns):
        t = entry.find("a:title", ns)
        link = entry.find("a:link", ns)
        title = (t.text or "").strip() if t is not None else ""
        href = link.get("href") if link is not None else ""
        if not title:
            continue
        posts.append({
            "title": title,
            "score": 0,
            "comments": 0,
            "url": href,
            "created_utc": None,
            "domain": _domain_of(href),
            "source": "rss",
        })
        if len(posts) >= limit:
            break
    return posts


def fetch_reddit():
    url = config.get("subreddit_url") or DEFAULT_CONFIG["subreddit_url"]
    limit = int(config.get("news_limit", 40))
    try:
        posts = _fetch_json(url, limit)
        if posts:
            return posts
        raise RuntimeError("risposta JSON vuota")
    except Exception as e:
        log(f"Endpoint .json non disponibile ({e}); uso il feed RSS di Reddit.")
        return _fetch_rss(_rss_url_from(url), limit)


# ---------------------------------------------------------------------------
# OpenRouter call
# ---------------------------------------------------------------------------
def call_openrouter(model, system_prompt, user_prompt, temperature=0.4, max_tokens=1600):
    key = config.get("openrouter_api_key", "")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY mancante")
    body = {
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        OPENROUTER_URL, data=data, method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost:%d" % PORT,
            "X-Title": "AgentAiNewsReddit",
        },
    )
    ctx = ssl.create_default_context()
    last_err = None
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=90, context=ctx) as resp:
                payload = json.loads(resp.read().decode("utf-8", "replace"))
            return payload["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:400]
            last_err = f"HTTP {e.code} dal modello {model}: {detail}"
            if e.code in (429, 500, 502, 503) and attempt == 0:
                time.sleep(3)
                continue
            break
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            if attempt == 0:
                time.sleep(2)
                continue
            break
    raise RuntimeError(last_err or "Errore sconosciuto OpenRouter")


def extract_json(text):
    """Estrae il primo oggetto JSON da una risposta del modello."""
    if not text:
        raise ValueError("Risposta vuota")
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t.lower().startswith("json"):
            t = t[4:]
    start = t.find("{")
    end = t.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("Nessun JSON trovato nella risposta")
    return json.loads(t[start:end + 1])


# ---------------------------------------------------------------------------
# Pipeline a piu' agenti
# ---------------------------------------------------------------------------
def run_extractor(news):
    """Agente 'estrattore': filtra le news rilevanti per i mercati."""
    model = config.get("default_model")
    headlines = "\n".join(f"- {p['title']}" for p in news[:int(config.get('news_limit', 40))])
    system = (
        "Sei un analista finanziario che filtra notizie. Rispondi SOLO con JSON valido, "
        "nessun testo extra."
    )
    user = (
        "Ecco i titoli da r/worldnews:\n\n" + headlines +
        "\n\nSeleziona SOLO le notizie potenzialmente rilevanti per i mercati finanziari "
        "(geopolitica, energia, banche centrali, commercio, materie prime, tecnologia, ecc.). "
        "Rispondi con questo JSON:\n"
        '{"macro_summary":"2-3 frasi sul contesto di mercato in italiano",'
        '"relevant":[{"title":"titolo","why":"perche rilevante","impact":"high|medium|low"}]}'
    )
    try:
        raw = call_openrouter(model, system, user, temperature=0.3, max_tokens=1200)
        data = extract_json(raw)
        return data.get("macro_summary", ""), data.get("relevant", [])
    except Exception as e:
        log(f"Estrattore fallito ({e}); uso i titoli grezzi.")
        return "", [{"title": p["title"], "why": "", "impact": "medium"} for p in news[:12]]


def run_analyst(agent, macro_summary, relevant, assets):
    """Un agente analista vota up/down/neutral su ogni asset."""
    model = agent.get("model") or config.get("default_model")
    name = agent.get("name", model)
    persona = agent.get("persona", "")
    rel_txt = "\n".join(
        f"- ({r.get('impact','?')}) {r.get('title','')} :: {r.get('why','')}"
        for r in relevant[:25]
    ) or "(nessuna notizia rilevante estratta)"
    asset_list = ", ".join(assets)
    system = (
        persona + "\n\n"
        "Analizzi notizie e dai una previsione direzionale di BREVE termine (giorni) su asset finanziari. "
        "Rispondi SOLO con JSON valido, in italiano, senza testo extra. "
        "Sii prudente: usa 'neutral' quando le notizie non sono chiaramente rilevanti."
    )
    user = (
        f"Contesto macro: {macro_summary}\n\n"
        f"Notizie rilevanti:\n{rel_txt}\n\n"
        f"Per CIASCUNO di questi asset: {asset_list}\n"
        "fornisci direzione e una motivazione breve. Schema JSON:\n"
        '{"overall_sentiment":"risk-on|risk-off|mixed",'
        '"comment":"1-2 frasi di sintesi",'
        '"assets":[{"asset":"nome esatto","direction":"up|down|neutral",'
        '"confidence":0-100,"rationale":"motivazione breve"}]}'
    )
    raw = call_openrouter(model, system, user, temperature=0.5, max_tokens=1800)
    data = extract_json(raw)
    # normalizza
    votes = {}
    for a in data.get("assets", []):
        asset = (a.get("asset") or "").strip()
        direction = (a.get("direction") or "neutral").lower()
        if direction not in ("up", "down", "neutral"):
            direction = "neutral"
        try:
            conf = max(0, min(100, int(round(float(a.get("confidence", 50))))))
        except Exception:
            conf = 50
        votes[asset] = {
            "direction": direction,
            "confidence": conf,
            "rationale": (a.get("rationale") or "").strip(),
        }
    return {
        "name": name,
        "model": model,
        "overall_sentiment": data.get("overall_sentiment", "mixed"),
        "comment": data.get("comment", ""),
        "votes": votes,
    }


def match_vote(votes, asset):
    """Trova il voto di un agente per un asset con matching tollerante."""
    if asset in votes:
        return votes[asset]
    al = asset.lower()
    for k, v in votes.items():
        kl = k.lower()
        if kl in al or al in kl:
            return v
    # match per parola chiave principale
    key = al.split("(")[0].split()[0]
    for k, v in votes.items():
        if key and key in k.lower():
            return v
    return None


def aggregate(reports, assets):
    """Consenso deterministico per asset: voto pesato per confidenza."""
    consensus = []
    sign = {"up": 1, "down": -1, "neutral": 0}
    for asset in assets:
        score = 0.0
        weight = 0.0
        breakdown = []
        for rep in reports:
            v = match_vote(rep["votes"], asset)
            if not v:
                continue
            s = sign[v["direction"]]
            w = max(v["confidence"], 1)
            score += s * w
            weight += w if v["direction"] != "neutral" else 0
            breakdown.append({
                "agent": rep["name"],
                "direction": v["direction"],
                "confidence": v["confidence"],
                "rationale": v["rationale"],
            })
        if not breakdown:
            consensus.append({
                "asset": asset, "direction": "neutral", "confidence": 0,
                "breakdown": [], "votes_up": 0, "votes_down": 0, "votes_neutral": 0,
            })
            continue
        norm = (score / weight) if weight else 0.0  # -1..1
        if norm > 0.15:
            direction = "up"
        elif norm < -0.15:
            direction = "down"
        else:
            direction = "neutral"
        confidence = int(round(min(abs(norm) * 100, 100)))
        consensus.append({
            "asset": asset,
            "direction": direction,
            "confidence": confidence,
            "score": round(norm, 3),
            "votes_up": sum(1 for b in breakdown if b["direction"] == "up"),
            "votes_down": sum(1 for b in breakdown if b["direction"] == "down"),
            "votes_neutral": sum(1 for b in breakdown if b["direction"] == "neutral"),
            "breakdown": breakdown,
        })
    return consensus


# ---------------------------------------------------------------------------
# Ciclo di analisi
# ---------------------------------------------------------------------------
def do_run():
    if not config.get("openrouter_api_key"):
        with _lock:
            state["status"] = "needs_config"
            state["last_error"] = "Inserisci la tua OpenRouter API key nelle impostazioni."
        return

    if not _run_lock.acquire(blocking=False):
        log("Analisi gia' in corso, salto.")
        return
    try:
        with _lock:
            state["status"] = "running"
            state["last_error"] = None
        log("Scarico le news da Reddit...")
        news = fetch_reddit()
        log(f"Ricevute {len(news)} news. Estrazione notizie rilevanti...")

        macro_summary, relevant = run_extractor(news)
        log(f"Notizie rilevanti: {len(relevant)}. Interrogo {len(config['agents'])} agenti...")

        reports = []
        for agent in config["agents"]:
            try:
                rep = run_analyst(agent, macro_summary, relevant, config["assets"])
                reports.append(rep)
                log(f"  ✓ {rep['name']} ({rep['model']}) ha risposto.")
            except Exception as e:
                log(f"  ✗ Agente '{agent.get('name')}' errore: {e}")

        if not reports:
            raise RuntimeError("Nessun agente ha risposto. Controlla API key e modelli.")

        consensus = aggregate(reports, config["assets"])

        with _lock:
            state["news"] = news[:int(config.get("news_limit", 40))]
            state["macro_summary"] = macro_summary
            state["relevant_news"] = relevant
            state["agent_reports"] = reports
            state["consensus"] = consensus
            state["last_run"] = now_iso()
            state["status"] = "ok"
            state["run_count"] += 1
        save_state()
        append_history(consensus, reports)
        log("Analisi completata.")
    except Exception as e:
        log(f"Errore analisi: {e}")
        traceback.print_exc()
        with _lock:
            state["status"] = "error"
            state["last_error"] = str(e)
    finally:
        _run_lock.release()


def save_state():
    try:
        with _lock:
            snapshot = dict(state)
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log(f"Impossibile salvare stato: {e}")


def append_history(consensus, reports):
    try:
        entry = {
            "ts": now_iso(),
            "sentiments": [r["overall_sentiment"] for r in reports],
            "assets": [
                {"asset": c["asset"], "direction": c["direction"], "confidence": c["confidence"]}
                for c in consensus
            ],
        }
        with open(HISTORY_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        log(f"History non salvata: {e}")


def read_history(limit=50):
    if not os.path.exists(HISTORY_PATH):
        return []
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()[-limit:]
        return [json.loads(l) for l in lines if l.strip()]
    except Exception:
        return []


def scheduler_loop():
    # carica stato salvato all'avvio
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                saved = json.load(f)
            with _lock:
                for k in ("news", "macro_summary", "relevant_news", "consensus",
                          "agent_reports", "last_run", "run_count"):
                    if k in saved:
                        state[k] = saved[k]
        except Exception:
            pass

    if not config.get("openrouter_api_key"):
        with _lock:
            state["status"] = "needs_config"
        log("In attesa di configurazione (manca la API key OpenRouter).")

    while True:
        interval = max(1, int(config.get("interval_minutes", 5))) * 60
        if config.get("auto_run") and config.get("openrouter_api_key"):
            do_run()
            with _lock:
                nxt = time.time() + interval
                state["next_run"] = datetime.fromtimestamp(nxt, timezone.utc).isoformat()
            # dormi a piccoli passi cosi' reagiamo a cambi di config
            slept = 0
            while slept < interval:
                if not config.get("auto_run"):
                    break
                time.sleep(2)
                slept += 2
        else:
            with _lock:
                if config.get("openrouter_api_key"):
                    state["status"] = "idle" if state["status"] != "ok" else state["status"]
                state["next_run"] = None
            time.sleep(2)


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass  # silenzia il logging di default

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/state":
            with _lock:
                snap = dict(state)
            snap["config"] = public_config()
            return self._send(200, snap)
        if path == "/api/history":
            return self._send(200, read_history())
        return self.serve_static(path)

    def serve_static(self, path):
        if path == "/" or path == "":
            path = "/index.html"
        safe = os.path.normpath(path).lstrip("\\/")
        full = os.path.join(PUBLIC_DIR, safe)
        if not full.startswith(PUBLIC_DIR) or not os.path.isfile(full):
            return self._send(404, {"error": "not found"})
        ext = os.path.splitext(full)[1].lower()
        ctype = CONTENT_TYPES.get(ext, "application/octet-stream")
        with open(full, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            payload = {}

        if path == "/api/config":
            return self.handle_config(payload)
        if path == "/api/run-now":
            threading.Thread(target=do_run, daemon=True).start()
            return self._send(200, {"ok": True})
        return self._send(404, {"error": "not found"})

    def handle_config(self, payload):
        global config
        allowed = {"default_model", "interval_minutes", "news_limit", "auto_run",
                   "subreddit_url", "assets", "agents"}
        for k in allowed:
            if k in payload:
                config[k] = payload[k]
        # la API key viene aggiornata solo se ne arriva una nuova non mascherata
        new_key = payload.get("openrouter_api_key")
        if new_key and not new_key.startswith("••••"):
            config["openrouter_api_key"] = new_key.strip()
        try:
            config["interval_minutes"] = max(1, int(config.get("interval_minutes", 5)))
            config["news_limit"] = max(5, min(100, int(config.get("news_limit", 40))))
        except Exception:
            pass
        save_config(config)
        with _lock:
            if config.get("openrouter_api_key") and state["status"] == "needs_config":
                state["status"] = "idle"
        log("Configurazione aggiornata.")
        return self._send(200, {"ok": True, "config": public_config()})


def main():
    t = threading.Thread(target=scheduler_loop, daemon=True)
    t.start()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print("=" * 60)
    print(f"  AgentAiNewsReddit attivo su:  http://localhost:{PORT}")
    print(f"  Cartella dati: {DATA_DIR}")
    if not config.get("openrouter_api_key"):
        print("  ⚠  Inserisci la tua OpenRouter API key nella pagina (Impostazioni).")
    print("  Strumento di studio - NON e' consulenza finanziaria.")
    print("=" * 60)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nArresto...")
        server.shutdown()


if __name__ == "__main__":
    main()
