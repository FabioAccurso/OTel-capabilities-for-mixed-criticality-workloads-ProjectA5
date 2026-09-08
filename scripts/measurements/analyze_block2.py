#!/usr/bin/env python3
"""Analisi del blocco 2 del DoE: il sampler di OTel riesce a proteggere gli span
del task critico quando HI e LO condividono una sola trace?

Due famiglie di variabili di risposta:
  1. ESPORTAZIONE — quanti span escono davvero, separati per criticita'.
     Il conteggio usa le sole righe di intestazione dell'exporter ostream
     (`^  name          : X`), non un grep di sottostringa su tutto il file:
     rt-app scrive il nome del task anche come attributo `config.name`, quindi
     un grep ingenuo conta il doppio (finding del Task 3).
  2. TEMPORIZZAZIONE di HI — variabile `slack`, non `run` ne' `period`:
     il blocco 1 ha mostrato che `run` risente del layout del binario (~30 us,
     piu' del segnale) e che `period` = end-start si accorcia proprio dove
     l'overhead cresce, perche' gli span nascono fuori da quella finestra.
"""
import csv, glob, gzip, math, os, re, sys, statistics as st
from collections import defaultdict

DOE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '2-DoE')
DOE = os.path.normpath(DOE)

# span attesi con n_lo=4 e trace_level=2: main, calibration, 5 thread, 5
# thread_loop[0], 5 phase[0]  (misurato allo smoke test)
SPANS_ATTESI = 17

def pct(v, p):
    v = sorted(v); k = (len(v) - 1) * p / 100; f = int(k)
    return v[f] if f + 1 >= len(v) else v[f] + (v[f + 1] - v[f]) * (k - f)

def opener(path):
    return gzip.open(path, 'rt', errors='replace') if path.endswith('.gz') else open(path, errors='replace')

def find(run_dir, *pats):
    for p in pats:
        m = glob.glob(os.path.join(run_dir, p))
        if m: return sorted(m)
    return []

# --- 1. esportazione ---------------------------------------------------------
NAME_RE = re.compile(r'^  name +: (.+?)\s*$')
TRACE_RE = re.compile(r'^  trace_id +: ([0-9a-f]+)')

def count_spans(run_dir):
    f = find(run_dir, 'stdout.log.gz', 'stdout.log')
    if not f: return None
    names, traces = defaultdict(int), set()
    for line in opener(f[0]):
        m = NAME_RE.match(line)
        if m: names[m.group(1)] += 1; continue
        m = TRACE_RE.match(line)
        if m: traces.add(m.group(1))
    hi = sum(c for n, c in names.items() if n.startswith('HI_task'))
    lo = sum(c for n, c in names.items() if n.startswith('LO_noise'))
    return dict(tot=sum(names.values()), hi=hi, lo=lo, traces=len(traces))

# --- 2. temporizzazione di HI ------------------------------------------------
def timing(run_dir):
    f = find(run_dir, '*HI_task*.log.gz', '*HI_task*.log')
    if not f: raise FileNotFoundError(run_dir)
    run, per, slack = [], [], []
    for l in opener(f[0]):
        if l.startswith('#'): continue
        c = l.split()
        if len(c) < 11: continue
        run.append(int(c[2])); per.append(int(c[3])); slack.append(int(c[7]))
    # Scarta TUTTO il transitorio di avvio, non solo la prima riga: HI (ind==0)
    # fissa t_zero e poi si blocca sulla barriera finche' i LO non sono pronti,
    # quindi le prime iterazioni hanno slack<0 per costruzione e il loro numero
    # scala col numero di thread (task 0.5).
    k = 0
    while k < len(slack) and slack[k] < 0: k += 1
    return dict(n=len(run), warmup=k,
                run_med=st.median(run[k:]),
                budget=st.median([r + s for r, s in zip(run[k:], slack[k:])]),
                slack_med=st.median(slack[k:]), slack_min=min(slack[k:]),
                p50=pct(per[k+1:], 50), pmax=max(per[k+1:]),
                miss=sum(1 for s in slack[k:] if s < 0))

def wilson(k, n, z=1.96):
    if n == 0: return (0.0, 0.0)
    p = k / n; d = 1 + z*z/n
    c = (p + z*z/(2*n)) / d
    h = z*math.sqrt(p*(1-p)/n + z*z/(4*n*n)) / d
    return (max(0.0, c-h), min(1.0, c+h))

# --- raccolta ----------------------------------------------------------------
cells = defaultdict(list)
for r in csv.DictReader(open(os.path.join(DOE, 'data_table.csv'))):
    if r['block'] != 'block2': continue
    d = r['run_dir']
    if not os.path.isdir(d): continue
    key = (int(r['sampler_type']), float(r['sampler_ratio']))
    cells[key].append((count_spans(d), timing(d)))

if not cells:
    sys.exit("nessun run di block2 in data_table.csv")

LAB = {0: 'AlwaysOn', 1: 'Ratio', 2: 'AlwaysOff'}
def label(k):
    s, r = k
    return f"{LAB[s]}" + (f" {r:g}" if s == 1 else "")
order = sorted(cells, key=lambda k: (k[0], k[1]))

print("BLOCCO 2 — il sampler sa proteggere il task critico?")
print(f"trace_level=2, processor=Batch, exporter=ostream, 1 HI + 4 LO, 20 s, "
      f"{len(cells[order[0]])} rip./cella\n")

