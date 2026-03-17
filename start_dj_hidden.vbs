Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

lockFile = "C:\Users\mariu\SpotifyDJ\data\dj.lock"

If fso.FileExists(lockFile) Then
    Set f = fso.OpenTextFile(lockFile, 1)
    pid = Trim(f.ReadLine())
    f.Close

    Set wmi = GetObject("winmgmts://./root/cimv2")
    Set procs = wmi.ExecQuery("SELECT * FROM Win32_Process WHERE ProcessId=" & pid)

    If procs.Count > 0 Then
        MsgBox "Spotify DJ is already running (PID " & pid & ").", 64, "Spotify DJ"
        WScript.Quit
    End If
End If

WshShell.Run """C:\Users\mariu\SpotifyDJ\start_dj.bat""", 0

Set WshShell = Nothing
Set fso = Nothing