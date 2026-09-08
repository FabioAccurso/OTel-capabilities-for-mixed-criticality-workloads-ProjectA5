#!/usr/bin/env python3
"""Variante della figura sul costo per iterazione (2-DoE/figures/03b_...).

Differenza rispetto alla figura principale: per la cella a 4 task di disturbo,
che presenta due comportamenti distinti fra le ripetizioni, si riporta la
mediana del **modo coerente** con le altre celle (7 ripetizioni su 15) anziche'
la mediana su tutte e 15, che cade sul modo anomalo e vale 1303 us.

Entrambi i valori sono misurati; cambia lo stimatore, non il dato. La figura
principale resta invariata: questa serve dove la barra fuori scala renderebbe
illeggibile il confronto fra le due modalita' di consegna.

  mediana su 15 ripetizioni  -> 1303 us   (figura 03, stimatore fedele)
  mediana del modo coerente  ->  327 us   (questa figura, 7 ripetizioni)
"""
import csv, os, statistics as st
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
OUT = os.path.join(ROOT, "2-DoE", "figures", "03b_overhead_processor.png")

plt.rcParams.update({
    "figure.dpi": 130, "savefig.dpi": 130, "font.size": 9,
    "axes.grid": True, "grid.alpha": 0.25, "grid.linestyle": "-",
    "axes.spines.top": False, "axes.spines.right": False,
    "axes.titlesize": 10.5, "axes.titleweight": "bold",
    "legend.frameon": False, "figure.constrained_layout.use": True,
})
C_BATCH, C_SIMPLE = "#1f6feb", "#d1242f"
NLOS = [0, 1, 4, 8]
SOGLIA_MODO = 9200          # separa i due comportamenti della cella n_lo=4

R = [r for r in csv.DictReader(open(os.path.join(ROOT, "2-DoE", "results.csv")))
     if r["block"] == "block3" and r["n_iters"]]
def sel(**kw):
    o = R
    for k, v in kw.items(): o = [x for x in o if x[k] == str(v)]
    return o

def costo(proc, n):
    base = st.median([float(x["budget_med_us"]) for x in sel(trace_level=0, processor_type=0, n_lo=n)])
    v = [float(x["budget_med_us"]) for x in sel(trace_level=3, processor_type=proc, n_lo=n)]
    coerenti = [x for x in v if x > SOGLIA_MODO]
    # si usa il modo coerente solo se la cella e' effettivamente bimodale
    usati = coerenti if 0 < len(coerenti) < len(v) else v
    return base - st.median(usati), len(usati), len(v)

fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(9.2, 3.9), gridspec_kw={"width_ratios": [1.25, 1]})
w = 0.36; xi = np.arange(len(NLOS))
for j, (proc, name, col) in enumerate(((0, "Batch", C_BATCH), (1, "Simple", C_SIMPLE))):
    vals = [costo(proc, n)[0] for n in NLOS]
    for ax in (ax1, ax2):
        b = ax.bar(xi + (j - 0.5) * w, vals, w, color=col, label=name if ax is ax1 else None)
        if ax is ax1 or proc == 0:
            ax.bar_label(b, fmt="%.0f", fontsize=8.5, padding=2)

ax1.set_xticks(xi); ax1.set_xticklabels([str(n) for n in NLOS])
ax1.set_xlabel("task di rumore concorrenti (n_lo)"); ax1.set_ylabel("costo per iterazione [us]")
ax1.set_title("3 — Il costo lo decide il processor, non il tracing")
ax1.legend(fontsize=8.5); ax1.set_ylim(0, 400)

ax2.set_ylim(0, 22); ax2.set_xticks(xi); ax2.set_xticklabels([str(n) for n in NLOS])
ax2.set_xlabel("task di rumore concorrenti (n_lo)")
ax2.set_title("zoom: Batch resta a ~13 us a ogni carico")
ax2.axhline(13, ls=":", c="#57606a", lw=1)

fig.savefig(OUT, bbox_inches="tight"); plt.close(fig)
print(f"scritto {OUT}  ({os.path.getsize(OUT)//1024} KB)")
for n in NLOS:
    c, u, t = costo(1, n)
    nota = "  (modo coerente)" if u != t else ""
    print(f"  Simple n_lo={n}: {c:.0f} us   [{u}/{t} ripetizioni]{nota}")
