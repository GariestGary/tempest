#!/usr/bin/env bash
# Run only in the disposable Ubuntu guest. Default is deliberately read-only.
set -Eeuo pipefail
umask 077

NS=tempest-ns-lab
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BACKEND="$ROOT/backend"
PYTHON="$BACKEND/.venv/bin/python"
MODE="${1:---dry-run}"
if [[ "$MODE" == --dry-run ]]; then
  cat <<'EOF'
DRY RUN: no state-changing commands executed.
Plan: add one empty inet nft table, one IPv4 fwmark rule, and one blackhole
route for 198.18.0.0/15 inside tempest-ns-lab; capture twice, verify all three
are unproven and rejected by the registry, then delete exact selectors.
On the disposable Ubuntu VM only, after checking snapshot, independent
hypervisor console and isolated network, rerun with --execute. A typed
confirmation is also required. Never run on TORNADO/TYPHOON/VORTEX or host netns.
EOF
  exit 0
fi
[[ "$MODE" == --execute ]] || { echo "Usage: $0 [--dry-run|--execute]" >&2; exit 2; }
[[ "$(uname -s)" == Linux && $EUID -eq 0 ]] || { echo "Linux disposable guest and operator privileges required" >&2; exit 2; }
[[ "${TEMPEST_VM_ID:-}" == tempest-lab-ubuntu && -n "${TEMPEST_SNAPSHOT_ID:-}" \
   && "${TEMPEST_SNAPSHOT_VERIFIED:-}" == YES && "${TEMPEST_CONSOLE_VERIFIED:-}" == YES \
   && "${TEMPEST_ISOLATED_NETWORK_VERIFIED:-}" == YES ]] || {
  echo "Set VM_ID=tempest-lab-ubuntu, SNAPSHOT_ID, and VERIFIED=YES attestations after out-of-band checks" >&2; exit 2;
}
[[ -x "$PYTHON" && -e "/run/netns/$NS" ]] || { echo "Python venv or pre-created target namespace unavailable" >&2; exit 2; }
for bin in ip nft stat sha256sum diff mktemp od cmp; do command -v "$bin" >/dev/null || { echo "Missing command: $bin" >&2; exit 2; }; done

BOOT_ID="$(cat /proc/sys/kernel/random/boot_id)"
TARGET_ID="$(stat -Lc '%d:%i' "/run/netns/$NS")"
PID1_ID="$(stat -Lc '%d:%i' /proc/1/ns/net)"
SHELL_ID="$(stat -Lc '%d:%i' /proc/self/ns/net)"
[[ "$TARGET_ID" != "$PID1_ID" ]] || { echo "Target namespace is PID 1 namespace" >&2; exit 2; }
[[ "$SHELL_ID" == "$PID1_ID" ]] || { echo "Operator shell is not in PID 1 namespace; refusing host comparison" >&2; exit 2; }
DEV="${TARGET_ID%%:*}"; INO="${TARGET_ID##*:}"
SUFFIX="$(od -An -N4 -tx1 /dev/urandom | tr -d ' \n')"
TABLE="tempest_foreign_$SUFFIX"
PRIORITY="$((32000 + 16#${SUFFIX:0:3} % 700))"
MARK="0x7e${SUFFIX:0:6}"
ROUTE_TABLE="$((20000 + 16#${SUFFIX:2:4} % 20000))"
PREFIX=198.18.0.0/15
EVIDENCE="$(mktemp -d "${TMPDIR:-/tmp}/tempest-foreign-${SUFFIX}.XXXXXX")"
chmod 700 "$EVIDENCE"
CREATED_TABLE=0 CREATED_RULE=0 CREATED_ROUTE=0
target() { ip netns exec "$NS" "$@"; }

