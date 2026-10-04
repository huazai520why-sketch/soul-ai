@echo off
REM setup_ssh.bat - double-click or right-click -> Run as administrator.
REM Elevates and runs setup_ssh.ps1 located in the same folder.
powershell -ExecutionPolicy Bypass -Command "Start-Process powershell -ArgumentList '-ExecutionPolicy Bypass -File \"%~dp0setup_ssh.ps1\"' -Verb RunAs -Wait"
