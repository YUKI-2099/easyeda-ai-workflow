# 经 SSH 远程作业时在用户桌面上左键单击一次（坑 #85）。
# 坐标按 win_ui_shot.ps1 截出的图量（物理像素）。必须先截图、读图确认弹窗和按钮，再点。
# 放进 /IT 交互式计划任务运行，换坐标就 /create /f 重建同名任务：
#   schtasks /create /f /tn ui_click /tr "powershell -NoProfile -ExecutionPolicy Bypass -File <路径>\win_ui_click.ps1 <X> <Y> <日志.txt>" /sc once /st 23:59 /ru <桌面用户> /it
#   schtasks /run /tn ui_click
# 只在用户同意、且这时不在用这台电脑时做：点击会抢走对方的鼠标。
param(
    [Parameter(Mandatory = $true)][int]$X,
    [Parameter(Mandatory = $true)][int]$Y,
    [string]$Log = ""
)
Add-Type @"
using System; using System.Runtime.InteropServices;
public class UiMouse {
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")] public static extern void mouse_event(int f, int dx, int dy, int d, int e);
}
"@
[UiMouse]::SetProcessDPIAware() | Out-Null
[UiMouse]::SetCursorPos($X, $Y) | Out-Null
Start-Sleep -Milliseconds 150
[UiMouse]::mouse_event(0x0002, 0, 0, 0, 0)   # LEFTDOWN
Start-Sleep -Milliseconds 80
[UiMouse]::mouse_event(0x0004, 0, 0, 0, 0)   # LEFTUP
if ($Log) { "clicked $X $Y $(Get-Date -Format o)" | Out-File -Append -Encoding utf8 $Log }
