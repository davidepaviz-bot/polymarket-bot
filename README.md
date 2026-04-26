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
├── main.py            # Entry point — orchestra l'intero loop
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

Implementa due strategie pluggabili:

#### A) Mean Reversion
| Condizione | Azione |
|---|---|
| YES price < 0.45 | **BUY YES** — il mercato sottovaluta l'outcome |
| YES price > 0.55 | **BUY NO** — il mercato sopravvaluta l'outcome |
| Altrimenti | HOLD |

#### B) Probabilistic Edge
Confronta la probabilità di mercato con una stima esterna:
- Se `stima - prezzo > +0.10` → **BUY YES**
- Se `stima - prezzo < -0.10` → **BUY NO**
- Altrimenti → HOLD

### 3. Paper Trading System (`paper_trader.py`)

- Capitale iniziale: **€200**
- Max **1 posizione** aperta alla volta
- Position sizing: **10% del capitale** per trade
- **Stop-loss** automatico al 30%
- Tracking completo: entry/exit price, PnL, equity curve

### 4. Execution Engine (Simulata)

L'engine simula l'esecuzione di ordini BUY/SELL su eventi binari YES/NO.
Nessun wallet crypto, nessuna API di exchange, nessun soldo reale.

### 5. Logging System

- Trade log salvato in `logs/trade_log.csv`
- Equity curve salvata in `logs/equity_curve.json`
- Output in console con balance, numero trade, profit cumulativo

---

## Quick Start

```bash
# 1. Clona il repository
git clone <repo-url>
cd polymarket-bot

# 2. Installa dipendenze
pip install -r requirements.txt

# 3. Avvia il bot (5 cicli di default)
python -m polymarket_bot

# 4. Oppure con opzioni personalizzate
python -m polymarket_bot --cycles 20 --interval 15 --strategy prob --capital 500
```

### Opzioni CLI

| Flag | Default | Descrizione |
|---|---|---|
| `--cycles` | 5 | Numero di cicli di trading |
| `--interval` | 30 | Secondi tra un ciclo e l'altro |
| `--strategy` | `mean_reversion` | Strategia: `mean_reversion` o `prob` |
| `--capital` | 200.0 | Capitale iniziale in € |
| `--markets` | 20 | Numero mercati da fetchare per ciclo |

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
