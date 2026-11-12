Set-StrictMode -Version 2

Import-Module (Join-Path $PSScriptRoot 'ChromePolicy.psm1') -ErrorAction Stop -DisableNameChecking
Import-Module (Join-Path $PSScriptRoot 'VpsTimezone.psm1') -ErrorAction Stop -DisableNameChecking

function Assert-PatchPath([string]$Path, [switch]$MustExist) {
    if ([string]::IsNullOrWhiteSpace($Path) -or -not [IO.Path]::IsPathRooted($Path) -or $Path.Replace('/', '\').StartsWith('\\')) {
        throw '请选择本机的绝对路径，暂不支持网络目录。'
    }
    $full = [IO.Path]::GetFullPath($Path)
    if ($full.Replace('/', '\').StartsWith('\\')) { throw '请选择本机的绝对路径，暂不支持网络目录。' }
    $cursor = $full
    while ($cursor) {
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force -ErrorAction Stop
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw '路径包含链接或重解析点，未修改。' }
        }
        $parent = [IO.Path]::GetDirectoryName($cursor)
        if ($parent -eq $cursor) { break }
        $cursor = $parent
    }
    if ($MustExist -and -not (Test-Path -LiteralPath $full)) { throw '所选路径不存在。' }
    return $full
}

function Get-PatchTarget([string]$ConfigDirectory) {
    $directory = Assert-PatchPath $ConfigDirectory -MustExist
    if (-not (Test-Path -LiteralPath $directory -PathType Container)) { throw '请选择 Clash Verge 配置文件夹。' }
    if (-not (Test-Path -LiteralPath (Join-Path $directory 'clash-verge.yaml')) -and
        -not (Test-Path -LiteralPath (Join-Path $directory 'verge.yaml'))) {
        throw '此目录没有 Clash Verge 配置标识，未修改。'
    }
    return (Assert-PatchPath (Join-Path $directory 'profiles\Script.js'))
}

function Get-PatchCandidates {
    foreach ($root in @($env:APPDATA, $env:LOCALAPPDATA)) {
        if ($root) {
            $candidate = Join-Path $root 'io.github.clash-verge-rev.clash-verge-rev'
            if (Test-Path -LiteralPath $candidate -PathType Container) { $candidate }
        }
    }
}

function Get-PatchStateRoot {
    if (-not $env:LOCALAPPDATA) { throw '缺少 LOCALAPPDATA，无法建立本机备份。' }
    return (Assert-PatchPath (Join-Path $env:LOCALAPPDATA 'claude-environment-patch'))
}

function Get-PatchBytesHash([byte[]]$Bytes) {
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha.ComputeHash($Bytes))).Replace('-', '').ToLowerInvariant() }
    finally { $sha.Dispose() }
}

function Get-PatchFileHash([string]$Path) {
    return (Get-PatchBytesHash ([IO.File]::ReadAllBytes($Path)))
}

function Write-PatchBytes([string]$Path, [byte[]]$Bytes) {
    $target = Assert-PatchPath $Path
    $parent = Split-Path -Parent $target
    if (-not (Test-Path -LiteralPath $parent)) { [IO.Directory]::CreateDirectory($parent) | Out-Null }
    Assert-PatchPath $parent -MustExist | Out-Null
    $temp = Join-Path $parent ('.claude-patch-' + [Guid]::NewGuid().ToString('N') + '.tmp')
    $previous = Join-Path $parent ('.claude-patch-previous-' + [Guid]::NewGuid().ToString('N') + '.tmp')
    $replaceCompleted = $false
    try {
        [IO.File]::WriteAllBytes($temp, $Bytes)
        Assert-PatchPath $target | Out-Null
        if (Test-Path -LiteralPath $target) {
            [IO.File]::Replace($temp, $target, $previous)
            $replaceCompleted = $true
        }
        else { [IO.File]::Move($temp, $target) }
    } finally {
        if (Test-Path -LiteralPath $temp) { [IO.File]::Delete($temp) }
        if ($replaceCompleted -and (Test-Path -LiteralPath $previous)) { [IO.File]::Delete($previous) }
    }
}

