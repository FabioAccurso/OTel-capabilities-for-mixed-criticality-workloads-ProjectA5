#!/usr/bin/env python3
"""Task 5b — grafici del DoE, da 2-DoE/results.csv (e dai log HI dove serve).

Ogni figura risponde a UNA domanda e mostra qualcosa che una tabella non mostra
altrettanto bene. Uso: ./plot_doe.py [--out 2-DoE/figures]
"""
import argparse, csv, glob, gzip, math, os, statistics as st
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
ap = argparse.ArgumentParser()
ap.add_argument("--results", default=os.path.join(ROOT, "2-DoE", "results.csv"))
ap.add_argument("--out", default=os.path.join(ROOT, "2-DoE", "figures"))
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

R = [r for r in csv.DictReader(open(A.results)) if r["n_iters"]]
def sel(**kw):
    o = R
    for k, v in kw.items(): o = [x for x in o if x[k] == str(v)]
    return o
def med(rows, c): return st.median([float(x[c]) for x in rows if x[c] != ""])
def wilson(k, n, z=1.96):
    if n == 0: return 0.0, 0.0
    p = k/n; d = 1 + z*z/n
    c = (p + z*z/(2*n))/d
    h = z*math.sqrt(p*(1-p)/n + z*z/(4*n*n))/d
    return max(0., c-h), min(1., c+h)
def save(fig, name):
    p = os.path.join(A.out, name); fig.savefig(p, bbox_inches="tight"); plt.close(fig)
    print(f"  {name}  ({os.path.getsize(p)//1024} KB)")

SAMPLERS = [(2,"0.0","AlwaysOff",0.0), (1,"0.1","Ratio 0.1",0.1), (1,"0.3","Ratio 0.3",0.3),
            (1,"0.5","Ratio 0.5",0.5), (1,"0.7","Ratio 0.7",0.7), (0,"0.0","AlwaysOn",1.0)]
ARMS = [(0,0,"controllo\n(no tracing)",C_CTRL), (3,0,"Batch",C_BATCH), (3,1,"Simple",C_SIMPLE)]
NLOS = [0,1,4,8]

# ---------------------------------------------------------------- FIG 1
# La decisione di campionamento e' per-trace: ogni run e' 0 o 17 span, mai in mezzo.
fig, ax = plt.subplots(figsize=(7.2, 4.0))
rng = np.random.default_rng(0)
for i, (stype, ratio, name, _) in enumerate(SAMPLERS):
    S = sel(block="block2", sampler_type=stype, sampler_ratio=ratio)
    y = np.array([int(x["spans_exported_total"] or 0) for x in S], float)
    x = i + rng.uniform(-0.17, 0.17, len(y))
    full = y >= 17
    ax.scatter(x[~full], y[~full], s=26, c="#c9ced6", edgecolors="#8b949e", lw=.5, zorder=3)
    ax.scatter(x[full], y[full], s=26, c=C_BATCH, edgecolors="white", lw=.5, zorder=3)
ax.axhspan(0.5, 16.5, color=C_SIMPLE, alpha=0.07, zorder=0)
ax.text(2.5, 8.5, "nessun run e' mai atterrato qui\n(un run parziale sarebbe la prova\nche il sampler distingue HI da LO)",
        ha="center", va="center", fontsize=8.5, color=C_SIMPLE, style="italic")
ax.set_xticks(range(len(SAMPLERS))); ax.set_xticklabels([s[2] for s in SAMPLERS])
ax.set_yticks([0, 17]); ax.set_yticklabels(["0\n(niente)", "17\n(tutto)"])
ax.set_ylim(-2.6, 20); ax.set_ylabel("span esportati nel run")
ax.set_title("1 — La decisione di campionamento e' per-trace, non per-task")
ax.set_xlabel("150 esecuzioni, 25 per criterio di campionamento  ·  ogni punto e' una esecuzione")
save(fig, "01_all_or_nothing.png")

