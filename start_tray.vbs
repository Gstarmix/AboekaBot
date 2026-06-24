Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
pythonw = "pythonw.exe"
defaultPath = "C:\Users\Gstar\AppData\Local\Programs\Python\Python312\pythonw.exe"
If fso.FileExists(defaultPath) Then
    pythonw = defaultPath
End If
Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = scriptDir
WshShell.Run """" & pythonw & """ """ & scriptDir & "\bot_tray.py""", 0, False