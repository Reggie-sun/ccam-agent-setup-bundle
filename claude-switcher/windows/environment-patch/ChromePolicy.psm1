Set-StrictMode -Version 2

function Get-ChromePolicyDefinitions([switch]$IncludeDns) {
    $items = @(
        @{ Subkey = ''; Name = 'ApplicationLocaleValue'; Kind = 'String'; Value = 'zh-CN' },
        @{ Subkey = 'ForcedLanguages'; Name = '1'; Kind = 'String'; Value = 'en-US' },
        @{ Subkey = 'ForcedLanguages'; Name = '2'; Kind = 'String'; Value = 'en' },
        @{ Subkey = ''; Name = 'WebRtcIPHandling'; Kind = 'String'; Value = 'disable_non_proxied_udp' },
        @{ Subkey = ''; Name = 'DefaultGeolocationSetting'; Kind = 'DWord'; Value = 2 }
    )
    if ($IncludeDns) { $items += @{ Subkey = ''; Name = 'DnsOverHttpsMode'; Kind = 'String'; Value = 'off' } }
    return $items
}

function Get-ChromeRegistryValue([ValidateSet('CurrentUser', 'LocalMachine')][string]$Hive, [string]$Subkey, [string]$Name) {
    $root = if ($Hive -eq 'CurrentUser') { [Microsoft.Win32.Registry]::CurrentUser } else { [Microsoft.Win32.Registry]::LocalMachine }
    $path = 'Software\Policies\Google\Chrome'
    if ($Subkey) { $path += '\' + $Subkey }
    $key = $root.OpenSubKey($path, $false)
    if ($null -eq $key) { return [pscustomobject]@{ Exists = $false; Kind = ''; Value = $null } }
    try {
        if ($key.GetValueNames() -notcontains $Name) { return [pscustomobject]@{ Exists = $false; Kind = ''; Value = $null } }
        return [pscustomobject]@{ Exists = $true; Kind = $key.GetValueKind($Name).ToString();
            Value = $key.GetValue($Name, $null, [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames) }
    } finally { $key.Dispose() }
}

function Set-ChromeRegistryValue([ValidateSet('CurrentUser', 'LocalMachine')][string]$Hive, [string]$Subkey, [string]$Name, [string]$Kind, $Value) {
    $root = if ($Hive -eq 'CurrentUser') { [Microsoft.Win32.Registry]::CurrentUser } else { [Microsoft.Win32.Registry]::LocalMachine }
    $path = 'Software\Policies\Google\Chrome'
    if ($Subkey) { $path += '\' + $Subkey }
    $typedValue = switch ($Kind) {
        'DWord' { [int]$Value; break }
        'QWord' { [long]$Value; break }
        'Binary' { ,([byte[]]$Value); break }
        'None' { ,([byte[]]$Value); break }
        'MultiString' { ,([string[]]$Value); break }
        default { [string]$Value }
    }
    $key = $root.CreateSubKey($path)
    try { $key.SetValue($Name, $typedValue, [Enum]::Parse([Microsoft.Win32.RegistryValueKind], $Kind)) }
    finally { $key.Dispose() }
}

function Remove-ChromeRegistryValue([ValidateSet('CurrentUser', 'LocalMachine')][string]$Hive, [string]$Subkey, [string]$Name) {
    $root = if ($Hive -eq 'CurrentUser') { [Microsoft.Win32.Registry]::CurrentUser } else { [Microsoft.Win32.Registry]::LocalMachine }
    $path = 'Software\Policies\Google\Chrome'
    if ($Subkey) { $path += '\' + $Subkey }
    $key = $root.OpenSubKey($path, $true)
    if ($null -eq $key) { return }
    try { $key.DeleteValue($Name, $false) }
    finally { $key.Dispose() }
}

function Test-ChromePolicyValue($Left, $Right) {
    if ([bool]$Left.Exists -ne [bool]$Right.Exists) { return $false }
    if (-not $Left.Exists) { return $true }
    return ($Left.Kind -ceq $Right.Kind -and
        ($Left.Value | ConvertTo-Json -Compress -Depth 5) -ceq ($Right.Value | ConvertTo-Json -Compress -Depth 5))
}

function Get-ChromePolicyPlan([switch]$IncludeDns) {
    $items = @()
    foreach ($definition in (Get-ChromePolicyDefinitions -IncludeDns:$IncludeDns)) {
        $before = Get-ChromeRegistryValue 'CurrentUser' $definition.Subkey $definition.Name
        $machine = Get-ChromeRegistryValue 'LocalMachine' $definition.Subkey $definition.Name
        $applied = [pscustomobject]@{ Exists = $true; Kind = $definition.Kind; Value = $definition.Value }
        if ($machine.Exists -and -not (Test-ChromePolicyValue $machine $applied)) {
            throw ('Chrome 机器级策略冲突：' + $definition.Name + '。请先联系管理员，本次未安装。')
        }
        $items += [pscustomobject]@{ Subkey = $definition.Subkey; Name = $definition.Name;
            Original = $before; Applied = $applied; Changed = (-not (Test-ChromePolicyValue $before $applied)) }
    }
    return [pscustomobject]@{ Version = 1; IncludeDns = [bool]$IncludeDns; Items = $items }
}

function Get-ChromePolicyState {
    foreach ($definition in (Get-ChromePolicyDefinitions -IncludeDns)) {
        $user = Get-ChromeRegistryValue 'CurrentUser' $definition.Subkey $definition.Name
        $machine = Get-ChromeRegistryValue 'LocalMachine' $definition.Subkey $definition.Name
        $desired = [pscustomobject]@{ Exists = $true; Kind = $definition.Kind; Value = $definition.Value }
        [pscustomobject]@{ Name = $definition.Name; Subkey = $definition.Subkey;
            UserValueWritten = (Test-ChromePolicyValue $user $desired); MachineValueExists = [bool]$machine.Exists;
            MachineConflict = ($machine.Exists -and -not (Test-ChromePolicyValue $machine $desired)); ChromeVerified = $false }
    }
}

function Assert-ChromePolicyPlan($Plan) {
    if ($Plan.Version -ne 1) { throw 'Chrome 备份版本不支持，未修改注册表。' }
    $definitions = @(Get-ChromePolicyDefinitions -IncludeDns:([bool]$Plan.IncludeDns))
    if (@($Plan.Items).Count -ne $definitions.Count) { throw 'Chrome 备份项目数量异常，未修改注册表。' }
    for ($index = 0; $index -lt $definitions.Count; $index++) {
        $item = $Plan.Items[$index]; $definition = $definitions[$index]
        $expected = [pscustomobject]@{ Exists = $true; Kind = $definition.Kind; Value = $definition.Value }
        if ($item.Subkey -cne $definition.Subkey -or $item.Name -cne $definition.Name -or
            -not (Test-ChromePolicyValue $item.Applied $expected) -or
            [bool]$item.Changed -ne (-not (Test-ChromePolicyValue $item.Original $expected))) {
            throw 'Chrome 备份内容超出策略白名单，未修改注册表。'
        }
        if ($item.Original.Exists -and $item.Original.Kind -notin @('String', 'ExpandString', 'DWord', 'QWord', 'Binary', 'MultiString', 'None')) {
            throw 'Chrome 原值类型无法恢复，未修改注册表。'
        }
    }
}

function Set-ChromePolicyState($Item, $State) {
    if ($State.Exists) { Set-ChromeRegistryValue 'CurrentUser' $Item.Subkey $Item.Name $State.Kind $State.Value }
    else { Remove-ChromeRegistryValue 'CurrentUser' $Item.Subkey $Item.Name }
    $actual = Get-ChromeRegistryValue 'CurrentUser' $Item.Subkey $Item.Name
    if (-not (Test-ChromePolicyValue $actual $State)) { throw ('Chrome 策略回读不一致：' + $Item.Name) }
}

function Apply-ChromePolicy($Plan) {
    Assert-ChromePolicyPlan $Plan
    foreach ($item in $Plan.Items) {
        $machine = Get-ChromeRegistryValue 'LocalMachine' $item.Subkey $item.Name
        if ($machine.Exists -and -not (Test-ChromePolicyValue $machine $item.Applied)) {
            throw ('Chrome 机器级策略冲突：' + $item.Name + '，未写入。')
        }
        $actual = Get-ChromeRegistryValue 'CurrentUser' $item.Subkey $item.Name
        if (-not (Test-ChromePolicyValue $actual $item.Original)) { throw 'Chrome 策略在备份后被修改，未写入。' }
    }
    $attempted = @()
    try {
        foreach ($item in $Plan.Items) {
            if (-not $item.Changed) { continue }
            $attempted += $item
            Set-ChromePolicyState $item $item.Applied
        }
    } catch {
        $originalException = $_.Exception
        $rollbackFailed = $false
        for ($i = $attempted.Count - 1; $i -ge 0; $i--) {
            $item = $attempted[$i]
            try {
                $actual = Get-ChromeRegistryValue 'CurrentUser' $item.Subkey $item.Name
                if (Test-ChromePolicyValue $actual $item.Original) { continue }
                if (-not (Test-ChromePolicyValue $actual $item.Applied)) { throw '策略已有其他修改。' }
                Set-ChromePolicyState $item $item.Original
            } catch { $rollbackFailed = $true }
        }
        $message = if ($rollbackFailed) { 'Chrome 策略安装未完成，部分原值未能恢复，请保留备份并人工处理。' } else { 'Chrome 策略安装未完成，已恢复本次修改的原值。' }
        $message += ' 原因：' + $originalException.GetType().FullName + ': ' + $originalException.Message
        $failure = New-Object InvalidOperationException($message, $originalException)
        $failure.Data['ChromeRollbackFailed'] = $rollbackFailed
        throw $failure
    }
}

function Assert-ChromePolicyRestorable($Plan) {
    Assert-ChromePolicyPlan $Plan
    foreach ($item in $Plan.Items) {
        if (-not $item.Changed) { continue }
        $actual = Get-ChromeRegistryValue 'CurrentUser' $item.Subkey $item.Name
        if (-not (Test-ChromePolicyValue $actual $item.Applied)) {
            throw ('Chrome 策略 ' + $item.Name + ' 已被再次修改，整次恢复已停止，未覆盖用户修改。')
        }
    }
}

function Restore-ChromePolicy($Plan) {
    Assert-ChromePolicyRestorable $Plan
    $attempted = @()
    try {
        foreach ($item in $Plan.Items) {
            if (-not $item.Changed) { continue }
            $attempted += $item
            Set-ChromePolicyState $item $item.Original
        }
    } catch {
        $originalException = $_.Exception
        $rollbackFailed = $false
        for ($i = $attempted.Count - 1; $i -ge 0; $i--) {
            $item = $attempted[$i]
            try {
                $actual = Get-ChromeRegistryValue 'CurrentUser' $item.Subkey $item.Name
                if (Test-ChromePolicyValue $actual $item.Applied) { continue }
                if (-not (Test-ChromePolicyValue $actual $item.Original)) { throw '策略已有其他修改。' }
                Set-ChromePolicyState $item $item.Applied
            } catch { $rollbackFailed = $true }
        }
        $message = if ($rollbackFailed) { 'Chrome 策略恢复未完成，部分值未能回退，请保留备份并人工处理。' } else { 'Chrome 策略恢复未完成，已回退至恢复前状态。' }
        $message += ' 原因：' + $originalException.GetType().FullName + ': ' + $originalException.Message
        $failure = New-Object InvalidOperationException($message, $originalException)
        $failure.Data['ChromeRollbackFailed'] = $rollbackFailed
        throw $failure
    }
}

Export-ModuleMember -Function Get-ChromePolicyPlan, Get-ChromePolicyState, Apply-ChromePolicy, Restore-ChromePolicy, Assert-ChromePolicyRestorable
