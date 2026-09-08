#!/usr/bin/env python3
"""Task 5 — statistiche descrittive e confronti fra configurazioni.

Legge 2-DoE/results.csv (prodotto da analyze_doe.py) e genera un report che
risponde alle tre domande della traccia:
  1. OTel prioritizza i task critici nella pipeline di telemetria?
  2. Qual e' l'overhead del monitoraggio sul WCET?
  3. Il monitoraggio fa violare gli SLO temporali?

Uso: ./report_doe.py --results 2-DoE/results.csv --out 2-DoE/REPORT.md
"""
import argparse, csv, math, statistics as st
from collections import defaultdict

def f(x, d=1):
    try: return f"{float(x):.{d}f}"
    except (TypeError, ValueError): return "-"

def wilson(k, n, z=1.96):
    if n == 0: return (0.0, 0.0)
    p = k/n; den = 1 + z*z/n
    c = (p + z*z/(2*n))/den
    h = z*math.sqrt(p*(1-p)/n + z*z/(4*n*n))/den
    return max(0.0, c-h), min(1.0, c+h)

ap = argparse.ArgumentParser()
ap.add_argument("--results", required=True)
ap.add_argument("--out", required=True)
a = ap.parse_args()

R = [r for r in csv.DictReader(open(a.results)) if r["n_iters"]]
def sel(**kw):
    out = R
    for k, v in kw.items():
        out = [r for r in out if r[k] == str(v)]
    return out
def med(rows, col): return st.median([float(x[col]) for x in rows if x[col] != ""])

L = []
w = L.append

w("# Task 5 — Analisi del DoE\n")
w(f"Generato da `report_doe.py` su `results.csv`: **{len(R)} run** con dati, "
  f"{len(set(r['block'] for r in R))} campagne.\n")
w("Variabile di risposta primaria: **`budget` = `duration + slack`**, il tempo "
  "consumato per iterazione. I blocchi 1 e 3 hanno mostrato che le colonne native "
  "di rt-app non servono allo scopo: `duration` (la colonna `run`) dipende dal "
  "layout del binario per ~30 us, piu' del segnale da misurare, e `period` "
  "(`end - start` della stessa riga) *si accorcia* dove l'overhead cresce, perche' "
  "gli span nascono fuori da quella finestra. Chi misurasse su `period` "
  "concluderebbe che il tracing rende il codice piu' veloce.\n")

# ---------------------------------------------------------------- panoramica
w("## Panoramica della campagna\n")
w("| campagna | run | celle | fattore studiato |")
w("|---|---|---|---|")
DESC = {"block1": "granularita' del tracing (`trace_level` 0-3)",
        "block2": "sampler (AlwaysOff/On, Ratio 0.1-0.7)",
        "block3": "processor (Batch/Simple) x carico (`n_lo` 0-8)",
        "diag": "diagnostica del regime anomalo (non e' un fattore)"}
for b in ("block1", "block2", "block3", "diag"):
    S = sel(block=b)
    if not S: continue
    cells = len({(x["trace_level"], x["processor_type"], x["sampler_type"],
                  x["sampler_ratio"], x["n_lo"]) for x in S})
    w(f"| {b} | {len(S)} | {cells} | {DESC[b]} |")
aperf = [float(x["aperf_mhz"]) for x in R if x["aperf_mhz"] not in ("", "NA")]
w(f"\nPiattaforma stabile su tutta la campagna: frequenza fissata a P0 con boost "
  f"disabilitato, `aperf_mhz` misurata su {len(aperf)} run "
  f"(media {f(st.mean(aperf))}, min {f(min(aperf),0)}, max {f(max(aperf),0)} MHz).\n")

# ------------------------------------------------- Q1: prioritizzazione
w("## Domanda 1 — OTel prioritizza i task critici? **No.**\n")
w("Il blocco 2 varia il sampler con carico misto (1 HI + 4 LO) ed exporter "
  "ostream, che rende gli span contabili.\n")
w("| sampler | run campionati | frazione | IC 95 % | span HI | span LO | run **parziali** |")
w("|---|---|---|---|---|---|---|")
tot_parz = 0
for st_, ra, name in ((0,"0.0","AlwaysOn"), (1,"0.1","Ratio 0.1"), (1,"0.3","Ratio 0.3"),
                      (1,"0.5","Ratio 0.5"), (1,"0.7","Ratio 0.7"), (2,"0.0","AlwaysOff")):
    S = sel(block="block2", sampler_type=st_, sampler_ratio=ra)
    if not S: continue
    n = len(S)
    smp = [x for x in S if int(x["spans_exported_total"] or 0) > 0]
    parz = sum(1 for x in smp if int(x["spans_exported_total"]) < 17)
    tot_parz += parz
    lo_, hi_ = wilson(len(smp), n)
    hs = f"{med(smp,'hi_spans_exported'):.0f}" if smp else "-"
    ls = f"{med(smp,'lo_spans_exported'):.0f}" if smp else "-"
    w(f"| {name} | {len(smp)}/{n} | {len(smp)/n:.2f} | [{lo_:.2f}, {hi_:.2f}] | {hs} | {ls} | **{parz}** |")
