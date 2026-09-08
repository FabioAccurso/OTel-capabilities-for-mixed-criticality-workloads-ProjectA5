#!/usr/bin/env python3
"""
Task 5 — analisi aggregata del DoE.

Attraversa ogni run prodotto da run_doe.sh, estrae le variabili di risposta dal
log per-thread di HI_task e le unisce ai livelli dei fattori presi da
data_table.csv, producendo:

  results.csv          una riga per run (485 run: block1, block2, block3, diag)
  results_summary.csv  una riga per cella, con mediane e conteggi

Colonne del log di rt-app (rt-app_utils.cpp:log_timing), separate da spazi:
  ind perf duration period start end rel_start slack c_duration c_period wu_lat
Tutti i tempi in microsecondi.

QUATTRO CORREZIONI rispetto alla versione originale (`analyze_doe.py.orig`),
tutte necessarie perche' senza di esse i numeri sarebbero sbagliati in silenzio:

1. **I log sono gzippati.** Le campagne committate hanno i `.log` compressi
   (756 MB -> 66 MB per il solo block3): la versione originale apriva solo `.log`
   e avrebbe trovato zero run.

2. **`count_exported_spans()` contava il doppio.** Faceva `content.count(nome)`
   su tutto `stdout.log`, ma l'exporter ostream scrive il nome del task DUE volte
   per span: come `name          : HI_task-0` e come attributo
   `config.name: HI_task-0`. Ora conta solo le righe di intestazione
   (`^  name +: <nome>`). Misurato su cfg_n4: 2 invece di 1 per HI, 8 invece di 4
   per LO.

3. **Si scarta TUTTO il transitorio di avvio, non solo la prima riga.** HI
   (`ind == 0`) fissa `t_zero` e poi si blocca su `pthread_barrier_wait` finche'
   tutti i thread non sono pronti; quando riparte, `t_first = t_zero` e' gia'
   vecchio di tutto il tempo di avvio degli altri. Il numero di righe con slack
   negativo per costruzione **scala col numero di thread** (misurato: 1, 2, 5, 10
   per n_lo 0, 1, 4, 8). Poiche' `n_lo` e' un fattore del blocco 3, uno scarto
   fisso di una riga lascerebbe 0, 1, 4 e 9 falsi miss nelle quattro celle, cioe'
   **un bias correlato proprio col fattore in studio**, che si leggerebbe come
   "piu' carico di sottofondo -> piu' deadline perse". Si scartano le righe
   iniziali finche' lo slack non diventa >= 0 la prima volta, e si registra
   quante ne sono state scartate (`warmup_rows`). Dopo il recupero, uno slack
   negativo e' un miss vero.

4. **`period` e `duration` non misurano l'overhead.** Il blocco 1 lo ha mostrato:
   `duration` (la colonna `run`) dipende dal layout del binario per ~30 us, piu'
   del segnale, e `period` = `end - start` della stessa riga *si accorcia* dove
   l'overhead cresce, perche' gli span nascono fuori da quella finestra. Restano
   in output per continuita', ma la metrica corretta e' **`budget_med_us` =
   mediana di `duration + slack`**, e il periodo vero e' il delta fra `start`
   consecutivi (`act_period_med_us`).

Uso:
  ./analyze_doe.py --data-table 2-DoE/data_table.csv --out 2-DoE/results.csv
"""
import argparse
import csv
import glob
import gzip
import os
import re
import statistics as st
from collections import defaultdict


def opener(path):
    return gzip.open(path, "rt", errors="replace") if path.endswith(".gz") \
        else open(path, errors="replace")


def find_hi_log(run_dir):
    """Solo log di HI. Nessun fallback su un log qualsiasi: prendere per sbaglio
    un log LO_noise darebbe numeri plausibili ma del task sbagliato."""
    for pat in ("*HI_task*.log.gz", "*HI_task*.log", "*HI*.log.gz", "*HI*.log"):
        m = sorted(glob.glob(os.path.join(run_dir, pat)))
        if m:
            return m[0]
    return None


