$ErrorActionPreference = 'Stop'
$taskDocPath = 'D:\project\test-ai\output\documents\ACDM双系统回答对比记录表_客座率修正版.docx'
$taskPdfPath = 'D:\project\test-ai\artifacts\analysis-qa\comparison-doc-render-seat\native-render.pdf'
New-Item -ItemType Directory -Path (Split-Path $taskPdfPath) -Force | Out-Null
$taskOffice = $null
$taskDocument = $null
$taskOriginalCount = 0
try {
    $taskOffice = New-Object -ComObject Word.Application
    $taskOriginalCount = $taskOffice.Documents.Count
    if ($taskOriginalCount -eq 0) {
        $taskOffice.Visible = $false
        $taskOffice.DisplayAlerts = 0
    }
    $taskDocument = $taskOffice.Documents.Open($taskDocPath, $false, $true)
    [void]$taskDocument.Fields.Update()
    $taskDocument.ExportAsFixedFormat($taskPdfPath, 17)
    Write-Output ('Native WPS pages: ' + $taskDocument.ComputeStatistics(2))
    if (-not (Test-Path -LiteralPath $taskPdfPath)) { throw 'Native export did not produce the PDF' }
} finally {
    if ($taskDocument) {
        $taskDocument.Close(0)
        [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($taskDocument)
    }
    if ($taskOffice) {
        if ($taskOriginalCount -eq 0 -and $taskOffice.Documents.Count -eq 0) { $taskOffice.Quit() }
        [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($taskOffice)
    }
}
