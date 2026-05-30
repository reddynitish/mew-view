$code = @'
using System;
using System.Runtime.InteropServices;
[Guid("5CDF2C82-841E-4546-9722-0CF74078229A"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioEndpointVolume {
  int f1(); int f2(); int f3(); int f4();
  int SetMasterVolumeLevel(float v, Guid g);
  int SetMasterVolumeLevelScalar(float v, Guid g);
  int GetMasterVolumeLevel(out float v);
  int GetMasterVolumeLevelScalar(out float v);
  int f5(); int f6(); int f7(); int f8();
  int SetMute([MarshalAs(UnmanagedType.Bool)] bool m, Guid g);
  int GetMute(out bool m);
}
[Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDevice { int Activate(ref Guid id, int clsCtx, IntPtr a, [MarshalAs(UnmanagedType.IUnknown)] out object o); }
[Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDeviceEnumerator { int f(); int GetDefaultAudioEndpoint(int dataFlow, int role, out IMMDevice ep); }
[ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")] class MMDeviceEnumeratorComObject { }
public class Audio {
  static IAudioEndpointVolume Vol() {
    var en = (IMMDeviceEnumerator)(new MMDeviceEnumeratorComObject());
    IMMDevice dev; en.GetDefaultAudioEndpoint(0,1, out dev);
    Guid iid = typeof(IAudioEndpointVolume).GUID; object o;
    dev.Activate(ref iid, 23, IntPtr.Zero, out o);
    return (IAudioEndpointVolume)o;
  }
  public static float Get(){ float v; Vol().GetMasterVolumeLevelScalar(out v); return v; }
  public static void Set(float v){ Vol().SetMasterVolumeLevelScalar(v, Guid.Empty); }
  public static void Mute(bool m){ Vol().SetMute(m, Guid.Empty); }
  public static bool IsMuted(){ bool m; Vol().GetMute(out m); return m; }
}
'@
Add-Type -TypeDefinition $code
Write-Output ("before vol={0:N2} muted={1}" -f [Audio]::Get(), [Audio]::IsMuted())
[Audio]::Mute($false)
[Audio]::Set(0.85)
Write-Output ("after  vol={0:N2} muted={1}" -f [Audio]::Get(), [Audio]::IsMuted())
