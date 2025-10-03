@echo off

:: Change the CWD to make sure it is the same as the script.
:: This matters if the script is being run from an external source such as a Razer macro hotkey.
CD /D %~dp0
python -x faulthandler %~dp0\MediaDownloaderApp.pyw

::pause
exit