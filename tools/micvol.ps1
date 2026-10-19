# 既定のマイクの入力音量を表示・変更する
# 使い方: powershell -ExecutionPolicy Bypass -File tools\micvol.ps1 [-Set 0-100]
param([double]$Set = -1)
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
[Guid("5CDF2C82-841E-4546-9722-0CF74078229A"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioEndpointVolume {
  int f(); int g(); int h();
  int SetMasterVolumeLevel(float level, IntPtr ctx);
  int SetMasterVolumeLevelScalar(float level, IntPtr ctx);
  int GetMasterVolumeLevel(out float level);
  int GetMasterVolumeLevelScalar(out float level);
  int a(); int b(); int c(); int d();
  int SetMute([MarshalAs(UnmanagedType.Bool)] bool mute, IntPtr ctx);
  int GetMute([MarshalAs(UnmanagedType.Bool)] out bool mute);
}
[Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDevice { int Activate(ref Guid id, int clsCtx, IntPtr p, [MarshalAs(UnmanagedType.IUnknown)] out object o); }
[Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDeviceEnumerator { int f(); int GetDefaultAudioEndpoint(int dataFlow, int role, out IMMDevice ep); }
[ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")] class MMDeviceEnumeratorComObject { }
public class MicVol {
  static IAudioEndpointVolume Vol() {
    var en = new MMDeviceEnumeratorComObject() as IMMDeviceEnumerator;
    IMMDevice dev; Marshal.ThrowExceptionForHR(en.GetDefaultAudioEndpoint(1, 0, out dev)); // 1 = eCapture
    var iid = typeof(IAudioEndpointVolume).GUID; object o;
    Marshal.ThrowExceptionForHR(dev.Activate(ref iid, 23, IntPtr.Zero, out o));
    return o as IAudioEndpointVolume;
  }
  public static float Get() { float v; Marshal.ThrowExceptionForHR(Vol().GetMasterVolumeLevelScalar(out v)); return v; }
  public static float GetDb() { float v; Marshal.ThrowExceptionForHR(Vol().GetMasterVolumeLevel(out v)); return v; }
  public static bool Muted() { bool m; Marshal.ThrowExceptionForHR(Vol().GetMute(out m)); return m; }
  public static void Set(float v) { Marshal.ThrowExceptionForHR(Vol().SetMasterVolumeLevelScalar(v, IntPtr.Zero)); }
}
"@
if ($Set -ge 0) { [MicVol]::Set([float]($Set / 100)) }
"Volume: {0:N0}%  ({1:N1} dB)  Muted: {2}" -f ([MicVol]::Get() * 100), [MicVol]::GetDb(), [MicVol]::Muted()


