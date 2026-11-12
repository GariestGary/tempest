param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$Commit,

    [ValidateSet('Plan', 'Registry', 'Inventory', 'Ipc', 'Boundary')]
    [string[]]$Suite = @('Inventory'),

    [ValidateRange(10, 600)]
    [int]$TimeoutSeconds = 120
)

$ErrorActionPreference = 'Stop'
$RootDir = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Tests = @{
    Plan      = 'tests/test_network_plan_contract.py'
    Registry  = 'tests/test_network_object_registry.py'
    Inventory = 'tests/test_linux_inventory.py'
    Ipc       = 'tests/test_network_agent_ipc.py'
    Boundary  = 'tests/test_agent_control_plane.py'
}

function Stop-Check([string]$Message, [int]$Code = 2) {
    [Console]::Error.WriteLine($Message)
    exit $Code
}

if (-not $IsWindows) { Stop-Check 'Run this script on TORNADO/Windows.' }
$Ssh = Get-Command ssh.exe -ErrorAction SilentlyContinue
if (-not $Ssh) { Stop-Check 'ssh.exe is unavailable.' }
$Git = Get-Command git.exe -ErrorAction SilentlyContinue
if (-not $Git) { Stop-Check 'git.exe is unavailable.' }

$LocalHead = (& $Git.Source -C $RootDir rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $LocalHead -ne $Commit.ToLowerInvariant()) {
    Stop-Check "Commit must equal local HEAD on master (found $LocalHead)."
}
$Branch = (& $Git.Source -C $RootDir branch --show-current).Trim()
if ($LASTEXITCODE -ne 0 -or $Branch -ne 'master') { Stop-Check 'Local branch must be master.' }
$LocalStatus = & $Git.Source -C $RootDir status --porcelain
if ($LASTEXITCODE -ne 0 -or $LocalStatus) { Stop-Check 'Local working tree must be clean.' }

$SelectedTests = @($Suite | Select-Object -Unique | ForEach-Object { $Tests[$_] })
if ($SelectedTests.Count -eq 0) { Stop-Check 'Select at least one suite.' }

$RemoteScript = @'
set -euo pipefail
expected_sha=$1
limit_seconds=$2
shift 2
repo=/home/gary/Docker/tempest

[[ "$expected_sha" =~ ^[0-9a-f]{40}$ && "$limit_seconds" =~ ^[0-9]+$ && $# -gt 0 ]] || exit 2

if [[ $(id -un) != gary || $(id -u) == 0 || ! -d /home/gary/Docker || \
      ! -d "$repo" || -L "$repo" ]]; then
    echo 'Remote identity or repository path is invalid.' >&2
    exit 2
fi

disk_kb=$(df -Pk /home/gary/Docker | awk 'END {print $4}')
memory_kb=$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)
cpu_count=$(nproc)
load_one=$(awk '{print $1}' /proc/loadavg)
cpu_busy=$(awk -v current_load="$load_one" -v cpus="$cpu_count" \
    'BEGIN {print (current_load > cpus * 1.5) ? 1 : 0}')
printf 'Preflight: disk_available_kb=%s memory_available_kb=%s load_1m=%s cpus=%s\n' \
    "$disk_kb" "$memory_kb" "$load_one" "$cpu_count"
if (( disk_kb < 2097152 || memory_kb < 1048576 || cpu_busy == 1 )); then
    echo 'Insufficient disk, memory, or CPU headroom; repository was not touched.' >&2
    exit 3
fi

cd "$repo"
export GIT_TERMINAL_PROMPT=0
if [[ $(git branch --show-current) != master || \
      $(git remote get-url origin) != https://github.com/GariestGary/tempest.git || \
      -n $(git status --porcelain) ]]; then
    echo 'Remote master, origin, or working tree is unexpected; no pull was attempted.' >&2
    exit 4
fi
if [[ ! -x backend/.venv/bin/python ]] || \
   ! backend/.venv/bin/python -c 'import sys, pytest; assert sys.version_info[:2] == (3, 11)'; then
    echo 'Python 3.11 / pytest venv is unavailable; follow docs/LOCAL_DEV.md.' >&2
    exit 5
fi

for test_file in "$@"; do
    case "$test_file" in
        tests/test_network_plan_contract.py|tests/test_network_object_registry.py|\
        tests/test_linux_inventory.py|tests/test_network_agent_ipc.py|\
        tests/test_agent_control_plane.py) ;;
        *) echo "Test path is not allowlisted: $test_file" >&2; exit 6 ;;
    esac
    [[ -f "backend/$test_file" ]] || { echo "Missing $test_file" >&2; exit 6; }
