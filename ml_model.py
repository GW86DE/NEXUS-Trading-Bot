"""
ML-Modell als BESTAETIGUNGS-Filter fuer die technischen Signale.

Was sich gegenueber der ersten Fassung geaendert hat und warum:

1. n_jobs=1 bei der Vorhersage + Batch-Vorhersage
   Vorher lief das Modell mit n_jobs=-1 (alle CPU-Kerne). Bei EINER grossen
   Vorhersage ist das schnell -- aber der Backtest fragte das Modell fuer
   JEDEN einzelnen Kursbalken einzeln ab, und jedes Mal wurde ein
   Parallel-Pool aufgebaut. Das war die Ursache fuer die tausenden
   sklearn-Warnungen UND fuer die lange Laufzeit.

2. Relative statt absoluter Features
   Vorher waren u.a. die absoluten SMA-Werte Features. Ein so trainiertes
   Modell funktioniert nur in genau der Kursregion der Trainingsdaten --
   steigt der Kurs spaeter deutlich, liegt alles ausserhalb des Gelernten.
   Jetzt werden Verhaeltnisse und Veraenderungen verwendet.

3. Label mit Mindest-Schwelle
   Vorher: Label=1, sobald der Kurs auch nur minimal steigt -- das Modell
   lernt dann vor allem Rauschen. Jetzt zaehlt eine Bewegung erst ab einer
   Mindestgroesse als "steigt".

4. Ehrliche Bewertung
   Zusaetzlich zur Accuracy wird ausgewiesen, wie gut reines Raten waere
   (Baseline). Nur wenn das Modell die Baseline schlaegt, hat es Wert.
"""

import warnings
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, classification_report

from indicators import add_all_indicators
import config
from cost_engine import reference_label_threshold

warnings.filterwarnings("ignore", category=UserWarning)

FEATURE_COLUMNS = [
    "rsi", "macd_hist", "volatility", "return_1", "return_5",
    "sma_ratio", "price_vs_slow", "rsi_change",
]

# Mindestbewegung (relativ), ab der ein Kursanstieg als Label=1 zaehlt.
# 0.001 = 0,1 % -- filtert reines Rauschen aus den Trainingsdaten.
LABEL_MIN_MOVE = 0.001


def build_features_and_labels(raw_df: pd.DataFrame, horizon: int = 1,
                              asset_type: str = "stock", currency: str = "USD",
                              underdog: bool = False):
    df = add_all_indicators(raw_df)

    future_return = df["close"].shift(-horizon) / df["close"] - 1
    if getattr(config, "ML_COST_AWARE_LABELS", True):
        thresholds = df["close"].astype(float).map(
            lambda px: reference_label_threshold(px, asset_type, currency, underdog)
        )
        df["label_threshold"] = thresholds
        df["label"] = (future_return > thresholds).astype(int)
    else:
        df["label_threshold"] = LABEL_MIN_MOVE
        df["label"] = (future_return > LABEL_MIN_MOVE).astype(int)

    df = df.dropna(subset=FEATURE_COLUMNS)
    if horizon > 0:
        df = df.iloc[:-horizon]

    return df[FEATURE_COLUMNS], df["label"], df


def train_model(raw_df: pd.DataFrame, horizon: int = 1,
                model_path: str = "ml_model.joblib", verbose: bool = True,
                asset_type: str = "stock", currency: str = "USD", underdog: bool = False):
    X, y, frame = build_features_and_labels(raw_df, horizon, asset_type, currency, underdog)

    if len(X) < 200:
        raise ValueError(
            f"Zu wenig Datenpunkte fuer sinnvolles Training "
            f"({len(X)} vorhanden, mind. 200 noetig)."
        )

    # Zeitlich sauber trennen -- KEIN Shuffle bei Zeitreihen!
    split = int(len(X) * 0.8)
    X_train, X_test = X.iloc[:split], X.iloc[split:]
    y_train, y_test = y.iloc[:split], y.iloc[split:]

    model = RandomForestClassifier(
        n_estimators=300,
        max_depth=6,
        min_samples_leaf=30,
        class_weight="balanced",
        random_state=42,
        n_jobs=-1,          # beim TRAINING sind alle Kerne sinnvoll
    )
    model.fit(X_train, y_train)

    # Fuer spaetere Einzel-Vorhersagen auf 1 Kern stellen (siehe Docstring)
    model.set_params(n_jobs=1)

    preds = model.predict(X_test)
    acc = accuracy_score(y_test, preds)

    # Baseline: was wuerde man erreichen, wenn man immer die haeufigere
    # Klasse raet? Nur wer DAS schlaegt, hat echten Mehrwert.
    baseline = max(y_test.mean(), 1 - y_test.mean())
    edge = acc - baseline

    if verbose:
        if getattr(config, "ML_COST_AWARE_LABELS", True):
            try:
                print(f"Kostenbewusstes Label: Mindestbewegung im Test im Mittel "
                      f"{frame['label_threshold'].iloc[split:].mean()*100:.3f} %")
            except Exception:
                __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)
        print(f"Test-Accuracy:      {acc:.3f}")
        print(f"Baseline (raten):   {baseline:.3f}")
        print(f"Vorsprung:          {edge:+.3f}")
        if edge <= 0:
            print(">>> Das Modell schlaegt reines Raten NICHT. Es liefert derzeit")
            print(">>> keinen Mehrwert -- die Strategie stuetzt sich dann faktisch")
            print(">>> allein auf die technischen Regeln.")
        else:
            print(">>> Leichter Vorsprung vorhanden. Vorsicht: kleine Vorspruenge")
            print(">>> verschwinden in der Praxis oft durch Gebuehren/Spreads.")
        print()
        print(classification_report(y_test, preds, zero_division=0))

    joblib.dump(model, model_path)
    if verbose:
        print(f"Modell gespeichert unter: {model_path}")

    return model, acc


