#!/usr/bin/env python3
"""Latenza di consegna degli span: t_arrivo_al_backend - t_chiusura_dello_span.

Misura il compromesso che il blocco 4 non copriva: Batch non fa pagare il task
critico in tempo di CPU, ma consegna la telemetria in ritardo, perche' la trattiene
in un buffer finche' non scatta una delle due soglie di risveglio
(min(max_queue_size/2, max_export_batch_size) = 512 span) o il timer da 5 s.

Attribuzione: l'albero e' phase_loop[N] <- phase[0] <- thread_loop[0] <- <task> <- main,
quindi si risale la catena dei parent fino allo span di thread, il cui nome e' il nome
del task nel JSON (HI_task-0 / LO_noise-N)."""
import glob, os, sys, statistics as st
from collections import defaultdict

DOE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '2-DoE', 'latency')
PROC = {'p0': 'Batch', 'p1': 'Simple'}

def load(run_dir):
    f = os.path.join(run_dir, 'spans.tsv')
    if not os.path.exists(f): return None
    rows = [l.rstrip('\n').split('\t') for l in open(f)][1:]
    rows = [r for r in rows if len(r) >= 8]
    by_id = {r[6]: r for r in rows}
    def owner(r):
        cur, seen = r, 0
        while cur is not None and seen < 8:
            n = cur[3]
            if n.startswith('HI_task') or n.startswith('LO_noise'): return n
            cur = by_id.get(cur[7]); seen += 1
        return None
    out = defaultdict(list)
    batches = {}
    leaves, unres = [], defaultdict(list)
    for r in rows:
        batches[int(r[1])] = int(r[2])
        if not r[3].startswith('phase_loop'): continue
        lat = int(r[0])/1e6 - (int(r[4]) + int(r[5]))/1e3
        o = owner(r)
        if o is not None:
            out['HI' if o.startswith('HI') else 'LO'].append(lat)
        else:
            unres[r[7]].append((lat, int(r[4])))
    # Fallback per i run abortiti: con SIGABRT gli span di thread non vengono mai
    # esportati e la catena dei parent resta monca. Si raggruppa allora per span
    # genitore (phase[0], uno per thread) e si classifica sul PASSO DI ATTIVAZIONE:
    # HI ha un timer assoluto da 10 000 us, quindi i suoi span partono su griglia
    # fissa; i LO sono in sovraccarico e derivano.
    # NON usare la durata: a n_lo=4 un'iterazione LO da 500 us di lavoro occupa ~2000 us
    # di orologio perche' ha un quarto di CPU, e diventa indistinguibile da HI.
    for pid, items in unres.items():
        ts = sorted(t for _, t in items)
        if len(ts) < 3: continue
        step = st.median([b - a for a, b in zip(ts, ts[1:])])
        k = 'HI' if 9900 <= step <= 10100 else 'LO'
        out[k].extend(l for l, _ in items)
    return out, batches

def q(v, p):
    v = sorted(v); return v[min(len(v)-1, int(len(v)*p))] if v else float('nan')

def main():
    cells = defaultdict(lambda: defaultdict(list))
    nbatch = defaultdict(list); bsize = defaultdict(list); nruns = defaultdict(int)
    for d in sorted(glob.glob(os.path.join(DOE, 'p*_n*'))):
        cell = os.path.basename(d)
        for r in sorted(glob.glob(os.path.join(d, 'run_*'))):
            got = load(r)
            if not got: continue
            lat, batches = got
            nruns[cell] += 1
            for k, v in lat.items(): cells[cell][k].extend(v)
            nbatch[cell].append(len(batches))
            bsize[cell].extend(batches.values())
    if not cells:
        print("nessun dato in", DOE); return

    order = sorted(cells, key=lambda c: (int(c.split('_n')[1]), c.split('_')[0]))
    print("=" * 76)
    print("LATENZA DI CONSEGNA DELLA TELEMETRIA        %d run" % sum(nruns.values()))
    print("=" * 76)
    print("\nA. TASK CRITICO — quanto e' vecchia la telemetria di HI quando arriva (ms)")
    print("   %-16s %4s %8s %9s %9s %9s %9s" % ("cella","run","n span","p50","p90","p99","max"))
    print("   " + "-" * 68)
    for c in order:
        v = cells[c]['HI']
        if not v: continue
        p, n = c.split('_n')
        print("   %-16s %4d %8d %9.1f %9.1f %9.1f %9.1f"
              % ("%s, n_lo=%s" % (PROC[p], n), nruns[c], len(v),
                 q(v,.5), q(v,.9), q(v,.99), max(v)))

    print("\nB. CONFRONTO CON IL RUMORE — stessa cella, span dei task best-effort (ms)")
    print("   %-16s %8s %9s %9s" % ("cella","n span","p50 LO","max LO"))
    print("   " + "-" * 46)
    for c in order:
        v = cells[c]['LO']
        if not v: continue
        p, n = c.split('_n')
        print("   %-16s %8d %9.1f %9.1f" % ("%s, n_lo=%s" % (PROC[p], n), len(v), q(v,.5), max(v)))

    print("\nC. COME ESCONO I BATCH")
    print("   %-16s %10s %12s %12s" % ("cella","batch/run","span/batch","span/batch max"))
    print("   " + "-" * 54)
    for c in order:
        p, n = c.split('_n')
        print("   %-16s %10.1f %12.1f %12d"
              % ("%s, n_lo=%s" % (PROC[p], n), st.median(nbatch[c]),
                 st.median(bsize[c]), max(bsize[c])))
    unattr = sum(len(cells[c]['?']) for c in order)
    if unattr: print("\n   span non attribuiti a un thread: %d" % unattr)

if __name__ == '__main__':
    main()