function Write-PatchJson([string]$Path, $Value) {
    $bytes = (New-Object Text.UTF8Encoding($false)).GetBytes(($Value | ConvertTo-Json -Depth 8))
    Write-PatchBytes $Path $bytes
}

function Get-PatchReceipt([string]$BackupDirectory) {
    $root = Join-Path (Get-PatchStateRoot) 'backups'
    $folder = Assert-PatchPath $BackupDirectory -MustExist
    $rootPrefix = $root.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if (-not $folder.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw '请选择本工具备份目录中的一份备份。'
    }
    $file = Assert-PatchPath (Join-Path $folder 'receipt.json') -MustExist
    $receipt = [IO.File]::ReadAllText($file) | ConvertFrom-Json -ErrorAction Stop
    if ($receipt.schemaVersion -ne 1 -or $receipt.scriptPath -ne (Get-PatchTarget $receipt.configDirectory)) {
        throw '备份记录不完整或目标不一致，未恢复。'
    }
    return $receipt
}

function Get-LatestPatchReceipt {
    $latest = Join-Path (Get-PatchStateRoot) 'latest.json'
    if (-not (Test-Path -LiteralPath $latest)) { return $null }
    Assert-PatchPath $latest -MustExist | Out-Null
    $pointer = [IO.File]::ReadAllText($latest) | ConvertFrom-Json -ErrorAction Stop
    return (Get-PatchReceipt $pointer.backupDirectory)
}

function Test-StockPatchScript([string]$Text) {
    $gap = '(?:\s|/\*[\s\S]*?\*/|//[^\r\n]*(?:\r?\n|$))*'
    $sep = '(?:\s|/\*[\s\S]*?\*/|//[^\r\n]*(?:\r?\n|$))+'
    $pattern = '\A' + $gap + 'function' + $sep + 'main' + $gap + '\(' + $gap + 'config' + $gap +
        '(?:,' + $gap + 'profileName' + $gap + ')?\)' + $gap + '\{' + $gap + 'return' + $sep +
        'config' + $gap + ';?' + $gap + '\}' + $gap + '\z'
    $plain = $Text.TrimStart([char]0xFEFF)
    return ([regex]::IsMatch($plain, '\A' + $gap + '\z') -or [regex]::IsMatch($plain, $pattern))
}

function Get-PatchStatus([string]$ConfigDirectory) {
    $target = Get-PatchTarget $ConfigDirectory
    $exists = Test-Path -LiteralPath $target -PathType Leaf
    $owned = $false
    $receipt = Get-LatestPatchReceipt
    if ($receipt -and $exists -and $receipt.scriptPath -eq $target) {
        $owned = (Get-PatchFileHash $target) -eq $receipt.appliedSha256
    }
    return [pscustomobject]@{ Timezone = (Get-TimeZone).Id; ScriptExists = $exists; OwnedScript = $owned; ScriptPath = $target }
}