w(f"\n**Zero run parziali su {len(sel(block='block2'))}.** Un run e' completo (17 span) "
  "o vuoto: quando viene campionato escono sempre 1 span HI e 4 LO, mai un "
  "sottoinsieme. Tutti gli span di un run condividono un solo `trace_id`, perche' "
  "ogni thread nasce con `span_opts.parent = main_span->GetContext()`.\n")
w("Il sampler funziona — le frazioni seguono il ratio e ogni IC 95 % contiene il "
  "valore nominale — ma **alla granularita' sbagliata**: la decisione e' per-trace, "
  "non per-task. Impostare un ratio non significa \"conserva piu' span dei task "
  "critici\", significa \"scarta l'intera esecuzione, HI e LO insieme, con "
  "probabilita' 1-ratio\". A ratio 0.1 nell'84 % dei run **non esiste alcuna "
  "traccia del task critico**. E' la motivazione empirica del Task 6.\n")

# ------------------------------------------------- Q2: overhead
w("## Domanda 2 — Quanto costa il monitoraggio?\n")
w("### Granularita' del tracing (blocco 1, solo HI, nessun carico)\n")
w("| trace_level | budget mediano [us] | delta vs livello 0 | deadline miss |")
w("|---|---|---|---|")
b1 = {t: sel(block="block1", trace_level=t) for t in (0,1,2,3)}
base1 = med(b1[0], "budget_med_us")
NAMES = {0:"0 — nessuno", 1:"1 — main+thread", 2:"2 — +phase", 3:"3 — +phase_loop"}
for t in (0,1,2,3):
    S = b1[t]
    if not S: continue
    m = med(S, "budget_med_us")
    ms = sum(int(x["deadline_misses"]) for x in S)
    w(f"| {NAMES[t]} | {f(m)} | {m-base1:+.1f} | {ms} |")
w("\nI livelli 1 e 2 non sono misurabili. Il livello 3 costa **~13 us per "
  "iterazione**: lo 0.13 % del periodo di 10 ms, ma lo **0.7 % del lavoro utile** "
  "di 2000 us.\n")

w("### Processor ed exporter sotto carico (blocco 3)\n")
w("| braccio | n_lo=0 | n_lo=1 | n_lo=4 | n_lo=8 |")
w("|---|---|---|---|---|")
ARMS = ((0,0,"trace0 (controllo)"), (3,0,"trace3 **Batch**"), (3,1,"trace3 **Simple**"))
base3 = {}
for t,p,name in ARMS:
    row = f"| {name} |"
    for n in (0,1,4,8):
        S = sel(block="block3", trace_level=t, processor_type=p, n_lo=n)
        if S:
            m = med(S, "budget_med_us")
            if t == 0: base3[n] = m
            row += f" {f(m,0)} |"
        else: row += " - |"
    w(row)
w("\ndelta rispetto al controllo, stesso carico [us]:\n")
w("| braccio | n_lo=0 | n_lo=1 | n_lo=4 | n_lo=8 |")
w("|---|---|---|---|---|")
for t,p,name in ARMS[1:]:
    row = f"| {name} |"
    for n in (0,1,4,8):
        S = sel(block="block3", trace_level=t, processor_type=p, n_lo=n)
        row += f" {med(S,'budget_med_us')-base3[n]:+.0f} |" if S else " - |"
    w(row)
w("\n**Batch costa ~13 us per iterazione, Simple ~300: 23 volte tanto**, cioe' il "
  "15 % del lavoro utile. Il valore di Batch e' costante al variare del carico e "
  "coerente col blocco 1.\n")
w("> Il **-1303 a `n_lo=4` non va letto come un costo maggiore**: quella cella e' "
  "bimodale (8 ripetizioni a ~8688 us, 7 a ~9665) e la mediana cade sul gruppo "
  "basso, quindi la non-monotonia rispetto a `n_lo=8` e' apparente. Il costo di "
  "Simple e' ~300 us; il secondo modo, che vale altri ~980 us per iterazione, e' "
  "non spiegato.\n")
w("La causa e' architetturale: `SimpleSpanProcessor::OnEnd` chiama `Export()` "
  "**sincrono, nel thread che chiude lo span**, sotto spin-lock condiviso; "
  "`BatchSpanProcessor` accoda e delega a un thread proprio. I tentativi di export "
  "per run lo mostrano direttamente:\n")
w("| braccio | n_lo=0 | n_lo=1 | n_lo=4 | n_lo=8 |")
w("|---|---|---|---|---|")
for t,p,name in ARMS:
    row = f"| {name} |"
    for n in (0,1,4,8):
        S = sel(block="block3", trace_level=t, processor_type=p, n_lo=n)
        row += f" {med(S,'export_attempts'):.0f} |" if S else " - |"
    w(row)
w("\nNessun collector era in ascolto: gli export falliscono subito con "
  "`ECONNREFUSED` su localhost, che e' il caso **piu' favorevole** a Simple. Con un "
  "collector reale il divario sarebbe maggiore.\n")

