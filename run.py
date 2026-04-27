"""
Script per avviare il bot da IDLE o qualsiasi IDE.
Apri questo file e premi F5 (Run Module) in IDLE.

Modifica i parametri qui sotto per personalizzare la run.
"""

from polymarket_bot.main import run_bot

# ── Parametri ──────────────────────────────────────────────
# Modifica questi valori e premi F5 per avviare il bot.

run_bot(
    # Timeframe: "short" (30s), "mid" (5 min), "long" (30 min)
    timeframe="mid",

    # Strategia: "mean_reversion", "prob", "sentiment", "ensemble"
    # "ensemble" combina tutte e 3: trada solo quando almeno 2/3 concordano
    strategy_name="ensemble",

    # Adaptive: True = Kelly sizing + parametri appresi dallo storico
    adaptive=True,

    # Cicli di trading (None = default dal timeframe)
    cycles=15,

    # Intervallo tra cicli in secondi (None = default dal timeframe)
    interval=None,

    # Capitale iniziale in €
    capital=200.0,

    # Numero di mercati da fetchare per ciclo
    market_limit=200,

    # LLM per sentiment: None (VADER gratis), "openai", o "groq"
    llm_provider=None,
)
