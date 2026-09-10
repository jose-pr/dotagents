@echo off
rem Windows entry for the curl shim (the POSIX twin is bin/curl). The
rem interpreter: %AGENTS_PYTHON% (the one dotagents runs under, exported by
rem `dotagents env`) when set and present, else whatever python.exe is on PATH.
rem No parenthesised block: %* is pasted into it before parsing, so a ')' in
rem a URL would end it early, and %ERRORLEVEL% inside one is the STALE value.
rem On its own line %ERRORLEVEL% is read after the command ran.
if not defined AGENTS_PYTHON goto :path
if not exist "%AGENTS_PYTHON%" goto :path
"%AGENTS_PYTHON%" "%~dp0curl.py" %*
exit /b %ERRORLEVEL%
:path
python.exe "%~dp0curl.py" %*
exit /b %ERRORLEVEL%
