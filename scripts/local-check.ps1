param(
    [ValidateSet('quick', 'backend', 'backend-full', 'frontend', 'full', 'help')]
    [string]$Mode = 'quick'
)

$ErrorActionPreference = 'Stop'
$RootDir = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$BackendDir = Join-Path $RootDir 'backend'
$FrontendDir = Join-Path $RootDir 'frontend'
$PythonExe = Join-Path $BackendDir '.venv\Scripts\python.exe'
$Timer = [System.Diagnostics.Stopwatch]::StartNew()

function Stop-Check([string]$Message, [int]$Code = 2) {
    Write-Error $Message
    exit $Code
}

function Invoke-Timed([string]$Label, [scriptblock]$Action) {
    $Watch = [System.Diagnostics.Stopwatch]::StartNew()
    Write-Host "`n==> $Label"
    & $Action
    $Code = if ($LASTEXITCODE -is [int]) { $LASTEXITCODE } else { 0 }
    $Watch.Stop()
    Write-Host ("<== {0}: exit {1}, {2:N2} sec" -f $Label, $Code, $Watch.Elapsed.TotalSeconds)
    if ($Code -ne 0) { exit $Code }
}

function Get-Node20 {
    $UserNode = Join-Path $env:LOCALAPPDATA 'TempestNode20\node_modules\node-win-x64\bin\node.exe'
    if (Test-Path -LiteralPath $UserNode) { return $UserNode }

    $Command = Get-Command node.exe -ErrorAction SilentlyContinue
    if ($Command) {
        $Version = (& $Command.Source --version 2>$null | Out-String).Trim()
        if ($Version -match '^v20\.') { return $Command.Source }
    }
    Stop-Check 'Node.js 20 is required. Follow the Windows setup in docs/LOCAL_DEV.md.'
}

function Get-NpmCli {
    $Command = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if (-not $Command) { Stop-Check 'npm.cmd was not found. Install Node.js/npm or repair the user-local Node 20 setup.' }
    $Cli = Join-Path (Split-Path $Command.Source -Parent) 'node_modules\npm\bin\npm-cli.js'
    if (-not (Test-Path -LiteralPath $Cli)) { Stop-Check "npm CLI was not found at $Cli" }
    return $Cli
}

function Require-Backend {
    if (-not (Test-Path -LiteralPath $PythonExe)) {
        Stop-Check 'backend/.venv is missing. Create it with uv as documented in docs/LOCAL_DEV.md.'
    }
    & $PythonExe -c 'import sys, pytest; assert sys.version_info[:2] == (3, 11)'
    if ($LASTEXITCODE -ne 0) { Stop-Check 'backend/.venv must contain Python 3.11 and pytest; repair it with uv.' }
}

function Require-Frontend {
    foreach ($Binary in @('vitest', 'eslint')) {
        $Path = Join-Path $FrontendDir "node_modules\.bin\$Binary.cmd"
        if (-not (Test-Path -LiteralPath $Path)) {
            Stop-Check "frontend/node_modules is incomplete ($Binary missing). Run npm ci --legacy-peer-deps in frontend/."
        }
    }
}

function Run-BackendTargeted {
    Require-Backend
    Push-Location $BackendDir
    try {
        Invoke-Timed 'Targeted backend pytest (first failure stops run)' {
            & $PythonExe -m pytest -x -q -rs tests/test_network_agent_ipc.py tests/test_network_plan_contract.py tests/test_agent_control_plane.py tests/test_network_plan_builder.py tests/test_agent_compose_isolation.py tests/test_network_object_registry.py tests/test_linux_inventory.py tests/test_install_script.py tests/test_update_script.py
        }
    }
    finally { Pop-Location }
}

function Run-BackendFullOnce {
    Require-Backend
    Push-Location $BackendDir
    try {
        $Wrapper = @'
import os, signal, subprocess, sys
cmd = [sys.executable, "-m", "pytest", "tests/", "-q", "-rs"]
p = subprocess.Popen(cmd, start_new_session=(os.name != "nt"), creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0))
try:
    raise SystemExit(p.wait(timeout=900))
except subprocess.TimeoutExpired:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True)
    else:
        os.killpg(p.pid, signal.SIGTERM)
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)
    print("BACKEND_PYTEST_FULL: INCOMPLETE (15 minute timeout)", file=sys.stderr)
    raise SystemExit(124)
