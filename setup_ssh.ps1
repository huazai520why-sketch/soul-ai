# setup_ssh.ps1
# Install OpenSSH Server + assistant (JIANG) admin access on a FRESH Windows host.
# Run it via setup_ssh.bat (right-click -> Run as administrator) or from an elevated PowerShell.
# ASCII-only on purpose so it survives any copy/encoding path.

$ErrorActionPreference = 'Stop'

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p  = New-Object Security.Principal.WindowsPrincipal($id)
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}
if (-not (Test-Admin)) {
    Write-Output "ERROR: must run as Administrator. Use setup_ssh.bat (Run as admin) or an elevated PowerShell."
    exit 1
}

# Start logging to a file so even if the window closes we keep the trace.
$log = "C:\setup_ssh.log"
try { Start-Transcript -Path $log -Force -ErrorAction Stop } catch { Write-Output "NOTE: could not start transcript: $_" }

try {

# ---- tunables ----
# Reuse the EXISTING local account "JIANG" (password 123456, set during reinstall).
# We do NOT create a new user and do NOT reset the password here, so the
# Windows password-complexity policy (which would reject "123456") never blocks us.
$user = "JIANG"
# Router (iStoreOS) already reserves 192.168.10.165 by MAC, so a fresh OS gets it via DHCP.
# Set to $true ONLY if the host does NOT come up as 192.168.10.165 after reinstall.
$SET_STATIC_IP = $false
$STATIC_IP     = "192.168.10.165"
$STATIC_GW     = "192.168.10.1"
$STATIC_MASK   = 24
# -------------------

Write-Output "[1/6] Installing OpenSSH Server..."
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0 | Out-Null
Set-Service sshd -StartupType Automatic
Set-Service ssh-agent -StartupType Automatic
Start-Service sshd
Write-Output "    sshd installed + started."

Write-Output "[2/6] Enabling WinRM (best effort)..."
try {
    Enable-PSRemoting -Force -ErrorAction Stop | Out-Null
    Set-Item WSMan:\localhost\Service\Auth\Basic -Value $true -Force -ErrorAction Stop
    Write-Output "    WinRM enabled."
} catch { Write-Output "    WinRM skipped: $_" }

Write-Output "[3/6] Reusing existing account $user (ensure admin)..."
if (-not (Get-LocalUser -Name $user -ErrorAction SilentlyContinue)) {
    Write-Output "    ERROR: local user '$user' does not exist. This script reuses the account you created at install."
    exit 1
}
try { Add-LocalGroupMember -Group "Administrators" -Member $user -ErrorAction SilentlyContinue } catch { }
Write-Output "    $user -> Administrators (password left as-is: 123456)."

Write-Output "[4/6] Installing assistant public key..."
$pub     = 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKMmhziwLajxE8tJp2u7X/sSf+qUttz5EkHc7LJSwY23 workbuddy-newhost'
$sshDir  = "C:\ProgramData\ssh"
$keyfile = "$sshDir\administrators_authorized_keys"
if (-not (Test-Path $sshDir))  { New-Item -Path $sshDir -ItemType Directory -Force | Out-Null }
if (-not (Test-Path $keyfile)) { New-Item -Path $keyfile -ItemType File -Force | Out-Null }
$existing = Get-Content $keyfile -ErrorAction SilentlyContinue
if ($existing -notcontains $pub) { Add-Content -Path $keyfile -Value $pub }

# Also drop the key into JIANG's own .ssh dir (covers login whether or not the account is an admin).
$userSsh = "C:\Users\$user\.ssh"
$userKey  = "$userSsh\authorized_keys"
if (-not (Test-Path $userSsh)) { New-Item -Path $userSsh -ItemType Directory -Force | Out-Null }
if (-not (Test-Path $userKey)) { New-Item -Path $userKey -ItemType File -Force | Out-Null }
$u = Get-Content $userKey -ErrorAction SilentlyContinue
if ($u -notcontains $pub) { Add-Content -Path $userKey -Value $pub }

# Relax StrictModes so key-file ACLs are not a blocker on Windows.
$cfg = "$sshDir\sshd_config"
$lines = [System.Collections.ArrayList]@(Get-Content $cfg)
# Remove any existing StrictModes/PubkeyAuthentication lines (commented or not) to avoid duplicates or Match-block errors.
for ($i = $lines.Count - 1; $i -ge 0; $i--) {
    if ($lines[$i] -match '^\s*#?\s*(StrictModes|PubkeyAuthentication)\b') {
        $lines.RemoveAt($i)
    }
}
# Insert directives BEFORE the first Match block so they stay at top level.
$matchIdx = -1
for ($i = 0; $i -lt $lines.Count; $i++) {
    if ($lines[$i] -match '^\s*Match\s') { $matchIdx = $i; break }
}
if ($matchIdx -ge 0) {
    $lines.Insert($matchIdx, 'StrictModes no')
    $lines.Insert($matchIdx, 'PubkeyAuthentication yes')
} else {
    $lines.Add('PubkeyAuthentication yes')
    $lines.Add('StrictModes no')
}
Set-Content -Path $cfg -Value $lines
Restart-Service sshd
Write-Output "    public key installed, sshd restarted."

Write-Output "[5/6] Firewall rules (22 + 5985)..."
try {
    New-NetFirewallRule -Name "OpenSSH-Server-In-TCP" -DisplayName "OpenSSH Server (sshd)" -Enabled True -Direction Inbound -Protocol TCP -LocalPort 22 -Action Allow -ErrorAction Stop | Out-Null
} catch { netsh advfirewall firewall add rule name="OpenSSH-Server-In-TCP" dir=in action=allow protocol=TCP localport=22 | Out-Null }
try {
    New-NetFirewallRule -Name "WinRM-In-TCP" -DisplayName "WinRM (5985)" -Enabled True -Direction Inbound -Protocol TCP -LocalPort 5985 -Action Allow -ErrorAction Stop | Out-Null
} catch { netsh advfirewall firewall add rule name="WinRM-In-TCP" dir=in action=allow protocol=TCP localport=5985 | Out-Null }
Write-Output "    firewall rules added."

Write-Output "[6/6] Static IP check..."
if ($SET_STATIC_IP) {
    try {
        $cur = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.IPAddress -eq $STATIC_IP }
        if (-not $cur) {
            $gw = Get-NetRoute -DestinationPrefix "0.0.0.0/0" -ErrorAction SilentlyContinue | Select-Object -First 1
            $idx = $gw.InterfaceIndex
            if (-not $idx) { $idx = (Get-NetAdapter | Where-Object { $_.Status -eq "Up" } | Select-Object -First 1).ifIndex }
            Remove-NetIPAddress -InterfaceIndex $idx -Confirm:$false -ErrorAction SilentlyContinue
            New-NetIPAddress -InterfaceIndex $idx -IPAddress $STATIC_IP -PrefixLength $STATIC_MASK -DefaultGateway $STATIC_GW -ErrorAction Stop | Out-Null
            Set-DnsClientServerAddress -InterfaceIndex $idx -ServerAddresses $STATIC_GW -ErrorAction SilentlyContinue
            Write-Output "    static IP set to $STATIC_IP/$STATIC_MASK gw $STATIC_GW."
        } else { Write-Output "    already $STATIC_IP, skipped." }
    } catch { Write-Output "    static IP step skipped (non-fatal): $_" }
} else {
    Write-Output "    SET_STATIC_IP is false; relying on router DHCP reservation (192.168.10.165)."
}

$ip = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.IPAddress -match '^192\.168\.10\.' } | Select-Object -First 1).IPAddress
Write-Output ""
Write-Output "==================== DONE ===================="
Write-Output "From the assistant host, connect with:"
Write-Output "  ssh -i C:\Users\JIAN\.ssh\newhost_key JIANG@192.168.10.165"
if ($ip) { Write-Output "  (this host currently reports IP: $ip)" }
Write-Output "============================================="
Read-Host -Prompt "All done. Press Enter to close this window"

} catch {
    Write-Output ""
    Write-Output "!!!!!!!!!!!!!!!! SCRIPT FAILED !!!!!!!!!!!!!!!!"
    Write-Output "Error: $($_.Exception.Message)"
    Write-Output "At:    $($_.InvocationInfo.PositionMessage)"
    Write-Output "Full log saved to $log"
    Write-Output "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
    Read-Host -Prompt "Press Enter to close"
    exit 1
}
