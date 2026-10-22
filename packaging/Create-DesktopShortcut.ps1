# (Re)create the desktop shortcut for the source checkout: the installed pythonw.exe runs
# run_desktop.pyw, so there is no console window and one 鸿雁 taskbar button.
# Explorer caches a shortcut's icon by file path for good (a reboot keeps a stale blank),
# so the icon is copied under a name derived from its content: a new mark gets a new path.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$home_ = (Select-String -Path "$root\.venv\pyvenv.cfg" -Pattern '^home\s*=\s*(.+)$').Matches[0].Groups[1].Value.Trim()
$pythonw = Join-Path $home_ 'pythonw.exe'
if (-not (Test-Path $pythonw)) { $pythonw = "$root\.venv\Scripts\pythonw.exe" }
$ico = "$root\packaging\hongyan.ico"
$hash = (Get-FileHash $ico -Algorithm SHA256).Hash.Substring(0, 8).ToLower()
$dir = "$env:LOCALAPPDATA\HongyanIcon"
New-Item -ItemType Directory -Force $dir | Out-Null
Get-ChildItem $dir -Filter 'hongyan-*.ico' | Where-Object Name -ne "hongyan-$hash.ico" | Remove-Item -Force
Copy-Item $ico "$dir\hongyan-$hash.ico" -Force
$lnk = Join-Path ([Environment]::GetFolderPath('Desktop')) '鸿雁 工作邮箱.lnk'
$s = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
$s.TargetPath = $pythonw; $s.Arguments = "`"$root\run_desktop.pyw`""; $s.WorkingDirectory = $root
$s.IconLocation = "$dir\hongyan-$hash.ico,0"; $s.WindowStyle = 1; $s.Description = '鸿雁 · 工作邮箱'
$s.Save()
Add-Type -Namespace Hongyan -Name Shell -MemberDefinition '[DllImport("shell32.dll")] public static extern void SHChangeNotify(int e,int f,IntPtr a,IntPtr b);'
[Hongyan.Shell]::SHChangeNotify(0x08000000, 0, [IntPtr]::Zero, [IntPtr]::Zero)
Write-Output "Shortcut: $lnk"
