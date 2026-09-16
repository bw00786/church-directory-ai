# Find processes locking a file via the Windows Restart Manager API (no admin needed).
param([Parameter(Mandatory)][string]$Path)

Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class RmApi {
    [StructLayout(LayoutKind.Sequential)] public struct RM_UNIQUE_PROCESS { public int dwProcessId; public System.Runtime.InteropServices.ComTypes.FILETIME ProcessStartTime; }
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)] public struct RM_PROCESS_INFO {
        public RM_UNIQUE_PROCESS Process;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 256)] public string strAppName;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 64)] public string strServiceShortName;
        public int ApplicationType; public uint AppStatus; public uint TSSessionId; [MarshalAs(UnmanagedType.Bool)] public bool bRestartable;
    }
    [DllImport("rstrtmgr.dll", CharSet = CharSet.Unicode)] public static extern int RmStartSession(out uint pSessionHandle, int dwSessionFlags, string strSessionKey);
    [DllImport("rstrtmgr.dll")] public static extern int RmEndSession(uint pSessionHandle);
    [DllImport("rstrtmgr.dll", CharSet = CharSet.Unicode)] public static extern int RmRegisterResources(uint pSessionHandle, uint nFiles, string[] rgsFilenames, uint nApplications, IntPtr rgApplications, uint nServices, string[] rgsServiceNames);
    [DllImport("rstrtmgr.dll")] public static extern int RmGetList(uint dwSessionHandle, out uint pnProcInfoNeeded, ref uint pnProcInfo, [In, Out] RM_PROCESS_INFO[] rgAffectedApps, ref uint lpdwRebootReasons);
}
"@

$handle = 0
$key = [Guid]::NewGuid().ToString()
if ([RmApi]::RmStartSession([ref]$handle, 0, $key) -ne 0) { throw "RmStartSession failed" }
try {
    if ([RmApi]::RmRegisterResources($handle, 1, @($Path), 0, [IntPtr]::Zero, 0, $null) -ne 0) { throw "RmRegisterResources failed" }
    $needed = 0; $count = 0; $reasons = 0
    $rc = [RmApi]::RmGetList($handle, [ref]$needed, [ref]$count, $null, [ref]$reasons)
    if ($rc -eq 234 -and $needed -gt 0) {  # ERROR_MORE_DATA
        $count = $needed
        $info = New-Object 'RmApi+RM_PROCESS_INFO[]' $count
        $rc = [RmApi]::RmGetList($handle, [ref]$needed, [ref]$count, $info, [ref]$reasons)
        if ($rc -ne 0) { throw "RmGetList failed: $rc" }
        foreach ($i in $info) {
            $p = Get-Process -Id $i.Process.dwProcessId -ErrorAction SilentlyContinue
            [pscustomobject]@{ Pid = $i.Process.dwProcessId; App = $i.strAppName; Type = $i.ApplicationType; Exe = $p.Path }
        }
    } elseif ($rc -eq 0) {
        "No process reported locking: $Path"
    } else { throw "RmGetList failed: $rc" }
} finally { [RmApi]::RmEndSession($handle) | Out-Null }