def parse_log(path):
    rows = []
    for line in opener(path):
        parts = line.split()
        if len(parts) < 11:
            continue
        try:
            int(parts[0])
        except ValueError:
            continue                      # riga di intestazione
        rows.append(dict(duration=int(parts[2]), period=int(parts[3]),
                         start=int(parts[4]), slack=int(parts[7]),
                         wu_latency=int(parts[10])))
    return rows


def summarize(rows):
    if not rows:
        return None
    # correzione 3: scarta tutto il transitorio di avvio
    k = 0
    while k < len(rows) and rows[k]["slack"] < 0:
        k += 1
    if k >= len(rows):
        return None                       # nessuna iterazione valida
    body = rows[k:]
    slacks = [r["slack"] for r in body]
    durations = [r["duration"] for r in body]
    periods = [r["period"] for r in body if r["period"] > 0]
    budget = [r["duration"] + r["slack"] for r in body]
    starts = [r["start"] for r in body]
    act = [b - a for a, b in zip(starts, starts[1:])]
    misses = sum(1 for s in slacks if s < 0)
    return {
        "warmup_rows": k,
        "n_iters": len(body),
        "deadline_misses": misses,
        "deadline_miss_ratio": misses / len(slacks),
        "budget_med_us": st.median(budget),
        "slack_med_us": st.median(slacks),
        "slack_min_us": min(slacks),
        "max_duration_us": max(durations),
        "mean_duration_us": round(st.mean(durations), 1),
        "med_duration_us": st.median(durations),
        "period_jitter_std_us": round(st.pstdev(periods), 2) if len(periods) > 1 else 0.0,
        "act_period_med_us": st.median(act) if act else "",
        "act_period_max_us": max(act) if act else "",
        "mean_wu_latency_us": round(st.mean(r["wu_latency"] for r in body), 2),
    }


# correzione 2: solo le righe di intestazione dell'exporter ostream
NAME_RE = re.compile(r"^  name +: (.+?)\s*$")
ZIPKIN_RE = re.compile(r"ZIPKIN EXPORTER")


def count_exported_spans(run_dir):
    """Span realmente esportati, per criticita'. Ha senso solo per i run con
    exporter ostream (blocco 2): con Zipkin stdout non contiene span."""
    m = sorted(glob.glob(os.path.join(run_dir, "stdout.log.gz"))) or \
        sorted(glob.glob(os.path.join(run_dir, "stdout.log")))
    if not m:
        return None, None, None
    hi = lo = tot = 0
    for line in opener(m[0]):
        g = NAME_RE.match(line)
        if not g:
            continue
        tot += 1
        if g.group(1).startswith("HI_task"):
            hi += 1
        elif g.group(1).startswith("LO_noise"):
            lo += 1
    return hi, lo, tot


def count_export_attempts(run_dir):
    """Tentativi di export verso Zipkin: una riga su stderr per tentativo.
    Distingue Batch (accumula) da Simple (esporta a ogni span chiuso)."""
    m = sorted(glob.glob(os.path.join(run_dir, "stderr.log.gz"))) or \
        sorted(glob.glob(os.path.join(run_dir, "stderr.log")))
    if not m:
        return None
    return sum(1 for line in opener(m[0]) if ZIPKIN_RE.search(line))


