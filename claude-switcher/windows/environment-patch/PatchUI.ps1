#requires -version 5.1
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'claude检测器补丁仅支持 Windows 10/11。' }
Add-Type -AssemblyName PresentationFramework, PresentationCore, WindowsBase, System.Windows.Forms
Import-Module (Join-Path $PSScriptRoot 'PatchCore.psm1') -Force
Import-Module (Join-Path $PSScriptRoot 'ChromePolicy.psm1') -DisableNameChecking
Import-Module (Join-Path $PSScriptRoot 'VpsTimezone.psm1') -DisableNameChecking

[xml]$xaml = @'
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="claude检测器 · Windows 环境补丁" Width="880" Height="870" MinWidth="760" MinHeight="700"
        WindowStartupLocation="CenterScreen" Background="#F4F6F8">
  <Grid Margin="24">
    <Grid.RowDefinitions>
      <RowDefinition Height="Auto"/><RowDefinition Height="Auto"/><RowDefinition Height="Auto"/>
      <RowDefinition Height="Auto"/><RowDefinition Height="Auto"/><RowDefinition Height="Auto"/><RowDefinition Height="Auto"/><RowDefinition Height="*"/>
    </Grid.RowDefinitions>
    <StackPanel>
      <TextBlock Text="claude检测器" FontSize="27" FontWeight="Bold" Foreground="#213C38"/>
      <TextBlock Margin="0,6,0,18" Text="Windows 环境补丁 · 检测、备份、应用与恢复" Foreground="#586772"/>
    </StackPanel>
    <Grid Grid.Row="1" Margin="0,0,0,12">
      <Grid.ColumnDefinitions><ColumnDefinition Width="120"/><ColumnDefinition Width="*"/><ColumnDefinition Width="82"/></Grid.ColumnDefinitions>
      <TextBlock Text="Clash Verge 配置" VerticalAlignment="Center"/>
      <TextBox x:Name="ConfigPath" Grid.Column="1" Padding="7"/>
      <Button x:Name="Browse" Grid.Column="2" Content="选择…" Margin="8,0,0,0"/>
    </Grid>
    <Grid Grid.Row="2" Margin="0,0,0,12">
      <Grid.ColumnDefinitions><ColumnDefinition Width="120"/><ColumnDefinition Width="*"/></Grid.ColumnDefinitions>
      <TextBlock Text="海外代理组名称" VerticalAlignment="Center"/>
      <TextBox x:Name="ProxyGroup" Grid.Column="1" Text="PROXY" Padding="7"/>
    </Grid>
    <Grid Grid.Row="3" Margin="0,0,0,15">
      <Grid.ColumnDefinitions><ColumnDefinition Width="120"/><ColumnDefinition Width="*"/></Grid.ColumnDefinitions>
      <TextBlock Text="系统时区" VerticalAlignment="Center"/>
      <ComboBox x:Name="Timezone" Grid.Column="1" Padding="7" SelectedIndex="0">
        <ComboBoxItem Content="保持当前时区" Tag="keep"/>
        <ComboBoxItem x:Name="VpsFollow" Content="跟随 VPS 实际系统时区（先读取）" Tag="vps" IsEnabled="False"/>
      </ComboBox>
    </Grid>
    <StackPanel Grid.Row="4" Margin="0,0,0,12">
      <Grid>
        <Grid.ColumnDefinitions><ColumnDefinition Width="120"/><ColumnDefinition Width="*"/><ColumnDefinition Width="44"/><ColumnDefinition Width="65"/><ColumnDefinition Width="130"/></Grid.ColumnDefinitions>
        <TextBlock Text="VPS SSH 目标" VerticalAlignment="Center"/>
        <TextBox x:Name="VpsDestination" Grid.Column="1" Padding="7" ToolTip="user@host 或已有 SSH 别名"/>
        <TextBlock Grid.Column="2" Text="端口" VerticalAlignment="Center" HorizontalAlignment="Center"/>
        <TextBox x:Name="VpsPort" Grid.Column="3" Text="22" Padding="7"/>
        <Button x:Name="ReadVpsTimezone" Grid.Column="4" Content="读取 VPS 时区" Margin="8,0,0,0"/>
      </Grid>
      <TextBlock x:Name="VpsTimezonePreview" Text="尚未读取 VPS；保持本机当前时区。" TextWrapping="Wrap" Margin="120,6,0,0" Foreground="#586772"/>
      <TextBlock Text="使用已有 OpenSSH 配置和密钥，只读 VPS 时区；不接收密码或私钥。读取本身不修改任何设置。"
                 TextWrapping="Wrap" Foreground="#586772" Margin="0,6,0,0"/>
    </StackPanel>
    <StackPanel Grid.Row="5" Margin="0,0,0,12">
      <CheckBox x:Name="ConfigureChrome" IsChecked="False" Margin="0,0,0,8">
        <TextBlock Text="配置当前用户全部 Chrome：中文界面、网页优先英语、限制 WebRTC UDP、默认拒绝定位" TextWrapping="Wrap"/>
      </CheckBox>
      <CheckBox x:Name="ConfigureChromeDns" IsChecked="False" IsEnabled="False" Margin="20,0,0,8">
        <TextBlock Text="让 Chrome DNS 跟随已接管 DNS 的 TUN（先验证 TUN 与全局 UDP 53 端口劫持）" TextWrapping="Wrap"/>
      </CheckBox>
      <TextBlock Text="以上选项会替换当前用户对应策略值并备份，可能显示“由组织管理”；英语为网页语言前缀，仍可追加其他语言。"
                 TextWrapping="Wrap" Foreground="#725719"/>
      <TextBlock Text="Chrome 时区跟随系统；已有窗口、界面语言及部分策略可能需要重开 Chrome，程序不会代为关闭。"
                 TextWrapping="Wrap" Foreground="#586772" Margin="0,5,0,0"/>
    </StackPanel>
    <StackPanel Grid.Row="6">
      <WrapPanel Margin="0,0,0,10">
        <Button x:Name="Inspect" Content="检测环境" Padding="14,8" Margin="0,0,8,8"/>
        <Button x:Name="Install" Content="安装补丁并备份" Padding="14,8" Margin="0,0,8,8"/>
        <Button x:Name="Verify" Content="检查 DNS 响应" Padding="14,8" Margin="0,0,8,8"/>
        <Button x:Name="Restore" Content="选择备份并恢复" Padding="14,8" Margin="0,0,8,8"/>
      </WrapPanel>
      <TextBlock Text="安装后请在 Clash Verge 重新应用订阅，再核查实际 DNS 上游。程序不会自动重启代理或浏览器。"
                 TextWrapping="Wrap" Foreground="#725719" Margin="0,0,0,12"/>
    </StackPanel>
    <TextBox Grid.Row="7" x:Name="Log" IsReadOnly="True" AcceptsReturn="True" TextWrapping="Wrap"
             VerticalScrollBarVisibility="Auto" Padding="12" Background="White" FontSize="14"/>
  </Grid>