def train_model_multi(data_by_symbol: dict, horizon: int = 1,
                      model_path: str = "ml_model.joblib", verbose: bool = True):
    """Trainiert ein gemeinsames Modell auf mehreren Symbolen – zeitlich sauber.

    ``data_by_symbol``: {symbol: {"df": DataFrame, "currency": "USD",
    "underdog": bool, "asset_type": "stock"}}. Fuer JEDES Symbol werden die
    letzten 20 % separat als Testperiode zurueckgehalten. Erst danach werden
    die Train-/Test-Saetze zusammengefuehrt. Dadurch werden nicht einfach
    spaetere Daten eines Symbols in dessen Training gemischt.
    """
    train_x=[]; train_y=[]; test_x=[]; test_y=[]; thresholds=[]; used=[]
    for symbol, item in (data_by_symbol or {}).items():
        raw=item.get("df") if isinstance(item,dict) else item
        if raw is None: continue
        asset=(item.get("asset_type","stock") if isinstance(item,dict) else "stock")
        currency=(item.get("currency","USD") if isinstance(item,dict) else "USD")
        underdog=bool(item.get("underdog",False)) if isinstance(item,dict) else False
        try:
            X,y,frame=build_features_and_labels(raw,horizon,asset,currency,underdog)
        except Exception:
            continue
        if len(X)<200: continue
        split=int(len(X)*0.8)
        if split<100 or len(X)-split<30: continue
        train_x.append(X.iloc[:split]); train_y.append(y.iloc[:split])
        test_x.append(X.iloc[split:]); test_y.append(y.iloc[split:])
        if "label_threshold" in frame:
            thresholds.append(float(frame["label_threshold"].iloc[split:].mean()))
        used.append(str(symbol))
    if not train_x:
        raise ValueError("Keine ausreichend langen Symbol-Datensaetze fuer Universums-Training.")
    X_train=pd.concat(train_x,ignore_index=True); y_train=pd.concat(train_y,ignore_index=True)
    X_test=pd.concat(test_x,ignore_index=True); y_test=pd.concat(test_y,ignore_index=True)
    model=RandomForestClassifier(n_estimators=400,max_depth=7,min_samples_leaf=35,
                                 class_weight="balanced",random_state=42,n_jobs=-1)
    model.fit(X_train,y_train); model.set_params(n_jobs=1)
    preds=model.predict(X_test); acc=accuracy_score(y_test,preds)
    baseline=max(float(y_test.mean()),1-float(y_test.mean())); edge=acc-baseline
    if verbose:
        print(f"Universums-Training: {len(used)} Symbole | Train {len(X_train):,} | Test {len(X_test):,}")
        print("Symbole:",", ".join(used))
        if thresholds:
            print(f"Kostenbewusste Test-Schwelle im Mittel: {np.mean(thresholds)*100:.3f} %")
        print(f"Test-Accuracy: {acc:.3f} | Baseline: {baseline:.3f} | Vorsprung: {edge:+.3f}")
        print(classification_report(y_test,preds,zero_division=0))
    joblib.dump(model,model_path)
    if verbose: print(f"Modell gespeichert unter: {model_path}")
    return model,acc


def load_model(model_path: str = "ml_model.joblib"):
    path = Path(model_path)
    if not path.exists():
        return None
    model = joblib.load(path)
    try:
        model.set_params(n_jobs=1)
    except Exception:
        __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)
    return model


def predict_up_probability(model, latest_features_row: pd.Series) -> float:
    """
    Wahrscheinlichkeit, dass der Kurs nennenswert steigt.
    Ohne Modell wird 0.5 (neutral) zurueckgegeben -- die Strategie
    faellt dann auf reine Technik-Regeln zurueck.
    """
    if model is None:
        return 0.5

    values = latest_features_row[FEATURE_COLUMNS]
    if values.isnull().any():
        return 0.5

    row = values.to_numpy(dtype=float).reshape(1, -1)
    proba = model.predict_proba(row)[0]
    classes = list(model.classes_)
    return float(proba[classes.index(1)]) if 1 in classes else 0.5


def predict_up_probability_batch(model, feature_df: pd.DataFrame) -> np.ndarray:
    """
    Vorhersage fuer viele Zeilen auf einmal -- um Groessenordnungen
    schneller als zeilenweise Aufrufe. Wird im Backtest genutzt.
    """
    if model is None:
        return np.full(len(feature_df), 0.5)

    proba = model.predict_proba(feature_df[FEATURE_COLUMNS].to_numpy(dtype=float))
    classes = list(model.classes_)
    if 1 not in classes:
        return np.full(len(feature_df), 0.5)
    return proba[:, classes.index(1)]
