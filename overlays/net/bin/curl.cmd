@echo off
rem Windows entry for the curl shim (the POSIX twin is bin/curl). The
rem interpreter: %AGENTS_PYTHON% (the one dotagents runs under, exported by
rem `dotagents env`) when set, else whatever python.exe is on PATH.
if defined AGENTS_PYTHON if exist "%AGENTS_PYTHON%" (
    "%AGENTS_PYTHON%" "%~dp0curl.py" %*
    exit /b %ERRORLEVEL%
)
python.exe "%~dp0curl.py" %*
