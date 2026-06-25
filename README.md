# AgentAiNewsReddit 📈🧠

Sito web che legge in tempo reale le **news finanziarie e di attualità** da più fonti
(CNBC, Bloomberg, Yahoo Finance, ZeroHedge, Reddit e altre) ogni N minuti e le passa a un
**panel di agenti OpenRouter** che esprimono un giudizio direzionale
(**rialzo / ribasso / neutro**) su indici e azioni di borsa, con una forza di consenso.

> ⚠️ **Strumento a scopo di studio.** Le previsioni sono generate da modelli linguistici
> su titoli di news e **NON costituiscono consulenza finanziaria**. Nessuna garanzia di accuratezza.

Tutto in **Python con la sola libreria standard** (nessun `pip install`) + una dashboard web.

---

## Come funziona

1. **Fetch multi-fonte** — il backend scarica i titoli da tutte le fonti attive (feed RSS/Atom
   o Reddit). Passando dal backend si evitano problemi di CORS e di User-Agent.
2. **Unione + deduplica** — i titoli delle varie testate vengono mescolati (round-robin) e
   deduplicati per titolo; ognuno resta taggato con la sua testata di origine.
3. **Agente estrattore** — un primo agente filtra solo le notizie rilevanti per i mercati e
   produce un breve riassunto del contesto macro.
4. **Panel di analisti** — più agenti (con persona e/o modello diversi) votano `up/down/neutral`
   con una confidenza per ciascun asset.
5. **Consenso** — il backend aggrega i voti (pesati per confidenza) in un verdetto per asset e
   in un **bias di mercato aggregato** (risk-on / risk-off / misto).
6. Si ripete automaticamente ogni **5 minuti** (configurabile). Lo storico viene salvato per
   poterlo studiare nel tempo.

---

## Requisiti

- **Python 3** (testato su 3.14). Nessuna libreria esterna: solo standard library.
- Una **API key OpenRouter** → https://openrouter.ai/keys

## Avvio

Doppio click su **`start.bat`**, oppure da terminale:

```powershell
python server.py
```

Poi apri **http://localhost:8765** nel browser.

Alla prima apertura:
1. Clicca **⚙ Impostazioni**
2. Incolla la tua **OpenRouter API key** e salva
3. Premi **▶ Analizza ora** (oppure aspetta il ciclo automatico)

---

## Configurazione (⚙ Impostazioni)

| Campo | Significato |
|-------|-------------|
| **API key** | la tua chiave OpenRouter (salvata solo in `data/config.json` sul tuo PC) |
| **Modello di default** | es. `openai/gpt-4o-mini`, `anthropic/claude-3.5-haiku`, `google/gemini-2.0-flash-001` — lista su https://openrouter.ai/models |
| **Intervallo** | minuti tra un'analisi e l'altra (default 5) |
| **News per fonte** | quanti titoli leggere da ciascuna fonte (tetto totale 70 per contenere i token) |
| **Fonti news** | checklist di feed (RSS/Atom o Reddit): spunta quelle da usare, aggiungi/rimuovi a piacere |
| **Asset / indici** | uno per riga (es. `S&P 500`, `FTSE MIB`, `Bitcoin`, `Intesa Sanpaolo`…) |
| **Agenti** | lista JSON: ogni agente ha `name`, `model` (vuoto = usa il default), `persona` |

### Fonti incluse (testate e funzionanti)

**Attive di default:** Reddit r/worldnews · CNBC Markets · CNBC Economy · Bloomberg Markets ·
Yahoo Finance · ZeroHedge.

**Disponibili (attivabili con un click):** MarketWatch · WSJ Markets · WSJ World · BBC Business ·
The Guardian Business · NYT Business · Investing.com · FT · ForexLive · CoinDesk · CNBC World ·
Reddit r/economics.

Per aggiungere una fonte basta incollare l'URL di un feed RSS/Atom nelle impostazioni.
Reddit `.json` viene provato per primo e, se bloccato (403), ripiega automaticamente sul feed RSS.

### Agenti — esempio con modelli diversi

Puoi mettere modelli diversi per "far discutere" più AI sullo stesso set di news:

```json
[
  {"name": "Macro Strategist",   "model": "anthropic/claude-3.5-haiku", "persona": "Analista macroeconomico: tassi, inflazione, banche centrali."},
  {"name": "Geopolitics & Risk", "model": "openai/gpt-4o-mini",         "persona": "Esperto di rischio geopolitico: conflitti, energia, sanzioni."},
  {"name": "Contrarian Trader",  "model": "",                           "persona": "Trader contrarian: cerca overreaction e mean-reversion."}
]
```

---

## Struttura dei file