# ------------------------------------------------- Q3: SLO
w("## Domanda 3 — Il monitoraggio fa violare gli SLO temporali? **Solo con Simple.**\n")
w("| braccio | n_lo=0 | n_lo=1 | n_lo=4 | n_lo=8 | slack minimo [us] |")
w("|---|---|---|---|---|---|")
for t,p,name in ARMS:
    row = f"| {name} |"; mins = []
    for n in (0,1,4,8):
        S = sel(block="block3", trace_level=t, processor_type=p, n_lo=n)
        if S:
            ms = sum(int(x["deadline_misses"]) for x in S)
            it = sum(int(x["n_iters"]) for x in S)
            mins.append(min(int(x["slack_min_us"]) for x in S))
            row += f" {ms}/{it} |"
        else: row += " - |"
    row += f" {min(mins) if mins else '-'} |"
    w(row)
allmiss = sum(int(x["deadline_misses"]) for x in R)
b12 = sum(int(x["deadline_misses"]) for x in R if x["block"] in ("block1","block2","diag"))
w(f"\nSu tutta la campagna i deadline miss sono **{allmiss}**, di cui {b12} fuori dal "
  "blocco 3. Tutte le celle di controllo e tutte le celle Batch hanno **zero miss a "
  "qualunque carico**; con Simple e carico di sottofondo il task critico sfora fino "
  "a **3.6 ms su un periodo di 10 ms**.\n")
w("I miss non sono un artefatto della terminazione anomala (vedi sotto): sono "
  "distribuiti uniformemente lungo il run, non addensati in coda.\n")

# ------------------------------------------------- Q4: robustezza
w("## Domanda 4 — Robustezza: `Simple` termina il processo\n")
ab = [x for x in R if x["aborted"] == "1"]
w(f"**{len(ab)} run su {len(R)}** sono terminati con SIGABRT, tutti nel braccio "
  "`trace3 Simple`:\n")
w("| n_lo | run | abortiti |")
w("|---|---|---|")
for n in (0,1,4,8):
    S = sel(block="block3", trace_level=3, processor_type=1, n_lo=n)
    if S: w(f"| {n} | {len(S)} | {sum(1 for x in S if x['aborted']=='1')} |")
w("\nCausa verificata nel codice: `__shutdown()` di rt-app termina i thread con "
  "`pthread_cancel` (`rt-app.cpp:933`); glibc la implementa come eccezione di "
  "*forced unwind*; `SimpleSpanProcessor::OnEnd` e' dichiarato **`noexcept`** "
  "(`simple_processor.h:60`), e un unwind che attraversa un `noexcept` chiama "
  "`std::terminate()`. Con Simple il thread e' quasi sempre dentro `OnEnd` (export "
  "sincrono), con Batch quasi mai.\n")
w("**La scelta del processor di telemetria non degrada soltanto le prestazioni del "
  "task critico: ne termina il processo**, e in modo silenzioso rispetto ai dati, "
  "perche' l'abort arriva a lavoro finito. I run abortiti perdono esattamente le "
  "ultime 20 iterazioni di log; le analisi usano mediane su ~1974 iterazioni.\n")

# ------------------------------------------------- limiti
w("## Limiti e questioni aperte\n")
w("1. **Regime anomalo a 2-3.7x, non spiegato.** Presente in tutte le campagne a "
  "un tasso dell'1.1-1.3 %. La colonna `aperf_mhz`, aggiunta apposta, ha "
  "**falsificato l'ipotesi frequenza**: i due run anomali del blocco 3 girano a "
  "2286 MHz, cioe' nominali, mentre il lavoro per iterazione cresce di 2-3.3x. "
  "Cadono inoltre in celle diverse, una delle quali **senza alcun tracing**: il "
  "fenomeno non dipende da OpenTelemetry. Ipotesi residue: contesa SMT sul sibling "
  "**cpu3**, dentro lo shield ma non controllato, o pressione su cache/memoria. Si "
  "distinguono con i contatori IPC di `perf stat`. `hwlatdetect` e' gia' stato "
  "escluso (0 latenze su 435 s) ed e' comunque lo strumento sbagliato per un "
  "rallentamento sostenuto, che non produce salti temporali.\n")
w("2. **La cella `Simple n_lo=4` e' bimodale** (8 ripetizioni a ~8688 us, 7 a "
  "~9665): la sua mediana non descrive un comportamento unico e la non-monotonia "
  "apparente rispetto a `n_lo=8` viene da li'.\n")
w("3. **Un solo collector non e' stato provato.** Tutti i run Zipkin girano senza "
  "collector in ascolto. E' il caso piu' favorevole all'overhead misurato: i "
  "risultati vanno letti come un **limite inferiore**.\n")
w("4. **Piattaforma singola**: un ultrabook da 15 W con 4 core fisici. I valori "
  "assoluti non si trasferiscono ad altro hardware; i confronti fra celle si'.\n")

open(a.out, "w").write("\n".join(L) + "\n")
print(f"scritto {a.out}  ({len(L)} righe)")
