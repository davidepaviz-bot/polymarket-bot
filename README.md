# Polymarket Paper Trading Bot 📊

Bot modulare per **paper trading** su [Polymarket](https://polymarket.com) — un prediction market dove il prezzo rappresenta una probabilità (0–1) e si tradano eventi YES/NO.



---

## Architettura

```
polymarket_bot/
├── market_reader.py   # Data ingestion da API pubblica Polymarket
├── strategy.py        # Motore segnali: Mean Reversion + Prob Edge + Sentiment Edge
├── sentiment_engine.py # News fetching + VADER/LLM sentiment analysis
├── paper_trader.py    # Paper trading engine + risk management
├── main.py            # Entry point — orchestra l'intero loop (short-term v4.0)
└── __main__.py        # Permette `python -m polymarket_bot`
```

### 1. Market Data Layer (`market_reader.py`)

Effettua il fetch dei mercati attivi tramite la **Gamma API** pubblica di Polymarket (nessuna autenticazione richiesta).

Dati estratti per ogni mercato:
- **Prezzo YES / NO** (probabilità implicita)
- **Volume** e **Liquidità**
- **Titolo evento** e stato del mercato

I dati vengono salvati in **CSV** e **JSON-lines** nella cartella `data/` per analisi successive.

### 2. Strategy Engine (`strategy.py`)

Implementa tre strategie pluggabili + filtro volatilità:

#### Filtro Volatilità
- Scarta mercati con prezzo YES < 0.10 o > 0.90 (risultato troppo certo)
- Scarta mercati con volume < 1,000 (poca liquidità)

#### A) Mean Reversion
| Condizione | Azione |
|---|---|
| YES price < 0.40 | **BUY YES** — il mercato sottovaluta l'outcome |
| YES price > 0.60 | **BUY NO** — il mercato sopravvaluta l'outcome |
| Altrimenti | HOLD |

#### B) Probabilistic Edge
Confronta la probabilità di mercato con una stima esterna:
- Se `stima - prezzo > +0.08` → **BUY YES**
- Se `stima - prezzo < -0.08` → **BUY NO**
- Altrimenti → HOLD

#### C) Sentiment Edge (v4.0)
Analizza notizie in tempo reale per stimare la probabilità vera:

1. **Estrae keyword** dal titolo del mercato (es. "France", "FIFA", "World Cup")
2. **Cerca notizie** su Google News RSS (gratis, nessuna API key)
3. **Analizza sentiment** con VADER (leggero) o LLM (OpenAI/Groq)
4. **Stima la probabilità** blendando sentiment + prezzo di mercato
5. **Genera segnale** se l'edge supera la soglia (8%)

| Modalità | Tool | Costo | Precisione |
|---|---|---|---|
| VADER (default) | `vaderSentiment` | Gratis | Buona |
| LLM (opzionale) | OpenAI/Groq API | ~$0.01/query | Ottima |

### 3. Sentiment Engine (`sentiment_engine.py`) — v4.0

Orchestra il flusso: **keyword extraction → news fetch → sentiment analysis → probability estimation**.

- **Google News RSS**: notizie gratis da migliaia di fonti, nessuna API key
- **VADER**: analisi sentiment rule-based, veloce e offline
- **LLM** (opzionale): prompt strutturato a OpenAI/Groq per stima probabilità diretta
- **Cache per ciclo**: evita di ri-fetchare le stesse notizie nello stesso ciclo

### 4. Paper Trading System (`paper_trader.py`)

- Capitale iniziale: **€200**
- Max **1 posizione** aperta alla volta
- Position sizing: **10% del capitale** per trade
- **Stop-loss** automatico al 5% (short-term)
- Tracking completo: entry/exit price, PnL, equity curve

### 5. Short-Term Trading Engine (`main.py` v4.0)

Il cuore del bot — orientato al **breve termine**:

| Meccanismo | Descrizione |
|---|---|
| **Take Profit** | Chiude se il prezzo si muove ≥3% a nostro favore |
| **Stop Loss** | Chiude se il prezzo si muove ≥5% contro di noi |
| **Max Hold** | Forza la chiusura dopo 10 cicli per liberare capitale |
| **Diversificazione** | Non trada lo stesso mercato due volte di fila (ultimi 5) |
| **Preferenza volatilità** | Preferisce mercati che stanno già mostrando movimento |
| **Chiusura fine sessione** | Chiude ogni posizione aperta alla fine della run |

**Nessuna simulazione di risoluzione** — il profitto/perdita deriva interamente dal movimento reale dei prezzi su Polymarket.

### 6. Logging System

- Trade log salvato in `logs/trade_log.csv`
- Equity curve salvata in `logs/equity_curve.json`
- Output in console con balance, PnL realizzato e unrealized

---

## Quick Start

```bash
# 1. Clona il repository
git clone https://github.com/davidepaviz-bot/polymarket-bot.git
cd polymarket-bot

# 2. Installa dipendenze
pip install -r requirements.txt

# 3. Avvia il bot (20 cicli, 30s di default)
python -m polymarket_bot

# 4. Run più lungo per catturare più movimenti di prezzo
python -m polymarket_bot --cycles 50 --interval 60

# 5. Usa la strategia probabilistic edge
python -m polymarket_bot --strategy prob --cycles 30

# 6. Usa la strategia sentiment (VADER — gratis, zero config)
python -m polymarket_bot --strategy sentiment --cycles 30

# 7. Usa la strategia sentiment con LLM (richiede OPENAI_API_KEY o GROQ_API_KEY)
export OPENAI_API_KEY="sk-..."
python -m polymarket_bot --strategy sentiment --llm openai --cycles 30

# 8. Sentiment con Groq (quasi gratis, molto veloce)
export GROQ_API_KEY="gsk_..."
python -m polymarket_bot --strategy sentiment --llm groq --cycles 30
```

### Opzioni CLI

| Flag | Default | Descrizione |
|---|---|---|
| `--cycles` | 20 | Numero di cicli di trading |
| `--interval` | 30 | Secondi tra un ciclo e l'altro |
| `--strategy` | `mean_reversion` | Strategia: `mean_reversion`, `prob`, o `sentiment` |
| `--capital` | 200.0 | Capitale iniziale in € |
| `--markets` | 200 | Numero mercati da fetchare per ciclo (paginazione automatica) |
| `--llm` | `None` | Provider LLM per sentiment: `openai` o `groq` (default: VADER) |

### Consigli per Short-Term Trading

- **Intervallo 30-60s**: permette di catturare micro-movimenti di prezzo reali
- **Cicli 30-50**: sessioni più lunghe = più opportunità di trading
- **Mercati volatili**: il bot preferisce automaticamente mercati con prezzi che si stanno muovendo

---

## Dipendenze

- Python 3.10+
- `requests` — per le chiamate API
- `pandas` — per analisi dati (opzionale, pronto per estensioni)
- `vaderSentiment` — analisi sentiment (per strategia sentiment)

**Opzionali (solo per modalità LLM):**
- `OPENAI_API_KEY` — per usare GPT-4o-mini
- `GROQ_API_KEY` — per usare Llama 3.1 (quasi gratis)

---

## Struttura Output

```
data/
├── market_snapshot.csv     # Storico prezzi mercati
└── market_snapshot.json    # Storico in JSON-lines

logs/
├── trade_log.csv           # Registro completo dei trade
└── equity_curve.json       # Evoluzione del capitale
```

---

## Versioni

| Versione | Descrizione |
|---|---|
| v1.0 | Bot base con mean reversion e prob edge |
| v2.0 | Filtro volatilità, diversificazione, risoluzione simulata |
| **v3.0** | **Short-term** — solo movimenti di prezzo reali, take-profit/stop-loss, max hold |
| **v3.1** | **Pool ampliato** — paginazione API (fino a 500+ mercati), filtri rilassati (0.10-0.90, vol>=1K) |
| **v4.0** | **Sentiment Engine** — analisi notizie con VADER/LLM, strategia sentiment_edge |

---

## Estendibilità

Il sistema è progettato per essere facilmente estendibile:

- **Nuove strategie**: implementa un metodo `evaluate(market) → Signal`
- **Persistenza**: i dati CSV/JSON sono pronti per analisi con pandas/matplotlib
- **Backtesting**: usa gli snapshot storici per testare strategie offline
- **Alerting**: aggiungi notifiche Telegram/Discord al trade log
- **ML models**: sostituisci le stime di probabilità con modelli predittivi
- **Sentiment avanzato**: aggiungi fonti come Reddit, Twitter/X, Telegram

---

