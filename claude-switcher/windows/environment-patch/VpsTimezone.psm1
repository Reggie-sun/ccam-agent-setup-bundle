Set-StrictMode -Version 2

function Assert-VpsTimezoneDestination([string]$Destination, [int]$Port) {
    if ($Port -lt 1 -or $Port -gt 65535) { throw 'SSH 端口必须为 1 到 65535。' }
    if ([string]::IsNullOrWhiteSpace($Destination) -or $Destination.Length -gt 320 -or
        $Destination -cnotmatch '\A(?:[A-Za-z0-9][A-Za-z0-9._-]*@)?[A-Za-z0-9][A-Za-z0-9._-]*\z') {
        throw '请输入 user@host 或已有 SSH 别名，仅支持安全 ASCII 名称；暂不支持 IPv6、空白或命令参数。'
    }
}

function Invoke-VpsTimezoneSsh([string]$Destination, [int]$Port) {
    Assert-VpsTimezoneDestination $Destination $Port
    $command = Get-Command -Name 'ssh.exe' -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $command) { throw '未找到现有 Windows OpenSSH ssh.exe，请由操作人员先准备 SSH 客户端。' }
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = $command.Path
    $info.Arguments = '-o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=8 -T -p ' +
        $Port + ' ' + $Destination + ' "timedatectl show -p Timezone --value"'
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardInput = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $process = New-Object Diagnostics.Process
    $process.StartInfo = $info
    $clock = [Diagnostics.Stopwatch]::StartNew()
    try {
        if (-not $process.Start()) { throw 'SSH 查询未能启动。' }
        $process.StandardInput.Close()
        $output = $process.StandardOutput.ReadToEndAsync()
        $errors = $process.StandardError.ReadToEndAsync()
        $remaining = [Math]::Max(1, 15000 - [int]$clock.ElapsedMilliseconds)
        if (-not $process.WaitForExit($remaining)) {
            $process.Kill()
            throw 'SSH 查询超过 15 秒，已终止本工具启动的 SSH 进程，未修改任何时区。'
        }
        $remaining = [Math]::Max(1, 15000 - [int]$clock.ElapsedMilliseconds)
        if (-not [Threading.Tasks.Task]::WaitAll([Threading.Tasks.Task[]]@($output, $errors), $remaining)) {
            if (-not $process.HasExited) { $process.Kill() }
            throw 'SSH 输出读取超时，未修改任何时区。'
        }
        return [pscustomobject]@{ ExitCode = $process.ExitCode; Stdout = $output.Result; Stderr = $errors.Result }
    } finally {
        $clock.Stop()
        $process.Dispose()
    }
}

function Resolve-VpsWindowsTimezone([string]$WindowsId) {
    try { return [TimeZoneInfo]::FindSystemTimeZoneById($WindowsId) }
    catch { throw ('当前 Windows 不支持此时区：' + $WindowsId + '。未修改时区。') }
}

function Convert-VpsTimezone([string]$IanaId) {
    if ($IanaId -cnotmatch '\A[A-Za-z][A-Za-z0-9._+-]*(?:/[A-Za-z0-9._+-]+)*\z') {
        throw 'VPS 返回的 IANA 时区名称无效，未修改时区。'
    }
    $file = Join-Path $PSScriptRoot 'iana-windows-map.json'
    $map = [IO.File]::ReadAllText($file) | ConvertFrom-Json -ErrorAction Stop
    $properties = @($map.PSObject.Properties | Where-Object { $_.Name -ceq $IanaId })
    if ($properties.Count -ne 1 -or $properties[0].Value -isnot [string]) {
        throw ('离线映射未收录 VPS 时区：' + $IanaId + '。请由操作人员核对，未按城市或偏移猜测。')
    }
    $zone = Resolve-VpsWindowsTimezone $properties[0].Value
    return [pscustomobject]@{ IanaId = $IanaId; WindowsId = $zone.Id }
}

function Get-VpsTimezone {
    [CmdletBinding()]
    param([Parameter(Mandatory=$true)][string]$Destination, [int]$Port = 22)
    Assert-VpsTimezoneDestination $Destination $Port
    $response = Invoke-VpsTimezoneSsh -Destination $Destination -Port $Port
    if ($response.ExitCode -ne 0) {
        throw 'VPS 时区查询失败。请先完成 SSH 密钥登录和主机密钥确认；本工具不接收密码或私钥。'
    }
    $raw = [string]$response.Stdout
    if ($raw -cnotmatch '\A([A-Za-z][A-Za-z0-9._+-]*(?:/[A-Za-z0-9._+-]+)*)(?:\r?\n)?\z') {
        throw 'VPS 输出不是单行有效 IANA 时区，未修改任何设置。'
    }
    $converted = Convert-VpsTimezone $Matches[1]
    return [pscustomobject]@{ IanaId = $converted.IanaId; WindowsId = $converted.WindowsId;
        Destination = $Destination; Port = $Port; FetchedUtc = [DateTime]::UtcNow.ToString('o') }
}

Export-ModuleMember -Function Get-VpsTimezone, Convert-VpsTimezone, Resolve-VpsWindowsTimezone
