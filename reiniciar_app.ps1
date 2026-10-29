# Reinicia el servidor local de ESTA carpeta (sin depender de que "python" este en el PATH).
# Lo usa la puerta de mando remota (ordenes_hoteles.json) para aplicar actualizaciones
# en hoteles cuyo programa viejo no sabe reiniciarse solo.
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$lanzador = Join-Path $dir "lanzador_escritorio.py"

$procs = @(Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^pythonw?\.exe$' -and $_.CommandLine -and ($_.CommandLine -like "*lanzador_escritorio.py*") -and ($_.CommandLine -like "*$dir*")
})
if ($procs.Count -eq 0) { exit 0 }

$exe = $procs[0].ExecutablePath
$ids = ($procs | ForEach-Object { $_.ProcessId }) -join ','

$plantilla = @'
Start-Sleep -Seconds 3
foreach ($i in @(__IDS__)) { Stop-Process -Id $i -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 2
Start-Process -FilePath '__EXE__' -ArgumentList ('"__LANZ__"', '--no-browser') -WorkingDirectory '__DIR__' -WindowStyle Hidden
'@
$relevo = $plantilla.Replace('__IDS__', $ids).Replace('__EXE__', $exe).Replace('__LANZ__', $lanzador).Replace('__DIR__', $dir)
$enc = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($relevo))
Start-Process -FilePath "powershell.exe" -ArgumentList @("-NoProfile", "-WindowStyle", "Hidden", "-EncodedCommand", $enc) -WindowStyle Hidden
exit 0
