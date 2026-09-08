import csv, glob, gzip, os, statistics as st
from collections import defaultdict

def pct(v,p):
    v=sorted(v); k=(len(v)-1)*p/100; f=int(k)
    return v[f] if f+1>=len(v) else v[f]+(v[f+1]-v[f])*(k-f)

def opener(path):
    """I log del DoE sono committati gzippati (21 MB -> 3.3 MB)."""
    return gzip.open(path, 'rt') if path.endswith('.gz') else open(path)

def find_hi_log(run_dir):
    for pat in ('*HI_task*.log.gz', '*HI_task*.log'):
        m = glob.glob(os.path.join(run_dir, pat))
        if m: return m[0]
    raise FileNotFoundError(run_dir)

def summarize(path):
    run=[];per=[];slack=[]
    for l in opener(path):
        if l.startswith('#'): continue
        c=l.split()
        if len(c)<11: continue
        run.append(int(c[2])); per.append(int(c[3])); slack.append(int(c[7]))
    # scarta il transitorio di avvio: righe iniziali con slack<0 (task 0.5 / task 2)
    k=0
    while k < len(slack) and slack[k] < 0: k += 1
    return dict(n=len(run), warmup=k,
                run_med=st.median(run[k:]), run_max=max(run[k:]),
                p50=pct(per[k+1:],50), p99=pct(per[k+1:],99), pmax=max(per[k+1:]),
                slack_min=min(slack[k:]), miss=sum(1 for s in slack[k:] if s<0))

cells=defaultdict(list)
for r in csv.DictReader(open('2-DoE/data_table.csv')):
    # Filtro indispensabile: il data_table raccoglie TUTTE le campagne. Senza,
    # il raggruppamento per trace_level mescolerebbe run con carico e sampler
    # diversi (es. i 150 run di block2 sono tutti a trace_level 2).
    if r['block'] != 'block1': continue
    cells[int(r['trace_level'])].append(summarize(find_hi_log(r['run_dir'])))

print("BLOCCO 1 — overhead puro di strumentazione (solo HI, nessun rumore, 20 rip./cella)")
print("livelli: 0=nessuno  1=main+thread  2=+phase  3=+phase_loop\n")
hdr=f"{'trace':<7}{'iter':>6}{'warmup':>8}{'run_med':>10}{'run_max':>10}{'p50':>9}{'p99':>9}{'per_max':>9}{'jitter':>9}{'miss':>7}"
print(hdr); print("-"*len(hdr))
base=None
for t in sorted(cells):
    S=cells[t]
    m=lambda k: st.mean(x[k] for x in S)
    row=dict(run_med=m('run_med'), run_max=m('run_max'), p50=m('p50'), p99=m('p99'),
             pmax=m('pmax'), jit=m('pmax')-m('p50'))
    if base is None: base=row
    print(f"{t:<7}{m('n'):>6.0f}{m('warmup'):>8.2f}{row['run_med']:>10.1f}{row['run_max']:>10.1f}"
          f"{row['p50']:>9.1f}{row['p99']:>9.1f}{row['pmax']:>9.1f}{row['jit']:>9.1f}"
          f"{sum(x['miss'] for x in S):>7}")

print("\nDelta rispetto a trace=0 (media su 20 rip.), in us e in %:")
print(f"{'trace':<7}{'d run_med':>12}{'d run_max':>14}{'d p99':>14}{'d per_max':>14}")
for t in sorted(cells):
    S=cells[t]; m=lambda k: st.mean(x[k] for x in S)
    f=lambda k,b: f"{m(k)-b:+8.1f} ({(m(k)-b)/b*100:+.2f}%)"
    print(f"{t:<7}{f('run_med',base['run_med']):>12}{f('run_max',base['run_max']):>14}"
          f"{f('p99',base['p99']):>14}{f('pmax',base['pmax']):>14}")

print("\nDispersione fra ripetizioni (dev.std sulle 20 medie per cella):")
print(f"{'trace':<7}{'run_med':>10}{'run_max':>10}{'p99':>10}{'per_max':>10}")
for t in sorted(cells):
    S=cells[t]; s=lambda k: st.stdev([x[k] for x in S])
    print(f"{t:<7}{s('run_med'):>10.2f}{s('run_max'):>10.2f}{s('p99'):>10.2f}{s('pmax'):>10.2f}")
