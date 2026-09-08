#!/usr/bin/env python3
"""Grafici del blocco 4 (backend reale: rete contro file) e della campagna di
latenza di consegna.

Continua la numerazione di plot_doe.py (01-08) con le figure 09-13. Ogni figura
risponde a UNA domanda e mostra qualcosa che una tabella non mostra altrettanto
bene:

  09  l'interazione fra processor ed exporter: nessuno dei due fattori e' costoso
      da solo, insieme costano 1592 us per iterazione
  10  dove finisce lo slack del task critico: tre celle tutte sopra zero, una che
      collassa a -164 ms
  11  la telemetria non si perde mai; il prezzo del processor sincrono e' la
      terminazione anomala del processo
  12  effetto collaterale sul carico best-effort: il thread del Batch costa ai LO
      piu' di quanto costi l'export sincrono
  13  il rovescio della medaglia del Batch: la freshness della telemetria del
      task critico dipende dal volume prodotto dagli altri

Uso: ./plot_block4.py [--out 2-DoE/figures]
"""
import argparse, csv, glob, gzip, os, statistics as st, sys
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
DOE = os.path.join(ROOT, "2-DoE")
sys.path.insert(0, HERE)
import analyze_block4 as B4          # timing(), spans_produced(), lo_iters(), find(), opener()
import analyze_latency as LAT        # load()

ap = argparse.ArgumentParser()
ap.add_argument("--out", default=os.path.join(DOE, "figures"))
A = ap.parse_args()
os.makedirs(A.out, exist_ok=True)

plt.rcParams.update({
    "figure.dpi": 130, "savefig.dpi": 130, "font.size": 9,
    "axes.grid": True, "grid.alpha": 0.25, "grid.linestyle": "-",
    "axes.spines.top": False, "axes.spines.right": False,
    "axes.titlesize": 10.5, "axes.titleweight": "bold",
    "legend.frameon": False, "figure.constrained_layout.use": True,
})
C_CTRL, C_BATCH, C_SIMPLE, C_ACC = "#7a7a7a", "#1f6feb", "#d1242f", "#bf8700"

def save(fig, name):
    p = os.path.join(A.out, name); fig.savefig(p, bbox_inches="tight"); plt.close(fig)
    print(f"  {name}  ({os.path.getsize(p)//1024} KB)")

# ---------------------------------------------------------------- dati blocco 4
ORDER = [("0", "2"), ("1", "2"), ("0", "0"), ("1", "0")]   # Batch+file, Simple+file, Batch+rete, Simple+rete
LABEL = {("0","2"): "Batch\n+ file",  ("1","2"): "Simple\n+ file",
         ("0","0"): "Batch\n+ rete",  ("1","0"): "Simple\n+ rete"}
COLOR = {"0": C_BATCH, "1": C_SIMPLE}

def slacks(run_dir):
    """Slack per iterazione del task critico, scartato il transitorio di avvio."""
    f = B4.find(run_dir, "*HI_task*.log.gz", "*HI_task*.log")
    if not f: return []
    s = []
    for l in B4.opener(f[0]):
        if l.startswith("#"): continue
        c = l.split()
        if len(c) >= 11: s.append(int(c[7]))
    k = 0
    while k < len(s) and s[k] < 0: k += 1
    return s[k:]

rows = [r for r in csv.DictReader(open(os.path.join(DOE, "data_table.csv")))
        if r["block"] == "block4"]
cells = defaultdict(list)
for r in rows:
    t = B4.timing(r["run_dir"])
    if t is None: continue
    prod, _ = B4.spans_produced(r["run_dir"])
    d = r["spans_delivered"]
    t.update(rc=int(r["exit_code"]), prod=prod,
             deliv=int(d) if d.lstrip("-").isdigit() else None,
             lo=B4.lo_iters(r["run_dir"]), slacks=slacks(r["run_dir"]))
    cells[(r["processor_type"], r["exporter_type"])].append(t)
print("blocco 4: %d run letti" % sum(len(v) for v in cells.values()))

# riferimento senza strumentazione: blocco 3, trace_level=0, stesso carico
base = st.median([x["budget"] for x in
                  (B4.timing(r["run_dir"]) for r in csv.DictReader(open(os.path.join(DOE, "data_table.csv")))
                   if r["block"] == "block3" and r["trace_level"] == "0" and r["n_lo"] == "4")
                  if x])

