# TCP-connect scan of port 80 (and optionally other ports) across a /24, independent of ICMP.
param(
    [string]$Subnet = '192.168.4',
    [int[]]$Ports = @(80, 554, 1259, 1240, 5678)
)

$openHosts = New-Object System.Collections.Generic.List[string]

for ($i = 1; $i -le 254; $i++) {
    $ip = "$Subnet.$i"
    foreach ($port in $Ports) {
        $client = New-Object System.Net.Sockets.TcpClient
        try {
            $connect = $client.BeginConnect($ip, $port, $null, $null)
            $success = $connect.AsyncWaitHandle.WaitOne(150)
            if ($success -and $client.Connected) {
                $openHosts.Add("$ip : $port")
            }
        } catch {
        } finally {
            $client.Close()
        }
    }
}

Write-Host "=== Hosts with open ports ==="
$openHosts
