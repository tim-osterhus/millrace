param(
  [Parameter(Mandatory=$true)][string]$OutDir,
  [string]$SpecPath = "agents/ui_verification_spec.yaml",
  [ValidateSet("smoke","standard","broad","")][string]$Coverage = "",
  [string]$Cmd = "",
  [switch]$UpdateLatest
)

$ErrorActionPreference = "Stop"

function Write-UiVerifyJson {
  param(
    [string]$Path,
    [hashtable]$Data
  )
  $json = ($Data | ConvertTo-Json -Depth 10)
  # ConvertTo-Json doesn't guarantee trailing newline; add one for nicer diffs.
  [System.IO.File]::WriteAllText($Path, $json + "`n")
}

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $OutDir "evidence") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $OutDir "meta") | Out-Null

$startedAt = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
$endedAt = ""

$status = "BLOCKED"
$executor = "playwright"
$analyzer = "none"

$errType = ""
$errMsg = ""

if (Test-Path $SpecPath) {
  try {
    Copy-Item -Force -Path $SpecPath -Destination (Join-Path $OutDir "meta/spec_resolved.yaml")
  } catch {
    # Best-effort; do not fail the run for spec copy issues.
  }
}

function Has-Cmd([string]$name) {
  return $null -ne (Get-Command $name -ErrorAction SilentlyContinue)
}

if (-not (Has-Cmd "node")) {
  $errType = "ENV"
  $errMsg = "node is not installed or not on PATH"
} elseif (-not (Has-Cmd "npx")) {
  $errType = "ENV"
  $errMsg = "npx is not installed or not on PATH"
} else {
  $pwOk = $true
  try {
    & npx playwright --version | Out-Null
  } catch {
    $pwOk = $false
  }

  if (-not $pwOk) {
    $errType = "ENV"
    $errMsg = "Playwright is not installed (add it to the project and rerun)"
  } else {
    $rc = 0
    try {
      if (-not [string]::IsNullOrWhiteSpace($Cmd)) {
        & ([ScriptBlock]::Create($Cmd))
        $rc = $LASTEXITCODE
      } else {
        & npx playwright test
        $rc = $LASTEXITCODE
      }
    } catch {
      $rc = 1
    }

    if ($rc -eq 0) {
      $status = "PASS"
    } else {
      $status = "FAIL"
      $errType = "ASSERTION"
      $errMsg = "Playwright exited non-zero (rc=$rc)"
    }
  }
}

$endedAt = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")

$result = @{
  status = $status
  executor = $executor
  analyzer = $analyzer
  coverage = $Coverage
  started_at = $startedAt
  ended_at = $endedAt
  evidence_dir = (Join-Path $OutDir "evidence")
  checks = @()
  errors = @()
  quota = $null
}

if (-not [string]::IsNullOrWhiteSpace($errType) -and -not [string]::IsNullOrWhiteSpace($errMsg)) {
  $result.errors += @{ type = $errType; message = $errMsg }
}

Write-UiVerifyJson -Path (Join-Path $OutDir "result.json") -Data $result

$report = @"
# UI Verification Report

UI_VERIFY: $status

- executor: $executor
- analyzer: $analyzer
- coverage: $Coverage
- started_at: $startedAt
- ended_at: $endedAt
- evidence_dir: $OutDir/evidence

## Notes

- This is a deterministic runner scaffold. Provide a project-specific Playwright suite and invoke it via `-Cmd` for meaningful coverage.
- Spec (if present): `$SpecPath` (copied to `meta/spec_resolved.yaml`).
"@

[System.IO.File]::WriteAllText((Join-Path $OutDir "report.md"), $report + "`n")

function Archive-PreviousUiVerifyBundle {
  param([string]$CurrentOutDir)

  $latestResultPath = "agents/ui_verification_result.json"
  if (-not (Test-Path $latestResultPath)) { return }

  $latest = $null
  try {
    $latest = Get-Content -Raw $latestResultPath | ConvertFrom-Json
  } catch {
    return
  }
  if ($null -eq $latest) { return }
  if (-not ($latest.PSObject.Properties.Name -contains "evidence_dir")) { return }

  $evidenceDir = [string]$latest.evidence_dir
  if ([string]::IsNullOrWhiteSpace($evidenceDir)) { return }

  $prevBundle = [System.IO.Path]::GetFullPath((Join-Path $evidenceDir ".."))
  $currentBundle = [System.IO.Path]::GetFullPath($CurrentOutDir)
  if ($prevBundle -eq $currentBundle) { return }
  if (-not (Test-Path $prevBundle)) { return }

  $uiRoot = [System.IO.Path]::GetFullPath("agents/diagnostics/ui_verify")
  if (-not $prevBundle.StartsWith($uiRoot, [System.StringComparison]::OrdinalIgnoreCase)) { return }

  $archivedRoot = Join-Path $uiRoot "archived"
  New-Item -ItemType Directory -Force -Path $archivedRoot | Out-Null

  $baseName = Split-Path -Leaf $prevBundle
  $dest = Join-Path $archivedRoot $baseName
  if (Test-Path $dest) {
    $dest = Join-Path $archivedRoot ($baseName + "-" + [DateTimeOffset]::UtcNow.ToUnixTimeSeconds())
  }

  Move-Item -Force -Path $prevBundle -Destination $dest
}

if ($UpdateLatest -and (Test-Path "agents")) {
  try { Archive-PreviousUiVerifyBundle -CurrentOutDir $OutDir } catch {}
  try { Copy-Item -Force -Path (Join-Path $OutDir "result.json") -Destination "agents/ui_verification_result.json" } catch {}
  try { Copy-Item -Force -Path (Join-Path $OutDir "report.md") -Destination "agents/ui_verification_report.md" } catch {}
}

switch ($status) {
  "PASS" { exit 0 }
  "FAIL" { exit 1 }
  "BLOCKED" { exit 2 }
  default { exit 3 }
}