'@
        Invoke-Timed 'Optional full backend pytest (15 minute hard limit)' { & $PythonExe -c $Wrapper }
        if ($LASTEXITCODE -eq 124) { Write-Host 'Record full backend result as INCOMPLETE; rely on Linux CI for the complete suite.' }
    }
    finally { Pop-Location }
}

function Run-Quick {
    Run-BackendTargeted
}

function Run-Frontend {
    Require-Frontend
    $NodeExe = Get-Node20
    $NpmCli = Get-NpmCli
    $Version = (& $NodeExe --version).Trim()
    if ($Version -notmatch '^v20\.') { Stop-Check "Expected Node.js 20, found $Version" }
    Push-Location $FrontendDir
    try {
        Invoke-Timed 'Frontend Vitest' { & $NodeExe $NpmCli run test:ci }
        Invoke-Timed 'Frontend ESLint' { & $NodeExe $NpmCli run lint }
        Invoke-Timed 'Frontend TypeScript/Vite build' { & $NodeExe $NpmCli run build }
    }
    finally { Pop-Location }
}

function Write-WindowsScope {
    Write-Host 'NOT_RUN_ON_WINDOWS: Linux AF_UNIX/SO_PEERCRED transport and runtime systemd checks.'
    Write-Host 'Pytest skips only AF_UNIX/SO_PEERCRED-specific cases and Bash subprocess cases without working Bash; skips are not passes.'
    Write-Host 'NOT_RUN_ON_WINDOWS: Docker runtime/Compose startup and real network integration are outside this local check.'
}

function Run-AgentComposeConfig {
    $Docker = Get-Command docker.exe -ErrorAction SilentlyContinue
    if (-not $Docker) {
        Write-Host 'NOT_RUN_ON_WINDOWS: Docker Compose config rendering (Docker CLI unavailable); YAML and security assertions ran in pytest.'
        return
    }
    $PreviousSecret = $env:TEMPEST_SECRET_KEY
    $PreviousGid = $env:TEMPEST_AGENT_GID
    try {
        $env:TEMPEST_SECRET_KEY = 'local-static-validation-only'
        $env:TEMPEST_AGENT_GID = '10001'
        Invoke-Timed 'Agent Compose static config' {
            & $Docker.Source compose --project-directory $RootDir -f (Join-Path $RootDir 'docker-compose.agent.yml') config --quiet
        }
    }
    finally {
        $env:TEMPEST_SECRET_KEY = $PreviousSecret
        $env:TEMPEST_AGENT_GID = $PreviousGid
    }
}

switch ($Mode) {
    'help' {
        @'
Usage: pwsh -File scripts/local-check.ps1 [quick|backend|backend-full|frontend|full]

  quick     Current Network Agent / NetworkPlan and Agent isolation tests
  backend   Targeted pytest for changed backend modules (stops at first failure)
  backend-full  Optional full backend pytest, once only, hard-limited to 15 minutes
  frontend  Vitest, ESLint, and TypeScript/Vite production build (Node.js 20)
  full      Targeted backend + frontend + static checks; full backend belongs in Linux CI
'@ | Write-Host
    }
    'quick' {
        Write-WindowsScope
        Run-Quick
    }
    'backend' {
        Write-WindowsScope
        Run-BackendTargeted
    }
    'backend-full' {
        Write-WindowsScope
        Run-BackendFullOnce
    }
    'frontend' { Run-Frontend }
    'full' {
        Write-WindowsScope
        Run-BackendTargeted
        Run-Frontend
        Run-AgentComposeConfig
        Invoke-Timed 'Git whitespace/error check' { git -C $RootDir diff --check }
        Write-Host 'LOCAL WINDOWS CHECKS PASS; Linux-only and Docker runtime checks remain NOT_RUN_ON_WINDOWS.'
    }
    default { Stop-Check "Unknown mode: $Mode" }
}

$Timer.Stop()
Write-Host ("Total elapsed: {0:N2} sec" -f $Timer.Elapsed.TotalSeconds)
