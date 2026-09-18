# OTel capabilities for mixed-criticality workloads

Valutazione sperimentale delle capacità di [OpenTelemetry](https://opentelemetry.io/) (tracing) nel monitorare un workload **mixed-criticality**: un task real-time critico e un carico best-effort di sottofondo, entrambi generati da un fork strumentato di [rt-app](https://github.com/scheduler-tools/rt-app). L'obiettivo è capire se e come OTel riesca a **prioritizzare i dati di telemetria del task critico** rispetto a quelli del task a bassa criticità, e quale **overhead** la strumentazione introduca sul WCET del task critico.

Il progetto nasce come elaborato del corso di Real-Time Systems (traccia A5, testo completo in [`traccia.md`](traccia.md)).

## Indice

- [Il workload](#il-workload)
- [Il fork di rt-app: le macro di strumentazione](#il-fork-di-rt-app-le-macro-di-strumentazione)
- [Struttura del repository](#struttura-del-repository)
- [Requisiti](#requisiti)
- [Build](#build)
- [Come lanciare la campagna sperimentale](#come-lanciare-la-campagna-sperimentale)
- [Risultati](#risultati)
- [Limiti e sviluppi futuri](#limiti-e-sviluppi-futuri)
- [Licenza](#licenza)

## Il workload

Il task set (generato da [`scripts/measurements/gen_config.py`](scripts/measurements/gen_config.py)) è composto da due tipi di task:

- **`HI_task`** — `SCHED_FIFO`, priorità 90, 2000 µs di lavoro su un periodo di 10000 µs scandito da un timer **assoluto**. È il task critico di cui si misurano WCET, jitter e deadline miss. Gira da solo su una CPU dedicata (default `cpu2`).
- **`LO_noise`** — `SCHED_OTHER`, ciclo `run 500 / sleep 500` (duty cycle 50%), replicato in *N* istanze (0/1/4/8 a seconda del blocco sperimentale). È il carico best-effort di sottofondo, deliberatamente in sovraccarico, che genera anche la propria telemetria per stressare sampler/processor/exporter. Gira su un core fisico diverso da quello di `HI_task` (default `cpu6`), per evitare che l'interferenza osservata sia dovuta a contesa SMT invece che a scheduling/telemetria.

## Il fork di rt-app: le macro di strumentazione

`rt-app` (in [`rt-app/`](rt-app)) è stato esteso con l'OpenTelemetry C++ SDK. La configurazione della pipeline di tracing avviene interamente a **compile time**, tramite cinque macro definite in [`rt-app/src/rt-app_types.h`](rt-app/src/rt-app_types.h) e usate in [`rt-app/src/rt-app.cpp`](rt-app/src/rt-app.cpp):

| Macro | Valori | Significato |
|---|---|---|
| `RTAPP_TRACE_LEVEL` | `0`–`3` | `0` nessuna strumentazione · `1` span di `main` + span di thread · `2` aggiunge span di fase (`phase`) · `3` aggiunge span di ogni iterazione della fase (`phase_loop`, il livello più granulare) |
| `RTAPP_PROCESSOR_TYPE` | `0`/`1` | `0` **BatchSpanProcessor** (accumula fino a 2048 span o 5 s, poi esporta su un thread dedicato) · `1` **SimpleSpanProcessor** (esporta in modo sincrono, nel thread che chiude lo span) |
| `RTAPP_SAMPLER_TYPE` | `0`/`1`/`2` | `0` AlwaysOn · `1` TraceIdRatioBased (usa `RTAPP_SAMPLER_RATIO`) · `2` AlwaysOff |
| `RTAPP_SAMPLER_RATIO` | `0.0`–`1.0` | probabilità di campionamento quando `RTAPP_SAMPLER_TYPE=1` |
| `RTAPP_EXPORTER_TYPE` | `0`/`1`/`2` | `0` **Zipkin** (HTTP, rete) · `1` OStream su stdout · `2` OStream su file (`spans.log` nella cartella del run) |

Ogni thread apre uno span radice sotto lo span di `main`, quindi `HI_task` e tutte le istanze di `LO_noise` condividono la **stessa trace** — un dettaglio centrale per i risultati (vedi sotto).

## Struttura del repository

```
.
├── traccia.md              # testo dell'assegnazione (Project A5)
├── rt-app/                  # fork di rt-app, strumentato con OpenTelemetry C++
│   └── src/rt-app.cpp       # generatore di carico real-time periodico + pipeline OTel
├── 1-configs/                # config JSON di riferimento per rt-app (n_lo = 0/1/4/8)
├── 2-DoE/                    # dati e risultati della campagna sperimentale (Design of Experiments)
│   ├── block1/ … block4/     # una cartella per "cella" (combinazione di fattori) × run_NN, log inclusi
│   ├── latency/               # campagna dedicata alla latenza di consegna degli span
│   ├── figures/                # grafici riassuntivi dei risultati
│   ├── data_table.csv          # una riga per run
│   ├── results.csv             # una riga per run, con i tempi estratti dai log
│   ├── results_summary.csv     # una riga per cella (mediane/conteggi)
│   └── index.txt               # indice run_id -> cartella del run
└── scripts/
    ├── measurements/          # generazione delle config e orchestrazione della campagna sperimentale
    └── utils_isolation/        # isolamento delle CPU e della frequenza per misure ripetibili
```

## Requisiti

- Un kernel Linux **PREEMPT-RT**, con isolamento configurato in cmdline (es. `isolcpus=managed_irq,domain,2,3,6,7 nohz_full=2,3,6,7 rcu_nocbs=2,3,6,7 irqaffinity=0,1,4,5`).
- [`opentelemetry-cpp`](https://github.com/open-telemetry/opentelemetry-cpp) compilato e installato in `otel-installdir/` **nella root del repository** (non versionata, vedi `.gitignore`), con almeno i moduli: `opentelemetry_exporter_zipkin_trace`, `opentelemetry_exporter_ostream_span[_builder]`, `opentelemetry_http_client_curl`, `opentelemetry_trace`, `opentelemetry_resources`, `opentelemetry_common`.
- `libcurl`, `libjson-c`, autotools (`autoconf`/`automake`/`libtool`), un compilatore C++17.
- `cpuset-tools` (`cset`) e `msr-tools` (`rdmsr`/`wrmsr`) per l'isolamento delle CPU e il pinning della frequenza — gli script MSR in [`scripts/utils_isolation/pin_cpu_freq.sh`](scripts/utils_isolation/pin_cpu_freq.sh) sono scritti per **CPU AMD famiglia 17h/19h**.
- Docker, per il collector Zipkin reale usato nel blocco 4.

## Build

```sh
# 1. build/installazione di opentelemetry-cpp in <root-repo>/otel-installdir
#    (con i moduli elencati sopra)

# 2. generazione del sistema di build di rt-app
cd rt-app
./autogen.sh
./configure

# 3. build per una specifica combinazione di macro
make CPPFLAGS="-DRTAPP_TRACE_LEVEL=3 -DRTAPP_PROCESSOR_TYPE=0 \
                -DRTAPP_SAMPLER_TYPE=0 -DRTAPP_SAMPLER_RATIO=0.5 \
                -DRTAPP_EXPORTER_TYPE=2"
```

Poiché le macro sono risolte a compile time, ogni combinazione richiede un binario diverso. In pratica non è necessario compilare a mano: [`run_doe.sh`](scripts/measurements/run_doe.sh) lo fa automaticamente per ogni cella della campagna, tenendo una cache dei binari già compilati in `bin/`.

## Come lanciare la campagna sperimentale

### 1. Preparazione della piattaforma

```sh
sudo scripts/utils_isolation/pin_cpu_freq.sh fix 0     # disabilita il boost, fissa la P-state 0
sudo scripts/utils_isolation/isolate_cpus.sh 2,3,6,7    # cpuset shield + redirect degli IRQ
```

Per il blocco 4 (unico che usa un backend di rete reale) serve anche il collector Zipkin, avviato **dopo** lo shield e confinato sulle CPU di housekeeping:

```sh
scripts/measurements/zipkin_collector.sh start
```

### 2. Esecuzione

```sh
scripts/measurements/run_doe.sh block1   # o block2 | block3 | block4
```

`REPS` e `DURATION` sono personalizzabili via variabile d'ambiente (default specifici per blocco, vedi tabella sotto); ad es. `REPS=3 DURATION=5 ./run_doe.sh block1` per uno smoke test rapido. Lo script esegue prima un **preflight** (sudo utilizzabile, shield attivo, boost disabilitato, contatori APERF/MPERF leggibili; per il blocco 4 anche raggiungibilità e isolamento del collector), poi compila (o riusa dalla cache) il binario di ogni cella, genera la config con `gen_config.py` ed esegue le ripetizioni **interlacciate** fra le celle (A/B/A/B…, non tutte le A e poi tutte le B), per non confondere la deriva termica della macchina con il fattore in studio. Ogni run produce una riga in `2-DoE/data_table.csv` e in `2-DoE/index.txt`, oltre ai log grezzi nella cartella del run.

| Blocco | Fattore in studio | Fissi | Celle × rip. |
|---|---|---|---|
| `block1` | granularità del tracing (`trace_level` 0–3) | solo `HI_task`, nessun rumore, sampler AlwaysOn, processor Batch | 4 × 20 = 80 run |
| `block2` | sampler (AlwaysOff / Ratio 0.1–0.7 / AlwaysOn) | `trace_level=2`, Batch, exporter ostream, 1 HI + 4 LO | 6 × 25 = 150 run |
| `block3` | processor (Batch/Simple) × carico (`n_lo` 0/1/4/8) | `trace_level` ∈ {0, 3}, sampler AlwaysOn, exporter Zipkin senza collector in ascolto | 12 × 15 = 180 run |
| `block4` | processor × exporter (file locale / rete Zipkin **reale**) | `trace_level=3`, AlwaysOn, 1 HI + 4 LO | 4 × 20 = 80 run |

### 3. Latenza di consegna (misura separata)

```sh
scripts/measurements/run_latency.sh
```

Redirige l'exporter Zipkin verso [`span_probe.py`](scripts/measurements/span_probe.py), un piccolo backend HTTP compatibile con l'API Zipkin che marca l'istante di arrivo di ogni POST, per misurare quanto tempo passa fra la chiusura di uno span e la sua consegna effettiva.

### 4. Pulizia

```sh
sudo scripts/utils_isolation/reset_isolation.sh
scripts/measurements/zipkin_collector.sh stop
```

## Risultati

La campagna sperimentale ha permesso di rispondere alle domande della traccia:

- **Prioritizzazione**: il sampler di OTel decide a livello di intera trace, non di singolo span. Poiché `HI_task` e i `LO_noise` condividono la stessa trace, non è possibile campionare selettivamente solo la telemetria del task critico: quando la trace viene scartata, si perde anche il task critico insieme al resto.
- **Overhead della strumentazione**: in assenza di carico di sottofondo, il costo del tracing puro sul task critico è trascurabile rispetto al periodo del task.
- **Overhead sotto carico**: dipende soprattutto dal tipo di `SpanProcessor` scelto. Con `BatchSpanProcessor` il costo per il task critico resta pressoché costante al variare del carico di sottofondo; con `SimpleSpanProcessor` (export sincrono nel thread del task) cresce sensibilmente con il carico, e nelle condizioni più gravose porta a violazioni della deadline del task critico.
- **Costo contro freshness**: emerge un compromesso fra il costo imposto al task critico e la rapidità con cui la sua telemetria arriva effettivamente a destinazione — il processor più sicuro per il real-time non è quello che consegna prima i dati.

Dati completi per-run e aggregati, insieme ai grafici, sono disponibili in `2-DoE/` (`data_table.csv`, `results.csv`, `results_summary.csv`, `figures/`).

## Limiti e sviluppi futuri

- **Copertura hardware**: i risultati sono stati ottenuti su una singola piattaforma; un confronto su hardware diverso aiuterebbe a distinguere gli effetti specifici della macchina da quelli architetturali di OTel.
- **Sampling criticality-aware**: dato che il sampling attuale opera per trace e non per singolo span, un possibile sviluppo è un sampler che dia priorità esplicita agli span del task critico indipendentemente dall'esito del campionamento sulla trace.
- **Processor con priorità differenziata**: un `SpanProcessor` che isoli il costo per il task critico (come fa oggi `Batch`) riducendo al contempo la latenza di consegna della sua telemetria (vantaggio oggi di `Simple`) combinerebbe i due comportamenti osservati come alternativi.

