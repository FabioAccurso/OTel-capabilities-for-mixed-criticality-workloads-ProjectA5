#!/usr/bin/env bash
# Shield a set of CPUs from the rest of the system for RT experiments.
# Works WITHOUT rebooting (uses cset shield / cgroups). Complementare, NON alternativo,
# ai parametri di boot: la cmdline in uso su questa macchina e'
#   isolcpus=managed_irq,domain,2,3,6,7 nohz_full=2,3,6,7 rcu_nocbs=2,3,6,7 irqaffinity=0,1,4,5
# I due meccanismi coprono cose diverse:
#   - `managed_irq` silenzia gli IRQ managed (code NVMe): restano affini alla loro CPU ma
#     blk-mq non le usa piu', quindi il contatore in /proc/interrupts va a zero. NON cambia
#     smp_affinity_list: la verifica giusta e' il contatore, non l'affinity;
#   - `irqaffinity=` fissa la maschera di DEFAULT all'allocazione degli IRQ. Serve perche'
#     isolcpus= da solo lascia /proc/irq/default_smp_affinity a ff (verificato 2026-08-27);
#   - questo script sposta a runtime gli IRQ che i driver hanno riassegnato dopo
#     l'allocazione, scavalcando il default. Resta la rete di sicurezza.
#
# Usage: sudo ./isolate_cpus.sh [cpu_list]   (default: 2,3,6,7)
#        cpu_list accetta sia "2,3" sia "2-3" sia "0-2,5"
#
# ATTENZIONE alla topologia SMT: isolare una CPU lasciando fuori il suo sibling e'
# controproducente (vedi il controllo piu' sotto). Su questa macchina i core fisici sono
# cpu0,1 / cpu2,3 / cpu4,5 / cpu6,7: si isolano a coppie.
#
# Task 0.4: corretti 3 difetti della versione originale (vedi 0-explore/0.4/NOTES.md)
#   1. usava `nproc`, che e' affinity-aware: DOPO `cset shield` la shell e' gia' confinata
#      nel cpuset "system", quindi nproc tornava 6 invece di 8 e l'elenco delle CPU non
#      isolate veniva calcolato sbagliato (0,1,4,5 invece di 0,1,4,5,6,7);
#   2. `grep -vFf` senza -x fa match di SOTTOSTRINGA: isolando la CPU 1 su una macchina
#      con >=10 CPU avrebbe escluso anche 10..19;
#   3. con la sintassi a intervallo ("2-3", valida per cset) nessun pattern faceva match e
#      le CPU isolate restavano nell'elenco -> gli IRQ NON venivano spostati, in silenzio.
# Aggiunto inoltre il backup delle affinita' IRQ, che reset_isolation.sh ora ripristina.
set -euo pipefail
ISO_CPUS="${1:-2,3,6,7}"
STATE_DIR=/var/tmp/rtapp-isolation
IRQ_BAK="${STATE_DIR}/irq_affinity.bak"

if [[ $EUID -ne 0 ]]; then
  echo "Run as root (sudo)." >&2
  exit 1
fi

if ! command -v cset >/dev/null 2>&1; then
  echo "cpuset-tools not found. Install with: sudo apt install cpuset" >&2
  exit 1
fi

