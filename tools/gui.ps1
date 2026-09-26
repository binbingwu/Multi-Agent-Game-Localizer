param([string]$cmd = "shot", [int]$x = 0, [int]$y = 0, [string]$out = "")
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class W {
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool ClientToScreen(IntPtr h, ref POINT p);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint f, uint dx, uint dy, uint d, IntPtr e);
  [DllImport("user32.dll")] public static extern void keybd_event(byte vk, byte scan, uint f, IntPtr e);
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
  public struct RECT { public int L, T, R, B; }
  public struct POINT { public int X, Y; }
}
"@
[W]::SetProcessDPIAware() | Out-Null
$p = Get-Process Evenicle -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
if (-not $p) { Write-Output "no window"; exit 1 }
$h = $p.MainWindowHandle
$rc = New-Object W+RECT; [W]::GetClientRect($h, [ref]$rc) | Out-Null
$pt = New-Object W+POINT; [W]::ClientToScreen($h, [ref]$pt) | Out-Null
$cw = $rc.R; $ch = $rc.B
[W]::SetForegroundWindow($h) | Out-Null
Start-Sleep -Milliseconds 200
if ($cmd -eq "click") {
  # x,y given in 1280x720 game coordinates
  $sx = $pt.X + [int]($x * $cw / 1280); $sy = $pt.Y + [int]($y * $ch / 720)
  [W]::SetCursorPos($sx, $sy) | Out-Null; Start-Sleep -Milliseconds 100
  [W]::mouse_event(2, 0, 0, 0, [IntPtr]::Zero); Start-Sleep -Milliseconds 60
  [W]::mouse_event(4, 0, 0, 0, [IntPtr]::Zero)
  Write-Output "clicked $sx $sy"
} elseif ($cmd -eq "key") {
  [W]::keybd_event([byte]$x, 0, 0, [IntPtr]::Zero); Start-Sleep -Milliseconds 60
  [W]::keybd_event([byte]$x, 0, 2, [IntPtr]::Zero)
  Write-Output "key $x"
} else {
  $bmp = New-Object System.Drawing.Bitmap $cw, $ch
  $g = [System.Drawing.Graphics]::FromImage($bmp)
  $g.CopyFromScreen($pt.X, $pt.Y, 0, 0, (New-Object System.Drawing.Size $cw, $ch))
  if ($cw -gt 1280) { $bmp2 = New-Object System.Drawing.Bitmap $bmp, 1280, 720; $bmp = $bmp2 }
  $bmp.Save($out, [System.Drawing.Imaging.ImageFormat]::Png)
  Write-Output "saved $cw x $ch to $out"
}