# ---------------------------------------------------------------- FIG 9
# I due fattori non sono additivi: e' l'interazione a costare.
fig, ax = plt.subplots(figsize=(6.4, 4.4))
xs = [0, 1]
for proc, name in (("0", "Batch (asincrono)"), ("1", "Simple (sincrono)")):
    y, pts = [], []
    for e in ("2", "0"):
        v = cells[(proc, e)]
        cost = [base - x["budget"] for x in v]
        y.append(st.median(cost)); pts.append(cost)
    ax.plot(xs, y, "-o", color=COLOR[proc], lw=2, ms=7, label=name, zorder=4)
    for i, c in enumerate(pts):
        ax.scatter(np.full(len(c), xs[i]) + np.random.default_rng(1).uniform(-.035, .035, len(c)),
                   c, s=12, color=COLOR[proc], alpha=.35, zorder=3)
    for i, v in enumerate(y):
        ax.annotate(f"{v:,.0f}".replace(",", " ") + " µs", (xs[i], v),
                    textcoords="offset points",
                    xytext=((0, 11) if proc == "1" else [(-36, -4), (0, -19)][i]),
                    ha="center", fontsize=9, fontweight="bold", color=COLOR[proc])
ax.set_yscale("log")
ax.set_ylim(5.5, 3.2e4)
ax.set_xticks(xs); ax.set_xticklabels(["file locale", "rete (collector Zipkin)"])
ax.set_xlim(-.35, 1.35)
ax.set_ylabel("costo per iterazione del task critico  (µs, scala log)")
ax.set_xlabel("destinazione della telemetria  ·  80 esecuzioni, 20 per cella")
ax.axhline(10000, ls=":", lw=1, color=C_ACC)
ax.text(1.32, 10400, "periodo del task critico (10 ms)", ha="right", fontsize=8, color=C_ACC)
ax.set_title("9 — Processor ed exporter non sono fattori indipendenti")
ax.legend(loc="upper left")
save(fig, "09_interaction_cost.png")

# ---------------------------------------------------------------- FIG 10
# Dove finisce il budget residuo: tre celle sopra zero, una che collassa.
fig, ax = plt.subplots(figsize=(7.2, 4.2))
for k in ORDER:
    v = np.concatenate([np.array(x["slacks"], float) for x in cells[k]])
    v = np.sort(v)
    y = np.arange(1, len(v) + 1) / len(v)
    ls = "-" if k[1] == "0" else "--"
    ax.plot(v / 1000.0, y * 100, ls, color=COLOR[k[0]], lw=1.8,
            label=LABEL[k].replace("\n", " "))
ax.axvline(0, color="#24292f", lw=1.2)
ax.text(0.35, 90, "scadenza rispettata →", fontsize=8.5, color="#57606a")
ax.text(-0.35, 90, "← scadenza persa", fontsize=8.5, color=C_SIMPLE, ha="right")
ax.set_xscale("symlog", linthresh=1.0)
ax.set_xlabel("slack dell'iterazione  (ms, scala logaritmica simmetrica attorno allo zero)")
ax.set_ylabel("% di iterazioni con slack ≤ x")
ax.set_ylim(0, 101)
ax.set_xlim(-400, 20)
ax.annotate("2.90 % delle iterazioni\nsotto zero; minimo −164 ms",
            xy=(-10, 2.9), xytext=(-190, 30), fontsize=8.5, color=C_SIMPLE,
            arrowprops=dict(arrowstyle="->", color=C_SIMPLE, lw=1))
ax.annotate("le altre tre celle sono\nsovrapposte qui: nessuna\niterazione sotto zero,\nslack sempre ~8 ms",
            xy=(7.6, 50), xytext=(0.25, 62), fontsize=8.5, color="#57606a",
            arrowprops=dict(arrowstyle="->", color="#57606a", lw=1))
ax.set_title("10 — Solo l'export sincrono verso la rete fa perdere scadenze")
ax.legend(loc="upper left", fontsize=8.5)
save(fig, "10_slack_ecdf_block4.png")

# ---------------------------------------------------------------- FIG 11
# La telemetria arriva tutta; il prezzo e' la terminazione anomala.
fig, (a1, a2) = plt.subplots(1, 2, figsize=(9.2, 3.9),
                             gridspec_kw={"width_ratios": [1.25, 1]})
x = np.arange(len(ORDER)); w = 0.36
prod, deliv, ok = [], [], []
for k in ORDER:
    v = [z for z in cells[k] if z["deliv"] is not None and z["rc"] == 0]
    if not v:                       # Simple+rete: tutti i run abortiscono
        v = [z for z in cells[k] if z["deliv"] is not None]
        ok.append(False)
    else:
        ok.append(True)
    prod.append(st.median([z["prod"] for z in v]))
    deliv.append(st.median([z["deliv"] for z in v]))
a1.bar(x - w/2, prod, w, color="#c9ced6", edgecolor="#8b949e", label="span prodotti")
a1.bar(x + w/2, deliv, w, color=[COLOR[k[0]] for k in ORDER], label="span consegnati al backend")
for i, (p, d) in enumerate(zip(prod, deliv)):
    a1.annotate("0 persi" if ok[i] else "log troncati\ndall'abort",
                (i, max(p, d)), textcoords="offset points", xytext=(0, 5),
                ha="center", fontsize=8, color="#57606a" if ok[i] else C_ACC)
