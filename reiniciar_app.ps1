# Reinicia limpiamente el servidor local de ESTA carpeta liberando su puerto (8081/8080)
# y relanzando Iniciar_Recepcion_Local.bat --no-browser sin abrir ventanas ni duplicar procesos.
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$leaf = Split-Path -Leaf $dir
$bat = Join-Path $dir "Iniciar_Recepcion_Local.bat"

# Determinar el puerto de este hotel desde data\hotel_perfil.json
$port = if ($dir -match 'terrazas') { 8081 } else { 8080 }
$perfilPath = Join-Path $dir "data\hotel_perfil.json"
if (Test-Path $perfilPath) {
    try {
        $cfg = Get-Content $perfilPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($cfg.app_port) { $port = [int]$cfg.app_port }
    } catch {}
}

$pids = New-Object System.Collections.Generic.HashSet[int]

# 1. Cualquier proceso escuchando en el puerto de este hotel
try {
    $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    foreach ($c in $conns) {
        if ($c.OwningProcess -gt 4) { [void]$pids.Add([int]$c.OwningProcess) }
    }
} catch {}

# 2. Cualquier python/pythonw ejecutando lanzador_escritorio de esta carpeta
try {
    $procs = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.Name -match '^pythonw?\.exe$' -and $_.CommandLine -and ($_.CommandLine -like "*lanzador_escritorio*") -and ($_.CommandLine -like "*$leaf*")
    }
    foreach ($p in $procs) {
        if ($p.ProcessId -gt 4) { [void]$pids.Add([int]$p.ProcessId) }
    }
} catch {}

$idList = ($pids | ForEach-Object { $_ }) -join ','
if (-not $idList) { $idList = "0" }

$plantilla = @'
Start-Sleep -Seconds 2
foreach ($i in @(__IDS__)) {
    if ($i -gt 4) { Stop-Process -Id $i -Force -ErrorAction SilentlyContinue }
}
Start-Sleep -Seconds 2
Start-Process -FilePath "cmd.exe" -ArgumentList @('/c', '""__BAT__" --no-browser"') -WorkingDirectory '__DIR__' -WindowStyle Hidden
'@

$relevo = $plantilla.Replace('__IDS__', $idList).Replace('__BAT__', $bat).Replace('__DIR__', $dir)
$enc = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($relevo))
Start-Process -FilePath "powershell.exe" -ArgumentList @("-NoProfile", "-WindowStyle", "Hidden", "-EncodedCommand", $enc) -WindowStyle Hidden
exit 0
