# Puts "Anastylos" and "Stop Anastylos" icons on the Windows desktop.
$desktop = [Environment]::GetFolderPath("Desktop")
$shell = New-Object -ComObject WScript.Shell
foreach ($item in @(@("Anastylos", "Start-Anastylos.bat"), @("Stop Anastylos", "Stop-Anastylos.bat"))) {
    $link = $shell.CreateShortcut((Join-Path $desktop "$($item[0]).lnk"))
    $link.TargetPath = Join-Path $PSScriptRoot $item[1]
    $link.WorkingDirectory = $PSScriptRoot
    $link.WindowStyle = 7
    $link.IconLocation = (Join-Path $PSScriptRoot "anastylos.ico")
    $link.Save()
}
"Icons created on the desktop."
