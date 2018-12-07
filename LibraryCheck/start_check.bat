REM Uses a hard-coded Visual Studio 2017 paths

set enterprise="C:\Program Files (x86)\Microsoft Visual Studio\Enterprise\Common7\Tools\VsDevCmd.bat"
set professional="C:\Program Files (x86)\Microsoft Visual Studio\Professional\Common7\Tools\VsDevCmd.bat"


if exist %enterprise% (
  call %enterprise%
) else (
  if exist %professional% (
    call %professional%
  ) else (
    echo No msbuild with msvc-14.1 found
    exit /b 1
  )
)