function Install-EnvironmentPatch {
    [CmdletBinding()]
    param([Parameter(Mandatory=$true)][string]$ConfigDirectory,
          [Parameter(Mandatory=$true)][string]$ProxyGroup,
          [string]$Timezone = '', [string]$TemplatePath = (Join-Path $PSScriptRoot 'dns-patch.js'),
          [switch]$ConfigureChrome, [switch]$ConfigureChromeDns,
          [switch]$FollowVpsTimezone, [string]$VpsDestination = '', [int]$VpsPort = 22)
    if ($env:OS -ne 'Windows_NT') { throw '安装操作仅支持 Windows。' }
    if ([string]::IsNullOrWhiteSpace($ProxyGroup) -or $ProxyGroup.Length -gt 200 -or $ProxyGroup -match '[\x00-\x1f\x7f]') {
        throw '请输入有效的代理组名称。'
    }
    if ($ProxyGroup -match '[&=%]') { throw '代理组名包含 &、= 或 %，可能被内核误解析为 DNS 参数。请由操作人员人工处理，未安装。' }
    if ($FollowVpsTimezone -and $Timezone) { throw '跟随 VPS 与手工时区不能同时指定，未安装。' }
    $vpsTimezone = $null
    if ($FollowVpsTimezone) {
        $vpsTimezone = Get-VpsTimezone -Destination $VpsDestination -Port $VpsPort
        $Timezone = $vpsTimezone.WindowsId
    } elseif ($Timezone) {
        $Timezone = (Resolve-VpsWindowsTimezone -WindowsId $Timezone).Id
    }
    if ($ConfigureChromeDns -and -not $ConfigureChrome) { throw '请先选择 Chrome 策略配置，再选择让 DNS 跟随 TUN。' }
    if ($ConfigureChromeDns) { Assert-PatchTunDns $ConfigDirectory }
    $chromePlan = $null
    $chromeChanges = $false
    if ($ConfigureChrome) {
        $chromePlan = Get-ChromePolicyPlan -IncludeDns:$ConfigureChromeDns
        $chromeChanges = @($chromePlan.Items | Where-Object { $_.Changed }).Count -gt 0
    }
    $target = Get-PatchTarget $ConfigDirectory
    $template = [IO.File]::ReadAllText((Assert-PatchPath $TemplatePath -MustExist))
    $declaration = 'const CLAUDE_PATCH_PROXY_GROUP = "__CLAUDE_PATCH_PROXY_GROUP__";'
    if ([regex]::Matches($template, [regex]::Escape($declaration)).Count -ne 1) { throw '补丁模板标记无效。' }
    $replacement = 'const CLAUDE_PATCH_PROXY_GROUP = ' + ($ProxyGroup | ConvertTo-Json -Compress) + ';'
    $script = $template.Replace($declaration, $replacement)
    [byte[]]$newBytes = (New-Object Text.UTF8Encoding($false)).GetBytes($script)
    $appliedHash = Get-PatchBytesHash $newBytes
    $existed = Test-Path -LiteralPath $target -PathType Leaf
    [byte[]]$original = @()
    $oldHash = ''
    $currentZone = (Get-TimeZone -ErrorAction Stop).Id
    $latest = Get-LatestPatchReceipt
    if ($existed) {
        $original = [IO.File]::ReadAllBytes($target)
        $oldHash = Get-PatchBytesHash $original
        $owned = $latest -and $latest.scriptPath -eq $target -and $latest.appliedSha256 -eq $oldHash
        if ($oldHash -eq $appliedHash -and (!$Timezone -or $Timezone -eq $currentZone) -and -not $chromeChanges) {
            return [pscustomobject]@{ Changed = $false; BackupDirectory = ''; VpsTimezone = $vpsTimezone;
                Message = '补丁文件和所选时区已一致；核心 DNS 上游仍需单独验证。' }
        }
        if (-not $owned -and -not (Test-StockPatchScript ([IO.File]::ReadAllText($target)))) {
            throw '检测到现有自定义脚本或安装后的人工修改，未覆盖。请先人工合并配置。'
        }
    }
    $stateRoot = Get-PatchStateRoot
    $backup = Join-Path (Join-Path $stateRoot 'backups') ([Guid]::NewGuid().ToString('N'))
    Assert-PatchPath $backup | Out-Null
    [IO.Directory]::CreateDirectory($backup) | Out-Null
    if ($existed) { [IO.File]::WriteAllBytes((Join-Path $backup 'Script.original.bin'), $original) }
    $receipt = [ordered]@{ schemaVersion = 1; status = 'installing'; backupDirectory = $backup;
        configDirectory = (Split-Path -Parent (Split-Path -Parent $target)); scriptPath = $target;
        existed = [bool]$existed; originalSha256 = $oldHash; originalTimezone = $currentZone;
        desiredTimezone = $Timezone; appliedSha256 = $appliedHash; createdUtc = [DateTime]::UtcNow.ToString('o') }
    if ($chromePlan) { $receipt['chromePolicy'] = $chromePlan }
    if ($vpsTimezone) { $receipt['vpsTimezone'] = $vpsTimezone }
    $receiptFile = Join-Path $backup 'receipt.json'
    Write-PatchJson $receiptFile $receipt
    $scriptWritten = $false
    $chromeApplied = $false
    try {
        if ($existed -and (Get-PatchFileHash $target) -ne $oldHash) { throw '安装前脚本被其他程序修改，已保留备份。' }
        if (-not $existed -and (Test-Path -LiteralPath $target)) { throw '安装前出现新的脚本，未覆盖。' }
        Write-PatchBytes $target $newBytes
        $scriptWritten = $true
        if ((Get-PatchFileHash $target) -ne $appliedHash) { throw '写入后脚本回读不一致。' }
        if ($chromePlan) {
            Apply-ChromePolicy $chromePlan
            $chromeApplied = $true
        }
        if ($Timezone -and $Timezone -ne $currentZone) {
            Set-TimeZone -Id $Timezone -ErrorAction Stop
            if ((Get-TimeZone -ErrorAction Stop).Id -ne $Timezone) { throw '时区回读不一致。' }
        }
        $receipt.status = 'applied'
        Write-PatchJson $receiptFile $receipt
        Write-PatchJson (Join-Path $stateRoot 'latest.json') @{ backupDirectory = $backup }
    } catch {
        $failure = $_.Exception.Message
        $rollbackErrors = @()
        if ($_.Exception.Data.Contains('ChromeRollbackFailed') -and $_.Exception.Data['ChromeRollbackFailed']) {
            $rollbackErrors += 'Chrome 策略部分原值未能恢复'
        }
        if ($chromeApplied) {
            try { Restore-ChromePolicy $chromePlan }
            catch { $rollbackErrors += 'Chrome 策略回滚失败' }
        }
        if ($scriptWritten) {
            try {
                if ((Get-PatchFileHash $target) -ne $appliedHash) { throw '脚本已被其他程序修改。' }
                if ($existed) { Write-PatchBytes $target $original } else { [IO.File]::Delete($target) }
            } catch { $rollbackErrors += '脚本回滚失败' }
        }
        try {
            if ((Get-TimeZone -ErrorAction Stop).Id -ne $currentZone) { Set-TimeZone -Id $currentZone -ErrorAction Stop }
        } catch { $rollbackErrors += '时区回滚失败' }
        $receipt.status = if ($rollbackErrors.Count) { 'rollback-failed' } else { 'rolled-back' }
        Write-PatchJson $receiptFile $receipt
        if ($rollbackErrors.Count) { throw ('安装未完成；' + ($rollbackErrors -join '、') + '。请保留备份：' + $backup) }
        throw ('安装未完成，已回滚本次改动。备份保留于：' + $backup + '。原因：' + $failure)
    }
    $message = '补丁已写入并备份。请在 Clash Verge 重新应用订阅；写入文件不等于核心已经生效。'
    if ($vpsTimezone) { $message += '本机时区已根据 VPS 实际系统时区 ' + $vpsTimezone.IanaId + ' 设置为 ' + $vpsTimezone.WindowsId + '。' }
    if ($chromePlan) { $message += '当前用户 Chrome 策略已写入，请到 chrome://policy 验证；界面语言可能需要重开 Chrome。' }
    return [pscustomobject]@{ Changed = $true; BackupDirectory = $backup; VpsTimezone = $vpsTimezone; Message = $message }
}

