#!/usr/bin/env python3
"""Analisi del blocco 4 del DoE: backend REALE, rete contro file.

Fino al blocco 3 ogni misura e' stata presa contro un endpoint irraggiungibile
(ECONNREFUSED immediato), cioe' il caso piu' favorevole possibile: il costo
misurato era quello di *tentare* un export, non di consegnarlo. Qui il collector
Zipkin risponde davvero, e il braccio "file" scrive gli span su un file locale.

Fattori:  processor in {Batch (0), Simple (1)}
          exporter  in {file (2), rete Zipkin (0)}
Fissi:    trace_level=3, sampler AlwaysOn, 1 HI su cpu2 + 4 LO su cpu6, 20 s.

Due domande, non una:
  1. quanto costa al task critico  -> budget = run + slack (mai `run` da sola,
     mai `period`: blocco 1, finding (b) e (d));
  2. quanta telemetria arriva davvero -> span prodotti contro span consegnati.
     La seconda non era osservabile senza backend: la coda del BatchSpanProcessor
     (max_queue_size = 2048) scarta in SILENZIO, e senza qualcuno che contasse
     dall'altra parte la perdita era invisibile.
"""
import csv, glob, gzip, os, statistics as st
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
    """Statistiche del task critico. Scarta TUTTO il transitorio di avvio: HI
    fissa t_zero e attende i LO sulla barriera, quindi le prime righe hanno slack
    negativo per costruzione e il loro numero scala col numero di thread."""
    f = find(run_dir, '*HI_task*.log.gz', '*HI_task*.log')
    if not f: return None
    run, per, slack = [], [], []
    for l in opener(f[0]):
        if l.startswith('#'): continue
        c = l.split()
        if len(c) < 11: continue
        run.append(int(c[2])); per.append(int(c[3])); slack.append(int(c[7]))
    k = 0
    while k < len(slack) and slack[k] < 0: k += 1
    if k >= len(slack): return None
    return dict(warmup=k, iters=len(slack) - k, rows=len(slack),
                run_med=st.median(run[k:]),
                budget=st.median([r + s for r, s in zip(run[k:], slack[k:])]),
                slack_min=min(slack[k:]),
                jit=pct(per[k+1:], 99) - pct(per[k+1:], 50) if len(per) > k + 2 else 0,
                miss=sum(1 for s in slack[k:] if s < 0))

def spans_produced(run_dir):
    """Span prodotti dall'applicazione, ricavati dai log e non stimati.
    A trace_level=3 ogni thread produce: 1 span di thread + 1 thread_loop +
    1 phase + 1 phase_loop per iterazione, PIU' un phase_loop finale (misurato:
    201 span per 200 iterazioni). Piu' `main` e `calibration`, una volta sole.
       prodotti = 2 + 4*n_thread + iterazioni_totali
    Verificato esatto sul pilota in tutti e quattro i bracci."""
    logs = find(run_dir, 'rtapp-*.log.gz', 'rtapp-*.log')
    if not logs: return None
    tot = 0
    for f in logs:
        tot += sum(1 for l in opener(f) if not l.startswith('#') and l.strip())
    return 2 + 4 * len(logs) + tot, len(logs)

def lo_iters(run_dir):
    logs = find(run_dir, 'rtapp-LO_noise-*.log.gz', 'rtapp-LO_noise-*.log')
    return sum(sum(1 for l in opener(f) if not l.startswith('#') and l.strip()) for f in logs)

PROC = {'0': 'Batch', '1': 'Simple'}
EXP  = {'0': 'rete',  '2': 'file'}

def lab(p, e): return f"{PROC[p]}+{EXP[e]}"