</Window>
'@
$reader = New-Object Xml.XmlNodeReader $xaml
$window = [Windows.Markup.XamlReader]::Load($reader)
$controls = @{}
foreach ($name in @('ConfigPath', 'Browse', 'ProxyGroup', 'Timezone', 'VpsFollow', 'VpsDestination', 'VpsPort', 'ReadVpsTimezone', 'VpsTimezonePreview', 'ConfigureChrome', 'ConfigureChromeDns', 'Inspect', 'Install', 'Verify', 'Restore', 'Log')) {
    $controls[$name] = $window.FindName($name)
}
$script:Busy = $false
$script:VpsTimezoneCache = $null
function Add-PatchLog([string]$Text) {
    $controls.Log.AppendText(('[' + (Get-Date -Format 'HH:mm:ss') + '] ' + $Text + [Environment]::NewLine))
    $controls.Log.ScrollToEnd()
}
function Invoke-PatchUi([scriptblock]$Operation) {
    if ($script:Busy) { return }
    $script:Busy = $true
    foreach ($name in @('Browse', 'ReadVpsTimezone', 'Inspect', 'Install', 'Verify', 'Restore')) { $controls[$name].IsEnabled = $false }
    Add-PatchLog '正在处理，请稍候…'
    $window.Dispatcher.Invoke([Action]{}, [Windows.Threading.DispatcherPriority]::Render)
    try { & $Operation }
    catch { Add-PatchLog ('未完成：' + $_.Exception.Message) }
    finally {
        foreach ($name in @('Browse', 'ReadVpsTimezone', 'Inspect', 'Install', 'Verify', 'Restore')) { $controls[$name].IsEnabled = $true }
        $script:Busy = $false
    }
}
$candidates = @(Get-PatchCandidates)
if ($candidates.Count) { $controls.ConfigPath.Text = $candidates[0] }
$controls.Browse.Add_Click({
    $dialog = New-Object Windows.Forms.FolderBrowserDialog
    $dialog.Description = '选择包含 clash-verge.yaml 或 verge.yaml 的配置目录'
    if ($dialog.ShowDialog() -eq [Windows.Forms.DialogResult]::OK) { $controls.ConfigPath.Text = $dialog.SelectedPath }
    $dialog.Dispose()
})
$controls.Inspect.Add_Click({ Invoke-PatchUi {
    Add-PatchLog ('当前系统时区：' + (Get-TimeZone).Id)
    foreach ($policy in (Get-ChromePolicyState)) {
        $name = if ($policy.Subkey) { $policy.Subkey + '/' + $policy.Name } else { $policy.Name }
        Add-PatchLog ('Chrome ' + $name + '：当前用户值匹配目标=' + $policy.UserValueWritten + '；机器级策略冲突=' + $policy.MachineConflict)
    }
    if (-not [string]::IsNullOrWhiteSpace($controls.ConfigPath.Text)) {
        $status = Get-PatchStatus $controls.ConfigPath.Text
        Add-PatchLog ('全局脚本存在：' + $status.ScriptExists + '；与本工具最近安装一致：' + $status.OwnedScript)
    }
    Add-PatchLog 'Chrome 策略是否被浏览器实际采纳，请到 chrome://policy 核对。'
    Add-PatchLog '此结果仅为本机配置检查；出口国家、账号可用性和 DNS 上游仍需实际验收。'
} })
$controls.ConfigureChrome.Add_Checked({ $controls.ConfigureChromeDns.IsEnabled = $true })
$controls.ConfigureChrome.Add_Unchecked({ $controls.ConfigureChromeDns.IsChecked = $false; $controls.ConfigureChromeDns.IsEnabled = $false })
function Reset-VpsTimezonePreview {
    $script:VpsTimezoneCache = $null
    $controls.VpsFollow.IsEnabled = $false
    $controls.Timezone.SelectedIndex = 0
    $controls.VpsTimezonePreview.Text = 'SSH 目标或端口已改变；请重新读取 VPS 时区。当前保持本机时区。'
}
function Get-UiVpsPort {
    $port = 0
    if (-not [int]::TryParse($controls.VpsPort.Text, [ref]$port) -or $port -lt 1 -or $port -gt 65535) {
        throw 'SSH 端口必须为 1 到 65535。'
    }
    return $port
}
$controls.VpsDestination.Add_TextChanged({ Reset-VpsTimezonePreview })
$controls.VpsPort.Add_TextChanged({ Reset-VpsTimezonePreview })
$controls.ReadVpsTimezone.Add_Click({ Invoke-PatchUi {
    $script:VpsTimezoneCache = $null
    $controls.VpsFollow.IsEnabled = $false
    $controls.Timezone.SelectedIndex = 0
    $controls.VpsTimezonePreview.Text = '正在只读查询 VPS 实际系统时区…'
    try { $result = Get-VpsTimezone -Destination $controls.VpsDestination.Text -Port (Get-UiVpsPort) }
    catch { $controls.VpsTimezonePreview.Text = 'VPS 时区读取未完成；保持本机当前时区。'; throw }
    $script:VpsTimezoneCache = $result
    $controls.VpsTimezonePreview.Text = 'VPS：' + $result.IanaId + ' → Windows：' + $result.WindowsId
    $controls.VpsFollow.IsEnabled = $true
    Add-PatchLog ('已读取 VPS 实际时区：' + $result.IanaId + ' → ' + $result.WindowsId + '。读取未修改时区；安装时将重新查询。')
} })
$controls.Install.Add_Click({ Invoke-PatchUi {
    $followVps = [string]$controls.Timezone.SelectedItem.Tag -eq 'vps'
    $port = 22
    if ($followVps) { $port = Get-UiVpsPort }
    $result = Install-EnvironmentPatch -ConfigDirectory $controls.ConfigPath.Text -ProxyGroup $controls.ProxyGroup.Text `
        -ConfigureChrome:([bool]$controls.ConfigureChrome.IsChecked) -ConfigureChromeDns:([bool]$controls.ConfigureChromeDns.IsChecked) `
        -FollowVpsTimezone:$followVps -VpsDestination $controls.VpsDestination.Text -VpsPort $port
    if ($result.VpsTimezone) {
        $script:VpsTimezoneCache = $result.VpsTimezone
        $controls.VpsTimezonePreview.Text = '安装时重新读取的 VPS：' + $result.VpsTimezone.IanaId + ' → Windows：' + $result.VpsTimezone.WindowsId
        Add-PatchLog ('安装时复核 VPS 实际时区：' + $result.VpsTimezone.IanaId + ' → ' + $result.VpsTimezone.WindowsId)
    }
    Add-PatchLog $result.Message
    if ($result.BackupDirectory) { Add-PatchLog ('本机备份：' + $result.BackupDirectory) }
} })
$controls.Verify.Add_Click({ Invoke-PatchUi {
    $result = Test-PatchDnsResponse $controls.ConfigPath.Text
    foreach ($line in $result.Results) { Add-PatchLog $line }
    Add-PatchLog $result.Message
} })
$controls.Restore.Add_Click({
    $dialog = New-Object Windows.Forms.FolderBrowserDialog
    $dialog.Description = '选择包含 receipt.json 的本工具备份目录'
    if ($env:LOCALAPPDATA) { $dialog.SelectedPath = Join-Path $env:LOCALAPPDATA 'claude-environment-patch\backups' }
    if ($dialog.ShowDialog() -eq [Windows.Forms.DialogResult]::OK) {
        $selected = $dialog.SelectedPath
        Invoke-PatchUi { Add-PatchLog (Restore-EnvironmentPatch $selected) }
    }
    $dialog.Dispose()
})
$window.Add_Closing({ param($sender, $eventArgs) if ($script:Busy) { $eventArgs.Cancel = $true } })
Add-PatchLog '请选择配置目录和已有海外代理组。首次建议保持当前时区。'
Add-PatchLog '需要跟随 VPS 时区时，请显式填写 SSH 目标并先读取；安装会再次查询，不使用 IP 城市推断时区。'
Add-PatchLog 'Chrome 设置默认不修改；勾选后仅备份并写入当前用户白名单策略，不读取登录、密码或书签。'
$window.ShowDialog() | Out-Null
