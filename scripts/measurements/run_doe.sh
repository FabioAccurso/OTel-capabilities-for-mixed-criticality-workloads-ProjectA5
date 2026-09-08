#!/usr/bin/env bash
# Orchestra il DoE: costruisce rt-app con le macro OTel di ogni cella, genera la
# config, e ripete il run REPS volte.
#
# Usage: ./run_doe.sh <block1|block2|block3>
#        REPS=3 DURATION=5 ./run_doe.sh block1     # smoke test rapido
#
# I path sono derivati dalla posizione dello script: non serve editarli.
#
# PREREQUISITI (verificati dal preflight, lo script si ferma se mancano):
#   sudo ./scripts/utils_isolation/pin_cpu_freq.sh fix 0
#   sudo ./scripts/utils_isolation/isolate_cpus.sh 2,3,6,7
#   sudo -v            (oppure esportare SUDO_ASKPASS: test.sh invoca sudo)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$HERE/../.." && pwd)"
RTAPP_SRC_DIR="${RTAPP_SRC_DIR:-$PROJECT_ROOT/rt-app/src}"
BIN_CACHE="${BIN_CACHE:-$PROJECT_ROOT/bin}"
DOE_ROOT="${DOE_ROOT:-$PROJECT_ROOT/2-DoE}"
GEN="$HERE/gen_config.py"
TEST="$HERE/test.sh"

DATA_TABLE="$DOE_ROOT/data_table.csv"
INDEX_FILE="$DOE_ROOT/index.txt"
HEADER="run_id,block,trace_level,processor_type,sampler_type,sampler_ratio,exporter_type,n_lo,rep,duration_s,mhz_med,aperf_mhz,tctl_pre_c,tctl_post_c,hi_loops,run_dir,exit_code,spans_delivered"

# sudo non interattivo: -n usa le credenziali in cache, -A l'helper SUDO_ASKPASS.
# Servono entrambi perche' -n disabilita l'askpass per definizione.
SUDO() { sudo -n "$@" 2>/dev/null || sudo -A "$@"; }

ZIPKIN_ENDPOINT="${ZIPKIN_ENDPOINT:-http://localhost:9411}"
# Frazione minima di iterazioni attese perche' un run sia considerato valido.
# 99 va bene dove l'overhead e' piccolo (blocchi 1-3), ma nel blocco 4 il crollo
# delle iterazioni completate E' il risultato da misurare (Simple+rete perde
# iterazioni per costruzione): li' la soglia serve solo a intercettare un crash
# precoce, non a giudicare la cella.
if [ -n "${MIN_LOOP_PCT:-}" ]; then MIN_LOOP_PCT_FROM_ENV=1; else MIN_LOOP_PCT_FROM_ENV=0; fi
MIN_LOOP_PCT="${MIN_LOOP_PCT:-99}"
TRUNCATED=0
MAX_TRUNCATED="${MAX_TRUNCATED:-12}"    # oltre questa soglia il problema e' il setup

HWMON=/sys/class/hwmon/hwmon3/temp1_input      # k10temp, Tctl
HI_CPU="${HI_CPU:-2}"                          # CPU del task critico (gen_config.py --hi-cpu)
F_TSC_MHZ="${F_TSC_MHZ:-2300}"                 # P0 nominale; constant_tsc -> MPERF conta al rate del TSC

