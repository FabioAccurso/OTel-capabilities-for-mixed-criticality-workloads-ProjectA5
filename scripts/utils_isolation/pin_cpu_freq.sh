#!/usr/bin/env bash
#
# pin_cpu_freq.sh — fissa la frequenza CPU su AMD family 17h/19h scrivendo i MSR.
#
# Perche' esiste: il kernel 6.12.79-rt17 di questa macchina e' compilato con
#   # CONFIG_CPU_FREQ is not set
#   # CONFIG_CPU_IDLE is not set
# quindi /sys/.../cpufreq NON esiste e cpupower/governor non sono utilizzabili.
# Le P-state sono gestite dal firmware (SMU). Questi MSR sono l'unica leva
# rimasta dallo user space.
#
# MSR usati (AMD PPR family 17h):
#   0xC0010015 HWCR        bit 25 = CpbDis  (1 = Core Performance Boost disabilitato)
#   0xC0010062 PStateCtl   bit 2:0 = P-state richiesta
#   0xC0010063 PStateStat  bit 2:0 = P-state corrente
#   0xC0010064+i PStateDef bit 63 = En, 13:8 = CpuDfsId, 7:0 = CpuFid
#                          freq_MHz = 200 * CpuFid / CpuDfsId
#
# ATTENZIONE: le scritture MSR valgono fino al reboot e non toccano voltaggi
# ne' limiti: 'fix' puo' solo ABBASSARE la frequenza, mai alzarla. Su un
# ultrabook 15 W (ASUS UX431DA) l'SMU puo' comunque scendere sotto la P-state
# richiesta per limiti termici/di potenza: usare 'status' durante i run per
# verificare che la frequenza sia davvero stabile.
#
# Uso:
#   sudo ./pin_cpu_freq.sh status        # legge e basta, non modifica nulla
#   sudo ./pin_cpu_freq.sh fix [idx]     # disabilita il boost + forza P-state idx (default 0)
#   sudo ./pin_cpu_freq.sh reset         # riabilita il boost + torna a P0

set -euo pipefail

MSR_HWCR=0xC0010015
MSR_PSTATE_CTL=0xC0010062
MSR_PSTATE_STAT=0xC0010063
MSR_PSTATE_DEF=0xC0010064   # + i, i = 0..7
CPB_BIT=25

cpus() { ls -d /sys/devices/system/cpu/cpu[0-9]* | sed 's#.*/cpu##' | sort -n; }

require() {
	[ "$(id -u)" -eq 0 ] || { echo "ERRORE: serve root (sudo $0 $*)" >&2; exit 1; }
	command -v rdmsr >/dev/null || { echo "ERRORE: manca rdmsr. Installa: sudo apt install msr-tools" >&2; exit 1; }
	command -v wrmsr >/dev/null || { echo "ERRORE: manca wrmsr. Installa: sudo apt install msr-tools" >&2; exit 1; }
	modprobe msr 2>/dev/null || true
	[ -e /dev/cpu/0/msr ] || { echo "ERRORE: /dev/cpu/0/msr assente (modulo msr non caricabile)" >&2; exit 1; }
	case "$(grep -m1 vendor_id /proc/cpuinfo)" in
		*AuthenticAMD*) ;;
		*) echo "ERRORE: script scritto per AMD family 17h/19h, questa CPU non e' AMD" >&2; exit 1 ;;
	esac
}

# rd <cpu> <msr>  -> valore intero (decimale, con segno se bit 63 e' 1)
rd() { echo $(( 0x$(rdmsr -p "$1" "$2") )); }

decode_pstates() {
	echo "P-state definite (MSR ${MSR_PSTATE_DEF}+i, lette su cpu0):"
	local i v en fid dfs
	for i in 0 1 2 3 4 5 6 7; do
		v=$(rd 0 $(printf '0x%X' $(( MSR_PSTATE_DEF + i ))))
		en=$(( (v >> 63) & 1 ))
		[ "$en" -eq 1 ] || continue
		fid=$(( v & 0xFF ))
		dfs=$(( (v >> 8) & 0x3F ))
		[ "$dfs" -ne 0 ] || continue
		printf '  P%d : CpuFid=%-3d CpuDfsId=%-2d -> %d MHz\n' "$i" "$fid" "$dfs" $(( 200 * fid / dfs ))
	done
}

status() {
	require status
	decode_pstates
	echo
	local hwcr cpb
	hwcr=$(rd 0 $MSR_HWCR)
	cpb=$(( (hwcr >> CPB_BIT) & 1 ))
	echo "HWCR.CpbDis = $cpb  ($( [ "$cpb" -eq 1 ] && echo 'boost DISABILITATO' || echo 'boost attivo' ))"
	echo
	printf '%-5s %-10s %-12s\n' cpu P-state 'MHz (live)'
	local c st mhz
	for c in $(cpus); do
		st=$(( $(rd "$c" $MSR_PSTATE_STAT) & 0x7 ))
		mhz=$(awk -v n="$c" '/cpu MHz/{if (i++ == n) {printf "%.0f", $4; exit}}' /proc/cpuinfo)
		printf '%-5s P%-9s %-12s\n' "$c" "$st" "$mhz"
	done
	echo
	local t
	t=$(cat /sys/class/hwmon/hwmon*/temp1_input 2>/dev/null | head -1)
	for h in /sys/class/hwmon/hwmon*; do
		[ "$(cat "$h/name" 2>/dev/null)" = k10temp ] && t=$(cat "$h/temp1_input")
	done
	[ -n "$t" ] && echo "Tctl = $(( t / 1000 )) C"
}

fix() {
	require fix
	local idx="${1:-0}"
	local c hwcr new
	for c in $(cpus); do
		hwcr=$(rd "$c" $MSR_HWCR)
		new=$(( hwcr | (1 << CPB_BIT) ))
		wrmsr -p "$c" $MSR_HWCR "$(printf '0x%X' "$new")"
		wrmsr -p "$c" $MSR_PSTATE_CTL "$(printf '0x%X' "$idx")"
	done
	echo "Boost disabilitato e P-state forzata a P$idx su tutte le CPU."
	echo
	status
}

reset_boost() {
	require reset
	local c hwcr new
	for c in $(cpus); do
		hwcr=$(rd "$c" $MSR_HWCR)
		new=$(( hwcr & ~(1 << CPB_BIT) ))
		wrmsr -p "$c" $MSR_HWCR "$(printf '0x%X' "$new")"
		wrmsr -p "$c" $MSR_PSTATE_CTL 0x0
	done
	echo "Boost riabilitato, P-state richiesta = P0 (comportamento di default)."
	echo
	status
}

case "${1:-status}" in
	status) status ;;
	fix)    fix "${2:-0}" ;;
	reset)  reset_boost ;;
	*)      sed -n '3,30p' "$0"; exit 1 ;;
esac
