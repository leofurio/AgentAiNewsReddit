# AgentAiNewsReddit 📈🧠

Sito web che legge le news di **r/worldnews** (`https://www.reddit.com/r/worldnews.json`)
ogni N minuti e le passa a un **panel di agenti OpenRouter** che esprimono un giudizio
direzionale (rialzo / ribasso / neutro) su indici e asset di borsa.

> ⚠️ **Strumento a scopo di studio.** Le previsioni sono generate da modelli linguistici
> su titoli di news e **NON costituiscono consulenza finanziaria**.

## Come funziona

1. **Fetch** — scarica i titoli da r/worldnews (con User-Agent corretto, niente CORS perché passa dal backend).
2. **Agente estrattore** — filtra solo le notizie rilevanti per i mercati e produce un riassunto macro.
3. **Panel di analisti** — più agenti (con personas/modelli diversi) votano `up/down/neutral`
   con una confidenza per ciascun asset.
4. **Consenso** — il backend aggrega i voti (pesati per confidenza) in un verdetto per asset.
5. Si ripete automaticamente ogni **5 minuti** (configurabile).

## Requisiti

- **Python 3** (già installato sul tuo PC: 3.14). Nessuna libreria esterna, solo standard library.
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
3. Premi **▶ Analizza ora** (o aspetta il ciclo automatico)

## Configurazione (⚙ Impostazioni)

| Campo | Significato |
|-------|-------------|
| API key | la tua chiave OpenRouter (salvata solo in `data/config.json` sul tuo PC) |
| Modello di default | es. `openai/gpt-4o-mini`, `anthropic/claude-3.5-haiku`, `google/gemini-2.0-flash-001` — lista su https://openrouter.ai/models |
| Intervallo | minuti tra un'analisi e l'altra (default 5) |
| News da leggere | quanti titoli passare agli agenti |
| Asset / indici | uno per riga (es. `S&P 500`, `FTSE MIB`, `Bitcoin`…) |
| Agenti | lista JSON: ogni agente ha `name`, `model` (vuoto = default), `persona`. Metti modelli diversi per far "discutere" più AI. |

Esempio di agente con modello specifico:

```json
[
  {"name":"Macro", "model":"anthropic/claude-3.5-haiku", "persona":"Analista macroeconomico..."},
  {"name":"Geo",   "model":"openai/gpt-4o-mini",          "persona":"Esperto di rischio geopolitico..."}
]
```

## File

```
server.py            backend (HTTP + scheduler + agenti)
public/index.html    dashboard
public/style.css
public/app.js
data/config.json     impostazioni salvate (creato al primo salvataggio)
data/latest.json     ultima analisi
data/history.jsonl   storico (una riga per run, per studiare nel tempo)
start.bat            avvio rapido su Windows
```

## Variabili d'ambiente (opzionali)

- `OPENROUTER_API_KEY` — imposta la chiave senza usare la UI
- `PORT` — porta del server (default 8765)

## Note

- I costi delle chiamate dipendono dal modello scelto su OpenRouter. Con `gpt-4o-mini`
  o modelli "flash" il costo per ciclo è molto basso.
- Se un agente fallisce (modello inesistente, rate limit), viene saltato e lo vedi nel log;
  gli altri continuano.