# Frequenza EFFETTIVA media di una CPU, da contatori cumulativi APERF/MPERF.
#   f_media = (dAPERF / dMPERF) * f_TSC
# Le due letture cadono FUORI dalla finestra di misura, come gia' avviene per
# mhz_med e tctl: `rdmsr -p N` forza un IPI verso la CPU N, quindi campionare
# *durante* il run inietterebbe interruzioni nel task critico e cambierebbe la
# condizione sperimentale rispetto ai blocchi 1 e 2. Il regime anomalo a ~3.5x
# dura run interi o centinaia di iterazioni consecutive, quindi una media
# sull'intero run e' piu' che sufficiente a rilevarlo (626 vs 2296 MHz attesi).
amperf() { echo "$(SUDO rdmsr -p "$HI_CPU" -d 0xE8 2>/dev/null || echo 0) $(SUDO rdmsr -p "$HI_CPU" -d 0xE7 2>/dev/null || echo 0)"; }
amperf_mhz() {   # $1..$2 = "aperf mperf" pre, post
    awk -v pre="$1" -v post="$2" -v f="$F_TSC_MHZ" 'BEGIN{
        split(pre,a," "); split(post,b," ");
        da=b[1]-a[1]; dm=b[2]-a[2];
        if (dm <= 0 || da <= 0) { print "NA" } else { printf "%.0f", da/dm*f }
    }'
}
# --- span consegnati al backend --------------------------------------------
# NON usare export_attempts (righe "ZIPKIN EXPORTER" su stderr): quelle contano i
# FALLIMENTI di connessione e vanno a zero proprio quando il collector funziona.
zipkin_spans() {
    curl -sS --max-time 5 "$ZIPKIN_ENDPOINT/metrics" 2>/dev/null \
        | python3 -c 'import json,sys; print(int(json.load(sys.stdin)["counter.zipkin_collector.spans.http"]))' 2>/dev/null \
        || echo NA
}
# Il BatchSpanProcessor fa un flush finale allo shutdown, che arriva DOPO la fine
# del processo: leggere subito attribuirebbe quegli span al run successivo.
# Si attende che il contatore resti fermo per 3 letture consecutive.
zipkin_settle() {
    local prev cur stable=0 i
    prev=$(zipkin_spans); cur=$prev
    for i in $(seq 1 20); do
        sleep 1; cur=$(zipkin_spans)
        if [ "$cur" = "$prev" ]; then
            stable=$((stable + 1)); [ "$stable" -ge 3 ] && break
        else
            stable=0
        fi
        prev=$cur
    done
    echo "$cur"
}

# Percentuale di heap usata dalla JVM del collector, 0-100 (o NA).
# Serve come allarme *precoce*: la guardia di vitalita' scatta solo quando Zipkin
# e' gia' morto, ma un collector che agonizza in GC continuo falsa i tempi del
# braccio Simple+rete (export sincrono) molto prima di smettere di rispondere.
# used = somma delle aree heap; max = la sola area che dichiara un tetto
# (G1 Old Gen porta -Xmx; Eden e Survivor riportano -1 = non definito).
zipkin_heap_pct() {
    curl -sS --max-time 5 "$ZIPKIN_ENDPOINT/prometheus" 2>/dev/null | python3 -c '
import re,sys
used=0.0; mx=0.0
for l in sys.stdin:
    m=re.match(r"jvm_memory_(used|max)_bytes\{area=\"heap\".*?\}\s+([-0-9.E+]+)",l)
    if not m: continue
    v=float(m.group(2))
    if m.group(1)=="used": used+=v
    elif v>mx: mx=v
print(int(used/mx*100) if mx>0 else "NA")
' 2>/dev/null || echo NA
}

mhz_med() { grep "cpu MHz" /proc/cpuinfo | awk '{print $4}' | sort -n | awk '{v[NR]=$1} END{print (NR%2)?v[(NR+1)/2]:int((v[NR/2]+v[NR/2+1])/2)}'; }
tctl()    { [ -r "$HWMON" ] && awk '{printf "%.1f", $1/1000}' "$HWMON" || echo "NA"; }