# Check exact selectors in either the prepared namespace or PID 1 namespace.
check_selectors() {
  local where="$1" expected="$2" rules routes nftout
  if [[ "$where" == target ]]; then
    rules="$(target ip -4 -j rule)"; routes="$(target ip -4 -j route show table all)"; nftout="$(target nft -j list ruleset)"
  else
    rules="$(ip -4 -j rule)"; routes="$(ip -4 -j route show table all)"; nftout="$(nft -j list ruleset)"
  fi
  RULES="$rules" ROUTES="$routes" NFT="$nftout" EXPECT="$expected" TABLE="$TABLE" PRIORITY="$PRIORITY" MARK="$MARK" ROUTE_TABLE="$ROUTE_TABLE" PREFIX="$PREFIX" \
    "$PYTHON" -c 'import json,os,sys
r=json.loads(os.environ["RULES"]); q=json.loads(os.environ["ROUTES"]); n=json.loads(os.environ["NFT"])
t=sum((x.get("family"),x.get("name"))==("inet",os.environ["TABLE"]) for e in n.get("nftables",[]) for x in [e.get("table",{})] if x)
p=int(os.environ["PRIORITY"]); m=int(os.environ["MARK"],0); tab=int(os.environ["ROUTE_TABLE"]); dst=os.environ["PREFIX"]
rule=sum(int(x.get("priority",x.get("pref",-1)))==p and int(str(x.get("fwmark","0")),0)==m and int(x.get("table",-1))==tab for x in r)
route=sum(x.get("dst")==dst and int(x.get("table",-1))==tab and x.get("type")=="blackhole" for x in q)
if any(x>1 for x in (t,rule,route)): sys.exit("ambiguous duplicate trial selector")
found=(t,rule,route); want=tuple(c=="1" for c in os.environ["EXPECT"])
if found!=want: sys.exit("selector conflict or unexpected trial state: "+repr(found))' 2>/dev/null
}
preflight() {
  local expect="${CREATED_TABLE}${CREATED_RULE}${CREATED_ROUTE}"
  [[ "$(cat /proc/sys/kernel/random/boot_id)" == "$BOOT_ID" ]] || { echo "Boot identity changed" >&2; return 1; }
  [[ "$(stat -Lc '%d:%i' "/run/netns/$NS")" == "$TARGET_ID" ]] || { echo "Target namespace identity changed" >&2; return 1; }
  [[ "$(stat -Lc '%d:%i' /proc/1/ns/net)" == "$PID1_ID" \
     && "$(stat -Lc '%d:%i' /proc/self/ns/net)" == "$PID1_ID" \
     && "$TARGET_ID" != "$PID1_ID" ]] || { echo "PID 1/operator namespace identity changed or overlaps target" >&2; return 1; }
  [[ -x "$PYTHON" ]] && command -v ip >/dev/null && command -v nft >/dev/null || { echo "Collector or exact cleanup tool unavailable" >&2; return 1; }
  local links routes
  links="$(target ip -j link)"; routes="$(target ip -4 -j route show table all)"
  LINKS="$links" ROUTES="$routes" "$PYTHON" -c 'import json,os,sys
links=json.loads(os.environ["LINKS"]); routes=json.loads(os.environ["ROUTES"])
if {x.get("ifname") for x in links}!={"lo"}: sys.exit("lab namespace contains a non-loopback link")
if any(x.get("dst")=="default" for x in routes): sys.exit("lab namespace has a default route")' || return 1
  check_selectors target "$expect" && check_selectors host 000
}

printf 'VM=%s snapshot=%s boot=%s target=%s pid1=%s\n' "$TEMPEST_VM_ID" "$TEMPEST_SNAPSHOT_ID" "$BOOT_ID" "$TARGET_ID" "$PID1_ID"
printf 'Selectors: inet table=%s; IPv4 rule priority=%s mark=%s table=%s; blackhole=%s\n' "$TABLE" "$PRIORITY" "$MARK" "$ROUTE_TABLE" "$PREFIX"
confirm="RUN $NS $SUFFIX"; echo "Type exactly '$confirm' to continue:"; IFS= read -r answer
[[ "$answer" == "$confirm" ]] || { echo "Confirmation mismatch" >&2; exit 2; }

cleanup() {
  local rc=$?; trap - EXIT
  if (( CREATED_ROUTE )); then
    if ! preflight || ! target ip -4 route del blackhole "$PREFIX" table "$ROUTE_TABLE"; then echo "STOP: exact route cleanup failed; restore snapshot and do not guess further deletes" >&2; exit 1; fi
    CREATED_ROUTE=0
  fi
  if (( CREATED_RULE )); then
    if ! preflight || ! target ip -4 rule del priority "$PRIORITY" fwmark "$MARK" lookup "$ROUTE_TABLE"; then echo "STOP: exact rule cleanup failed; restore snapshot and do not guess further deletes" >&2; exit 1; fi
    CREATED_RULE=0
  fi
  if (( CREATED_TABLE )); then
    if ! preflight || ! target nft delete table inet "$TABLE"; then echo "STOP: exact nft cleanup failed; restore snapshot and do not guess further deletes" >&2; exit 1; fi
    CREATED_TABLE=0
  fi
  exit "$rc"
}
trap cleanup EXIT

capture() {
  local dest="$1"; preflight
  target env PYTHONPATH="$BACKEND" "$PYTHON" -m app.network_agent.linux_inventory_capture \
    --name "$NS" --boot-id "$BOOT_ID" --netns-device "$DEV" --netns-inode "$INO" --output-dir "$dest"
}
host_capture() {
  local dest="$1"
  mkdir -m 700 "$dest"
  ip -j link >"$dest/link.json"
  ip -4 -j rule >"$dest/rule4.json"; ip -6 -j rule >"$dest/rule6.json"
  ip -4 -j route show table all >"$dest/route4.json"; ip -6 -j route show table all >"$dest/route6.json"
  nft -j list ruleset >"$dest/nft.json"
}

