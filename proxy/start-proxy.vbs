' Launch the DeepSeek jailbreak proxy (hidden, idempotent).
'
' Put this file in the proxy's own directory; it resolves proxy.py next to itself.
' The Windows login item calls THIS file by absolute path.
'
' NOTE: the interpreter is NOT hard-coded - it probes py.exe / python.exe /
' python3.exe and falls back to whatever is on PATH.
Option Explicit

Dim shell, fso, http, py, base, cmd, candidates, i

Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
base = fso.GetParentFolderName(WScript.ScriptFullName)

' --- already running? ---
On Error Resume Next
Set http = CreateObject("MSXML2.XMLHTTP")
http.Open "GET", "http://127.0.0.1:8790/health", False
http.send
If Err.Number = 0 Then
    If http.status = 200 Then
        WScript.Quit 0
    End If
End If
On Error GoTo 0
Err.Clear

' --- pick a python interpreter (never hard-code a machine path) ---
py = ""
candidates = Array("py.exe", "python.exe", "python3.exe")
For i = 0 To UBound(candidates)
    If fso.FileExists(candidates(i)) Then
        py = candidates(i)
        Exit For
    End If
Next
If py = "" Then
    py = "python"
End If

' --- launch (hidden) ---
shell.CurrentDirectory = base
cmd = Chr(34) & py & Chr(34) & " proxy.py --port 8790 --verbose"
shell.Run cmd, 0, False
