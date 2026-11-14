# Standalone Windows PowerShell 5.1+ regression tests; no installer execution,
# downloads, registry writes, or changes to an existing ccam installation.
$ErrorActionPreference = 'Stop'
$tokens = $null
$parseErrors = $null
$installer = Join-Path (Split-Path $PSScriptRoot -Parent) 'install.ps1'
$ast = [System.Management.Automation.Language.Parser]::ParseFile($installer, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw ($parseErrors | Out-String) }
foreach ($name in @('Fail', 'Get-InstallArchitecture', 'Test-InstallPath')) {
  $definition = $ast.Find({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name }, $true)
  if (-not $definition) { throw "Missing installer helper: $name" }
  Invoke-Expression $definition.Extent.Text
}
function Assert-Equal($Actual, $Expected, $Label) {
  if ($Actual -cne $Expected) { throw "$Label : expected [$Expected], got [$Actual]" }
}
Assert-Equal (Get-InstallArchitecture 'AMD64' '') 'amd64' 'Native x64'
Assert-Equal (Get-InstallArchitecture 'ARM64' '') 'arm64' 'Native ARM64'
Assert-Equal (Get-InstallArchitecture 'x86' 'AMD64') 'amd64' 'WOW64 x64'
Assert-Equal (Get-InstallArchitecture 'x86' 'ARM64') 'arm64' 'WOW64 ARM64'
$rejected = $false
try { Get-InstallArchitecture 'x86' '' | Out-Null } catch { $rejected = $_.Exception.Message -like '*unsupported CPU*' }
Assert-Equal $rejected $true 'Reject unsupported 32-bit OS'
Assert-Equal (Test-InstallPath '' 'C:\Tools') $false 'Empty persistent PATH'
Assert-Equal (Test-InstallPath 'C:\Other;"c:\TOOLS\"' 'C:\Tools') $true 'Case, quotes, trailing slash'
Assert-Equal (Test-InstallPath '%USERPROFILE%\.local\bin;C:\Other' (Join-Path $HOME '.local\bin')) $true 'Expand existing registry variables'
Assert-Equal (Test-InstallPath 'C:\Tools-old' 'C:\Tools') $false 'Whole entry comparison'
Write-Host 'PASS: installer parsing and 9 architecture/PATH regression checks'
