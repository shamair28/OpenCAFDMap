@echo off
rem Open CAFD Map launcher (paths, build, E-Sys with labels, BimmerCode Backup Editor)
where pythonw >NUL 2>&1 && (start "" pythonw "%~dp0launcher.py" & exit /B 0)
where python >NUL 2>&1 && (start "" python "%~dp0launcher.py" & exit /B 0)
echo Python 3.10+ is required: https://www.python.org/downloads/ (tick "Add python.exe to PATH")
pause
