@echo off
rem Windows entry for the curl shim (the POSIX twin is bin/curl). The
rem interpreter: %AGENTS_PYTHON% (the one dotagents runs under, exported by
rem `dotagents env`) when set and present, else whatever python.exe is on PATH.
rem No parenthesised block: %* is pasted into it before parsing, so a ')' in
rem a URL would end it early, and %ERRORLEVEL% inside one is the STALE value.
rem On its own line %ERRORLEVEL% is read after the command ran.
rem The overlay's lib\, then every overlay's (%AGENTS_PYTHONPATH%), ahead of
rem the caller's PYTHONPATH, so curl.py and what it starts import them outside
rem a `dotagents env` session too; setlocal keeps that out of the caller's cmd.
rem Empty parts are left out: an empty PYTHONPATH entry means the current dir.
setlocal
set "_net_pp=%~dp0..\lib"
if defined AGENTS_PYTHONPATH set "_net_pp=%_net_pp%;%AGENTS_PYTHONPATH%"
if defined PYTHONPATH set "_net_pp=%_net_pp%;%PYTHONPATH%"
set "PYTHONPATH=%_net_pp%"
set "_net_pp="
if not defined AGENTS_PYTHON goto :path
if not exist "%AGENTS_PYTHON%" goto :path
"%AGENTS_PYTHON%" "%~dp0curl.py" %*
exit /b %ERRORLEVEL%
:path
python.exe "%~dp0curl.py" %*
exit /b %ERRORLEVEL%
