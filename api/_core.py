#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AgentAiNewsReddit - core logic (condivisa)
==========================================
Tutta la logica di business (config, fetch news multi-fonte, chiamate OpenRouter,
pipeline a piu' agenti, aggregazione, stato) vive qui, senza alcun server HTTP.

Viene importata sia da:
  - server.py        -> server locale standalone (python server.py)
  - api/index.py      -> funzione serverless su Vercel

Solo libreria standard di Python: nessun pip install necessario.

ATTENZIONE: strumento a scopo di studio/ricerca. NON e' consulenza finanziaria.
"""

import ast
import json
import os
import re
import ssl
import sys
import threading
import time
import traceback
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeout
from datetime import datetime, timezone

# Su Windows la console usa cp1252: forza UTF-8 cosi' emoji/accenti non fanno crashare.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Percorsi e cartella dati
# ---------------------------------------------------------------------------
# Su Vercel (e in generale in ambienti serverless) il filesystem e' di sola
# lettura tranne /tmp, che e' effimero. Scegliamo una cartella dati scrivibile:
#   1) DATA_DIR esplicita da env, se impostata
#   2) /tmp/agentnews quando girare su Vercel (env VERCEL)
#   3) <repo>/data in locale
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PUBLIC_DIR = os.path.join(BASE_DIR, "public")

IS_SERVERLESS = bool(
    os.environ.get("VERCEL")
    or os.environ.get("AWS_LAMBDA_FUNCTION_NAME")
    or os.environ.get("AGENT_SERVERLESS")  # forzata dalle funzioni in api/ (vedi _handler.py)
)
DATA_DIR = (
    os.environ.get("DATA_DIR")
    or ("/tmp/agentnews" if IS_SERVERLESS else os.path.join(BASE_DIR, "data"))
)
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
STATE_PATH = os.path.join(DATA_DIR, "latest.json")
HISTORY_PATH = os.path.join(DATA_DIR, "history.jsonl")

try:
    os.makedirs(DATA_DIR, exist_ok=True)
except Exception:
    # filesystem di sola lettura: si prosegue lo stesso (persistenza disabilitata)
    pass

PORT = int(os.environ.get("PORT", "8765"))
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
# Reddit blocca gli User-Agent generici: usiamo uno UA browser realistico.
REDDIT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# Modello di default leggibile da ENV (OPENROUTER_MODEL ha priorita', DEFAULT_MODEL e' un alias).
DEFAULT_MODEL = (
    os.environ.get("OPENROUTER_MODEL")
    or os.environ.get("DEFAULT_MODEL")
    or "openai/gpt-4o-mini"
)
# Se il modello e' fissato via ENV, non e' modificabile dall'interfaccia.
MODEL_LOCKED = bool(os.environ.get("OPENROUTER_MODEL") or os.environ.get("DEFAULT_MODEL"))

# Su serverless tutto deve stare dentro il timeout della funzione (Vercel Hobby: 60s).
# Per starci: fetch in parallelo + agenti in parallelo + timeout/tentativi ridotti + un
# "budget" complessivo oltre il quale restituiamo i risultati parziali gia' pronti.
LLM_TIMEOUT = int(os.environ.get("LLM_TIMEOUT", "18" if IS_SERVERLESS else "90"))
HTTP_TIMEOUT = int(os.environ.get("HTTP_TIMEOUT", "7" if IS_SERVERLESS else "25"))
# Tentativi per chiamata: su serverless 1 (i retry con sleep mangiano il budget).
LLM_ATTEMPTS = int(os.environ.get("LLM_ATTEMPTS", "1" if IS_SERVERLESS else "2"))
# Budget complessivo per una run, in secondi (0 = nessun limite, usato in locale).
# Tenuto sotto i 60s del piano Hobby con margine per la risposta HTTP.
RUN_BUDGET = int(os.environ.get("RUN_BUDGET", "45" if IS_SERVERLESS else "0"))

DEFAULT_CONFIG = {
    "openrouter_api_key": os.environ.get("OPENROUTER_API_KEY", ""),
    "default_model": DEFAULT_MODEL,
    "interval_minutes": 5,
    "news_limit": 12,          # titoli letti PER FONTE
    "auto_run": True,
    "subreddit_url": "https://www.reddit.com/r/worldnews.json",  # legacy (vedi 'sources')
    # Fonti testate e funzionanti (feed RSS/Atom o Reddit). enabled=True => usata.
    "sources": [
        {"name": "Reddit r/worldnews", "url": "https://www.reddit.com/r/worldnews.json", "enabled": True},
        {"name": "CNBC Markets",       "url": "https://www.cnbc.com/id/10000664/device/rss/rss.html", "enabled": True},
        {"name": "CNBC Economy",       "url": "https://www.cnbc.com/id/20910258/device/rss/rss.html", "enabled": True},
        {"name": "Bloomberg Markets",  "url": "https://feeds.bloomberg.com/markets/news.rss", "enabled": True},
        {"name": "Yahoo Finance",      "url": "https://finance.yahoo.com/news/rssindex", "enabled": True},
        {"name": "ZeroHedge",          "url": "https://feeds.feedburner.com/zerohedge/feed", "enabled": True},
        {"name": "MarketWatch Top",    "url": "http://feeds.marketwatch.com/marketwatch/topstories/", "enabled": False},
        {"name": "WSJ Markets",        "url": "https://feeds.a.dj.com/rss/RSSMarketsMain.xml", "enabled": False},
        {"name": "WSJ World",          "url": "https://feeds.a.dj.com/rss/RSSWorldNews.xml", "enabled": False},
        {"name": "BBC Business",       "url": "https://feeds.bbci.co.uk/news/business/rss.xml", "enabled": False},
        {"name": "Guardian Business",  "url": "https://www.theguardian.com/uk/business/rss", "enabled": False},
        {"name": "NYT Business",       "url": "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml", "enabled": False},
        {"name": "Investing.com",      "url": "https://www.investing.com/rss/news.rss", "enabled": False},
        {"name": "FT Home",            "url": "https://www.ft.com/rss/home", "enabled": False},
        {"name": "ForexLive",          "url": "https://www.forexlive.com/feed/news/", "enabled": False},
        {"name": "CoinDesk (crypto)",  "url": "https://www.coindesk.com/arc/outboundfeeds/rss/", "enabled": False},
        {"name": "CNBC World",         "url": "https://www.cnbc.com/id/100727362/device/rss/rss.html", "enabled": False},
        {"name": "Reddit r/economics", "url": "https://www.reddit.com/r/economics/.rss", "enabled": False},
    ],
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
    # La chiave/modello da ENV hanno sempre la priorita' se impostate (utile su
    # Vercel, dove il config.json su /tmp e' effimero e non affidabile).
    if os.environ.get("OPENROUTER_API_KEY"):
        cfg["openrouter_api_key"] = os.environ["OPENROUTER_API_KEY"]
    if os.environ.get("OPENROUTER_MODEL") or os.environ.get("DEFAULT_MODEL"):
        cfg["default_model"] = DEFAULT_MODEL
    return cfg


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log(f"Impossibile salvare la config ({e}). Su Vercel usa le variabili d'ambiente.")


config = load_config()


def public_config():
    """Config sicura da mandare al browser (chiave mascherata)."""
    c = dict(config)
    key = c.get("openrouter_api_key", "") or ""
    c["openrouter_api_key"] = ("•••• " + key[-4:]) if len(key) >= 4 else ""
    c["api_key_set"] = bool(key)
    c["model_locked"] = MODEL_LOCKED
    return c


def update_config(payload):
    """Applica un aggiornamento di config (dal POST /api/config) e lo salva."""
    global config
    allowed = {"default_model", "interval_minutes", "news_limit", "auto_run",
               "subreddit_url", "assets", "agents", "sources"}
    # Se il modello e' fissato via ENV, ignora ogni tentativo di cambiarlo dall'interfaccia.
    if MODEL_LOCKED:
        allowed.discard("default_model")
        config["default_model"] = DEFAULT_MODEL
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
    return public_config()


# ---------------------------------------------------------------------------
# Fetch multi-fonte (RSS/Atom + Reddit)
# ---------------------------------------------------------------------------
MAX_HEADLINES = 70   # tetto totale di titoli passati agli agenti (controlla i token)
FEED_ACCEPT = "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8"


def _http_bytes(url, accept, timeout=None):
    if timeout is None:
        timeout = HTTP_TIMEOUT
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
        return resp.read()


def _http_get(url, accept, timeout=None):
    return _http_bytes(url, accept, timeout).decode("utf-8", "replace")


def _domain_of(url):
    try:
        return url.split("//", 1)[-1].split("/", 1)[0]
    except Exception:
        return ""


def _local(tag):
    return tag.rsplit("}", 1)[-1].lower()


_TAG_RE = re.compile(r"<[^>]+>")


def _clean(text):
    return _TAG_RE.sub("", " ".join((text or "").split())).strip()


def _rss_url_from(url):
    base = url.split("?", 1)[0]
    if base.endswith(".json"):
        base = base[:-5]
    base = base.rstrip("/")
    return base + "/.rss"


def _fetch_json(url, limit, source_name):
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
            "source": source_name,
        })
    return posts


def _parse_feed(raw_bytes, source_name, limit):
    """Parser generico RSS 2.0 + Atom (namespace ignorati)."""
    root = ET.fromstring(raw_bytes)
    posts = []
    for el in root.iter():
        if _local(el.tag) not in ("item", "entry"):
            continue
        title = ""
        href = ""
        for ch in el:
            lt = _local(ch.tag)
            if lt == "title" and not title:
                title = _clean(ch.text)
            elif lt == "link" and not href:
                href = ch.get("href") or _clean(ch.text)
        if not title:
            continue
        posts.append({
            "title": title, "score": 0, "comments": 0, "url": href,
            "created_utc": None, "domain": _domain_of(href), "source": source_name,
        })
        if len(posts) >= limit:
            break
    return posts


def fetch_one(source, limit):
    """Scarica una singola fonte. Reddit .json prova JSON poi ripiega su RSS."""
    url = source.get("url", "")
    name = source.get("name") or _domain_of(url)
    if "reddit.com" in url and url.split("?", 1)[0].rstrip("/").endswith(".json"):
        try:
            posts = _fetch_json(url, limit, name)
            if posts:
                return posts
        except Exception as e:
            log(f"  {name}: .json bloccato ({e}); provo RSS")
        url = _rss_url_from(url)
    raw = _http_bytes(url, FEED_ACCEPT)
    return _parse_feed(raw, name, limit)


def _active_sources():
    srcs = config.get("sources")
    if not srcs:  # retro-compatibilità con la vecchia config a fonte singola
        srcs = [{"name": "Reddit", "url": config.get("subreddit_url",
                 DEFAULT_CONFIG["subreddit_url"]), "enabled": True}]
    return [s for s in srcs if s.get("enabled", True) and s.get("url")]


def fetch_news():
    sources = _active_sources()
    if not sources:
        raise RuntimeError("Nessuna fonte attiva: abilita almeno una fonte nelle impostazioni.")
    per = max(3, int(config.get("news_limit", 12)))
    # Scarico le fonti IN PARALLELO: in serie 6+ feed con i loro timeout sforerebbero
    # facilmente il limite della funzione su Vercel.
    results = {}
    errors = []
    with ThreadPoolExecutor(max_workers=min(8, len(sources))) as ex:
        futures = {ex.submit(fetch_one, src, per): src for src in sources}
        for fut in as_completed(futures):
            src = futures[fut]
            try:
                posts = fut.result()
                results[src["name"]] = posts
                log(f"  {src['name']}: {len(posts)} titoli")
            except Exception as e:
                errors.append(src.get("name", "?"))
                log(f"  {src.get('name','?')}: errore ({e})")
    # mantengo l'ordine originale delle fonti per il round-robin
    lists = [results[s["name"]] for s in sources if s["name"] in results]
    # round-robin tra le fonti per mescolare le testate
    merged = []
    i = 0
    while True:
        added = False
        for lst in lists:
            if i < len(lst):
                merged.append(lst[i])
                added = True
        if not added:
            break
        i += 1
    # dedup per titolo normalizzato
    seen = set()
    deduped = []
    for p in merged:
        k = re.sub(r"\W+", " ", p["title"].lower()).strip()[:90]
        if k and k not in seen:
            seen.add(k)
            deduped.append(p)
    if not deduped:
        raise RuntimeError("Nessuna fonte raggiungibile: " + ", ".join(errors))
    return deduped[:MAX_HEADLINES]


# ---------------------------------------------------------------------------
# OpenRouter call
# ---------------------------------------------------------------------------
def call_openrouter(model, system_prompt, user_prompt, temperature=0.4, max_tokens=1600,
                    force_json=False):
    key = config.get("openrouter_api_key", "")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY mancante")
    base_body = {
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "HTTP-Referer": os.environ.get("OPENROUTER_REFERER", "http://localhost:%d" % PORT),
        "X-Title": "AgentAiNewsReddit",
    }
    ctx = ssl.create_default_context()
    last_err = None
    use_json = force_json   # chiede output JSON; se il modello non lo supporta, si ripiega
    attempts_left = max(1, LLM_ATTEMPTS)
    while attempts_left > 0:
        body = dict(base_body)
        if use_json:
            body["response_format"] = {"type": "json_object"}
        req = urllib.request.Request(
            OPENROUTER_URL, data=json.dumps(body).encode("utf-8"),
            method="POST", headers=headers,
        )
        try:
            with urllib.request.urlopen(req, timeout=LLM_TIMEOUT, context=ctx) as resp:
                payload = json.loads(resp.read().decode("utf-8", "replace"))
            return payload["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:400]
            last_err = f"HTTP {e.code} dal modello {model}: {detail}"
            # il modello potrebbe non supportare response_format: riprova senza, gratis
            if use_json and e.code in (400, 404, 415, 422, 500):
                use_json = False
                continue
            if e.code in (429, 500, 502, 503) and attempts_left > 1:
                time.sleep(3)
                attempts_left -= 1
                continue
            break
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            if attempts_left > 1:
                time.sleep(2)
                attempts_left -= 1
                continue
            break
        attempts_left -= 1
    raise RuntimeError(last_err or "Errore sconosciuto OpenRouter")


def _repair_json(s):
    """Ripara il JSON 'sporco' dei modelli: virgolette interne non-escaped,
    a-capo/tab dentro le stringhe, ecc. Scansione a stati carattere per carattere."""
    out = []
    in_str = False
    esc = False
    n = len(s)
    for i, c in enumerate(s):
        if in_str:
            if esc:
                out.append(c)
                esc = False
            elif c == "\\":
                out.append(c)
                esc = True
            elif c == '"':
                # è davvero fine stringa? guarda il prossimo char non-spazio
                j = i + 1
                while j < n and s[j] in " \t\r\n":
                    j += 1
                nxt = s[j] if j < n else ""
                if nxt in ",}]:" or nxt == "":
                    out.append('"')
                    in_str = False
                else:
                    out.append('\\"')  # virgoletta interna -> escape
            elif c == "\n":
                out.append("\\n")
            elif c == "\r":
                out.append("\\r")
            elif c == "\t":
                out.append("\\t")
            else:
                out.append(c)
        else:
            out.append(c)
            if c == '"':
                in_str = True
    return "".join(out)


def _balance_json(s):
    """Chiude un JSON 'troncato': se la risposta del modello è stata tagliata a metà
    (max_tokens), bilancia stringhe e parentesi aperte cosi' da recuperare il possibile."""
    s = s.rstrip()
    stack = []
    in_str = False
    esc = False
    last_close = -1   # indice dell'ultima chiusura completa di stringa/oggetto/array
    for i, c in enumerate(s):
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
                last_close = i
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                stack.append("}")
            elif c == "[":
                stack.append("]")
            elif c in "}]":
                if stack:
                    stack.pop()
                last_close = i
    if in_str:
        # tagliato dentro una stringa: torna all'ultimo valore/struttura completa
        if last_close >= 0:
            return _balance_json(s[:last_close + 1])
        return None
    out = re.sub(r",\s*$", "", s)   # togli virgola penzolante
    for closer in reversed(stack):
        out += closer
    return out


def extract_json(text):
    """Estrae il primo oggetto JSON da una risposta del modello, riparandolo se serve."""
    if not text:
        raise ValueError("Risposta vuota")
    t = text.strip()
    # togli eventuali fence ```json ... ```
    t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
    t = re.sub(r"\s*```$", "", t).strip()
    start = t.find("{")
    if start == -1:
        raise ValueError("Nessun JSON trovato nella risposta")
    end = t.rfind("}")
    # se manca la graffa di chiusura, la risposta e' troncata: prendi fino in fondo
    blob = t[start:end + 1] if end > start else t[start:]
    cleaned = re.sub(r",(\s*[}\]])", r"\1", blob)  # togli virgole finali
    # 1-2) tentativo diretto, poi senza virgole finali
    for cand in (blob, cleaned):
        try:
            return json.loads(cand)
        except json.JSONDecodeError:
            pass
    # 3) ripara virgolette/newline interni alle stringhe
    try:
        return json.loads(re.sub(r",(\s*[}\]])", r"\1", _repair_json(cleaned)))
    except json.JSONDecodeError:
        pass
    # 3b) JSON troncato (max_tokens): bilancia stringhe/parentesi aperte
    for src in (cleaned, _repair_json(cleaned)):
        bal = _balance_json(src)
        if bal:
            try:
                return json.loads(re.sub(r",(\s*[}\]])", r"\1", bal))
            except Exception:
                pass
    # 4) il modello ha risposto con un dict stile Python (apici singoli, True/False/None)
    py = re.sub(r"\bnull\b", "None",
                re.sub(r"\bfalse\b", "False",
                       re.sub(r"\btrue\b", "True", cleaned)))
    try:
        val = ast.literal_eval(py)
        if isinstance(val, dict):
            return val
    except Exception:
        pass
    raise ValueError("JSON non interpretabile dalla risposta del modello")


# ---------------------------------------------------------------------------
# Pipeline a piu' agenti
# ---------------------------------------------------------------------------
def run_extractor(news):
    """Agente 'estrattore': filtra le news rilevanti per i mercati."""
    model = config.get("default_model")
    headlines = "\n".join(f"- [{p.get('source','?')}] {p['title']}" for p in news[:MAX_HEADLINES])
    system = (
        "Sei un analista finanziario che filtra notizie. Restituisci ESCLUSIVAMENTE un oggetto "
        "JSON valido: usa le virgolette doppie per chiavi e valori. Se devi citare qualcosa "
        "dentro un valore, usa apici singoli. Niente testo prima o dopo il JSON, niente a-capo "
        "dentro le stringhe."
    )
    user = (
        "Ecco titoli da varie testate finanziarie e di attualità (la testata è tra parentesi quadre):\n\n"
        + headlines +
        "\n\nSeleziona SOLO le notizie potenzialmente rilevanti per i mercati finanziari "
        "(geopolitica, energia, banche centrali, commercio, materie prime, tecnologia, ecc.). "
        "Rispondi con questo JSON:\n"
        '{"macro_summary":"2-3 frasi sul contesto di mercato in italiano",'
        '"relevant":[{"title":"titolo","why":"perche rilevante","impact":"high|medium|low"}]}'
    )
    last = None
    for attempt in range(max(1, LLM_ATTEMPTS)):
        raw = None
        try:
            raw = call_openrouter(model, system, user,
                                  temperature=(0.3 if attempt == 0 else 0.1),
                                  max_tokens=1200, force_json=True)
            data = extract_json(raw)
            return data.get("macro_summary", ""), data.get("relevant", [])
        except Exception as e:
            last = e
            if raw is not None:
                log(f"  estrattore: parsing fallito ({e}); risposta: {raw[:200]!r}")
    log(f"Estrattore fallito ({last}); uso i titoli grezzi.")
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
        "Restituisci ESCLUSIVAMENTE un oggetto JSON valido, in italiano, senza testo prima o dopo. "
        "Usa le virgolette doppie per chiavi e valori; per citare qualcosa dentro un valore usa "
        "apici singoli. Niente a-capo dentro le stringhe: motivazioni brevi su una sola riga. "
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
    data = None
    last_err = None
    attempts = max(1, LLM_ATTEMPTS)
    max_tokens = 1600 if IS_SERVERLESS else 2000
    for attempt in range(attempts):
        raw = None
        try:
            raw = call_openrouter(model, system, user,
                                  temperature=(0.35 if attempt == 0 else 0.1),
                                  max_tokens=max_tokens, force_json=True)
            data = extract_json(raw)
            break
        except Exception as e:
            last_err = e
            if raw is not None:
                log(f"  {name}: parsing JSON fallito ({e}); risposta: {raw[:200]!r}")
    if data is None:
        raise RuntimeError(f"risposta non in JSON dopo {attempts} tentativi ({last_err})")
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
            state["last_error"] = "Inserisci la tua OpenRouter API key (variabile d'ambiente OPENROUTER_API_KEY o impostazioni)."
        return

    if not _run_lock.acquire(blocking=False):
        log("Analisi gia' in corso, salto.")
        return
    run_start = time.monotonic()
    try:
        with _lock:
            state["status"] = "running"
            state["last_error"] = None
        srcs = _active_sources()
        log(f"Scarico le news da {len(srcs)} fonti...")
        news = fetch_news()
        log(f"Totale {len(news)} titoli (deduplicati). Estrazione notizie rilevanti...")
        # Persisto subito le news: cosi' restano visibili anche se gli agenti
        # non fanno in tempo a rispondere dentro il budget (Vercel).
        with _lock:
            state["news"] = news
        save_state()

        macro_summary, relevant = run_extractor(news)
        agents = config["agents"]
        with _lock:
            state["macro_summary"] = macro_summary
            state["relevant_news"] = relevant
        save_state()
        log(f"Notizie rilevanti: {len(relevant)}. Interrogo {len(agents)} agenti in parallelo...")

        # Gli agenti girano IN PARALLELO e con un budget di tempo complessivo: se la
        # funzione e' vicina al timeout (Vercel), restituiamo i pareri gia' pronti.
        reports = []
        ex = ThreadPoolExecutor(max_workers=min(8, max(1, len(agents))))
        futures = {
            ex.submit(run_analyst, ag, macro_summary, relevant, config["assets"]): ag
            for ag in agents
        }
        remaining = None
        if RUN_BUDGET:
            remaining = max(1.0, RUN_BUDGET - (time.monotonic() - run_start))
        try:
            for fut in as_completed(futures, timeout=remaining):
                ag = futures[fut]
                try:
                    rep = fut.result()
                    reports.append(rep)
                    log(f"  ✓ {rep['name']} ({rep['model']}) ha risposto.")
                except Exception as e:
                    log(f"  ✗ Agente '{ag.get('name')}' errore: {e}")
        except FuturesTimeout:
            log(f"  ⏱ Budget tempo esaurito: raccolti {len(reports)}/{len(agents)} pareri in tempo.")
        # non blocchiamo la risposta aspettando gli agenti rimasti indietro
        ex.shutdown(wait=False, cancel_futures=True)

        consensus = aggregate(reports, config["assets"]) if reports else []

        with _lock:
            state["news"] = news
            state["macro_summary"] = macro_summary
            state["relevant_news"] = relevant
            state["agent_reports"] = reports
            state["consensus"] = consensus
            state["last_run"] = now_iso()
            state["status"] = "ok"
            state["run_count"] += 1
            # Niente pareri in tempo: mostriamo comunque news e contesto, con un avviso.
            state["last_error"] = (None if reports else
                                   "Nessun agente ha risposto entro il limite di tempo: "
                                   "mostro solo news e contesto. Usa un modello piu' veloce "
                                   "o riduci agenti/asset.")
        save_state()
        if reports:
            append_history(consensus, reports)
        log("Analisi completata." if reports else
            "Analisi completata senza pareri (timeout agenti): news e contesto disponibili.")
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


def load_state_from_disk():
    """Rilegge lo stato salvato su disco dentro lo stato in memoria (utile su
    serverless: ogni invocazione e' un processo nuovo)."""
    if not os.path.exists(STATE_PATH):
        return
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            saved = json.load(f)
        with _lock:
            for k in ("news", "macro_summary", "relevant_news", "consensus",
                      "agent_reports", "last_run", "run_count", "status",
                      "last_error", "logs"):
                if k in saved:
                    state[k] = saved[k]
    except Exception:
        pass


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


def state_snapshot():
    """Snapshot dello stato + config pubblica, pronto per la risposta JSON."""
    with _lock:
        snap = dict(state)
    snap["config"] = public_config()
    return snap


def scheduler_loop():
    """Loop di analisi automatica (solo per il server locale standalone)."""
    load_state_from_disk()

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