# --------------------------------------------------------------------------
# Preflight: lo stato di piattaforma NON e' persistente (vedi CLAUDE.md). Senza
# questi controlli il DoE gira ugualmente ma i numeri non valgono nulla, ed e'
# il tipo di errore che ci si accorge solo a campagna finita.
# --------------------------------------------------------------------------
preflight() {
    local fail=0

    if ! SUDO true >/dev/null 2>&1; then
        echo "[preflight] KO  sudo non utilizzabile senza terminale." >&2
        echo "               lancia 'sudo -v' prima, oppure esporta SUDO_ASKPASS." >&2
        fail=1
    fi

    # Shield: se non e' attivo test.sh:31 ricade SILENZIOSAMENTE su un run non
    # isolato. Meglio fermarsi che raccogliere 80 run inutilizzabili.
    if cset shield >/dev/null 2>&1; then
        echo "[preflight] OK  shield attivo: $(cset shield 2>&1 | grep -o 'CPUSPEC([^)]*)' | tail -1)"
    else
        echo "[preflight] KO  nessun cpuset shield attivo." >&2
        echo "               sudo $PROJECT_ROOT/scripts/utils_isolation/isolate_cpus.sh 2,3,6,7" >&2
        fail=1
    fi

    # Frequenza fissa: le scritture MSR non sopravvivono al reboot.
    local cpb
    cpb=$(SUDO rdmsr -p 0 -f 25:25 0xC0010015 2>/dev/null || echo "?")
    if [ "$cpb" = "1" ]; then
        echo "[preflight] OK  boost disabilitato, MHz mediana $(mhz_med), Tctl $(tctl) C"
    else
        echo "[preflight] KO  Core Performance Boost ancora attivo (CpbDis=$cpb)." >&2
        echo "               sudo $PROJECT_ROOT/scripts/utils_isolation/pin_cpu_freq.sh fix 0" >&2
        fail=1
    fi

    # APERF/MPERF: senza questi il regime anomalo a ~3.5x resta non diagnosticabile.
    local am; am=$(amperf)
    if [ "${am%% *}" != "0" ] && [ "${am##* }" != "0" ]; then
        echo "[preflight] OK  APERF/MPERF leggibili su cpu$HI_CPU (f_TSC=$F_TSC_MHZ MHz)"
    else
        echo "[preflight] KO  rdmsr non legge APERF/MPERF su cpu$HI_CPU." >&2
        echo "               sudo modprobe msr; sudo apt install msr-tools" >&2
        fail=1
    fi

    [ "$fail" -eq 0 ] || { echo "[preflight] interrotto." >&2; exit 1; }
}

# Preflight aggiuntivo del blocco 4: il collector deve rispondere E girare fuori
# dallo shield. Se gira sulle CPU isolate misureremmo la contesa fra collector e
# task critico sullo stesso core, che e' un altro esperimento.
preflight_collector() {
    local fail=0
    if curl -sf -o /dev/null --max-time 5 "$ZIPKIN_ENDPOINT/health"; then
        echo "[preflight] OK  collector Zipkin risponde su $ZIPKIN_ENDPOINT"
    else
        echo "[preflight] KO  nessun collector su $ZIPKIN_ENDPOINT." >&2
        echo "               $PROJECT_ROOT/scripts/measurements/zipkin_collector.sh start" >&2
        fail=1
    fi
    if "$HERE/zipkin_collector.sh" check >/dev/null 2>&1; then
        echo "[preflight] OK  collector confinato sulle CPU di housekeeping"
    else
        echo "[preflight] KO  il collector non e' confinato fuori dallo shield." >&2
        "$HERE/zipkin_collector.sh" check >&2 || true
        fail=1
    fi
    [ "$fail" -eq 0 ] || { echo "[preflight] interrotto." >&2; exit 1; }
}

# --- build (o riuso dalla cache) del binario per una combinazione di macro ---
build_bin() {
    local trace=$1 proc=$2 samp=$3 ratio=$4 exporter=$5
    local tag="t${trace}_p${proc}_s${samp}_r${ratio}_e${exporter}"
    local bin_path="$BIN_CACHE/rtapp_${tag}"
    if [ -x "$bin_path" ]; then echo "$bin_path"; return; fi
    echo "[build] $tag" >&2
    (
        cd "$RTAPP_SRC_DIR"
        make clean >/dev/null
        make -j"$(nproc --all)" CPPFLAGS="-DRTAPP_TRACE_LEVEL=${trace} -DRTAPP_PROCESSOR_TYPE=${proc} -DRTAPP_SAMPLER_TYPE=${samp} -DRTAPP_SAMPLER_RATIO=${ratio} -DRTAPP_EXPORTER_TYPE=${exporter}" >/dev/null
    ) >&2
    cp "$RTAPP_SRC_DIR/rt-app" "$bin_path"
    echo "$bin_path"
}