# ---------------------------------------------------------------- FIG 2
# Il sampler rispetta il ratio... ma applicandolo all'intera esecuzione.
fig, ax = plt.subplots(figsize=(5.6, 4.4))
xs, ys, los, his, names = [], [], [], [], []
for stype, ratio, name, nominal in SAMPLERS:
    S = sel(block="block2", sampler_type=stype, sampler_ratio=ratio)
    k = sum(1 for x in S if int(x["spans_exported_total"] or 0) > 0)
    lo, hi = wilson(k, len(S))
    xs.append(nominal); ys.append(k/len(S)); los.append(k/len(S)-lo); his.append(hi-k/len(S)); names.append(name)
ax.plot([0,1],[0,1], ls="--", c="#8b949e", lw=1, zorder=1, label="atteso se la probabilita' e' rispettata")
ax.errorbar(xs, ys, yerr=[los,his], fmt="o", ms=6, c=C_BATCH, ecolor=C_BATCH,
            elinewidth=1.4, capsize=4, zorder=3, label="misurato (IC 95 %, Wilson, n=25)")
for x, y, n in zip(xs, ys, names):
    ax.annotate(n, (x, y), textcoords="offset points", xytext=(7, -11), fontsize=8, color="#57606a")
ax.set_xlabel("ratio richiesto"); ax.set_ylabel("frazione di run con almeno uno span")
ax.set_xlim(-.06, 1.08); ax.set_ylim(-.06, 1.12); ax.legend(loc="upper left", fontsize=8)
ax.set_title("2 — Il sampler rispetta la probabilita' richiesta")
save(fig, "02_sampling_fraction.png")

# ---------------------------------------------------------------- FIG 3
# Overhead per iterazione: Batch ~13 us, Simple ~300 us.
fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(9.2, 3.9), gridspec_kw={"width_ratios":[1.25,1]})
w = 0.36; xi = np.arange(len(NLOS))
base = {n: med(sel(block="block3", trace_level=0, processor_type=0, n_lo=n), "budget_med_us") for n in NLOS}
for j, (t, p, name, col) in enumerate(ARMS[1:]):
    v = [base[n] - med(sel(block="block3", trace_level=t, processor_type=p, n_lo=n), "budget_med_us") for n in NLOS]
    b = ax1.bar(xi + (j-0.5)*w, v, w, color=col, label=name.replace("\n"," "))
    ax1.bar_label(b, fmt="%.0f", fontsize=8, padding=2)
ax1.set_xticks(xi); ax1.set_xticklabels([f"{n}" for n in NLOS])
ax1.set_xlabel("task di rumore concorrenti (n_lo)"); ax1.set_ylabel("costo per iterazione [us]")
ax1.set_title("3 — Il costo lo decide il processor, non il tracing")
ax1.legend(fontsize=8.5); ax1.set_ylim(0, 1480)
ax1.annotate("cella bimodale:\n8 rip. a ~8688 us, 7 a ~9665\nla mediana non rappresenta\nun comportamento unico",
             xy=(2-0.5*w, 1303), xytext=(0.26, 0.80), textcoords="axes fraction",
             fontsize=7.5, color=C_ACC, ha="center",
             arrowprops=dict(arrowstyle="->", color=C_ACC, lw=1))
for j, (t, p, name, col) in enumerate(ARMS[1:]):
    v = [base[n] - med(sel(block="block3", trace_level=t, processor_type=p, n_lo=n), "budget_med_us") for n in NLOS]
    b = ax2.bar(xi + (j-0.5)*w, v, w, color=col)
    if p == 0: ax2.bar_label(b, fmt="%.0f", fontsize=8, padding=2)
ax2.set_ylim(0, 22); ax2.set_xticks(xi); ax2.set_xticklabels([f"{n}" for n in NLOS])
ax2.set_xlabel("task di rumore concorrenti (n_lo)")
ax2.set_title("zoom: Batch resta a ~13 us a ogni carico")
ax2.axhline(13, ls=":", c="#57606a", lw=1)
save(fig, "03_overhead_processor.png")

# ---------------------------------------------------------------- FIG 4
# La causa: export sincrono contro export delegato a un thread.
fig, ax = plt.subplots(figsize=(6.4, 4.0))
for t, p, name, col in ARMS:
    v = [med(sel(block="block3", trace_level=t, processor_type=p, n_lo=n), "export_attempts") for n in NLOS]
    v = [max(x, 0.6) for x in v]
    ax.plot(NLOS, v, "o-", color=col, lw=1.8, ms=6, label=name.replace("\n", " "))
    for x, y in zip(NLOS, v):
        if y > 1: ax.annotate(f"{y:.0f}", (x, y), textcoords="offset points", xytext=(6, 4), fontsize=7.6, color=col)
