# 经 SSH 远程作业时截取用户桌面整屏（坑 #85）。
# SSH 会话里直接跑看不到桌面；放进 /IT 交互式计划任务、以当前登录的桌面用户运行：
#   schtasks /create /f /tn ui_shot /tr "powershell -NoProfile -ExecutionPolicy Bypass -File <路径>\win_ui_shot.ps1 <输出.png>" /sc once /st 23:59 /ru <桌面用户> /it
#   schtasks /run /tn ui_shot
# 先声明 DPI 感知：缩放不是 100% 时，截图像素 = 物理像素 = win_ui_click.ps1 的坐标。
param([Parameter(Mandatory = $true)][string]$Out)
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
Add-Type @"
using System; using System.Runtime.InteropServices;
public class UiDpi { [DllImport("user32.dll")] public static extern bool SetProcessDPIAware(); }
"@
[UiDpi]::SetProcessDPIAware() | Out-Null
$b = [System.Windows.Forms.SystemInformation]::VirtualScreen
$bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($b.Left, $b.Top, 0, 0, $bmp.Size)
$bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $bmp.Dispose()
