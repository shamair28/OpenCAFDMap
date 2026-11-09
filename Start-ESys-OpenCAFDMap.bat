@echo off
rem Starts E-Sys with Open CAFD Map labels, using the paths saved by the launcher (config.json).
rem This is the only way our labelling runs: E-Sys started any other way is untouched.
python "%~dp0ocm\esys.py" launch
if errorlevel 1 pause