a1.set_xticks(x); a1.set_xticklabels([LABEL[k] for k in ORDER], fontsize=8.5)
a1.set_ylabel("span per esecuzione (mediana)")
a1.set_ylim(0, 46000)
a1.set_title("11a — Nessuno span perso, in nessuna cella")
a1.legend(loc="lower left", fontsize=8)

ab = [100.0 * sum(1 for z in cells[k] if z["rc"] == 134) / len(cells[k]) for k in ORDER]
a2.bar(x, ab, 0.6, color=[COLOR[k[0]] for k in ORDER],
       edgecolor=[COLOR[k[0]] for k in ORDER], linewidth=1.4,
       hatch=["", "", "", ""])
a2.bar(x, [0.9 if v == 0 else 0 for v in ab], 0.6,
       color=[COLOR[k[0]] for k in ORDER])   # celle a zero: un accenno visibile
for i, v in enumerate(ab):
    a2.annotate("%d/20" % round(v * 20 / 100), (i, v), textcoords="offset points",
                xytext=(0, 4), ha="center", fontsize=9, fontweight="bold")
a2.set_xticks(x); a2.set_xticklabels([LABEL[k] for k in ORDER], fontsize=8.5)
a2.set_ylabel("% di esecuzioni terminate da SIGABRT")
a2.set_ylim(0, 118)
a2.set_title("11b — Il prezzo è la terminazione del processo")
save(fig, "11_delivery_and_abort.png")

# ---------------------------------------------------------------- FIG 12
# Chi paga l'asincronia: il carico best-effort che condivide il core.
fig, ax = plt.subplots(figsize=(6.4, 4.0))
for i, k in enumerate(ORDER):
    v = [z["lo"] for z in cells[k]]
    ax.bar(i, st.median(v), 0.6, color=COLOR[k[0]], alpha=.85)
    ax.scatter(np.full(len(v), i) + np.random.default_rng(2).uniform(-.13, .13, len(v)),
               v, s=13, color="#24292f", alpha=.55, zorder=3)
    ax.annotate("%d" % st.median(v), (i, st.median(v)), textcoords="offset points",
                xytext=(0, 6), ha="center", fontsize=9, fontweight="bold")
ax.set_xticks(range(len(ORDER))); ax.set_xticklabels([LABEL[k] for k in ORDER])
ax.set_ylabel("iterazioni completate dai 4 task best-effort")
ax.set_xlabel("ogni punto è una delle 20 ripetizioni")
ax.set_title("12 — A parità di exporter su file, il Batch costa ai best-effort\npiù dell'export sincrono")
save(fig, "12_lo_throughput.png")

# ---------------------------------------------------------------- FIG 13
# Campagna di latenza: la freshness della telemetria di HI.
NL = [0, 1, 4]
lat = {}
for proc in ("p0", "p1"):
    for n in NL:
        acc = []
        for d in sorted(glob.glob(os.path.join(DOE, "latency", f"{proc}_n{n}", "run_*"))):
            r = LAT.load(d)
            if r: acc.extend(r[0]["HI"])
        lat[(proc, n)] = np.array(acc)
fig, ax = plt.subplots(figsize=(6.8, 4.3))
for proc, name, c in (("p0", "Batch (asincrono)", C_BATCH), ("p1", "Simple (sincrono)", C_SIMPLE)):
    p50 = [np.percentile(lat[(proc, n)], 50) for n in NL]
    p99 = [np.percentile(lat[(proc, n)], 99) for n in NL]
    ax.plot(range(len(NL)), p50, "-o", color=c, lw=2, ms=7, label=name + " — mediana")
    ax.plot(range(len(NL)), p99, ":", color=c, lw=1.4, label=name + " — 99° percentile")
    ax.fill_between(range(len(NL)), p50, p99, color=c, alpha=.10)
    for i, v in enumerate(p50):
        ax.annotate(("%.1f ms" % v) if v < 100 else ("%.0f ms" % v), (i, v),
                    textcoords="offset points", xytext=(0, -16 if proc == "p1" else 9),
                    ha="center", fontsize=8.5, fontweight="bold", color=c)
ax.set_yscale("log")
ax.set_ylim(0.34, 1.1e4)
ax.set_xticks(range(len(NL))); ax.set_xticklabels(["nessuno", "1 task", "4 task"])
ax.set_xlabel("carico best-effort presente accanto al task critico\n"
              "(il task critico produce sempre 100 span al secondo)")
ax.set_ylabel("età della telemetria del task critico\nall'arrivo al backend  (ms, scala log)")
ax.axhline(5000, ls=":", lw=1, color=C_ACC)
ax.text(2.02, 5400, "timer del Batch (5 s)", ha="right", fontsize=8, color=C_ACC)
ax.set_title("13 — Con il Batch la freshness della telemetria critica\ndipende da quanto producono gli altri")
ax.legend(loc="center left", fontsize=8.5)
save(fig, "13_delivery_latency.png")