# "0-2,5" -> righe "0","1","2","5"
expand_cpus() {
  local part a b i
  for part in ${1//,/ }; do
    if [[ $part == *-* ]]; then
      a=${part%%-*}; b=${part##*-}
      for ((i = a; i <= b; i++)); do echo "$i"; done
    else
      echo "$part"
    fi
  done
}

# nproc --all, non nproc: il conteggio non deve dipendere dall'affinita' del chiamante
ALL_CPUS=$(nproc --all)
NON_ISO=$(seq 0 $((ALL_CPUS - 1)) | grep -vxFf <(expand_cpus "$ISO_CPUS") | paste -sd, -)

if [[ -z "$NON_ISO" ]]; then
  echo "ERRORE: '${ISO_CPUS}' isolerebbe tutte le ${ALL_CPUS} CPU, non resta nulla per il sistema." >&2
  exit 1
fi
while read -r c; do
  if (( c < 0 || c >= ALL_CPUS )); then
    echo "ERRORE: CPU ${c} fuori range (0-$((ALL_CPUS - 1)))." >&2
    exit 1
  fi
done < <(expand_cpus "$ISO_CPUS")

echo "[isolate] CPU isolate: ${ISO_CPUS}   CPU di sistema: ${NON_ISO}   (totali: ${ALL_CPUS})"

mapfile -t iso_arr < <(expand_cpus "$ISO_CPUS")

# --- controllo topologia SMT ------------------------------------------------
# I thread SMT di uno stesso core fisico condividono unita' di esecuzione, L1 e L2.
# Isolare cpuX lasciando il sibling cpuY al sistema significa che il carico su cpuY ruba
# risorse a cpuX: l'isolamento diventa in buona parte fittizio.
smt_warn=""
for c in "${iso_arr[@]}"; do
  sib_file="/sys/devices/system/cpu/cpu${c}/topology/thread_siblings_list"
  [[ -r $sib_file ]] || continue
  while read -r sib; do
    [[ $sib == "$c" ]] && continue
    found=0
    for i in "${iso_arr[@]}"; do
      [[ $i == "$sib" ]] && { found=1; break; }
    done
    (( found )) || smt_warn+="[isolate]   cpu${c} e' isolata ma il suo sibling SMT cpu${sib} no"$'\n'
  done < <(expand_cpus "$(cat "$sib_file")")
done
if [[ -n $smt_warn ]]; then
  echo "[isolate] ATTENZIONE: isolamento parziale di core fisici:"
  printf '%s' "$smt_warn"
  echo "[isolate]   Il carico di sistema sul sibling ruba unita' di esecuzione alla CPU"
  echo "[isolate]   isolata. Isola i core interi (qui: coppie 0,1 / 2,3 / 4,5 / 6,7)."
fi

# Backup delle affinita' IRQ, una sola volta: se lo script viene rilanciato a shield
# gia' attivo non deve salvare lo stato gia' modificato.
mkdir -p "$STATE_DIR"
if [[ ! -f "$IRQ_BAK" ]]; then
  for f in /proc/irq/*/smp_affinity_list; do
    printf '%s\t%s\n' "$f" "$(cat "$f")"
  done > "$IRQ_BAK"
  echo "[isolate] affinita' IRQ originali salvate in ${IRQ_BAK}"
fi

echo "[isolate] shielding CPUs ${ISO_CPUS} (system + kthreads moved off)..."
cset shield --cpu="${ISO_CPUS}" --kthread=on

echo "[isolate] moving all IRQ affinities off the isolated CPUs..."
ok=0; ko=0
for f in /proc/irq/*/smp_affinity_list; do
  if echo "${NON_ISO}" > "$f" 2>/dev/null; then ok=$((ok + 1)); else ko=$((ko + 1)); fi
done
echo "[isolate]   IRQ riassegnati: ${ok}   rifiutati dal kernel: ${ko}"
if (( ko > 0 )); then
  echo "[isolate]   (i rifiuti sono IRQ 'managed' per-CPU, tipicamente code NVMe: il kernel"
  echo "[isolate]    non ne consente la migrazione. Con isolcpus=managed_irq restano affini"
  echo "[isolate]    alla loro CPU ma smettono di sparare: verificare con /proc/interrupts.)"
fi

# --- verifica ---------------------------------------------------------------
echo "[isolate] verifica:"
# NB: niente pipeline che possa uscire !=0 dentro $( ), altrimenti pipefail + set -e
# abortiscono lo script proprio mentre verifica (successo del test = grep senza match).
residuo=0
for f in /proc/irq/*/smp_affinity_list; do
  mapfile -t cur_arr < <(expand_cpus "$(cat "$f")")
  for c in "${cur_arr[@]}"; do
    for i in "${iso_arr[@]}"; do
      if [[ "$c" == "$i" ]]; then residuo=$((residuo + 1)); break 2; fi
    done
  done
done
echo "[isolate]   IRQ che toccano ancora ${ISO_CPUS}: ${residuo}"
echo "[isolate]   (i kthread per-CPU come migration/N, ksoftirqd/N, ktimers/N NON sono"
echo "[isolate]    spostabili: e' un limite di cset, non un errore)"

echo "[isolate] done. Launch experiments INSIDE the shield with:"
echo "  sudo cset shield --exec -- <command...>"
echo "To undo everything: sudo ./reset_isolation.sh"