ax.set_yscale("log"); ax.set_xticks(NLOS)
ax.set_xlabel("task di rumore concorrenti (n_lo)")
ax.set_ylabel("tentativi di export per run (scala log)")
ax.set_title("4 — Perche' Simple costa 23 volte tanto")
ax.legend(fontsize=8.5, loc="lower right", bbox_to_anchor=(1.0, 0.02))
save(fig, "04_export_attempts.png")

# ---------------------------------------------------------------- FIG 5
# Distribuzione dello slack: dove il margine del task critico va sotto zero.
def hi_slack(rows, cap=None):
    out = []
    for r in rows:
        m = sorted(glob.glob(os.path.join(r["run_dir"], "*HI_task*.log*")))
        if not m: continue
        op = gzip.open(m[0], "rt", errors="replace") if m[0].endswith(".gz") else open(m[0])
        sl = []
        for l in op:
            c = l.split()
            if len(c) < 11: continue
            try: int(c[0])
            except ValueError: continue
            sl.append(int(c[7]))
        k = 0
        while k < len(sl) and sl[k] < 0: k += 1
        out += sl[k:]
        if cap and len(out) > cap: break
    return np.array(out)

fig, (axA, axB) = plt.subplots(1, 2, figsize=(9.2, 3.9))
for t, p, name, col in ARMS:
    v = hi_slack(sel(block="block3", trace_level=t, processor_type=p, n_lo=8))
    xs = np.sort(v); ys = np.arange(1, len(xs)+1)/len(xs)
    ls = "--" if p == 0 and t == 0 else "-"
    axA.plot(xs, ys, color=col, lw=1.9, ls=ls, label=f"{name.replace(chr(10),' ')}  (n={len(xs)})")
    axB.plot(xs, ys*100, color=col, lw=1.9, ls=ls)
axA.set_xlabel("slack [us]  ·  margine prima della deadline"); axA.set_ylabel("frazione di iterazioni")
axA.set_title("5 — Il margine del task critico (n_lo = 8)")
axA.legend(fontsize=8, loc="upper left")
axA.text(0.30, 0.55, "controllo e Batch sono\nsovrapposti: nessuna delle\ndue curve tocca lo zero",
         transform=axA.transAxes, ha="center", fontsize=7.8, color="#57606a", style="italic")
axA.axvline(0, color=C_SIMPLE, lw=1, ls="--")
axB.set_xlim(-4000, 500); axB.set_ylim(0, 0.35)
axB.axvline(0, color=C_SIMPLE, lw=1, ls="--")
axB.axvspan(-4000, 0, color=C_SIMPLE, alpha=0.07)
axB.set_xlabel("slack [us]"); axB.set_ylabel("% di iterazioni")
axB.set_title("zoom sulla coda: solo Simple attraversa lo zero")
axB.text(-2000, 0.22, "DEADLINE\nPERSA", ha="center", fontsize=9, color=C_SIMPLE, weight="bold")
save(fig, "05_slack_distribution.png")

# ---------------------------------------------------------------- FIG 6
# Robustezza: Simple non degrada il processo, lo termina.
fig, ax = plt.subplots(figsize=(6.0, 3.8))
th, rate, lab = [], [], []
for n in NLOS:
    S = sel(block="block3", trace_level=3, processor_type=1, n_lo=n)
    if not S: continue
    k = sum(1 for x in S if x["aborted"] == "1")
    th.append(n+1); rate.append(100*k/len(S)); lab.append(f"{k}/{len(S)}")
b = ax.bar([str(t) for t in th], rate, 0.55, color=[C_BATCH if r == 0 else C_SIMPLE for r in rate])
ax.bar_label(b, labels=lab, fontsize=8.5, padding=3)
ax.set_ylim(0, 118); ax.set_ylabel("% di run terminati con SIGABRT")
ax.set_xlabel("thread applicativi (1 HI + n_lo)")
ax.set_title("6 — Con Simple il processo real-time non rallenta: muore")
ax.text(0.02, 0.93, "trace_level 3, SimpleSpanProcessor\nBatch e controllo: 0 abort su 120 run",
        transform=ax.transAxes, fontsize=8, color="#57606a", va="top")
