# Distill local runtime — Windows PowerShell entrypoint
# Mirrors runtime/scripts/start.sh (Git Bash / WSL preferred for Docker scripts).
#
# Usage (from repo root or runtime/):
#   .\runtime\scripts\start.ps1
#   .\runtime\scripts\start.ps1 -Native
#   .\runtime\scripts\start.ps1 -Audio
#   .\runtime\scripts\start.ps1 -NoWait

param(
    [switch]$Native,
    [switch]$Audio,
    [switch]$Cpu,
    [switch]$NoWait,
    [switch]$Help
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RuntimeDir = Resolve-Path (Join-Path $ScriptDir "..")
$RootDir = Resolve-Path (Join-Path $RuntimeDir "..")

if ($Help) {
    Write-Host @"
Distill runtime start (PowerShell)

  .\start.ps1           Docker compose full stack (GPU profile)
  .\start.ps1 -Native   Start vLLM + STT + diarize as background jobs
  .\start.ps1 -Audio    STT + diarize only
  .\start.ps1 -Cpu      Diarize only (compose cpu profile)
  .\start.ps1 -NoWait   Do not block until models are ready

Prefer Git Bash / WSL for full bash parity: ./runtime/scripts/start.sh
"@
    exit 0
}

Write-Host "=========================================="
Write-Host "Distill Runtime — startup (Windows)"
Write-Host "=========================================="

$envFile = Join-Path $RuntimeDir ".env"
$envExample = Join-Path $RuntimeDir ".env.example"
if (-not (Test-Path $envFile) -and (Test-Path $envExample)) {
    Copy-Item $envExample $envFile
    Write-Host "Created runtime/.env from example"
}

# Load KEY=VALUE from runtime/.env (simple parser)
if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith("#")) { return }
        $i = $line.IndexOf("=")
        if ($i -lt 1) { return }
        $k = $line.Substring(0, $i).Trim()
        $v = $line.Substring($i + 1).Trim().Trim('"').Trim("'")
        Set-Item -Path "Env:$k" -Value $v
    }
}

$bash = Get-Command bash -ErrorAction SilentlyContinue
if ($bash -and -not $Native) {
    # Prefer the maintained bash scripts when Git Bash / WSL bash exists
    $args = @()
    if ($Native) { $args += "--native" }
    if ($Audio) { $args += "--audio" }
    if ($Cpu) { $args += "--cpu" }
    if ($NoWait) { $args += "--no-wait" }
    & bash (Join-Path $ScriptDir "start.sh") @args
    exit $LASTEXITCODE
}

if (-not $Native) {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw "Docker not found. Install Docker Desktop or use -Native."
    }
    $profile = "full"
    if ($Audio) { $profile = "audio" }
    if ($Cpu) { $profile = "cpu" }
    Push-Location $RuntimeDir
    try {
        $whisperDev = if ($env:WHISPER_DEVICE) { $env:WHISPER_DEVICE } else { "cuda" }
        $diarizeDev = if ($env:DIARIZATION_DEVICE) { $env:DIARIZATION_DEVICE } else { "cpu" }
        if ($Cpu) { $diarizeDev = "cpu" }
        function Get-DockerfileForDevice([string]$Device, [string]$Profile) {
            $d = $Device.ToLower()
            if ($d -eq "auto") {
                if ($Profile -eq "cpu") { $d = "cpu" } else { $d = "cuda" }
            }
            if ($d -in @("cuda", "gpu")) { "Dockerfile.cuda" } else { "Dockerfile" }
        }
        $env:STT_DOCKERFILE = Get-DockerfileForDevice $whisperDev $profile
        $env:DIARIZE_DOCKERFILE = Get-DockerfileForDevice $diarizeDev $profile
        Write-Host "[compose] STT -> $($env:STT_DOCKERFILE) (WHISPER_DEVICE=$whisperDev)"
        Write-Host "[compose] Diarize -> $($env:DIARIZE_DOCKERFILE) (DIARIZATION_DEVICE=$diarizeDev)"
        docker compose --profile $profile up -d --build
    } finally {
        Pop-Location
    }
} else {
    $logDir = Join-Path $RuntimeDir "logs"
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    Write-Host "[native] starting PowerShell jobs (logs under $logDir)"

    if (-not $Audio -and -not $Cpu) {
        Start-Process -WindowStyle Hidden -FilePath "bash" -ArgumentList @(
            (Join-Path $ScriptDir "run_vllm.sh")
        ) -RedirectStandardOutput (Join-Path $logDir "vllm.log") `
          -RedirectStandardError (Join-Path $logDir "vllm.err.log") `
          -PassThru | ForEach-Object { $_.Id | Out-File (Join-Path $logDir "vllm.pid") }
    }

    if (-not $Cpu) {
        Start-Process -WindowStyle Hidden -FilePath "bash" -ArgumentList @(
            (Join-Path $ScriptDir "run_stt.sh")
        ) -RedirectStandardOutput (Join-Path $logDir "stt.log") `
          -RedirectStandardError (Join-Path $logDir "stt.err.log") `
          -PassThru | ForEach-Object { $_.Id | Out-File (Join-Path $logDir "stt.pid") }
    }

    Start-Process -WindowStyle Hidden -FilePath "bash" -ArgumentList @(
        (Join-Path $ScriptDir "run_diarize.sh")
    ) -RedirectStandardOutput (Join-Path $logDir "diarize.log") `
      -RedirectStandardError (Join-Path $logDir "diarize.err.log") `
      -PassThru | ForEach-Object { $_.Id | Out-File (Join-Path $logDir "diarize.pid") }

    Write-Host "Native jobs launched via bash. Prefer: bash ./runtime/scripts/start.sh --native"
}

$vllmPort = if ($env:VLLM_PORT) { $env:VLLM_PORT } else { "8001" }
$sttPort = if ($env:STT_PORT) { $env:STT_PORT } else { "8080" }
$diarPort = if ($env:DIARIZE_PORT) { $env:DIARIZE_PORT } else { "8090" }
$llmModel = if ($env:LLM_MODEL) { $env:LLM_MODEL } else { "Qwen/Qwen2.5-7B-Instruct" }
$whisper = if ($env:WHISPER_MODEL) { $env:WHISPER_MODEL } else { "large-v3" }

Write-Host @"

Point Distill API (.env at repo root) at this stack:

  LLM_ENDPOINT=http://127.0.0.1:$vllmPort/v1/chat/completions
  LLM_API_KEY=local
  LLM_MODEL_NAME=$llmModel

  STT_ENDPOINT=http://127.0.0.1:$sttPort/v1/audio/transcriptions
  STT_API_KEY=local
  STT_MODEL=$whisper

  DIARIZATION_ENDPOINT=http://127.0.0.1:$diarPort
  DIARIZATION_ALLOW_FALLBACK=0
  DISTILL_ENABLE_PYANNOTE=0

Then: python -m api
"@

if (-not $NoWait) {
    if ($bash) {
        $wargs = @()
        if ($Audio) { $wargs += "--audio" }
        if ($Cpu) { $wargs += "--diarize" }
        & bash (Join-Path $ScriptDir "wait_ready.sh") @wargs
    } else {
        Write-Host "Install Git Bash to use wait_ready.sh, or poll:"
        Write-Host "  curl http://127.0.0.1:$diarPort/health"
    }
}
