# 把 pptx 逐页导出为 PNG，供视觉检查（渲染闭环的第一步）。
#
#   powershell -File scripts/render_pptx_png.ps1 -Pptx <文件> -OutDir <目录> [-Width 1600]
#
# 用本机 PowerPoint 的 COM 自动化：不需要额外安装，且渲染结果就是老师看到的效果
# （LibreOffice 的字体度量与 PowerPoint 有差异，能不用就不用）。

param(
    [Parameter(Mandatory = $true)][string]$Pptx,
    [Parameter(Mandatory = $true)][string]$OutDir,
    [int]$Width = 1600
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$resolved = (Resolve-Path $Pptx).Path
if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Path $OutDir -Force | Out-Null }
$outDirPath = (Resolve-Path $OutDir).Path

$application = $null
$deck = $null
try {
    $application = New-Object -ComObject PowerPoint.Application
    # ReadOnly=true, Untitled=false, WithWindow=false：不弹窗、不改动原文件
    $deck = $application.Presentations.Open($resolved, $true, $false, $false)
    $height = [int]($Width * 9 / 16)
    $index = 1
    foreach ($slide in $deck.Slides) {
        $target = Join-Path $outDirPath ("slide-{0:d2}.png" -f $index)
        $slide.Export($target, "PNG", $Width, $height)
        $index++
    }
    Write-Output "已导出 $($index - 1) 页到 $outDirPath"
}
finally {
    if ($deck) { $deck.Close() | Out-Null }
    if ($application) { $application.Quit() | Out-Null }
}