save(fig, "06_abort_rate.png")

# ---------------------------------------------------------------- FIG 7
# Perche' la metrica ovvia (`run`) porta a una conclusione falsa.
# Tre pannelli: l'anatomia di un'iterazione (che spiega da dove viene il
# "budget"), la metrica sbagliata, la metrica giusta.
import matplotlib.gridspec as gridspec
fig = plt.figure(figsize=(9.6, 6.4))
gs = gridspec.GridSpec(2, 2, height_ratios=[1, 1.35], hspace=0.52, wspace=0.28)
axT = fig.add_subplot(gs[0, :]); ax1 = fig.add_subplot(gs[1, 0]); ax2 = fig.add_subplot(gs[1, 1])

lv = [0, 1, 2, 3]
runv = [med(sel(block="block1", trace_level=t), "med_duration_us") for t in lv]
slkv = [med(sel(block="block1", trace_level=t), "slack_med_us") for t in lv]
budv = [med(sel(block="block1", trace_level=t), "budget_med_us") for t in lv]
PER = 10000.0
rest = [PER - b for b in budv]

# --- pannello alto: anatomia dell'iterazione (livello 0 vs livello 3) -------
C_RUN, C_SLACK, C_REST = "#8fb8e8", "#dfe4ea", C_SIMPLE
for i, t in enumerate([0, 3]):
    y = 1 - i
    axT.barh(y, runv[t], color=C_RUN, edgecolor="white", height=.52)
    axT.barh(y, slkv[t], left=runv[t], color=C_SLACK, edgecolor="white", height=.52)
    axT.barh(y, rest[t]*40, left=runv[t]+slkv[t], color=C_REST, edgecolor="white", height=.52)
    axT.text(runv[t]/2, y, f"run\n{runv[t]:.0f} us", ha="center", va="center", fontsize=8.2)
    axT.text(runv[t]+slkv[t]/2, y, f"slack\n{slkv[t]:.0f} us", ha="center", va="center", fontsize=8.2)
    xend = runv[t] + slkv[t] + rest[t]*40
    axT.annotate(f"{rest[t]:.0f} us", xy=(xend + 90, y), va="center", fontsize=9.5,
                 color=C_REST, weight="bold")
axT.set_yticks([0, 1]); axT.set_yticklabels(["trace_level 3", "trace_level 0"], fontsize=9)
axT.set_xlim(0, 11700); axT.set_xlabel("un'iterazione = 10 000 us esatti (timer su griglia assoluta)", fontsize=9)
axT.set_title("Anatomia di un'iterazione: `budget` = `run` + `slack`, e cio' che resta e' l'overhead invisibile",
              fontsize=10)
axT.grid(False); axT.spines["left"].set_visible(False)
axT.text(PER+560, 0.5, "\"il resto\": il tempo dell'iterazione\nche non compare ne' in `run` ne' in `slack`\n(disegnato a scala 40x per renderlo visibile)",
         fontsize=7.8, color=C_REST, va="center", style="italic")
axT.axvline(PER, color="#57606a", lw=1, ls=":")

# --- basso sinistra: la metrica sbagliata ----------------------------------
b1 = ax1.bar([str(t) for t in lv], runv, .55, color=["#c9ced6", "#f0a3a8", "#c9ced6", "#c9ced6"])
ax1.bar_label(b1, fmt="%.0f", fontsize=8.5, padding=2)
ax1.set_ylim(1900, 2035); ax1.set_ylabel("colonna `run` [us]")
ax1.set_title("La metrica sbagliata: `run` da solo", fontsize=10)
ax1.set_xlabel("trace_level")
ax1.text(.5, .04, "il livello 1 risulta PIU' VELOCE di quello\nsenza tracing: e' l'allineamento del binario,\nnon un guadagno reale",
         transform=ax1.transAxes, ha="center", fontsize=7.8, color=C_SIMPLE, style="italic")

