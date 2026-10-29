# Reinicia el servidor local de ESTA carpeta (sin depender de que "python" este en el PATH).
# Lo usa la puerta de mando remota (ordenes_hoteles.json) para aplicar actualizaciones
# en hoteles cuyo programa viejo no sabe reiniciarse solo.
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$lanzador = Join-Path $dir "lanzador_escritorio.py"

$procs = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^pythonw?\.exe$' -and $_.CommandLine -and ($_.CommandLine -like "*lanzador_escritorio.py*") -and ($_.CommandLine -like "*$dir*")
}
if (-not $procs) { exit 0 }

$exe = $procs[0].ExecutablePath
$ids = ($procs | ForEach-Object { $_.ProcessId }) -join ','

$relevo = "Start-Sleep -Seconds 3; foreach (`$i in @($ids)) { Stop-Process -Id `$i -Force -ErrorAction SilentlyContinue }; Start-Sleep -Seconds 2; Start-Process -FilePath '$exe' -ArgumentList '\"$lanzador\" --no-browser' -WorkingDirectory '$dir' -WindowStyle Hidden"
Start-Process -FilePath "powershell.exe" -ArgumentList @("-NoProfile", "-WindowStyle", "Hidden", "-Command", $relevo) -WindowStyle Hidden
exit 0
