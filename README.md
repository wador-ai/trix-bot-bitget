# TRIX Bot Bitget

Bot de trading automatisé basé sur l'indicateur **TRIX**, conçu pour l'exchange
**Bitget** et le framework **[Freqtrade](https://www.freqtrade.io/)**. Le dépôt
inclut également un environnement de recherche/backtest vectorisé
(`vectorbt`, `pandas-ta`) et d'optimisation bayésienne des paramètres.

> ⚠️ **Avertissement** — Le trading de crypto-actifs comporte un risque élevé de
> perte en capital. Ce logiciel est fourni **à titre éducatif et de recherche**,
> sans aucune garantie. Commencez **toujours** en `dry_run` (paper trading).
> N'engagez jamais de fonds que vous ne pouvez pas vous permettre de perdre.
> L'auteur décline toute responsabilité en cas de perte financière.

---

## 1. Stratégie TRIX

Le **TRIX** (Triple Exponential Average) est un oscillateur de momentum qui
mesure le taux de variation d'une triple moyenne mobile exponentielle (EMA) du
prix. Le triple lissage filtre le bruit de marché et ne laisse passer que les
mouvements de fond.

**Calcul :**

1. `EMA1 = EMA(close, length)`
2. `EMA2 = EMA(EMA1, length)`
3. `EMA3 = EMA(EMA2, length)`
4. `TRIX = variation_pct(EMA3)` (en %, × 10 000 selon convention)
5. `Signal = EMA(TRIX, signal_length)`

**Logique de trading :**

- **Entrée (long)** : croisement haussier du TRIX au-dessus de sa ligne de
  signal (`TRIX > Signal`), idéalement au-dessus de la ligne zéro.
- **Sortie** : croisement baissier (`TRIX < Signal`), ou déclenchement du
  stop-loss / take-profit.

Les signaux sont **décalés d'une barre** (anti-lookahead) : un signal calculé
sur la bougie *t* n'agit qu'à l'ouverture de *t+1*.

---

## 2. Structure des dossiers

```
trix-bot-bitget/
├── README.md
├── .gitignore
├── requirements.txt
├── setup.sh                 # installation Freqtrade (env. dédié)
├── config/
│   ├── config.example.json  # template Freqtrade (versionné)
│   └── config.json          # config réelle avec clés API (IGNORÉ par Git)
├── strategies/              # stratégies Freqtrade (TrixStrategy.py)
├── scripts/                 # utilitaires (téléchargement données, optim.)
├── notebooks/               # recherche & analyse (Jupyter)
├── data/                    # données OHLCV locales (IGNORÉ sauf .gitkeep)
├── logs/                    # journaux d'exécution (IGNORÉ sauf .gitkeep)
└── tests/                   # tests unitaires (pytest)
```

---

## 3. Installation

### 3.1 Cloner et créer l'environnement

```bash
git clone <url-du-depot> trix-bot-bitget
cd trix-bot-bitget
python3 -m venv .venv
source .venv/bin/activate
```

### 3.2 Dépendances de recherche/backtest

```bash
pip install -r requirements.txt
```

### 3.3 Freqtrade (installation séparée)

Freqtrade s'installe dans son propre environnement. Utilisez le script fourni
ou la procédure officielle :

```bash
./setup.sh
# ou : https://www.freqtrade.io/en/stable/installation/
```

### 3.4 Configuration & clés API Bitget

1. Créez une clé API sur Bitget (droits **lecture + trading**, **jamais** de
   droit de retrait/withdraw).
2. Copiez le template et renseignez vos clés :

   ```bash
   cp config/config.example.json config/config.json
   ```

3. Éditez `config/config.json` (clé, secret, passphrase).
   **`config/config.json` est ignoré par Git** — vos clés ne seront jamais
   versionnées.

---

## 4. Commandes

> Adapter le nom de stratégie (`TrixStrategy`) et les dates à votre cas.

### Télécharger les données historiques

```bash
freqtrade download-data --config config/config.json \
  --timeframe 1h --timerange 20230101-20240101
```

### Backtest

```bash
freqtrade backtesting --config config/config.json \
  --strategy TrixStrategy --timeframe 1h \
  --timerange 20230101-20240101
```

### Paper trading (dry-run)

Vérifier que `"dry_run": true` dans `config/config.json`, puis :

```bash
freqtrade trade --config config/config.json --strategy TrixStrategy
```

### Live (réel — à vos risques)

⚠️ Passer `"dry_run": false` **seulement** après validation complète en backtest
**et** en dry-run prolongé.

```bash
freqtrade trade --config config/config.json --strategy TrixStrategy
```

---

## 5. Paramètres de la stratégie

| Paramètre        | Valeur  | Description                                              |
|------------------|---------|---------------------------------------------------------|
| `trix_length`    | 18      | Période des EMA du triple lissage TRIX                  |
| `signal_length`  | 9       | Période de l'EMA de la ligne de signal                 |
| `timeframe`      | 1h      | Unité de temps des bougies                              |
| `stoploss`       | -10 %   | Stop-loss maximal par trade                             |
| `signal_shift`   | 1       | Décalage anti-lookahead (barres)                        |

Ces valeurs sont un **point de départ** ; elles doivent être validées par
backtest et optimisation (voir `scripts/` et l'optimisation bayésienne).

---

## 6. Gestion du risque

- **Risque par trade : 2 %** du capital maximum exposé par position.
- **Stop dynamique ATR ×2** : stop-loss placé à 2 × l'ATR sous le prix
  d'entrée (en complément du stop fixe de -10 %).
- **Maximum 3 positions ouvertes** simultanément (`max_open_trades: 3`).
- Diversification sur la whitelist (`BTC/USDT`, `ETH/USDT`, `SOL/USDT`).
- Aucun droit de retrait sur la clé API.

L'objectif est de **survivre aux séries de pertes** : la taille de position se
dimensionne pour que le stop n'entame jamais plus de 2 % du capital.

---

## 7. Tests

```bash
pytest
```

---

## 8. Avertissement (rappel)

Ce projet est un **laboratoire de recherche**. Aucune performance passée
(backtest ou dry-run) ne garantit les résultats futurs. Les frais, le slippage
et la latence réels peuvent dégrader significativement les performances. Vous
êtes seul responsable de l'usage de ce logiciel et de vos décisions de trading.