# --- basso destra: la metrica giusta, col segno di FIG 3 -------------------
cost = [rest[t] - rest[0] for t in lv]          # quanto overhead IN PIU' rispetto al livello 0
b2 = ax2.bar([str(t) for t in lv], cost, .55, color=["#c9ced6", "#c9ced6", "#c9ced6", C_BATCH])
ax2.bar_label(b2, fmt="%.0f", fontsize=8.5, padding=2)
ax2.set_ylim(-1.5, 19); ax2.set_ylabel("overhead in piu' vs livello 0 [us]")
ax2.set_title("La metrica giusta: quanto cresce `il resto`", fontsize=10)
ax2.set_xlabel("trace_level")
ax2.text(.5, .70, "= 10000 - (`run` + `slack`)\nrispetto al livello 0.\nPiu' alto = piu' costoso",
         transform=ax2.transAxes, ha="center", fontsize=7.8, color="#57606a", style="italic")
fig.suptitle("7 — Perche' la metrica ovvia porta a una conclusione falsa",
             fontsize=11.5, fontweight="bold", y=1.005)
save(fig, "07_metric_artifact.png")

# ---------------------------------------------------------------- FIG 8
# Il regime anomalo: l'ipotesi "frequenza" falsificata.
# L'asse x copre l'intero intervallo fino a 626 MHz, altrimenti il confronto
# "dove sarebbero i punti se fosse la frequenza" non e' visibile.
fig, ax = plt.subplots(figsize=(7.6, 4.2))
xs = np.array([float(x["aperf_mhz"]) for x in R if x["aperf_mhz"] not in ("", "NA")])
ys = np.array([float(x["med_duration_us"]) for x in R if x["aperf_mhz"] not in ("", "NA")])
norm = ys < 2600
F_NOM, F_PRED = 2296.0, 2296.0/3.29     # 3.29x = il piu' lento dei due anomali
ax.axvspan(F_PRED-45, F_PRED+45, color=C_ACC, alpha=0.12, zorder=0)
ax.scatter(xs[norm], ys[norm], s=18, c="#c9ced6", edgecolors="#8b949e", lw=.4,
           label=f"run normali (n={norm.sum()})", zorder=3)
ax.scatter(xs[~norm], ys[~norm], s=110, marker="D", c=C_SIMPLE, edgecolors="white", lw=1.2,
           zorder=5, label=f"run in regime anomalo (n={(~norm).sum()})")
# dove sarebbero se la causa fosse la frequenza
ax.scatter([F_PRED]*int((~norm).sum()), ys[~norm], s=110, marker="D", facecolors="none",
           edgecolors=C_ACC, lw=1.4, ls="--", zorder=4)
for y in ys[~norm]:
    ax.annotate("", xy=(F_PRED+55, y), xytext=(xs[~norm][0]-45, y),
                arrowprops=dict(arrowstyle="->", color=C_ACC, lw=1.1, ls=":"))
ax.axvline(F_NOM, ls=":", c="#57606a", lw=1.2)
ax.text(F_NOM-55, 2600, "frequenza nominale\n2296 MHz", fontsize=8, color="#57606a", ha="right")
ax.text(F_PRED, 9300, "se la causa fosse la frequenza,\ni due run anomali starebbero QUI\n(2296 / 3.29 = 698 MHz)",
        fontsize=8.5, color=C_ACC, ha="center", va="top", weight="bold")
ax.text(F_NOM-55, 9300, "invece stanno a 2286 MHz,\ncioe' alla frequenza nominale:\nil lavoro per iterazione\ncresce, i MHz no",
        fontsize=8.5, color=C_SIMPLE, ha="right", va="top", weight="bold")
ax.set_xlim(450, 2520); ax.set_ylim(1500, 9600)
ax.set_xlabel("frequenza effettiva misurata durante il run (APERF/MPERF) [MHz]")
ax.set_ylabel("durata mediana dell'iterazione [us]")
ax.set_title("8 — L'ipotesi \"e' un calo di frequenza\" e' falsificata")
ax.legend(fontsize=8, loc="lower left")
save(fig, "08_anomalous_regime.png")

print(f"\n{len(glob.glob(os.path.join(A.out,'*.png')))} figure in {A.out}")
