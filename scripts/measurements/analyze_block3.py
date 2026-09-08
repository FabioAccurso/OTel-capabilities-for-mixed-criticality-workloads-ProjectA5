#!/usr/bin/env python3
"""Analisi del blocco 3 del DoE: contesa processor/exporter sotto carico crescente.

Fattori:  trace_level in {0 (controllo), 3 (volume massimo di span)}
          processor  in {Batch, Simple}   (solo a trace_level=3)
          n_lo       in {0, 1, 4, 8}      (carico di sottofondo su cpu6)
Exporter Zipkin (e0) senza collector in ascolto: gli export falliscono, ma ogni
tentativo lascia una riga su stderr, che diventa un contatore dei tentativi.

La domanda del blocco: il `SimpleSpanProcessor` esporta in modo SINCRONO nel
thread che chiude lo span, quindi a trace_level=3 il task critico paga
direttamente 2000 export in 20 s. Il `BatchSpanProcessor` li sposta su un thread
proprio (SCHED_OTHER). Quanto costa la differenza a HI, e come scala col carico?

Variabile di risposta: `slack`, e il budget `run + slack` (blocco 1: `run` risente
del layout del binario, `period` si accorcia proprio dove l'overhead cresce).
"""
import csv, glob, gzip, os, re, sys, statistics as st
from collections import defaultdict

DOE = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '2-DoE'))

def pct(v, p):
    v = sorted(v); k = (len(v)-1)*p/100; f = int(k)
    return v[f] if f+1 >= len(v) else v[f] + (v[f+1]-v[f])*(k-f)

def opener(path):
    return gzip.open(path, 'rt', errors='replace') if path.endswith('.gz') else open(path, errors='replace')

def find(run_dir, *pats):
    for p in pats:
        m = glob.glob(os.path.join(run_dir, p))
        if m: return sorted(m)
    return []

def timing(run_dir):
    f = find(run_dir, '*HI_task*.log.gz', '*HI_task*.log')
    if not f: raise FileNotFoundError(run_dir)
    run, per, slack = [], [], []
    for l in opener(f[0]):
        if l.startswith('#'): continue
        c = l.split()
        if len(c) < 11: continue
        run.append(int(c[2])); per.append(int(c[3])); slack.append(int(c[7]))
    # Scarta TUTTO il transitorio: HI (ind==0) fissa t_zero e si blocca sulla
    # barriera finche' i LO non sono pronti. Il numero di righe fasulle SCALA con
    # n_lo (1, 2, 5, 10 per n_lo 0, 1, 4, 8) e n_lo e' un fattore di questo
    # blocco: uno scarto fisso creerebbe un bias correlato col fattore in studio.
    k = 0
    while k < len(slack) and slack[k] < 0: k += 1
    if k >= len(slack): return None
    return dict(n=len(run), warmup=k,
                run_med=st.median(run[k:]),
                budget=st.median([r+s for r, s in zip(run[k:], slack[k:])]),
                slack_med=st.median(slack[k:]), slack_min=min(slack[k:]),
                p50=pct(per[k+1:], 50) if len(per) > k+1 else 0,
                pmax=max(per[k+1:]) if len(per) > k+1 else 0,
                miss=sum(1 for s in slack[k:] if s < 0),
                miss_idx=[i for i in range(k, len(slack)) if slack[i] < 0],
                iters=len(slack)-k)

ZIP_RE = re.compile(r'ZIPKIN EXPORTER')
def export_attempts(run_dir):
    f = find(run_dir, 'stderr.log.gz', 'stderr.log')
    if not f: return 0
    return sum(1 for l in opener(f[0]) if ZIP_RE.search(l))

cells = defaultdict(list)
aperf = defaultdict(list)
for r in csv.DictReader(open(os.path.join(DOE, 'data_table.csv'))):
    if r['block'] != 'block3': continue
    d = r['run_dir']
    if not os.path.isdir(d): continue
    key = (int(r['trace_level']), int(r['processor_type']), int(r['n_lo']))
    t = timing(d)
    if t is None: continue
    t['zipkin'] = export_attempts(d)
    cells[key].append(t)
    try: aperf[key].append(int(r['aperf_mhz']))
    except (ValueError, TypeError): pass

if not cells: sys.exit("nessun run di block3 in data_table.csv")

def lab(k):
    t, p = k[0], k[1]      # accetta sia (t,p) sia (t,p,n_lo)
    return "trace0 (controllo)" if t == 0 else f"trace3 {'Batch' if p==0 else 'Simple'}"

arms = sorted({(t, p) for t, p, _ in cells}, key=lambda x: (x[0], x[1]))
nlos = sorted({n for _, _, n in cells})
reps = len(next(iter(cells.values())))

print("BLOCCO 3 — contesa processor/exporter sotto carico di sottofondo crescente")
print(f"exporter Zipkin senza collector, {reps} rip./cella, 20 s\n")

# ---- A. budget ------------------------------------------------------------
print("A. BUDGET PER ITERAZIONE (run + slack), mediana fra ripetizioni [us]")
print("   Il budget e' la metrica corretta: costante se non c'e' overhead.\n")
hdr = f"{'braccio':<20}" + "".join(f"{'n_lo='+str(n):>20}" for n in nlos)
print(hdr); print("-"*len(hdr))
base = {}
for a in arms:
    row = f"{lab(a):<20}"
    for n in nlos:
        S = cells.get((a[0], a[1], n))
        if S:
            v = [x['budget'] for x in S]
            row += f"{st.median(v):>9.0f} [{min(v):.0f}-{max(v):.0f}]".rjust(20)
            if a[0] == 0: base[n] = st.median(v)
        else: row += f"{'-':>20}"
    print(row)
