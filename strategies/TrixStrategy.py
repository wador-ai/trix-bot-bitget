# pragma pylint: disable=missing-docstring, invalid-name, too-many-locals
"""
TrixStrategy — Stratégie Freqtrade basée sur l'indicateur TRIX.

Logique :
    - Entrée long quand le TRIX croise au-dessus de sa ligne de signal,
      au-dessus de zéro, RSI non suracheté, volume présent.
    - Sortie quand le TRIX recroise sous le signal OU repasse sous zéro.
    - Stop-loss dynamique basé sur l'ATR (× 2) en plus du stop fixe -10 %.

⚠️ Recherche/éducation uniquement. Toujours valider en backtest puis en
dry-run avant tout passage en réel.
"""

import logging
from functools import reduce
from datetime import datetime

import pandas_ta as ta
from pandas import DataFrame

from freqtrade.strategy import IStrategy, IntParameter, DecimalParameter
from freqtrade.persistence import Trade
from freqtrade.vendor.qtpylib import indicators as qtpylib


logger = logging.getLogger(__name__)


class TrixStrategy(IStrategy):
    """Stratégie de momentum TRIX pour Freqtrade (interface v3)."""

    # === Métadonnées d'interface ===
    INTERFACE_VERSION = 3

    # === Paramètres de marché ===
    timeframe = "1h"
    can_short = False

    # === Objectifs de gain (ROI) — désactivé pour laisser courir les gagnants ===
    minimal_roi = {"0": 0.99}  # ROI inatteignable -> sortie pilotée par trailing stop / signal

    # === Stop-loss fixe de sécurité ===
    stoploss = -0.10

    # Stop ATR désactivé en v3 : le trailing stop natif gère la sortie des gagnants.
    # (use_custom_stoploss=True prendrait le pas sur trailing_stop et le rendrait inopérant.)
    use_custom_stoploss = False

    # === Trailing stop (v6 hyperopt) : verrouille les gains une fois l'offset atteint ===
    # Valeurs câblées dynamiquement depuis les DecimalParameter via bot_loop_start.
    trailing_stop = True
    trailing_stop_positive = 0.01
    trailing_stop_positive_offset = 0.05
    trailing_only_offset_is_reached = True

    # === Paramètres OPTIMISABLES (hyperopt) ===
    #   buy  : géométrie de l'indicateur TRIX + seuil RSI
    #   sell : paramètres du trailing stop (câblés dans bot_loop_start)
    # Défauts = meilleurs paramètres hyperopt (epoch 97/200, SharpeHyperOptLoss, 200 epochs)
    trix_length = IntParameter(10, 25, default=19, space="buy")
    trix_signal = IntParameter(5, 15, default=6, space="buy")
    rsi_threshold = IntParameter(55, 75, default=59, space="buy")
    trailing_stop_positive_opt = DecimalParameter(
        0.01, 0.05, default=0.01, decimals=2, space="sell")
    trailing_stop_positive_offset_opt = DecimalParameter(
        0.02, 0.08, default=0.05, decimals=2, space="sell")

    # === Paramètres d'indicateurs fixés a priori (non optimisés) ===
    rsi_length = 14     # période du RSI
    atr_length = 14     # période de l'ATR
    atr_multiplier = 3.0  # multiplicateur du stop ATR (dormant : use_custom_stoploss=False)
    ema_trend_length = 200  # filtre de tendance de fond

    # === Réglages d'exécution ===
    process_only_new_candles = True
    # 210 bougies de chauffe : nécessaire pour stabiliser l'EMA200 du filtre de tendance
    startup_candle_count = 210

    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    # ------------------------------------------------------------------ #
    # Utilitaire de log : Freqtrade n'expose pas self.log() nativement,  #
    # on encapsule le logger du module pour répondre au besoin métier.   #
    # ------------------------------------------------------------------ #
    def log(self, message: str) -> None:
        """Log applicatif simple, préfixé par le nom de la stratégie."""
        logger.info("[TrixStrategy] %s", message)

    def bot_loop_start(self, **kwargs) -> None:
        """Câble les paramètres trailing optimisés sur les attributs lus par le moteur.

        Freqtrade lit trailing_stop_positive(_offset) depuis les attributs de la
        stratégie ; on les rafraîchit ici à partir des DecimalParameter (sell space)
        pour que l'hyperopt de l'espace 'sell' agisse réellement sur le trailing stop.
        """
        self.trailing_stop_positive = self.trailing_stop_positive_opt.value
        self.trailing_stop_positive_offset = self.trailing_stop_positive_offset_opt.value

    # ------------------------------------------------------------------ #
    # 1. Calcul des indicateurs                                          #
    # ------------------------------------------------------------------ #
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """Calcule TRIX, ligne de signal, histogramme, RSI et ATR."""

        # --- TRIX et sa ligne de signal via pandas_ta ---
        # ta.trix() renvoie un DataFrame : 1re colonne = TRIX, 2e = signal.
        trix_df = ta.trix(
            dataframe["close"],
            length=self.trix_length.value,
            signal=self.trix_signal.value,
        )
        dataframe["trix"] = trix_df.iloc[:, 0]
        dataframe["trix_signal"] = trix_df.iloc[:, 1]

        # --- Histogramme TRIX (écart TRIX − signal) ---
        dataframe["trix_hist"] = dataframe["trix"] - dataframe["trix_signal"]

        # --- RSI 14 périodes (filtre de surchauffe) ---
        dataframe["rsi"] = ta.rsi(dataframe["close"], length=self.rsi_length)

        # --- ATR 14 périodes (volatilité, sert au stop dynamique) ---
        dataframe["atr"] = ta.atr(
            dataframe["high"],
            dataframe["low"],
            dataframe["close"],
            length=self.atr_length,
        )

        # --- EMA 200 : filtre de tendance de fond (long uniquement en tendance haussière) ---
        dataframe["ema200"] = ta.ema(dataframe["close"], length=self.ema_trend_length)

        return dataframe

    # ------------------------------------------------------------------ #
    # 2. Signal d'entrée (long)                                          #
    # ------------------------------------------------------------------ #
    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """Conditions d'achat : croisement TRIX haussier + filtres."""

        conditions = [
            # TRIX croise AU-DESSUS de sa ligne de signal
            qtpylib.crossed_above(dataframe["trix"], dataframe["trix_signal"]),
            # Momentum positif : TRIX au-dessus de zéro
            dataframe["trix"] > 0,
            # Filtre de tendance : prix au-dessus de l'EMA200 (tendance de fond haussière)
            dataframe["close"] > dataframe["ema200"],
            # Momentum confirmé : histogramme TRIX croissant (accélération du momentum)
            dataframe["trix_hist"] > dataframe["trix_hist"].shift(1),
            # Pas en zone de surachat (seuil optimisable)
            dataframe["rsi"] < self.rsi_threshold.value,
            # Volume réel présent sur la bougie
            dataframe["volume"] > 0,
        ]

        # Toutes les conditions doivent être vraies simultanément
        dataframe.loc[
            reduce(lambda a, b: a & b, conditions),
            ["enter_long", "enter_tag"],
        ] = (1, "trix_cross_up")

        return dataframe

    # ------------------------------------------------------------------ #
    # 3. Signal de sortie                                               #
    # ------------------------------------------------------------------ #
    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """Sortie unique (v3) : rupture de la tendance de fond (close < EMA200).

        Le croisement TRIX baissier a été retiré : en v2 il générait l'essentiel
        des sorties perdantes. Les gagnants sont désormais sécurisés par le
        trailing stop ; on ne ferme sur signal que si la tendance de fond casse.
        """
        dataframe.loc[
            dataframe["close"] < dataframe["ema200"],
            ["exit_long", "exit_tag"],
        ] = (1, "ema200_break")

        return dataframe

    # ------------------------------------------------------------------ #
    # 4. Stop-loss dynamique basé sur l'ATR (× atr_multiplier)            #
    # ------------------------------------------------------------------ #
    def custom_stoploss(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        **kwargs,
    ) -> float:
        """
        Renvoie un stop-loss relatif (négatif) calé sur atr_multiplier × ATR.

        Le stop ATR ne se déclenche que s'il est PLUS serré que le stop fixe
        -10 %, ce qui protège le capital quand la volatilité est faible.
        """
        # Récupère le dataframe analysé pour lire le dernier ATR connu
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if dataframe is None or dataframe.empty:
            return self.stoploss  # repli sur le stop fixe

        last_candle = dataframe.iloc[-1].squeeze()
        atr = last_candle.get("atr")

        # Garde-fous : ATR indisponible ou prix nul -> stop fixe
        if atr is None or atr != atr or current_rate <= 0:  # NaN check via atr!=atr
            return self.stoploss

        # Distance de stop = 2 × ATR, convertie en ratio relatif au prix courant
        stop_distance = self.atr_multiplier * atr
        atr_stoploss = -(stop_distance / current_rate)

        # On garde le plus protecteur des deux (le plus proche de 0 = plus serré)
        new_stop = max(atr_stoploss, self.stoploss)

        self.log(
            f"{pair} stop ATR={atr_stoploss:.4f} fixe={self.stoploss:.4f} "
            f"-> retenu={new_stop:.4f} (ATR={atr:.6f}, profit={current_profit:.4f})"
        )
        return new_stop

    # ------------------------------------------------------------------ #
    # 5. Journalisation des trades (entrée / sortie)                     #
    # ------------------------------------------------------------------ #
    def confirm_trade_entry(
        self,
        pair: str,
        order_type: str,
        amount: float,
        rate: float,
        time_in_force: str,
        current_time: datetime,
        entry_tag,
        side: str,
        **kwargs,
    ) -> bool:
        """Log à l'ouverture d'un trade ; n'altère pas la décision (True)."""
        self.log(
            f"ENTREE {side} {pair} | montant={amount:.6f} prix={rate:.6f} "
            f"tag={entry_tag} type={order_type}"
        )
        return True

    def confirm_trade_exit(
        self,
        pair: str,
        trade: Trade,
        order_type: str,
        amount: float,
        rate: float,
        time_in_force: str,
        exit_reason: str,
        current_time: datetime,
        **kwargs,
    ) -> bool:
        """Log à la fermeture d'un trade ; n'altère pas la décision (True)."""
        profit = trade.calc_profit_ratio(rate)
        self.log(
            f"SORTIE {pair} | prix={rate:.6f} raison={exit_reason} "
            f"profit={profit:.4%}"
        )
        return True
