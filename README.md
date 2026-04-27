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
├── trade_history.py   # Storage persistente + adaptive training + Kelly sizing
├── main.py            # Entry point — orchestra l'intero loop (v5.0 adaptive)
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
- Position sizing: **10% fisso** o **Kelly Criterion** (adattivo)
- **Stop-loss** automatico al 5% (short-term)
- Tracking completo: entry/exit price, PnL, equity curve

### 5. Trade History & Adaptive Engine (`trade_history.py`) — v5.0

Sistema di storage persistente e training adattivo:

#### Storage Persistente
- Ogni trade viene salvato in `history/trade_history.json` con metadati completi
- I dati si accumulano tra le sessioni — il bot **impara** dalla propria storia
- Metadati per ogni trade: sentiment score, edge, categoria mercato, exit reason, hold cycles

#### Training Adattivo
Analizza la performance storica per ottimizzare i parametri:
- **Edge band analysis**: win rate per fascia di edge (low 8-12%, mid 12-18%, high >18%)
- **Sentiment band analysis**: performance per sentiment negativo/neutrale/positivo
- **Category tracking**: win rate per categoria (NBA, soccer, politics, crypto...)
- **Raccomandazioni automatiche**: alza min-edge se le trade a basso edge perdono, evita categorie con <20% win rate

#### Kelly Criterion Sizing
Dimensiona le posizioni in base alla probabilità e allo storico:

| Parametro | Formula |
|---|---|
| **Edge alto + buon win rate** | Posizione più grande (fino al 25%) |
| **Edge basso + win rate basso** | Posizione minima (3%) |
| **Nessuno storico** | Default fisso 10% |

Formula: `f* = (p × b - q) / b` dove p = win probability, b = avg_win/avg_loss. Usa **half-Kelly** per sicurezza.

### 6. Multi-Timeframe Engine (`main.py` v6.0)

Il cuore del bot — supporta tre timeframe con parametri ottimizzati:

| Timeframe | Intervallo | Take Profit | Stop Loss | Max Hold | Trailing Stop |
|---|---|---|---|---|---|
| **short** | 30s | 3-5% (variabile) | 5% | 8 cicli | No |
| **mid** | 5 min | 5-12% (variabile) | 8% | 20 cicli | Sì (+3%, trail 2%) |
| **long** | 30 min | 8-20% (variabile) | 12% | 30 cicli | Sì (+5%, trail 3%) |

#### Take Profit Variabile
Il target di take-profit scala con:
- **Edge strength**: edge alto → TP più alto (lascia correre i winner)
- **Tempo di hold**: dopo 75% del max-hold, TP scende per prendere profitto prima della chiusura forzata

#### Trailing Stop-Loss (mid/long)
Protegge i guadagni quando il prezzo si muove a favore:
1. Si **attiva** dopo un guadagno minimo (es. +3% per mid)
2. **Segue** il prezzo mantenendo una distanza fissa dal picco (es. 2%)
3. Se il prezzo ritraccia della distanza trailing → chiude con profitto

Esempio: entry=0.20, prezzo sale a 0.24 (+20%), poi scende a 0.228 → trailing stop chiude con +14% invece di rischiare lo stop-loss

**Nessuna simulazione di risoluzione** — il profitto/perdita deriva interamente dal movimento reale dei prezzi su Polymarket.

### 7. Logging System

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

# 9. Modalità adattiva con Kelly sizing (v5.0)
python -m polymarket_bot --strategy sentiment --adaptive --cycles 50

# 10. Run multipla per accumulare dati di training
python -m polymarket_bot --strategy sentiment --adaptive --cycles 30  # run 1
python -m polymarket_bot --strategy sentiment --adaptive --cycles 30  # run 2 (impara da run 1)
python -m polymarket_bot --strategy sentiment --adaptive --cycles 30  # run 3 (impara da run 1+2)

# 11. Mid-term con sentiment (v6.0) — 5 min tra cicli, trailing stop
python -m polymarket_bot --timeframe mid --strategy sentiment --adaptive

# 12. Long-term — 30 min tra cicli, TP 8-20%, trailing stop aggressivo
python -m polymarket_bot --timeframe long --strategy sentiment --adaptive

# 13. Override intervallo su qualsiasi timeframe
python -m polymarket_bot --timeframe long --interval 600 --cycles 20  # 10 min custom
```

### Opzioni CLI

| Flag | Default | Descrizione |
|---|---|---|
| `--timeframe` | `short` | Timeframe: `short` (30s), `mid` (5min), `long` (30min) |
| `--cycles` | da preset | Numero di cicli (default dal timeframe) |
| `--interval` | da preset | Secondi tra cicli (default dal timeframe) |
| `--strategy` | `mean_reversion` | Strategia: `mean_reversion`, `prob`, o `sentiment` |
| `--capital` | 200.0 | Capitale iniziale in € |
| `--markets` | 200 | Numero mercati da fetchare per ciclo (paginazione automatica) |
| `--llm` | `None` | Provider LLM per sentiment: `openai` o `groq` (default: VADER) |
| `--adaptive` | `False` | Abilita modo adattivo: Kelly sizing + parametri appresi |

### Consigli per Timeframe

- **Short** (30s): per testare strategie velocemente. Lo spread domina, pochi profitti reali
- **Mid** (5 min): buon compromesso tra velocità e movimenti reali. Il trailing stop protegge i guadagni
- **Long** (30 min): cattura trend più ampi. Meno trade ma più significativi. Ideale con `--adaptive`
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

history/
├── trade_history.json      # Database persistente di tutti i trade (si accumula)
└── training_stats.json     # Statistiche apprese dall'adaptive engine
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
| **v5.0** | **Adaptive Engine** — storage persistente, training adattivo, Kelly sizing dinamico |
| **v6.0** | **Multi-Timeframe** — mid/long term, take-profit variabile, trailing stop-loss |

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

