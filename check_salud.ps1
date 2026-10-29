$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$script = Join-Path $dir "check_salud.py"
$py = $null
$proc = Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^pythonw?\.exe$' -and $_.ExecutablePath -and $_.ExecutablePath -notlike '*WindowsApps*' } | Select-Object -First 1
if ($proc) {
    $cand = Join-Path (Split-Path $proc.ExecutablePath -Parent) "python.exe"
    if (Test-Path $cand) { $py = $cand } else { $py = $proc.ExecutablePath }
}
if ($py -and (Test-Path $script)) {
    & $py $script
}