# --- una singola ripetizione ------------------------------------------------
run_one() {
    local block=$1 trace=$2 proc=$3 samp=$4 ratio=$5 exporter=$6 n_lo=$7 rep=$8 dur=$9
    local bin=${10} cfg=${11} cell_dir=${12}
    local run_dir="$cell_dir/run_$(printf '%02d' "$rep")"

    # Ripresa: se questo run e' gia' in data_table non lo rifaccio. Serve a
    # riprendere una campagna interrotta senza rieseguire cio' che e' gia' valido
    # e senza spostare l'ordine interlacciato delle ripetizioni.
    if grep -qF ",$run_dir," "$DATA_TABLE" 2>/dev/null; then
        printf '  rep %2d  %-28s [gia\x27 presente, saltato]\n' "$rep" "$(basename "$cell_dir")"
        return 0
    fi

    local t_pre; t_pre=$(tctl)
    local am_pre; am_pre=$(amperf)
    # Braccio "rete": il contatore del collector e' cumulativo, si legge prima e dopo.
    local zk_pre="NA"
    if [ "$exporter" = "0" ] && [ "$block" = "block4" ]; then
        zk_pre=$(zipkin_spans)
        # Guardia di vitalita': un collector morto (o che agonizza per esaurimento
        # di heap) non da' errore a rt-app, rallenta le risposte e falsa sia i
        # tempi sia i conteggi. E' gia' successo: MEM_MAX_SPANS troppo alto ha
        # esaurito la heap a meta' campagna e i run successivi mostravano un
        # crollo degli span consegnati che sembrava un risultato.
        if [ "$zk_pre" = "NA" ]; then
            echo "[ERRORE] il collector non risponde prima di $run_dir." >&2
            echo "         campagna interrotta: i run successivi non varrebbero nulla." >&2
            exit 1
        fi
        local zk_heap; zk_heap=$(zipkin_heap_pct)
        if [ "$zk_heap" != "NA" ] && [ "$zk_heap" -ge "${MAX_HEAP_PCT:-85}" ]; then
            echo "[ERRORE] heap del collector al ${zk_heap}% prima di $run_dir." >&2
            echo "         soglia ${MAX_HEAP_PCT:-85}%: oltre, il GC falsa i tempi del braccio" >&2
            echo "         sincrono. Ridurre MEM_MAX_SPANS o alzare -Xmx e ripartire." >&2
            exit 1
        fi
    fi
    # NON abortire su exit != 0: con RTAPP_PROCESSOR_TYPE=1 (SimpleSpanProcessor)
    # rt-app termina con SIGABRT *dopo* aver scritto tutti i log. Causa (misurata
    # il 2026-08-28): __shutdown() chiude i thread con pthread_cancel, che in
    # glibc e' implementata come eccezione di forced-unwind; SimpleSpanProcessor::
    # OnEnd e' `noexcept`, e un unwind che attraversa un noexcept chiama
    # std::terminate. Con Simple l'export e' sincrono dentro OnEnd, quindi il
    # thread ci passa molto tempo e il cancel lo colpisce li'. I dati del run sono
    # completi: l'abort e' in fase di shutdown. Si registra exit_code e si valida
    # il run sul CONTENUTO, non sull'exit status.
    local rc=0
    bash "$TEST" "$run_dir" "$bin" "$cfg" >/dev/null || rc=$?
    local am_post; am_post=$(amperf)
    local t_post; t_post=$(tctl)
    local mhz; mhz=$(mhz_med)
    local af; af=$(amperf_mhz "$am_pre" "$am_post")

    # I log di rt-app nascono root: cset shield --exec esegue come root.
    SUDO chown -R "$(id -u):$(id -g)" "$run_dir" >/dev/null 2>&1 || true

    # Un run fallito lascia la cartella e i file vuoti: senza questo controllo
    # sarebbe indistinguibile da uno riuscito (task 0.5, finding (c)).
    local hi_log hi_loops
    hi_log=$(ls "$run_dir"/*HI_task*.log 2>/dev/null | head -1 || true)
    [ -n "$hi_log" ] || hi_log=$(ls "$run_dir"/rtapp-*.log 2>/dev/null | head -1 || true)
    if [ -z "$hi_log" ] || [ ! -s "$hi_log" ]; then
        echo "[ERRORE] run $run_dir non ha prodotto un log valido (exit=$rc), campagna interrotta." >&2
        exit 1
    fi
    hi_loops=$(( $(wc -l < "$hi_log") - 1 ))
    # Soglia al 99 % delle iterazioni nominali: il ritardo di avvio scala col
    # numero di thread (task 0.5: fino a ~77 ms con n_lo=8, cioe' ~8 iterazioni su
    # 2000) e va tollerato, un troncamento da crash precoce no.
    local want=$(( dur * 100 ))          # period = 10 ms -> dur*1000/10
    local floor=$(( want * MIN_LOOP_PCT / 100 ))
    # Un run troncato non e' utilizzabile per le statistiche di timing, ma nel
    # blocco 4 il crollo NON e' un errore di setup: la combinazione Simple+rete
    # corrompe l'heap e uccide il processo all'avvio (visto: "corrupted
    # double-linked list", 3 log LO vuoti, HI a 32 iterazioni su 2000). Fermare
    # la campagna renderebbe impossibile misurare la frequenza di quel guasto,
    # che e' un risultato. Con ALLOW_TRUNCATED=1 il run viene REGISTRATO con il
    # suo hi_loops reale e la campagna prosegue; l'analisi lo riconosce dal
    # numero di iterazioni e lo esclude dalle mediane contandolo come crash.
    # Resta una soglia di guardia: se troppi run sono troncati il problema e'
    # nel setup, non nella cella, e allora ci si ferma davvero.
    if [ "$hi_loops" -lt "$floor" ]; then
        if [ "${ALLOW_TRUNCATED:-0}" != "1" ]; then
            echo "[ERRORE] run $run_dir troncato: $hi_loops iterazioni su $want attese" >&2
            echo "         (exit=$rc) — i dati non sono utilizzabili, campagna interrotta." >&2
            exit 1
        fi
        TRUNCATED=$((TRUNCATED + 1))
        echo "[ATTENZIONE] run $run_dir troncato: $hi_loops/$want iterazioni (exit=$rc)" >&2
        echo "             registrato e saltato nelle statistiche; troncati finora: $TRUNCATED" >&2
        if [ "$TRUNCATED" -gt "$MAX_TRUNCATED" ]; then
            echo "[ERRORE] troppi run troncati ($TRUNCATED): non e' la cella, e' il setup." >&2
            exit 1
        fi
    fi

    # --- span effettivamente arrivati al backend ---------------------------
    # file: si contano le righe di intestazione in spans.log (stessa regex di
    #       analyze_doe.py); poi il file si comprime, sono ~50 MB per run.
    # rete: differenza del contatore cumulativo del collector.
    local spans_del="NA"
    if [ "$exporter" = "2" ] && [ -s "$run_dir/spans.log" ]; then
        spans_del=$(grep -cE "^  name +: " "$run_dir/spans.log" || echo 0)
        gzip -f "$run_dir/spans.log"
    elif [ "$exporter" = "0" ] && [ "$zk_pre" != "NA" ]; then
        local zk_post; zk_post=$(zipkin_settle)
        if [ "$zk_post" != "NA" ]; then spans_del=$(( zk_post - zk_pre )); fi
    fi

    echo "$RUN_COUNTER,$block,$trace,$proc,$samp,$ratio,$exporter,$n_lo,$rep,$dur,$mhz,$af,$t_pre,$t_post,$hi_loops,$run_dir,$rc,$spans_del" >> "$DATA_TABLE"
    echo "$RUN_COUNTER $run_dir" >> "$INDEX_FILE"
    printf '  rep %2d  %-28s loops=%-6s span=%-7s MHz=%-6s aperf=%-6s Tctl %s->%s C%s\n' \
        "$rep" "$(basename "$cell_dir")" "$hi_loops" "$spans_del" "$mhz" "$af" "$t_pre" "$t_post" \
        "$([ "$rc" -ne 0 ] && echo "  [exit=$rc]" || true)"
    RUN_COUNTER=$((RUN_COUNTER + 1))
}

# --- esegue un blocco: build di tutte le celle, poi ripetizioni INTERLEAVED --
# CLAUDE.md: "randomizzare/alternare l'ordine dei run (A/B/A/B, non tutti gli A
# poi tutti i B)". Eseguire 20 ripetizioni della cella 1 e poi 20 della cella 2
# renderebbe la deriva termica lungo la campagna un bias sistematico su una sola
# cella, cioe' confuso col fattore in studio.
run_block() {
    local block=$1 reps=$2 dur=$3; shift 3
    local cells=("$@")
    local -a bins cfgs dirs

    echo "[$block] ${#cells[@]} celle x $reps ripetizioni da ${dur}s"
    local i=0 c
    for c in "${cells[@]}"; do
        read -r trace proc samp ratio exporter n_lo <<< "$c"
        bins[i]=$(build_bin "$trace" "$proc" "$samp" "$ratio" "$exporter")
        dirs[i]="$DOE_ROOT/$block/t${trace}_p${proc}_s${samp}_r${ratio}_e${exporter}_n${n_lo}"
        mkdir -p "${dirs[i]}"
        cfgs[i]="${dirs[i]}/config.json"
        python3 "$GEN" --n-lo "$n_lo" --duration "$dur" --out "${cfgs[i]}" >/dev/null
        i=$((i + 1))
    done

    local rep
    for rep in $(seq 1 "$reps"); do
        for i in "${!cells[@]}"; do
            read -r trace proc samp ratio exporter n_lo <<< "${cells[i]}"
            run_one "$block" "$trace" "$proc" "$samp" "$ratio" "$exporter" "$n_lo" \
                    "$rep" "$dur" "${bins[i]}" "${cfgs[i]}" "${dirs[i]}"
        done
    done
}

# ============================ BLOCK 1 =======================================
# Overhead puro di strumentazione: solo HI, nessun carico di sottofondo, sampler
# AlwaysOn, processor Batch fissi. Fattore: granularita' del tracing.
# 4 celle x 20 rip. = 80 run.        cella = "trace proc samp ratio exporter n_lo"
block1() {
    run_block block1 "${REPS:-20}" "${DURATION:-20}" \
        "0 0 0 0.0 0 0" "1 0 0 0.0 0 0" "2 0 0 0.0 0 0" "3 0 0 0.0 0 0"
}

# ============================ BLOCK 2 =======================================
# Granularita' di campionamento: il ratio sampler di OTel riesce davvero a
# proteggere gli span HI quando HI e LO condividono una sola trace? trace_level=2,
# processor=Batch, carico misto (1 HI + 4 LO). Usa l'exporter ostream
# (exporter=1) per avere gli span contabili su stdout.log: dal Task 3 basta la
# macro, non serve piu' modificare main().
# 6 celle x 25 rip. = 150 run.
block2() {
    run_block block2 "${REPS:-25}" "${DURATION:-20}" \
        "2 0 2 0.0 1 4" "2 0 0 0.0 1 4" "2 0 1 0.1 1 4" \
        "2 0 1 0.3 1 4" "2 0 1 0.5 1 4" "2 0 1 0.7 1 4"
}

# ============================ BLOCK 3 =======================================
# Contesa processor/exporter sotto carico di sottofondo crescente.
# trace_level in {0 (controllo), 3 (volume massimo di span)}, processor in
# {Batch, Simple}, sampler AlwaysOn, n_lo in {0,1,4,8}.
# 12 celle x 15 rip. = 180 run.
block3() {
    local cells=()
    local n
    for n in 0 1 4 8; do
        cells+=("0 0 0 0.0 0 $n" "3 0 0 0.0 0 $n" "3 1 0 0.0 0 $n")
    done
    run_block block3 "${REPS:-15}" "${DURATION:-20}" "${cells[@]}"
}

# ============================ BLOCK 4 =======================================
# Backend reale: rete contro file, per entrambi i processor. Fino al blocco 3
# tutte le misure erano contro un endpoint IRRAGGIUNGIBILE (ECONNREFUSED
# immediato), cioe' il caso piu' favorevole: il costo misurato era quello di
# *tentare* un export, non di consegnarlo.
# Fattori: processor {Batch, Simple} x exporter {file (2), rete Zipkin (0)}.
# Fissi: trace_level=3 (volume massimo, e' li' che le differenze si vedono),
# sampler AlwaysOn, 1 HI + 4 LO.
# 4 celle x 20 rip. = 80 run.
#
# MIN_LOOP_PCT abbassato: nel pilota Simple+rete completa 791 iterazioni su 800
# (98.9 %), cioe' sotto la soglia del 99 % usata altrove. Quel crollo E' il
# risultato, non un run da scartare; la soglia resta solo per intercettare un
# crash precoce che troncherebbe i dati.
block4() {
    [ "$MIN_LOOP_PCT_FROM_ENV" = "0" ] && MIN_LOOP_PCT=50
    ALLOW_TRUNCATED=1
    run_block block4 "${REPS:-20}" "${DURATION:-20}" \
        "3 0 0 0.0 2 4" "3 1 0 0.0 2 4" "3 0 0 0.0 0 4" "3 1 0 0.0 0 4"
}

# ============================ DIAG ==========================================
# Non fa parte del DoE: campagna diagnostica per il regime anomalo a ~3.5x visto
# 1 volta nel blocco 1 e 2 volte nel blocco 2 (2/150 = 1.3 %). Riproduce le
# CONDIZIONI ESATTE del blocco 2 (stesse celle, stessa durata) sulle due celle in
# cui il fenomeno e' comparso, piu' AlwaysOn come controllo, con la colonna
# aperf_mhz ora attiva. Se un run anomalo si ripresenta, aperf_mhz dice subito se
# la CPU era davvero a ~626 MHz (ipotesi frequenza) o a 2296 (ipotesi falsificata).
blockdiag() {
    run_block diag "${REPS:-25}" "${DURATION:-20}" \
        "2 0 2 0.0 1 4" "2 0 1 0.3 1 4" "2 0 0 0.0 1 4"
}

mkdir -p "$BIN_CACHE" "$DOE_ROOT"
# L'header e' cambiato (aggiunta aperf_mhz dopo mhz_med). Un data_table scritto
# con lo schema vecchio non va appeso in silenzio: le righe nuove avrebbero una
# colonna in piu' e l'intero file diventerebbe disallineato senza errori.
if [ -s "$DATA_TABLE" ] && [ "$(head -1 "$DATA_TABLE")" != "$HEADER" ]; then
    if [ "$(head -1 "$DATA_TABLE")" = "${HEADER/,spans_delivered/}" ]; then
        cp "$DATA_TABLE" "${DATA_TABLE}.pre-spans.bak"
        { echo "$HEADER"; sed -n '2,$p' "${DATA_TABLE}.pre-spans.bak" | sed 's/$/,NA/'; } > "$DATA_TABLE"
        echo "[migrazione] data_table: aggiunta colonna spans_delivered=NA alle righe pre-esistenti"
        echo "             backup in $(basename "${DATA_TABLE}.pre-spans.bak")"
    elif [ "$(head -1 "$DATA_TABLE")" = "${HEADER/,exit_code/}" ]; then
        cp "$DATA_TABLE" "${DATA_TABLE}.pre-exitcode.bak"
        { echo "$HEADER"; sed -n '2,$p' "${DATA_TABLE}.pre-exitcode.bak" | sed 's/$/,0/'; } > "$DATA_TABLE"
        echo "[migrazione] data_table: aggiunta colonna exit_code=0 alle righe pre-esistenti"
        echo "             backup in $(basename "${DATA_TABLE}.pre-exitcode.bak")"
    elif [ "$(head -1 "$DATA_TABLE")" = "${HEADER/,aperf_mhz/}" ] || \
         [ "$(head -1 "$DATA_TABLE")" = "${HEADER/,aperf_mhz/}" ]; then
        cp "$DATA_TABLE" "${DATA_TABLE}.pre-aperf.bak"
        awk -F, -v OFS=, 'NR==1{next} {for(i=NF;i>11;i--)$(i+1)=$i; $12="NA"; print}' \
            "${DATA_TABLE}.pre-aperf.bak" > "${DATA_TABLE}.tmp"
        { echo "$HEADER"; cat "${DATA_TABLE}.tmp"; } > "$DATA_TABLE"
        rm -f "${DATA_TABLE}.tmp"
        echo "[migrazione] data_table: aggiunta colonna aperf_mhz=NA alle righe pre-esistenti"
        echo "             backup in $(basename "${DATA_TABLE}.pre-aperf.bak")"
    else
        echo "[ERRORE] header di $DATA_TABLE non riconosciuto, non lo tocco." >&2
        exit 1
    fi
fi
[ -s "$DATA_TABLE" ] || echo "$HEADER" > "$DATA_TABLE"
touch "$INDEX_FILE"
RUN_COUNTER=$(( $(wc -l < "$INDEX_FILE") + 1 ))

case "${1:-}" in
    block1|block2|block3) preflight; "$1" ;;
    block4) preflight; preflight_collector; block4 ;;
    diag) preflight; blockdiag ;;
    *) echo "Usage: $0 <block1|block2|block3|block4|diag>"; exit 1 ;;
esac

echo "[run_doe] '$1' completato. Risultati in: $DOE_ROOT"