# ---- A. esportazione --------------------------------------------------------
print("A. SPAN ESPORTATI (media per run, e quanti run hanno esportato qualcosa)")
hdr = (f"{'sampler':<14}{'run':>5}{'span_tot':>10}{'span_HI':>9}{'span_LO':>9}"
       f"{'trace_id':>10}{'run campionati':>16}{'frazione [IC95%]':>26}")
print(hdr); print("-" * len(hdr))
for k in order:
    S = [c for c, _ in cells[k]]
    n = len(S)
    sampled = sum(1 for x in S if x['tot'] > 0)
    lo_, hi_ = wilson(sampled, n)
    m = lambda f: st.mean(x[f] for x in S)
    print(f"{label(k):<14}{n:>5}{m('tot'):>10.2f}{m('hi'):>9.2f}{m('lo'):>9.2f}"
          f"{m('traces'):>10.2f}{f'{sampled}/{n}':>16}"
          f"{f'{sampled/n:.2f}  [{lo_:.2f}, {hi_:.2f}]':>26}")

# ---- B. tutto-o-niente ------------------------------------------------------
print("\nB. LA DECISIONE E' PER-TRACE, NON PER-TASK")
print(f"   Un run 'parziale' e' un run con 0 < span < {SPANS_ATTESI}: sarebbe la prova")
print("   che il sampler distingue fra span (e quindi fra criticita').")
print(f"\n{'sampler':<14}{'run a 0 span':>14}{'run completi':>14}{'run parziali':>14}"
      f"{'span HI se campionato':>24}{'span LO se campionato':>24}")
print("-" * 104)
tot_parz = 0
for k in order:
    S = [c for c, _ in cells[k]]
    zero = sum(1 for x in S if x['tot'] == 0)
    full = sum(1 for x in S if x['tot'] >= SPANS_ATTESI)
    parz = len(S) - zero - full
    tot_parz += parz
    on = [x for x in S if x['tot'] > 0]
    hi_s = f"{st.mean(x['hi'] for x in on):.2f}" if on else "-"
    lo_s = f"{st.mean(x['lo'] for x in on):.2f}" if on else "-"
    print(f"{label(k):<14}{zero:>14}{full:>14}{parz:>14}{hi_s:>24}{lo_s:>24}")
print(f"\n   run parziali su tutto il blocco: {tot_parz}")

# ---- C. temporizzazione di HI ----------------------------------------------
print("\nC. TEMPORIZZAZIONE DI HI (variabile di risposta: slack; budget = run+slack)")
print("   Aggregazione fra ripetizioni con la MEDIANA, non la media: esistono run")
print("   interi in un regime a ~3.7x (vedi tabella E) che rendono la media inservibile.")
hdr = (f"{'sampler':<14}{'iter':>6}{'warmup':>8}{'budget':>10}{'run_med':>10}"
       f"{'slack_med':>11}{'slack_min':>11}{'p50':>9}{'per_max':>9}{'miss':>6}")
print(hdr); print("-" * len(hdr))
for k in order:
    T = [t for _, t in cells[k]]
    m = lambda f: st.median([x[f] for x in T])
    print(f"{label(k):<14}{m('n'):>6.0f}{m('warmup'):>8.2f}{m('budget'):>10.1f}"
          f"{m('run_med'):>10.1f}{m('slack_med'):>11.1f}{min(x['slack_min'] for x in T):>11}"
          f"{m('p50'):>9.1f}{m('pmax'):>9.1f}{sum(x['miss'] for x in T):>6}")

# ---- D. costo del campionare vs non campionare ------------------------------
print("\nD. COSTO DI UN RUN CAMPIONATO (solo celle Ratio, confronto interno)")
print("   Stessa cella, stesso binario: gli unici due gruppi differiscono solo")
print("   per l'esito del sorteggio del sampler. Isola il costo dell'export.")
on_b, off_b = [], []
for k in order:
    if k[0] != 1: continue
    for c, t in cells[k]:
        (on_b if c['tot'] > 0 else off_b).append(t['budget'])
if on_b and off_b:
    print(f"\n   campionati    n={len(on_b):<4} budget mediano {st.median(on_b):8.1f} us")
    print(f"   scartati      n={len(off_b):<4} budget mediano {st.median(off_b):8.1f} us")
    print(f"   delta                          {st.median(on_b)-st.median(off_b):+8.1f} us per iterazione")
else:
    print("   (un solo gruppo presente, confronto non possibile)")

# ---- E. run anomali ---------------------------------------------------------
# Il blocco 1 aveva gia' incontrato un run in un regime ~3.7x piu' lento
# (trace=1 rep 2, prime 840 iterazioni a ~7270 us). Non e' rumore da mediare
# via: e' un secondo regime, e va contato ed esposto, non nascosto in una media.
print("\nE. RUN IN REGIME ANOMALO (run_med > 1.5x la mediana della propria cella)")
tot_out = 0
righe = []
for k in order:
    T = [t for _, t in cells[k]]
    base = st.median([x['run_med'] for x in T])
    for i, x in enumerate(T, 1):
        if x['run_med'] > 1.5 * base:
            tot_out += 1
            righe.append(f"   {label(k):<14} rip.{i:<3} run_med={x['run_med']:>8.0f} "
                         f"(cella={base:.0f}, {x['run_med']/base:.2f}x)  miss={x['miss']}")
print("\n".join(righe) if righe else "   nessuno")
n_tot = sum(len(v) for v in cells.values())
print(f"\n   {tot_out} run anomali su {n_tot} ({tot_out/n_tot*100:.1f}%)")
