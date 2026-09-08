#!/bin/bash
# Latenza di CONSEGNA degli span: quanto tempo passa fra la chiusura di uno span e
# il momento in cui arriva al backend. E' il compromesso che Batch paga in cambio del
# disaccoppiamento dal task real-time, e che Simple non paga affatto.
#
# Strumento: span_probe.py, un backend Zipkin-compatibile che marca il tempo di
# arrivo di ogni POST. NON si puo' usare l'exporter ostream per questa misura:
# OStreamSpanExporter::Export() scrive ma NON fa flush (il flush sta in ForceFlush),
# quindi i byte escono quando si riempie il buffer di libstdc++ e non quando lo span
# viene consegnato -- l'istante osservato sarebbe un artefatto del buffering.
#
# L'endpoint si ridirige con OTEL_EXPORTER_ZIPKIN_ENDPOINT, che rt-app onora perche'
# usa ZipkinExporterOptions di default. Va passato con `env` DENTRO cset shield --exec:
# sudo azzera l'ambiente, quindi esportarlo nella shell non basterebbe.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
OUT="$ROOT/2-DoE/latency"
PORT="${PORT:-9412}"
REPS="${REPS:-5}"
DURATION="${DURATION:-20}"

SUDO() { if [ "$(id -u)" -eq 0 ]; then "$@"; elif [ -n "${SUDO_ASKPASS:-}" ]; then sudo -A "$@"; else sudo "$@"; fi; }

SUDO cset shield >/dev/null 2>&1 || { echo "[preflight] KO shield non attivo" >&2; exit 1; }
echo "[preflight] OK shield attivo"

run_one() {
    local proc=$1 nlo=$2 rep=$3
    local cell="p${proc}_n${nlo}"
    local d="$OUT/$cell/run_$(printf %02d "$rep")"
    [ -s "$d/spans.tsv" ] && { echo "  skip $cell rep $rep (gia' fatto)"; return 0; }
    mkdir -p "$d"
    python3 "$HERE/gen_config.py" --n-lo "$nlo" --duration "$DURATION" --out "$d/config.json" >/dev/null
    python3 - "$d" <<'PY'
import json,sys
p=sys.argv[1]+"/config.json"; c=json.load(open(p)); c['global']['logdir']=sys.argv[1]
json.dump(c,open(p,'w'),indent=2)
PY
    setsid python3 "$HERE/span_probe.py" "$d/spans.tsv" "$PORT" >"$d/probe.log" 2>&1 </dev/null &
    local ppid=$!
    local ok=0 i
    for i in $(seq 1 50); do curl -sf -o /dev/null --max-time 1 "http://127.0.0.1:$PORT/health" && { ok=1; break; }; sleep 0.2; done
    [ "$ok" = 1 ] || { echo "[ERRORE] probe non risponde" >&2; kill $ppid 2>/dev/null; return 1; }

    SUDO cset shield --exec -- env "OTEL_EXPORTER_ZIPKIN_ENDPOINT=http://127.0.0.1:$PORT/api/v2/spans" \
        "$ROOT/bin/rtapp_t3_p${proc}_s0_r0.0_e0" "$d/config.json" >"$d/stdout.log" 2>"$d/stderr.log"
    local rc=$?
    sleep 3                      # il flush finale del Batch arriva dopo l'uscita del processo
    kill $ppid 2>/dev/null; wait $ppid 2>/dev/null
    SUDO chown -R "$(id -u):$(id -g)" "$d"

    local nsp err
    nsp=$(( $(wc -l < "$d/spans.tsv") - 1 ))
    err=$(grep -c "ZIPKIN EXPORTER" "$d/stderr.log" 2>/dev/null); err=${err:-0}
    printf "  %-8s rep %2d  span=%-7d errori_export=%-4s exit=%s\n" "$cell" "$rep" "$nsp" "$err" "$rc"
    [ "$nsp" -gt 0 ] || { echo "[ERRORE] nessuno span raccolto" >&2; return 1; }
}

echo "[latency] processor {Batch,Simple} x n_lo {0,1,4} x $REPS rip. da ${DURATION}s"
for rep in $(seq 1 "$REPS"); do
    for nlo in 0 1 4; do
        for proc in 0 1; do
            run_one "$proc" "$nlo" "$rep" || exit 1
        done
    done
done
echo "[latency] completato. Dati in $OUT"
