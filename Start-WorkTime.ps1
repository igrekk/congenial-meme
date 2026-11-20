$ErrorActionPreference = 'Stop'
$appDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$candidates = @()
$bundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
if (Test-Path -LiteralPath $bundledPython) {
    $candidates += [pscustomobject]@{ Exe = $bundledPython; Prefix = @() }
}
$pyLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
if ($pyLauncher) {
    $candidates += [pscustomobject]@{ Exe = $pyLauncher.Source; Prefix = @('-3') }
}
foreach ($commandName in @('python.exe', 'python3.exe')) {
    $command = Get-Command $commandName -ErrorAction SilentlyContinue
    if ($command -and $command.Source -notlike '*\WindowsApps\*') {
        $candidates += [pscustomobject]@{ Exe = $command.Source; Prefix = @() }
    }
}
$selectedPython = $null
foreach ($candidate in $candidates) {
    try {
        $prefixArguments = $candidate.Prefix
        & $candidate.Exe @prefixArguments -c 'import sys, sqlite3; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>$null
        if ($LASTEXITCODE -eq 0) { $selectedPython = $candidate; break }
    } catch { }
}
if (-not $selectedPython) {
    Write-Host 'Python 3.10 or newer is required. Install Python from https://www.python.org/downloads/windows/ and enable Add Python to PATH.'
    exit 1
}
Set-Location -LiteralPath $appDirectory
$prefixArguments = $selectedPython.Prefix
& $selectedPython.Exe @prefixArguments (Join-Path $appDirectory 'server.py') --open-browser
exit $LASTEXITCODE
