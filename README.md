# Polymarket Paper Trading Bot 📊

Bot modulare per **paper trading** su [Polymarket](https://polymarket.com) — un prediction market dove il prezzo rappresenta una probabilità (0–1) e si tradano eventi YES/NO.

> ⚠️ **Nessun soldo reale viene utilizzato.** Tutto il trading è simulato con un capitale iniziale di €200.

---

## Architettura

```
polymarket_bot/
├── market_reader.py   # Data ingestion da API pubblica Polymarket
├── strategy.py        # Motore segnali: Mean Reversion + Probabilistic Edge
├── paper_trader.py    # Paper trading engine + risk management
├── main.py            # Entry point — orchestra l'intero loop (short-term v3.0)
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

Implementa due strategie pluggabili + filtro volatilità:

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

### 3. Paper Trading System (`paper_trader.py`)

- Capitale iniziale: **€200**
- Max **1 posizione** aperta alla volta
- Position sizing: **10% del capitale** per trade
- **Stop-loss** automatico al 5% (short-term)
- Tracking completo: entry/exit price, PnL, equity curve

### 4. Short-Term Trading Engine (`main.py` v3.0)

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

### 5. Logging System

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
```

### Opzioni CLI

| Flag | Default | Descrizione |
|---|---|---|
| `--cycles` | 20 | Numero di cicli di trading |
| `--interval` | 30 | Secondi tra un ciclo e l'altro |
| `--strategy` | `mean_reversion` | Strategia: `mean_reversion` o `prob` |
| `--capital` | 200.0 | Capitale iniziale in € |
| `--markets` | 200 | Numero mercati da fetchare per ciclo (paginazione automatica) |

### Consigli per Short-Term Trading

- **Intervallo 30-60s**: permette di catturare micro-movimenti di prezzo reali
- **Cicli 30-50**: sessioni più lunghe = più opportunità di trading
- **Mercati volatili**: il bot preferisce automaticamente mercati con prezzi che si stanno muovendo

---

## Dipendenze

- Python 3.10+
- `requests` — per le chiamate API
- `pandas` — per analisi dati (opzionale, pronto per estensioni)

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

---

## Estendibilità

Il sistema è progettato per essere facilmente estendibile:

- **Nuove strategie**: implementa un metodo `evaluate(market) → Signal`
- **Persistenza**: i dati CSV/JSON sono pronti per analisi con pandas/matplotlib
- **Backtesting**: usa gli snapshot storici per testare strategie offline
- **Alerting**: aggiungi notifiche Telegram/Discord al trade log
- **ML models**: sostituisci le stime di probabilità con modelli predittivi

---

## ⚠️ Disclaimer

Questo progetto è **puramente educativo**. Non utilizza soldi reali, wallet crypto, o API di exchange. È pensato per simulare e comprendere le dinamiche dei prediction market.
