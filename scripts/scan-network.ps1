# Ping-sweep the local /24 subnets to find devices (e.g. ATEM Mini Pro, PTZOptics camera).
param(
    [string[]]$Subnets = @('192.168.4', '192.168.5', '192.168.6', '192.168.7')
)

$alive = New-Object System.Collections.Generic.List[string]

foreach ($subnet in $Subnets) {
    $tasks = New-Object System.Collections.Generic.List[object]
    for ($i = 1; $i -le 254; $i++) {
        $ip = "$subnet.$i"
        $p = New-Object System.Net.NetworkInformation.Ping
        $task = $p.SendPingAsync($ip, 400)
        $tasks.Add([PSCustomObject]@{ IP = $ip; Task = $task; Ping = $p })
    }
    foreach ($t in $tasks) {
        try {
            $reply = $t.Task.GetAwaiter().GetResult()
            if ($reply.Status -eq 'Success') {
                $alive.Add($t.IP)
            }
        } catch {
        } finally {
            $t.Ping.Dispose()
        }
    }
}

Write-Host "=== Alive hosts ==="
$alive | Sort-Object { [version]($_ -replace '^\d+\.\d+\.(\d+)\.(\d+)$', '0.0.$1.$2') }

Write-Host ""
Write-Host "=== ARP table (post-scan) ==="
arp -a
