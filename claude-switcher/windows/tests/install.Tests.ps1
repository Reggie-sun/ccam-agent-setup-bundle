$ErrorActionPreference = 'Stop'
$installer = Join-Path (Split-Path $PSScriptRoot -Parent) 'install.ps1'
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($installer, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw ($errors | Out-String) }
$definition = $ast.Find({ param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Resolve-InstallTimeZone'
}, $true)
if (-not $definition) { throw 'Missing timezone resolver' }
# Load only the pure helper; never execute the installer.
. ([scriptblock]::Create($definition.Extent.Text))
$map = Join-Path (Split-Path $PSScriptRoot -Parent) 'environment-patch\iana-windows-map.json'
foreach ($case in @(
    @('America/Los_Angeles', 'Pacific Standard Time'),
    @('Asia/Tokyo', 'Tokyo Standard Time'),
    @('Europe/Berlin', 'W. Europe Standard Time'),
    @('Asia/Kolkata', 'India Standard Time')
)) {
    if ((Resolve-InstallTimeZone $case[0] $map) -cne $case[1]) { throw "Wrong mapping: $($case[0])" }
}
foreach ($unknown in @('', 'Unknown/City', 'asia/tokyo')) {
    $rejected = $false
    try { Resolve-InstallTimeZone $unknown $map | Out-Null } catch { $rejected = $true }
    if (-not $rejected) { throw "Unknown timezone accepted: $unknown" }
}
Write-Host 'PASS: 7 timezone mapping and rejection checks; installer not executed'