print("\n   mediana [min-max fra le ripetizioni]. Un intervallo largo segnala che la")
print("   cella non e' omogenea: trace3 Simple n_lo=4 e' BIMODALE (~8688 e ~9665,")
print("   8 e 7 ripetizioni), quindi la sua mediana non descrive un comportamento unico.")

print("\n   delta rispetto al controllo trace0, stesso n_lo [us]:")
hdr2 = f"{'braccio':<20}" + "".join(f"{'n_lo='+str(n):>12}" for n in nlos)
print(hdr2)
for a in arms:
    if a[0] == 0: continue
    row = f"{lab(a):<20}"
    for n in nlos:
        S = cells.get((a[0], a[1], n))
        row += f"{st.median([x['budget'] for x in S])-base[n]:>+12.1f}" if S and n in base else f"{'-':>12}"
    print(row)

# ---- B. deadline miss -----------------------------------------------------
print("\nB. DEADLINE MISS (dopo lo scarto del transitorio di avvio)")
hdr = f"{'braccio':<20}" + "".join(f"{'n_lo='+str(n):>12}" for n in nlos)
print(hdr); print("-"*len(hdr))
for a in arms:
    row = f"{lab(a):<20}"
    for n in nlos:
        S = cells.get((a[0], a[1], n))
        if S:
            m = sum(x['miss'] for x in S); it = sum(x['iters'] for x in S)
            row += f"{f'{m}/{it}':>12}"
        else: row += f"{'-':>12}"
    print(row)
print("\n   righe scartate come transitorio (media), attese 1/2/5/10 per n_lo 0/1/4/8:")
for a in arms:
    row = f"{lab(a):<20}"
    for n in nlos:
        S = cells.get((a[0], a[1], n))
        row += f"{st.mean([x['warmup'] for x in S]):>12.1f}" if S else f"{'-':>12}"
    print(row)

# ---- C. tentativi di export ----------------------------------------------
print("\nC. TENTATIVI DI EXPORT (righe 'ZIPKIN EXPORTER' su stderr, media per run)")
print("   Batch accumula e spara a tick da 5 s; Simple esporta a ogni span chiuso.")
hdr = f"{'braccio':<20}" + "".join(f"{'n_lo='+str(n):>12}" for n in nlos)
print(hdr); print("-"*len(hdr))
for a in arms:
    row = f"{lab(a):<20}"
    for n in nlos:
        S = cells.get((a[0], a[1], n))
        row += f"{st.mean([x['zipkin'] for x in S]):>12.1f}" if S else f"{'-':>12}"
    print(row)

# ---- D. jitter ------------------------------------------------------------
print("\nD. TEMPORIZZAZIONE DI HI: slack minimo [us] (margine peggiore osservato)")
hdr = f"{'braccio':<20}" + "".join(f"{'n_lo='+str(n):>12}" for n in nlos)
print(hdr); print("-"*len(hdr))
for a in arms:
    row = f"{lab(a):<20}"
    for n in nlos:
        S = cells.get((a[0], a[1], n))
        row += f"{min(x['slack_min'] for x in S):>12}" if S else f"{'-':>12}"
    print(row)

# ---- E. run anomali + aperf ----------------------------------------------
print("\nE. RUN IN REGIME ANOMALO (run_med > 1.5x la mediana della propria cella)")
print("   Colonna aperf_mhz ora attiva: se un anomalo compare, dice subito se la")
print("   CPU era davvero a ~626 MHz (ipotesi frequenza) o a ~2296 (falsificata).")
out = []
for k in sorted(cells):
    S = cells[k]
    b = st.median([x['run_med'] for x in S])
    for i, x in enumerate(S):
        if x['run_med'] > 1.5*b:
            af = aperf[k][i] if i < len(aperf.get(k, [])) else 'NA'
            out.append(f"   {lab(k):<20} n_lo={k[2]:<3} rip.{i+1:<3} "
                       f"run_med={x['run_med']:>8.0f} ({x['run_med']/b:.2f}x)  aperf_mhz={af}")
print("\n".join(out) if out else "   nessuno")
tot = sum(len(v) for v in cells.values())
print(f"\n   {len(out)} run anomali su {tot} ({len(out)/tot*100:.1f}%)")
if aperf:
    allv = [v for l in aperf.values() for v in l]
    print(f"   aperf_mhz su tutto il blocco: n={len(allv)} media={st.mean(allv):.1f} "
          f"min={min(allv)} max={max(allv)}")

# ---- F. posizione dei deadline miss ---------------------------------------
# I run del braccio Simple abortiscono allo shutdown (SIGABRT) e perdono le
# ultime ~20 iterazioni di log. Se i miss fossero concentrati in coda sarebbero
# un artefatto della terminazione, non un effetto del processor: vanno quindi
# localizzati, non solo contati.
print("\nF. POSIZIONE DEI DEADLINE MISS DENTRO IL RUN")
print("   Se fossero un artefatto dello shutdown si addenserebbero a fine run.")
pos_all = []
for k in sorted(cells):
    for x in cells[k]:
        pos_all += [(i, k) for i in x.get('miss_idx', [])]
if pos_all:
    fr = [i/2000 for i, _ in pos_all]
    q = [0, 0, 0, 0]
    for f in fr: q[min(3, int(f*4))] += 1
    print(f"   {len(pos_all)} miss totali, per quarto del run:")
    for i, c in enumerate(q):
        print(f"     {i*25:>3}-{(i+1)*25:>3} %  {'#'*c} {c}")
    print(f"   primo miss a idx {min(i for i,_ in pos_all)}, ultimo a idx {max(i for i,_ in pos_all)}")
else:
    print("   nessun deadline miss nel blocco")