# Complete private baseline capture, followed by fresh preflight before each mutation.
preflight
capture "$EVIDENCE/baseline"
host_capture "$EVIDENCE/host-before"
preflight
target nft add table inet "$TABLE"; CREATED_TABLE=1
preflight
target ip -4 rule add priority "$PRIORITY" fwmark "$MARK" lookup "$ROUTE_TABLE"; CREATED_RULE=1
preflight
target ip -4 route add blackhole "$PREFIX" table "$ROUTE_TABLE"; CREATED_ROUTE=1
preflight
capture "$EVIDENCE/seeded-1"
capture "$EVIDENCE/seeded-2"
for feed in link rule4 rule6 route4 route6 nft; do cmp -s "$EVIDENCE/seeded-1/$feed.json" "$EVIDENCE/seeded-2/$feed.json" || { echo "Capture changed: $feed" >&2; exit 1; }; done

# The test reuses parser-derived claims as intents; reject_unproven_desired is read-only.
preflight
PYTHONPATH="$BACKEND" "$PYTHON" - "$EVIDENCE/baseline" "$EVIDENCE/seeded-1" "$NS" "$TABLE" "$PRIORITY" "$MARK" "$ROUTE_TABLE" "$PREFIX" <<'PY'
import json,sys
from pathlib import Path
from uuid import uuid4
from app.network_agent.linux_inventory import NamespaceIdentity,bind_ip_family,parse_linux_inventory,reject_unproven_desired
from app.network_agent.registry import ClaimKind,NetworkObject,ObjectKind,RegistryConflict
baseline_dir,seeded_dir,name,table,priority,mark,table_id,prefix=sys.argv[1:]; baseline_dir=Path(baseline_dir); d=Path(seeded_dir)
manifest=json.loads((d/'manifest.json').read_text()); ident=manifest['namespace_identity']; ns=NamespaceIdentity(name,ident[0],ident[1],ident[2])
def parse(directory):
 read=lambda key:(directory/f'{key}.json').read_text()
 rules=json.loads(bind_ip_family(read('rule4'),'inet','rule'))+json.loads(bind_ip_family(read('rule6'),'inet6','rule'))
 routes=json.loads(bind_ip_family(read('route4'),'inet','route'))+json.loads(bind_ip_family(read('route6'),'inet6','route'))
 return parse_linux_inventory(links_json=read('link'),rules_json=json.dumps(rules),routes_json=json.dumps(routes),nft_json=read('nft'),namespace=ns)
s=parse(d); baseline=parse(baseline_dir)
assert s.complete and baseline.complete and s.namespace_identity==baseline.namespace_identity==(ns.boot_id,ns.netns_device,ns.netns_inode) and all(o.proof is None for o in s.objects)
matches=[]
for o in s.objects:
 k=json.loads(o.kernel_key) if o.kernel_key else []
 if o.kind==ObjectKind.NFT_TABLE and k[:2]==['inet',table]: matches.append(o)
 elif o.kind==ObjectKind.IP_RULE and k[1]==int(priority) and k[3]==int(mark,0) and k[5]==int(table_id): matches.append(o)
 elif o.kind==ObjectKind.ROUTE and k and k[2]==prefix and str(k[1])==table_id and k[6]=='blackhole': matches.append(o)
assert len(matches)==3, f'expected exactly 3 trial observations, got {len(matches)}'
trial_ids={o.object_id for o in matches}
before={o.object_id:o.fingerprint for o in baseline.objects}; after={o.object_id:o.fingerprint for o in s.objects}
assert set(after)-set(before)==trial_ids, 'unexpected added/removed observations'
assert all(after[key]==value for key,value in before.items()), 'unexpected pre-existing observation drift'
for i,o in enumerate(matches):
 intent=NetworkObject(f'tempest-trial-{i}',o.kind,name,'tempest',uuid4(),1,o.claims,o.fingerprint)
 try: reject_unproven_desired(s,(intent,),namespace=ns)
 except RegistryConflict: pass
 else: raise AssertionError(f'registry accepted foreign {o.kind}')
assert all(o.proof is None for o in matches)
print('PASS: collector returned three unproven foreign objects; registry rejected all three intents')
PY

# Exact reverse cleanup; each deletion has fresh identity/selector/cleanup preflight.
preflight; target ip -4 route del blackhole "$PREFIX" table "$ROUTE_TABLE"; CREATED_ROUTE=0
preflight; target ip -4 rule del priority "$PRIORITY" fwmark "$MARK" lookup "$ROUTE_TABLE"; CREATED_RULE=0
preflight; target nft delete table inet "$TABLE"; CREATED_TABLE=0
preflight
capture "$EVIDENCE/after"
host_capture "$EVIDENCE/host-after"
for feed in link rule4 rule6 route4 route6 nft; do
  cmp -s "$EVIDENCE/baseline/$feed.json" "$EVIDENCE/after/$feed.json" || { echo "Target baseline mismatch after cleanup: $feed" >&2; exit 1; }
  cmp -s "$EVIDENCE/host-before/$feed.json" "$EVIDENCE/host-after/$feed.json" || { echo "PID 1 namespace changed: $feed" >&2; exit 1; }
done
echo "PASS: cleanup and target/PID1 before-after feeds match. Private evidence: $EVIDENCE"