done

git fetch origin master
fetched_sha=$(git rev-parse FETCH_HEAD)
if [[ "$fetched_sha" != "$expected_sha" ]]; then
    echo "Fetched master $fetched_sha differs from requested $expected_sha; no pull was attempted." >&2
    exit 7
fi
if ! git merge-base --is-ancestor HEAD FETCH_HEAD; then
    echo 'Remote master cannot fast-forward to the requested commit; no checkout was changed.' >&2
    exit 8
fi
git -c core.hooksPath=/dev/null merge --ff-only FETCH_HEAD
tested_sha=$(git rev-parse HEAD)
if [[ "$tested_sha" != "$expected_sha" || -n $(git status --porcelain) ]]; then
    echo 'Remote checkout differs from requested commit; tests were not started.' >&2
    exit 8
fi

tmp_dir=$(mktemp -d /tmp/tempest-remote-check.XXXXXXXX)
cleanup() {
    if [[ "$tmp_dir" == /tmp/tempest-remote-check.* && -d "$tmp_dir" ]]; then
        rm -rf -- "$tmp_dir"
    fi
}
trap cleanup EXIT

cd backend
export TMPDIR="$tmp_dir" PYTHONDONTWRITEBYTECODE=1
echo "TEST_SHA=$tested_sha"
echo "TEST_FILES=$*"
set +e
nice -n 10 /usr/bin/time -f 'Elapsed=%e s MaxRSS=%M KiB CPU=%P' \
    timeout --kill-after=5s "${limit_seconds}s" \
    .venv/bin/python -m pytest -x -q -rs -p no:cacheprovider \
    --basetemp "$tmp_dir/pytest" "$@" 2>&1 | tee "$tmp_dir/pytest.log"
result=${PIPESTATUS[0]}
set -e
summary=$(grep -E '[0-9]+ (passed|failed|skipped|error)' "$tmp_dir/pytest.log" | tail -n 1 || true)
passed=0 failed=0 skipped=0 errors=0
if [[ "$summary" =~ ([0-9]+)[[:space:]]passed ]]; then passed=${BASH_REMATCH[1]}; fi
if [[ "$summary" =~ ([0-9]+)[[:space:]]failed ]]; then failed=${BASH_REMATCH[1]}; fi
if [[ "$summary" =~ ([0-9]+)[[:space:]]skipped ]]; then skipped=${BASH_REMATCH[1]}; fi
if [[ "$summary" =~ ([0-9]+)[[:space:]]error ]]; then errors=${BASH_REMATCH[1]}; fi
printf 'RESULT passed=%s failed=%s skipped=%s errors=%s exit=%s SHA=%s\n' \
    "$passed" "$failed" "$skipped" "$errors" "$result" "$tested_sha"
exit "$result"
'@

$StartInfo = [System.Diagnostics.ProcessStartInfo]::new()
$StartInfo.FileName = $Ssh.Source
foreach ($Argument in @('-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', 'vortex',
                        'bash', '-s', '--', $Commit.ToLowerInvariant(), [string]$TimeoutSeconds) + $SelectedTests) {
    [void]$StartInfo.ArgumentList.Add($Argument)
}
$StartInfo.UseShellExecute = $false
$StartInfo.RedirectStandardInput = $true
$StartInfo.RedirectStandardOutput = $true
$StartInfo.RedirectStandardError = $true
$StartInfo.StandardInputEncoding = [System.Text.UTF8Encoding]::new($false)
$Process = [System.Diagnostics.Process]::Start($StartInfo)
$OutputTask = $Process.StandardOutput.ReadToEndAsync()
$ErrorTask = $Process.StandardError.ReadToEndAsync()
$Process.StandardInput.Write($RemoteScript.Replace("`r`n", "`n") + "`n")
$Process.StandardInput.Close()

if (-not $Process.WaitForExit(($TimeoutSeconds + 90) * 1000)) {
    $Process.Kill($true)
    Stop-Check 'SSH command exceeded the local time limit; result is incomplete.' 124
}
$OutputTask.GetAwaiter().GetResult() | Write-Host
$ErrorTask.GetAwaiter().GetResult() | ForEach-Object { if ($_) { [Console]::Error.WriteLine($_) } }
exit $Process.ExitCode
