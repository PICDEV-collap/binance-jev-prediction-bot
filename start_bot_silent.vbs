' Binance Prediction Markets Bot - Silent Launcher (Completely Invisible)
Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
WshShell.Run "cmd /c start_bot.bat", 0, False
