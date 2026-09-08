#!/usr/bin/env python3
"""
Generate an rt-app mixed-criticality task-set JSON config.

- HI_task: SCHED_FIFO, prio 90, 2 ms di lavoro su un periodo di 10 ms scandito da un
  evento `timer` ASSOLUTO -> il task critico di cui misuriamo WCET, jitter e deadline miss.
- LO_noise: SCHED_OTHER, `run 500 / sleep 500`, replicato N volte via "instance" -> carico
  best-effort di sottofondo, che genera anche telemetria per stressare
  exporter/sampler/processor.

Perche' HI usa `timer` e LO no
------------------------------
Lo `slack` (colonna 8 del log, da cui `analyze_doe.py` ricava il `deadline_miss_ratio`)
viene calcolato da rt-app **solo** dentro `case rtapp_timer:` (`rt-app.cpp:727-746`), come
differenza fra l'istante assoluto della prossima attivazione e adesso. Con `"sleep"` —
una `clock_nanosleep` relativa eseguita dopo il `run` — lo slack resta 0 su ogni riga e il
`deadline_miss_ratio` sarebbe 0 **per costruzione**, non perche' le deadline sono
rispettate (task 0.5, finding (h)). Inoltre con `sleep` il periodo e' `run + sleep +
overhead`, quindi eredita l'errore del run: non e' un task davvero periodico.

LO invece resta su `sleep` di proposito. E' volutamente sovraccarico (con --n-lo 4 sono
4 x 50 % su una sola CPU), e con un timer `absolute` un task in ritardo non dorme mai piu':
`t_next` resta indietro e ogni iterazione successiva salta l'attesa, trasformando il rumore
da "duty cycle del 50 %" a busy loop puro. Cambierebbe la natura del carico. Sul task LO
non misuriamo deadline, quindi lo slack non serve.

Topologia (ASUS UX431DA, Ryzen 7 3700U: 4 core fisici, 8 thread SMT):
  cpu0,1 -> core 0    cpu2,3 -> core 1    cpu4,5 -> core 2    cpu6,7 -> core 3
HI_task e LO_noise devono stare su core FISICI DIVERSI, altrimenti si contendono le
unita' di esecuzione dello stesso core via SMT e l'interferenza misurata non e' piu'
attribuibile a scheduling/telemetria. Default: HI su cpu2 (core 1), LO su cpu6 (core 3),
coerenti con isolcpus=managed_irq,domain,2,3,6,7 sulla cmdline.

Calibrazione: `"calibration"` e' un INTERO fisso (ns per iterazione), mai `"CPU0"`.
`"CPU0"` fa partire l'auto-calibrazione, che costa ~8 s non deterministici e — dentro un
cpuset che non contiene la CPU 0 — calibra su un'altra CPU senza dirlo (la
`sched_setaffinity` fallisce e il valore di ritorno non e' controllato,
`rt-app.cpp:2071-2082`). Il default 29 viene dal task 0.6: il costo reale misurato e'
28.76 ns/iterazione, e fra i due interi possibili 29 sbaglia di -0.83 % sui 2000 us
nominali contro il +2.71 % di 28.

Usage:
  ./gen_config.py --n-lo 4 --duration 20 --out cfg_n4.json
  ./gen_config.py --n-lo 0 --duration 20 --out cfg_hi_only.json   # Block 1
"""
import argparse
import json
import sys


def expand_cpu_list(spec):
    """'2-3,6' -> [2, 3, 6]"""
    out = []
    for part in str(spec).split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return out


def thread_siblings(cpu):
    """CPU logiche che condividono il core fisico con `cpu` (siblings SMT)."""
    path = f"/sys/devices/system/cpu/cpu{cpu}/topology/thread_siblings_list"
    try:
        with open(path) as f:
            return set(expand_cpu_list(f.read().strip()))
    except OSError:
        return set()          # topologia non leggibile: nessun controllo possibile


def warn_if_smt_shared(hi_cpu, lo_cpus):
    """Avvisa se HI e LO finiscono sullo stesso core fisico."""
    siblings = thread_siblings(hi_cpu)
    if not siblings:
        return
    clash = sorted(siblings & set(lo_cpus))
    if clash:
        print(
            f"ATTENZIONE: HI_task (cpu{hi_cpu}) e LO_noise (cpu{clash}) sono thread SMT\n"
            f"  dello stesso core fisico. Si contendono le unita' di esecuzione del core,\n"
            f"  quindi l'interferenza misurata sara' in buona parte contesa hardware e non\n"
            f"  scheduling/telemetria. Usa core diversi (es. --hi-cpu 2 --lo-cpus 6),\n"
            f"  oppure tienilo cosi' solo se stai deliberatamente misurando l'effetto SMT.",
            file=sys.stderr,
        )


def build_config(n_lo, duration, hi_cpu, lo_cpus, calibration):
    tasks = {
        "HI_task": {
            "policy": "SCHED_FIFO",
            "priority": 90,
            "cpus": [hi_cpu],
            "loop": -1,
            "run": 2000,
            # periodo COMPLETO (non lo sleep): il timer attende fino al prossimo
            # istante di attivazione assoluto. "absolute" tiene la griglia fissa, cosi'
            # un'iterazione lunga erode lo slack invece di spostare le successive.
            "timer": {"ref": "unique", "period": 10000, "mode": "absolute"},
        }
    }
    if n_lo > 0:
        tasks["LO_noise"] = {
            "instance": n_lo,
            "policy": "SCHED_OTHER",
            "cpus": lo_cpus,
            "loop": -1,
            "run": 500,
            "sleep": 500,
        }
    return {
        "tasks": tasks,
        "global": {
            "duration": duration,
            "default_policy": "SCHED_OTHER",
            "calibration": calibration,
            "logdir": "./",
            "log_basename": "rtapp",
        },
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n-lo", type=int, default=0, help="number of LO_noise instances")
    ap.add_argument("--duration", type=int, default=20, help="run duration in seconds")
    ap.add_argument("--hi-cpu", type=int, default=2, help="CPU pinned for HI_task")
    ap.add_argument("--lo-cpus", type=int, nargs="+", default=[6],
                     help="CPU(s) for LO_noise instances (core fisico diverso da --hi-cpu)")
    ap.add_argument("--calibration", type=int, default=29,
                     help="pLoad fisso in ns (INTERO: rt-app rifiuta i float). Mai \"CPU0\": "
                          "vedi il task 0.6 in CLAUDE.md")
    ap.add_argument("--out", required=True, help="output JSON path")
    args = ap.parse_args()

    if args.n_lo > 0:
        warn_if_smt_shared(args.hi_cpu, args.lo_cpus)

    cfg = build_config(args.n_lo, args.duration, args.hi_cpu, args.lo_cpus,
                       args.calibration)
    with open(args.out, "w") as f:
        json.dump(cfg, f, indent=2)
    print(f"wrote {args.out}  (n_lo={args.n_lo}, duration={args.duration}s, "
          f"HI=cpu{args.hi_cpu}, LO=cpu{args.lo_cpus}, calibration={args.calibration})")
