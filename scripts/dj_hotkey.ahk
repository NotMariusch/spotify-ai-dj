#Requires AutoHotkey v2.0
#SingleInstance Force

DJ_PATH := "C:\Users\mariu\SpotifyDJ\data\dj_input.txt"

SendDJ(value)
{
    Run('cmd /c echo ' value ' > "' DJ_PATH '"', , "Hide")
}

F13 & 1::SendDJ(1)
F13 & 2::SendDJ(2)
F13 & 3::SendDJ(3)
F13 & 4::SendDJ(4)
F13 & 5::SendDJ(5)
F13 & 6::SendDJ("ban")
F13 & 7::SendDJ("quit")