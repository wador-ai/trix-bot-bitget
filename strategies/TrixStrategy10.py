# pragma pylint: disable=missing-docstring, invalid-name, too-many-locals
"""
TrixStrategy10 — v10 : v9 + filtre ADX durci (>25) pour éviter les ranges.

Hypothèse testée : la v8 (long-only) perdait hors marché haussier car elle ne
captait pas les baisses. En autorisant le short (miroir des conditions long),
on cherche à savoir si le côté short rétablit un edge en régime baissier 2025+.

Base : TrixStrategy8 (1h, TRIX figé 18/9, ADX>20, EMA200, trailing 0.01/0.10).
Paramètres FIGÉS (pas d'hyperopt — déjà stables en v8).

⚠️ Recherche/éducation uniquement. Futures = risque accru. Valider en OOS avant réel.
"""

import logging
from functools import reduce

import pandas_ta as ta
from pandas import DataFrame

from freqtrade.strategy import IStrategy
from freqtrade.vendor.qtpylib import indicators as qtpylib


logger = logging.getLogger(__name__)


class TrixStrategy10(IStrategy):
    """TRIX 1h long + short, filtre ADX>20, paramètres figés (v8)."""

    INTERFACE_VERSION = 3

    # === Paramètres de marché ===
    timeframe = "1h"
    can_short = True

    # === ROI désactivé ; trailing stop gère les gagnants ===
    minimal_roi = {"0": 0.99}

    # === Stop-loss fixe ===
    stoploss = -0.10
    use_custom_stoploss = False

    # === Trailing stop (valeurs figées issues de v8) ===
    trailing_stop = True
    trailing_stop_positive = 0.01
    trailing_stop_positive_offset = 0.10
    trailing_only_offset_is_reached = True

    # === Indicateurs FIGÉS ===
    trix_length = 18
    trix_signal = 9
    rsi_length = 14
    rsi_long_max = 70    # long : pas de surchauffe
    rsi_short_min = 30   # short : pas de survente
    ema_trend_length = 200
    adx_length = 14
    adx_threshold = 25  # v10 : durci 20 -> 25 pour filtrer les marchés range

    # === Exécution ===
    process_only_new_candles = True
    startup_candle_count = 210

    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    # Levier (futures) : 1x, on ne teste que la direction, pas l'effet de levier.
    def leverage(self, pair, current_time, current_rate, proposed_leverage,
                 max_leverage, side, **kwargs) -> float:
        return 1.0

    def log(self, message: str) -> None:
        logger.info("[TrixStrategy10] %s", message)

    # ------------------------------------------------------------------ #
    # 1. Indicateurs (identiques v8)                                     #
    # ------------------------------------------------------------------ #
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        trix_df = ta.trix(dataframe["close"], length=self.trix_length, signal=self.trix_signal)
        dataframe["trix"] = trix_df.iloc[:, 0]
        dataframe["trix_signal"] = trix_df.iloc[:, 1]
        dataframe["trix_hist"] = dataframe["trix"] - dataframe["trix_signal"]

        dataframe["rsi"] = ta.rsi(dataframe["close"], length=self.rsi_length)
        dataframe["ema200"] = ta.ema(dataframe["close"], length=self.ema_trend_length)
        dataframe["adx"] = ta.adx(
            dataframe["high"],
            dataframe["low"],
            dataframe["close"],
            length=self.adx_length,
        )[f"ADX_{self.adx_length}"]

        return dataframe

    # ------------------------------------------------------------------ #
    # 2. Entrées : LONG (v8) + SHORT (miroir)                            #
    # ------------------------------------------------------------------ #
    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # --- LONG (identique v8) ---
        long_cond = [
            qtpylib.crossed_above(dataframe["trix"], dataframe["trix_signal"]),
            dataframe["trix"] > 0,
            dataframe["trix_hist"] > dataframe["trix_hist"].shift(1),  # histogramme croissant
            dataframe["rsi"] < self.rsi_long_max,
            dataframe["close"] > dataframe["ema200"],
            dataframe["adx"] > self.adx_threshold,
            dataframe["volume"] > 0,
        ]
        dataframe.loc[
            reduce(lambda a, b: a & b, long_cond),
            ["enter_long", "enter_tag"],
        ] = (1, "trix_long")

        # --- SHORT (miroir du long) ---
        short_cond = [
            qtpylib.crossed_below(dataframe["trix"], dataframe["trix_signal"]),
            dataframe["trix"] < 0,
            dataframe["trix_hist"] < dataframe["trix_hist"].shift(1),  # histogramme décroissant
            dataframe["rsi"] > self.rsi_short_min,
            dataframe["close"] < dataframe["ema200"],
            dataframe["adx"] > self.adx_threshold,
            dataframe["volume"] > 0,
        ]
        dataframe.loc[
            reduce(lambda a, b: a & b, short_cond),
            ["enter_short", "enter_tag"],
        ] = (1, "trix_short")

        return dataframe

    # ------------------------------------------------------------------ #
    # 3. Sorties : LONG close<EMA200 (v8) + SHORT close>EMA200 (miroir)  #
    # ------------------------------------------------------------------ #
    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # Sortie LONG : rupture baissière de la tendance de fond
        dataframe.loc[
            dataframe["close"] < dataframe["ema200"],
            ["exit_long", "exit_tag"],
        ] = (1, "exit_long_ema200")

        # Sortie SHORT : rupture haussière de la tendance de fond
        dataframe.loc[
            dataframe["close"] > dataframe["ema200"],
            ["exit_short", "exit_tag"],
        ] = (1, "exit_short_ema200")

        return dataframe