function Restore-EnvironmentPatch([string]$BackupDirectory) {
    if ($env:OS -ne 'Windows_NT') { throw '恢复操作仅支持 Windows。' }
    if (-not $BackupDirectory) {
        $receipt = Get-LatestPatchReceipt
        if (-not $receipt) { throw '没有本工具的备份记录，未删除或修改文件。' }
        $BackupDirectory = $receipt.backupDirectory
    }
    $receipt = Get-PatchReceipt $BackupDirectory
    $target = $receipt.scriptPath
    if (-not (Test-Path -LiteralPath $target -PathType Leaf) -or (Get-PatchFileHash $target) -ne $receipt.appliedSha256) {
        throw '当前脚本已改变或不存在，未覆盖。请人工核对所选备份。'
    }
    $zone = (Get-TimeZone -ErrorAction Stop).Id
    if ($receipt.desiredTimezone -and $zone -ne $receipt.desiredTimezone) { throw '系统时区已被再次修改，未恢复。请人工核对。' }
    [byte[]]$original = @()
    if ($receipt.existed) {
        $file = Assert-PatchPath (Join-Path $BackupDirectory 'Script.original.bin') -MustExist
        $original = [IO.File]::ReadAllBytes($file)
        if ((Get-PatchBytesHash $original) -ne $receipt.originalSha256) { throw '原始备份校验失败，未恢复。' }
    }
    $chromePlan = $null
    if ($receipt.PSObject.Properties['chromePolicy'] -and $receipt.chromePolicy) {
        $chromePlan = $receipt.chromePolicy
        Assert-ChromePolicyRestorable $chromePlan
    }
    $chromeRestored = $false
    try {
        if ($receipt.desiredTimezone -and $zone -ne $receipt.originalTimezone) {
            Set-TimeZone -Id $receipt.originalTimezone -ErrorAction Stop
            if ((Get-TimeZone).Id -ne $receipt.originalTimezone) { throw '时区恢复回读失败。' }
        }
        if ($chromePlan) {
            Restore-ChromePolicy $chromePlan
            $chromeRestored = $true
        }
        Assert-PatchPath $target -MustExist | Out-Null
        if ((Get-PatchFileHash $target) -ne $receipt.appliedSha256) { throw '恢复前脚本被其他程序修改。' }
        if ($receipt.existed) { Write-PatchBytes $target $original } else { [IO.File]::Delete($target) }
    } catch {
        $rollbackErrors = @()
        if ($_.Exception.Data.Contains('ChromeRollbackFailed') -and $_.Exception.Data['ChromeRollbackFailed']) {
            $rollbackErrors += 'Chrome 策略部分值未能回退'
        }
        if ($chromeRestored) {
            try { Apply-ChromePolicy $chromePlan }
            catch { $rollbackErrors += 'Chrome 策略回退失败' }
        }
        if ((Get-TimeZone).Id -ne $zone) {
            try { Set-TimeZone -Id $zone -ErrorAction Stop }
            catch { $rollbackErrors += '时区回退失败' }
        }
        $message = '本次恢复未完成；备份已保留。'
        if ($rollbackErrors.Count) { $message += ($rollbackErrors -join '、') + '，请人工核对。' }
        throw $message
    }
    $receipt.status = 'restored'
    Write-PatchJson (Join-Path $BackupDirectory 'receipt.json') $receipt
    return '已恢复所选备份。请在 Clash Verge 重新应用订阅；核心状态需再次验证。'
}

