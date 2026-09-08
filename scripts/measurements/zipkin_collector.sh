#!/usr/bin/env bash
# Collector Zipkin reale per il blocco 4 del DoE (braccio "net").
#
#   zipkin_collector.sh start|stop|status|count|check
#
# VINCOLO D'ORDINE, non negoziabile:
#   lo shield (isolate_cpus.sh) va attivato PRIMA di avviare docker.
# Le unit docker.service/containerd.service hanno "Delegate=yes": se partono per prime,
# systemd delega il controller cpuset alla gerarchia cgroup v2 e il controller resta
# occupato, per cui `cset` (strumento cgroup v1) non riesce piu' a montare /cpusets e
# `isolate_cpus.sh` fallisce. Con lo shield gia' attivo il controller e' tenuto dalla
# gerarchia v1, systemd non puo' delegarlo e docker parte senza conflitti.
# Per lo stesso motivo docker e' stato DISABILITATO all'avvio automatico
# (systemctl disable docker.service docker.socket containerd.service).
#
# Niente --cpuset-cpus sul container: userebbe il controller v2 e romperebbe lo shield.
# Il confinamento sulle CPU di housekeeping arriva dal cpuset "system" dello shield,
# che il comando `check` verifica esplicitamente.

set -euo pipefail

NAME="${ZIPKIN_NAME:-zipkin}"
IMAGE="${ZIPKIN_IMAGE:-openzipkin/zipkin:latest}"
ENDPOINT="${ZIPKIN_ENDPOINT:-http://localhost:9411}"
HOUSEKEEPING="${HOUSEKEEPING_CPUS:-0-1,4-5}"

SUDO() {
    if [ "$(id -u)" -eq 0 ]; then "$@"
    elif [ -n "${SUDO_ASKPASS:-}" ]; then sudo -A "$@"
    else sudo "$@"
    fi
}

start() {
    SUDO systemctl start containerd.service docker.service
    SUDO docker rm -f "$NAME" >/dev/null 2>&1 || true
    SUDO docker run -d --name "$NAME" --network host \
        -e MEM_MAX_SPANS="${MEM_MAX_SPANS:-100000}" \
        -e JAVA_OPTS="${JAVA_OPTS:--Xms512m -Xmx2g}" \
        "$IMAGE" >/dev/null
    for _ in $(seq 1 60); do
        if curl -sf -o /dev/null "$ENDPOINT/health"; then echo "[zipkin] pronto su $ENDPOINT"; check; return 0; fi
        sleep 1
    done
    echo "[zipkin] ERRORE: non risponde su $ENDPOINT/health" >&2; exit 1
}

stop() { SUDO docker rm -f "$NAME" >/dev/null 2>&1 || true; SUDO systemctl stop docker.service docker.socket containerd.service || true; echo "[zipkin] fermato"; }

status() { SUDO docker ps --filter "name=^${NAME}$" --format '{{.Names}}  {{.Status}}' || true; }

# Preflight del blocco 4: il collector deve rispondere E girare fuori dallo shield.
check() {
    curl -sf -o /dev/null "$ENDPOINT/health" || { echo "[zipkin] ERRORE: non risponde" >&2; exit 1; }
    local pid aff
    pid=$(SUDO docker inspect -f '{{.State.Pid}}' "$NAME")
    aff=$(SUDO bash -c "cat /proc/$pid/task/*/status | grep Cpus_allowed_list | sort -u | awk '{print \$2}'")
    if [ "$(echo "$aff" | wc -l)" -ne 1 ] || [ "$aff" != "$HOUSEKEEPING" ]; then
        echo "[zipkin] ERRORE: thread su CPU '$aff', attese '$HOUSEKEEPING'" >&2
        echo "[zipkin] lo shield e' attivo? il collector non deve girare sulle CPU isolate." >&2
        exit 1
    fi
    echo "[zipkin] OK: risponde, thread confinati su $aff"
}

# Contatori cumulativi lato collector: "span POST dropped".
# Si leggono prima e dopo ogni run e si sottraggono -- non serve azzerare lo stato.
count() {
    curl -sS "$ENDPOINT/metrics" | python3 -c '
import json,sys
d=json.load(sys.stdin)
print(int(d["counter.zipkin_collector.spans.http"]),
      int(d["counter.zipkin_collector.messages.http"]),
      int(d["counter.zipkin_collector.spans_dropped.http"]))'
}

case "${1:-status}" in
    start) start ;; stop) stop ;; status) status ;; check) check ;; count) count ;;
    *) echo "uso: $0 start|stop|status|check|count" >&2; exit 1 ;;
esac
