param([Parameter(Mandatory=$true)][string]$Artifact)
$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $Artifact -PathType Leaf)) { throw 'MSI artifact is missing' }
$signature = Get-AuthenticodeSignature -LiteralPath $Artifact
if ($signature.Status -ne 'Valid' -or $null -eq $signature.SignerCertificate -or
    $null -eq $signature.TimeStamperCertificate) { throw 'A valid timestamped Authenticode signature is required' }
$signingOids = @($signature.SignerCertificate.Extensions | Where-Object { $_.Oid.Value -eq '2.5.29.37' } | ForEach-Object { $_.EnhancedKeyUsages | ForEach-Object { $_.Value } })
$timestampOids = @($signature.TimeStamperCertificate.Extensions | Where-Object { $_.Oid.Value -eq '2.5.29.37' } | ForEach-Object { $_.EnhancedKeyUsages | ForEach-Object { $_.Value } })
if ('1.3.6.1.5.5.7.3.3' -notin $signingOids -or '1.3.6.1.5.5.7.3.8' -notin $timestampOids) {
    throw 'Signer or timestamp certificate purpose is invalid'
}
$timestampChain = New-Object System.Security.Cryptography.X509Certificates.X509Chain
try {
    $timestampChain.ChainPolicy.RevocationMode = 'Online'
    $timestampChain.ChainPolicy.RevocationFlag = 'EntireChain'
    if (-not $timestampChain.Build($signature.TimeStamperCertificate)) { throw 'Timestamp certificate trust failed' }
} finally { $timestampChain.Dispose() }
$signtool = "${env:ProgramFiles(x86)}\Windows Kits\10\bin\10.0.22621.0\x64\signtool.exe"
if (-not (Test-Path -LiteralPath $signtool)) { $signtool = 'signtool' }
& $signtool verify /pa /all /tw /v $Artifact
if ($LASTEXITCODE -ne 0) { throw 'Strict timestamped MSI signature verification failed' }
