$ErrorActionPreference = 'Continue'
$dl = 'D:\Downloads'
New-Item -ItemType Directory -Force -Path $dl | Out-Null

# Resolve exact Node 22 LTS MSI URL
try {
    $idx = Invoke-WebRequest -Uri 'https://nodejs.org/dist/latest-v22.x/' -UseBasicParsing -TimeoutSec 60
    $nodeRel = ($idx.Links | Where-Object { $_.href -match 'node-v22\..*-x64\.msi' } | Select-Object -First 1).href
    $nodeUrl = 'https://nodejs.org' + $nodeRel
} catch {
    $nodeUrl = 'https://nodejs.org/dist/v22.11.0/node-v22.11.0-x64.msi'
}
Write-Host "Node URL: $nodeUrl"

$jobs = @()
$jobs += Start-Process -FilePath 'curl.exe' -ArgumentList @('-sSL','-o',"$dl\OllamaSetup.exe",'https://ollama.com/download/OllamaSetup.exe') -PassThru -WindowStyle Hidden
$jobs += Start-Process -FilePath 'curl.exe' -ArgumentList @('-sSL','-o',"$dl\WeChatSetup.exe",'https://dldir1.qq.com/weixin/Windows/WeChatSetup.exe') -PassThru -WindowStyle Hidden
$jobs += Start-Process -FilePath 'curl.exe' -ArgumentList @('-sSL','-o',"$dl\node.msi",$nodeUrl) -PassThru -WindowStyle Hidden

$jobs | ForEach-Object { $_.WaitForExit() }
Write-Host "=== Downloads finished ==="
Get-ChildItem $dl | Select-Object Name, @{N='MB';E={[math]::Round($_.Length/1MB,1)}}