```
api/_core.py         logica condivisa: config + fetch multi-fonte + OpenRouter + pipeline agenti
api/_handler.py      base comune per le funzioni serverless
api/state.py         GET  /api/state     (stato corrente)
api/run-now.py       POST /api/run-now   (esegue un'analisi e restituisce il risultato)
api/config.py        POST /api/config    (aggiorna le impostazioni)
api/history.py       GET  /api/history   (storico)
api/cron.py          GET  /api/cron      (innescata da Vercel Cron)
server.py            server locale standalone (HTTP + scheduler) attorno a api/_core.py
vercel.json          configurazione deploy Vercel (routing + cron + maxDuration)
requirements.txt     vuoto: solo standard library
public/index.html    dashboard
public/style.css
public/app.js
data/config.json     impostazioni salvate in locale (contiene la API key)
data/latest.json     ultima analisi
data/history.jsonl   storico (una riga per run)
start.bat            avvio rapido su Windows
```

> 🔒 La cartella `data/` è in `.gitignore`: **la tua API key non finisce mai su Git/GitHub.**

## Variabili d'ambiente

| Variabile | Significato |
|-----------|-------------|
| `OPENROUTER_API_KEY` | la tua chiave OpenRouter (consigliata su Vercel) |
| `OPENROUTER_MODEL` | modello di default, es. `openai/gpt-4o-mini`, `anthropic/claude-3.5-haiku` (alias: `DEFAULT_MODEL`). Se impostata, il modello è **bloccato**: non è modificabile dalle impostazioni della UI |
| `PORT` | porta del server locale (default `8765`) |
| `DATA_DIR` | cartella dati (default: `data/` in locale, `/tmp/agentnews` su Vercel) |
| `LLM_TIMEOUT` | timeout per chiamata al modello in secondi (default `90` locale, `22` su serverless) |
| `HTTP_TIMEOUT` | timeout per il fetch di ogni fonte (default `25` locale, `8` su serverless) |
| `LLM_ATTEMPTS` | tentativi per chiamata al modello (default `2` locale, `1` su serverless) |
| `RUN_BUDGET` | budget totale in secondi per una analisi su serverless (default `52`; `0` = nessun limite) |
| `OPENROUTER_REFERER` | header `HTTP-Referer` inviato a OpenRouter (default `http://localhost:PORT`) |

---

## 🚀 Deploy su Vercel

L'app gira su Vercel come **funzioni serverless Python** (cartella `api/`) + dashboard statica
(cartella `public/`). Nessuna dipendenza da installare: solo standard library.

1. **Importa il repo** su [vercel.com](https://vercel.com) (New Project → importa questo repository).
2. In **Settings → Environment Variables** imposta almeno:
   - `OPENROUTER_API_KEY` = la tua chiave OpenRouter
   - `OPENROUTER_MODEL` = il modello, es. `openai/gpt-4o-mini` (il **model in ENV**)
3. **Deploy.** Apri l'URL del progetto e premi **▶ Analizza ora**.

### Come funziona su Vercel (differenze dal locale)

- **Niente scheduler in background**: su serverless i thread non sopravvivono alla richiesta.
  L'analisi gira in modo **sincrono** quando premi *Analizza ora* (`/api/run-now`) e il risultato
  torna direttamente nella risposta, così la dashboard si aggiorna subito.
- **Analisi periodica** via **Vercel Cron** (`/api/cron`, configurata in `vercel.json`).
  Il default è **una volta al giorno** (`0 6 * * *`) perché il piano **Hobby** permette solo cron
  giornalieri; con il piano **Pro** puoi aumentare la frequenza (es. `0 */6 * * *`).
  In ogni caso puoi sempre lanciare l'analisi a mano dal pulsante.
- **Persistenza effimera**: lo stato va in `/tmp` (perso ai cold start e non condiviso fra istanze).
  Per questo la configurazione va messa nelle **variabili d'ambiente**, non solo nelle impostazioni UI.
- **Timeout**: ogni analisi deve stare nel timeout della funzione (`maxDuration: 60` in `vercel.json`,
  il massimo sul piano Hobby). Per starci, su serverless l'app **scarica le fonti e interroga gli
  agenti in parallelo**, riduce timeout/tentativi e applica un **budget di tempo** (`RUN_BUDGET`)
  oltre il quale restituisce i pareri gia' pronti. Per il massimo della resa usa comunque
  **modelli veloci** (mini/flash/haiku) e **pochi agenti/asset**. Su piano **Pro** puoi alzare
  `maxDuration` e i timeout per analisi piu' ricche.

## Note

- I costi dipendono dal modello scelto su OpenRouter: con modelli "mini"/"flash" o `:free`
  il costo per ciclo è molto basso o nullo.
- Se una **fonte** è irraggiungibile (es. rate limit) viene saltata e le altre continuano.
- Se un **agente** fallisce (modello inesistente, errore API) viene saltato e lo vedi nel log;
  gli altri completano comunque l'analisi.