def main():
    rows = [r for r in csv.DictReader(open(os.path.join(DOE, 'data_table.csv')))
            if r['block'] == 'block4']
    if not rows:
        print("nessun run di block4 in data_table.csv"); return

    cells = defaultdict(list)
    for r in rows:
        t = timing(r['run_dir'])
        if t is None: continue
        prod, nthr = spans_produced(r['run_dir'])
        deliv = int(r['spans_delivered']) if r['spans_delivered'].lstrip('-').isdigit() else None
        t.update(rep=int(r['rep']), rc=int(r['exit_code']), prod=prod, deliv=deliv,
                 lo=lo_iters(r['run_dir']), aperf=r['aperf_mhz'], tctl=r['tctl_post_c'])
        cells[(r['processor_type'], r['exporter_type'])].append(t)

    order = [('0','2'), ('1','2'), ('0','0'), ('1','0')]
    order = [k for k in order if k in cells]

    print("=" * 78)
    print("BLOCCO 4 — backend reale: rete contro file        %d run" % len(rows))
    print("=" * 78)

    # --- A. costo per iterazione ------------------------------------------
    # Riferimento: la cella di controllo del blocco 3 (trace_level=0, stesso
    # carico, stesso exporter Zipkin), che e' l'unica misura di "budget senza
    # strumentazione" disponibile a n_lo=4.
    base_rows = [r for r in csv.DictReader(open(os.path.join(DOE, 'data_table.csv')))
                 if r['block'] == 'block3' and r['trace_level'] == '0' and r['n_lo'] == '4']
    base = None
    if base_rows:
        b = [timing(r['run_dir']) for r in base_rows]
        base = st.median([x['budget'] for x in b if x])

    print("\nA. COSTO PER ITERAZIONE DEL TASK CRITICO")
    print("   budget = run + slack, mediana sulle iterazioni e poi sulle ripetizioni")
    if base: print("   riferimento senza strumentazione (blocco 3, trace_level=0, n_lo=4): %.1f us" % base)
    print()
    print("   %-14s %4s %10s %10s %10s %9s" % ("cella", "n", "budget_us", "costo_us", "run_med", "jitter"))
    print("   " + "-" * 62)
    for k in order:
        v = cells[k]
        bud = st.median([x['budget'] for x in v])
        cost = (base - bud) if base else float('nan')
        print("   %-14s %4d %10.1f %10.1f %10.1f %9.1f"
              % (lab(*k), len(v), bud, cost, st.median([x['run_med'] for x in v]),
                 st.median([x['jit'] for x in v])))

    # --- B. telemetria consegnata -----------------------------------------
    # Separata per exit code, e non per pignoleria: con SIGABRT rt-app perde
    # l'ultimo blocco di righe di timing non ancora scritte (rt-app.cpp:1845-1852
    # svuota l'array `timings` solo a fine thread, attraverso stdio). Quindi il
    # DENOMINATORE e' sottostimato mentre il conteggio dei consegnati e' corretto,
    # e mescolando i due gruppi si vedono rese sopra il 100 % che sembrano un
    # paradosso e sono solo un artefatto del troncamento.
    print("\nB. TELEMETRIA: PRODOTTA CONTRO CONSEGNATA")
    print("   'persi' = prodotti - consegnati. Con Batch la coda (2048) scarta in silenzio.")
    for tag, keep, note in (
            ("run terminati normalmente (exit 0) — confronto valido",
             lambda x: x['rc'] == 0, None),
            ("run abortiti (SIGABRT)",
             lambda x: x['rc'] == 134,
             "   'prodotti' e' un limite INFERIORE: log troncati, resa sovrastimata.")):
        sel = {k: [x for x in cells[k] if x['deliv'] is not None and keep(x)] for k in order}
        if not any(sel.values()): continue
        print("\n   %s" % tag)
        if note: print(note)
        print("   %-14s %4s %11s %11s %10s %8s"
              % ("cella", "n", "prodotti", "consegnati", "persi", "resa"))
        print("   " + "-" * 64)
        for k in order:
            v = sel[k]
            if not v: continue
            pr = st.median([x['prod'] for x in v]); d = st.median([x['deliv'] for x in v])
            print("   %-14s %4d %11.0f %11.0f %10.0f %7.1f%%"
                  % (lab(*k), len(v), pr, d, pr - d, 100.0 * d / pr))

    # Controllo incrociato del conteggio. Nel braccio file i consegnati si contano
    # direttamente dalle intestazioni di spans.log, senza passare dal collector:
    # se li' lo scarto e' zero, la formula analitica e' esatta e uno scarto nel
    # braccio rete misura perdite vere invece che un errore di conteggio. E' la
    # garanzia che serve dopo aver gia' avuto un collector che degradava in
    # silenzio (OOM da MEM_MAX_SPANS troppo alto, 2026-09).
    print("\n   Controllo incrociato del conteggio (solo run exit 0)")
    print("   %-14s %-30s %s" % ("cella", "fonte dei consegnati", "scarto consegnati-prodotti"))
    print("   " + "-" * 72)
    for k in order:
        v = [x for x in cells[k] if x['deliv'] is not None and x['rc'] == 0]
        if not v: continue
        dd = [x['deliv'] - x['prod'] for x in v]
        src = "spans.log, conteggio diretto" if k[1] == '2' else "contatore Zipkin /metrics"
        print("   %-14s %-30s min %+d, max %+d" % (lab(*k), src, min(dd), max(dd)))

    # --- C. deadline miss e abort -----------------------------------------
    # I miss si SOMMANO fra ripetizioni, mai si mediano: una cella con 6 miss su
    # 3 run di 20 avrebbe mediana 0 e il risultato sparirebbe.
    print("\nC. SCADENZE E TERMINAZIONE")
    print("   %-14s %8s %8s %12s %10s" % ("cella", "miss", "run c/miss", "slack_min_us", "abort"))
    print("   " + "-" * 58)
    for k in order:
        v = cells[k]
        print("   %-14s %8d %8d %12d %10s"
              % (lab(*k), sum(x['miss'] for x in v), sum(1 for x in v if x['miss']),
                 min(x['slack_min'] for x in v),
                 "%d/%d" % (sum(1 for x in v if x['rc'] == 134), len(v))))

    # --- D. lavoro effettivamente svolto ----------------------------------
    print("\nD. ITERAZIONI COMPLETATE (throughput)")
    print("   HI: attese 2000 su 20 s. LO: 4 istanze volutamente in sovraccarico.")
    print("   %-14s %12s %14s" % ("cella", "HI (su 2000)", "LO (4 thread)"))
    print("   " + "-" * 44)
    for k in order:
        v = cells[k]
        print("   %-14s %12.0f %14.0f"
              % (lab(*k), st.median([x['rows'] for x in v]), st.median([x['lo'] for x in v])))

    # --- E. run anomali ----------------------------------------------------
    an = [(lab(*k), x) for k in order for x in cells[k]
          if x['run_med'] > 1.5 * st.median([y['run_med'] for y in cells[k]])]
    print("\nE. RUN ANOMALI (run_med oltre 1.5x la mediana della cella): %d" % len(an))
    for name, x in an:
        print("   %-14s rep %2d  run_med=%6.0f  aperf=%s MHz" % (name, x['rep'], x['run_med'], x['aperf']))

if __name__ == '__main__':
    main()
