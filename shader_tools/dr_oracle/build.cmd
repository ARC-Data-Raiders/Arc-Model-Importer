@echo off
call "C:\Program Files\Microsoft Visual Studio\18\Community\VC\Auxiliary\Build\vcvars64.bat" >nul
cl /nologo /std:c++17 /O2 /EHsc /W4 /utf-8 /DUNICODE /D_UNICODE src\*.cpp /Fe:"%~1\dr_oracle.exe" /Fo:"%~1\\" /link d3d11.lib dxgi.lib d3dcompiler.lib