function Get-PatchYamlScalar([string]$Text, [string]$Key) {
    $matches = [regex]::Matches($Text, '(?m)^' + [regex]::Escape($Key) + ':\s*([^\r\n]*)$')
    if ($matches.Count -gt 1) { throw '控制接口配置重复，无法安全读取。' }
    if ($matches.Count -eq 0) { return '' }
    $value = $matches[0].Groups[1].Value.Trim()
    if ($value -match '^"([^"\\]*)"\s*(?:#.*)?$' -or $value -match "^'([^']*)'\s*(?:#.*)?$") { return $Matches[1] }
    $value = ($value -replace '\s+#.*$', '').Trim()
    if ($value -notmatch '^[A-Za-z0-9_.:/@+\-]*$') { throw '控制接口包含复杂 YAML，请由操作人员核对。' }
    return $value
}

function Get-PatchApi([string]$ConfigDirectory) {
    Get-PatchTarget $ConfigDirectory | Out-Null
    $file = Assert-PatchPath (Join-Path $ConfigDirectory 'clash-verge.yaml') -MustExist
    $text = [IO.File]::ReadAllText($file)
    $address = Get-PatchYamlScalar $text 'external-controller'
    if (-not $address) { throw '未找到控制接口，DNS 上游尚未验证。' }
    if ($address -notmatch '^[a-zA-Z]+://') { $address = 'http://' + $address }
    $uri = $null
    if (-not [Uri]::TryCreate($address, [UriKind]::Absolute, [ref]$uri) -or
        $uri.Scheme -ne 'http' -or $uri.Host.Trim('[', ']') -notin @('127.0.0.1', 'localhost', '::1') -or
        $uri.UserInfo -or $uri.AbsolutePath -ne '/' -or $uri.Query -or $uri.Fragment) {
        throw '仅允许本机回环 HTTP 控制接口，未发送认证信息。'
    }
    return @{ Base = $uri.GetLeftPart([UriPartial]::Authority); Secret = (Get-PatchYamlScalar $text 'secret') }
}

