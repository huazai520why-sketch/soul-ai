$ProgressPreference='SilentlyContinue'
$ts = New-Object -ComObject Schedule.Service
$ts.Connect()
$f = $ts.GetFolder('\')
foreach ($name in @('OllamaServer','OpenClaw Gateway')) {
  try {
    $t = $f.GetTask($name)
    $tr = ($t.Definition.Triggers | ForEach-Object { $_.Type }) -join ','
    $act = ($t.Definition.Actions | ForEach-Object { $_.Path }) -join ','
    $state = $t.State
    "$name | trigger(s)=$tr | state=$state | action=$act"
  } catch { "$name ERR: $_" }
}
