REM Uses a hard-coded Visual Studio paths

for /f "delims=" %%G IN (devtools_paths.txt) DO (
  if exist "%%G" (
    echo using %%G
    call "%%G"
    goto :found
  )
)
:notfound
echo No VsDevCmd.bat for msbuild found
pause
exit /b 1

:found
msbuild make.msbuild
pause