function Invoke-PatchJsonGet($Api, [string]$Route) {
    $request = [Net.HttpWebRequest]::Create($Api.Base + $Route)
    $request.Proxy = $null
    $request.AllowAutoRedirect = $false
    $request.Timeout = 4000
    $request.ReadWriteTimeout = 4000
    if ($Api.Secret) { $request.Headers['Authorization'] = 'Bearer ' + $Api.Secret }
    $response = $request.GetResponse()
    try {
        $reader = New-Object IO.StreamReader($response.GetResponseStream())
        try { return ($reader.ReadToEnd() | ConvertFrom-Json -ErrorAction Stop) }
        finally { $reader.Dispose() }
    } finally { $response.Dispose() }
}

function Assert-PatchTunDns([string]$ConfigDirectory) {
    $api = Get-PatchApi $ConfigDirectory
    try { $config = Invoke-PatchJsonGet $api '/configs' }
    catch { throw '无法核实本机 TUN 状态，未更改 Chrome DNS。请先打开代理核心控制接口。' }
    $tunProperty = $config.PSObject.Properties['tun']
    if (-not $tunProperty -or -not $tunProperty.Value) { throw '未检测到 TUN 配置，未更改 Chrome DNS。' }
    $tun = $tunProperty.Value
    $enabled = $tun.PSObject.Properties['enable']
    $hijack = $tun.PSObject.Properties['dns-hijack']
    $capturesDns = $false
    if ($hijack) {
        foreach ($rule in @($hijack.Value)) {
            if ($rule -is [string] -and $rule -in @('any:53', 'udp://any:53')) { $capturesDns = $true }
        }
    }
    if (-not $enabled -or $enabled.Value -ne $true -or -not $capturesDns) {
        throw '请先开启 TUN 与全局 UDP 53 端口 DNS 劫持（any:53），再选择让 Chrome DNS 跟随 TUN；本次未安装。'
    }
}

function Test-PatchDnsResponse([string]$ConfigDirectory) {
    $api = Get-PatchApi $ConfigDirectory
    $nonce = 'patch-check-' + [Guid]::NewGuid().ToString('N') + '.claude.ai'
    $rows = @()
    foreach ($type in @('A', 'HTTPS', 'TXT')) {
        try {
            $answer = Invoke-PatchJsonGet $api ('/dns/query?name=' + [Uri]::EscapeDataString($nonce) + '&type=' + $type)
            $rows += ($type + '：收到 DNS 响应，状态码 ' + $answer.Status)
        } catch { $rows += ($type + '：请求失败或超时，尚未验证') }
    }
    return [pscustomobject]@{ UpstreamVerified = $false; Results = $rows;
        Message = '此检查只证明控制接口返回 DNS 响应，不能证明真实上游或无泄漏。交付验收需另查核心日志中的 DNS 上游。' }
}

Export-ModuleMember -Function Get-PatchCandidates, Get-PatchStatus, Install-EnvironmentPatch, Restore-EnvironmentPatch, Test-PatchDnsResponse
