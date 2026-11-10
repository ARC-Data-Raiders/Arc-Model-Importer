@echo off
setlocal
call "C:\Program Files\Microsoft Visual Studio\18\Community\VC\Auxiliary\Build\vcvars64.bat" -vcvars_ver=14.44 >nul
if errorlevel 1 exit /b 1
cl /nologo /O2 /fp:fast /LD /W3 "%~dp0svml_shim.c" /Fe:"%~1\svml_shim.dll" /Fo:"%~1\\"
exit /b %errorlevel%