RESP = ["warmup_rows", "n_iters", "deadline_misses", "deadline_miss_ratio",
        "budget_med_us", "slack_med_us", "slack_min_us", "max_duration_us",
        "mean_duration_us", "med_duration_us", "period_jitter_std_us",
        "act_period_med_us", "act_period_max_us", "mean_wu_latency_us"]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-table", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--summary", default=None,
                    help="CSV di riepilogo per cella (default: <out> con suffisso _summary)")
    args = ap.parse_args()

    with open(args.data_table) as f:
        input_rows = list(csv.DictReader(f))

    out_fields = list(input_rows[0].keys()) + RESP + \
        ["hi_spans_exported", "lo_spans_exported", "spans_exported_total",
         "export_attempts", "aborted"]

    written, skipped = 0, []
    rows_out = []
    for row in input_rows:
        run_dir = row["run_dir"]
        merged = dict(row)
        log_path = find_hi_log(run_dir) if os.path.isdir(run_dir) else None
        summary = summarize(parse_log(log_path)) if log_path else None
        if summary:
            merged.update(summary)
            written += 1
        else:
            merged.update({k: "" for k in RESP})
            skipped.append(run_dir)
        hi, lo, tot = count_exported_spans(run_dir) if os.path.isdir(run_dir) else (None, None, None)
        merged["hi_spans_exported"] = "" if hi is None else hi
        merged["lo_spans_exported"] = "" if lo is None else lo
        merged["spans_exported_total"] = "" if tot is None else tot
        att = count_export_attempts(run_dir) if os.path.isdir(run_dir) else None
        merged["export_attempts"] = "" if att is None else att
        merged["aborted"] = 1 if row.get("exit_code", "0") not in ("0", "", "NA") else 0
        rows_out.append(merged)

    with open(args.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=out_fields)
        w.writeheader()
        w.writerows(rows_out)
    print(f"scritto {args.out}  ({written} run con dati, {len(skipped)} senza)")
    for s in skipped[:5]:
        print(f"    senza dati: {s}")

    # ---------------- riepilogo per cella ----------------
    summ_path = args.summary or os.path.splitext(args.out)[0] + "_summary.csv"
    KEY = ["block", "trace_level", "processor_type", "sampler_type",
           "sampler_ratio", "exporter_type", "n_lo"]
    cells = defaultdict(list)
    for r in rows_out:
        if r["n_iters"] != "":
            cells[tuple(r[k] for k in KEY)].append(r)

    fields = KEY + ["n_runs", "n_aborted", "iters_tot", "deadline_misses_tot",
                    "deadline_miss_ratio", "budget_med_us", "slack_med_us",
                    "slack_min_us", "med_duration_us", "act_period_med_us",
                    "jitter_med_us", "warmup_med", "export_attempts_med",
                    "hi_spans_med", "lo_spans_med", "runs_sampled", "aperf_mhz_med"]
    with open(summ_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for key in sorted(cells):
            S = cells[key]
            med = lambda c: st.median([float(x[c]) for x in S if x[c] != ""])
            it = sum(int(x["n_iters"]) for x in S)
            ms = sum(int(x["deadline_misses"]) for x in S)
            sampled = sum(1 for x in S if x["spans_exported_total"] not in ("", 0, "0"))
            d = dict(zip(KEY, key))
            d.update(n_runs=len(S), n_aborted=sum(x["aborted"] for x in S),
                     iters_tot=it, deadline_misses_tot=ms,
                     deadline_miss_ratio=round(ms/it, 8) if it else "",
                     budget_med_us=med("budget_med_us"), slack_med_us=med("slack_med_us"),
                     slack_min_us=min(int(x["slack_min_us"]) for x in S),
                     med_duration_us=med("med_duration_us"),
                     act_period_med_us=med("act_period_med_us"),
                     jitter_med_us=round(med("act_period_max_us")-med("act_period_med_us"), 1),
                     warmup_med=med("warmup_rows"),
                     export_attempts_med=med("export_attempts") if any(x["export_attempts"] != "" for x in S) else "",
                     hi_spans_med=med("hi_spans_exported") if any(x["hi_spans_exported"] != "" for x in S) else "",
                     lo_spans_med=med("lo_spans_exported") if any(x["lo_spans_exported"] != "" for x in S) else "",
                     runs_sampled=sampled,
                     aperf_mhz_med=med("aperf_mhz") if any(x.get("aperf_mhz", "") not in ("", "NA") for x in S) else "")
            w.writerow(d)
    print(f"scritto {summ_path}  ({len(cells)} celle)")


if __name__ == "__main__":
    main()
