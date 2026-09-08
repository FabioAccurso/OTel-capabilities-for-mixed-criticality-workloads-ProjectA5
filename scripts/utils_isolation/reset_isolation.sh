#!/usr/bin/env bash
# Undo isolate_cpus.sh
#
# Task 0.4: la versione originale faceva solo `cset shield --reset` e NON ripristinava le
# affinita' IRQ, che restavano spostate fino al reboot. Ora vengono rilette dal backup
# scritto da isolate_cpus.sh.
set -euo pipefail
STATE_DIR=/var/tmp/rtapp-isolation
IRQ_BAK="${STATE_DIR}/irq_affinity.bak"

if [[ $EUID -ne 0 ]]; then
  echo "Run as root (sudo)." >&2
  exit 1
fi

cset shield --reset
echo "[isolate] shield removed."

if [[ -f "$IRQ_BAK" ]]; then
  ok=0; ko=0
  while IFS=$'\t' read -r f v; do
    [[ -w "$f" ]] || { ko=$((ko + 1)); continue; }
    if echo "$v" > "$f" 2>/dev/null; then ok=$((ok + 1)); else ko=$((ko + 1)); fi
  done < "$IRQ_BAK"
  echo "[isolate] affinita' IRQ ripristinate: ${ok}   non ripristinabili: ${ko}"
  rm -f "$IRQ_BAK"
else
  echo "[isolate] nessun backup IRQ in ${IRQ_BAK}: affinita' IRQ lasciate come sono."
fi

# cset monta una gerarchia cgroup v1 in /cpusets e cosi' facendo sottrae il controller
# 'cpuset' a cgroup v2. Smontarla lo restituisce alla gerarchia unificata.
if mountpoint -q /cpusets 2>/dev/null; then
  if umount /cpusets 2>/dev/null; then
    echo "[isolate] /cpusets smontato: controller cpuset restituito a cgroup v2."
  else
    echo "[isolate] /cpusets ancora montato (in uso): il controller cpuset resta su cgroup v1"
    echo "[isolate] fino al reboot. Non e' un problema per rt-app."
  fi
fi
