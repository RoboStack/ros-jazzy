:: Generated by vinca http://github.com/RoboStack/vinca.
:: DO NOT EDIT!
setlocal

:: MSVC is preferred.
set CC=cl.exe
set CXX=cl.exe

:: PYTHON_INSTALL_DIR should be a relative path, see
:: https://github.com/ament/ament_cmake/blob/2.3.2/ament_cmake_python/README.md
:: So we compute the relative path of %SP_DIR% w.r.t. to LIBRARY_PREFIX,
:: but it is not trivial to do this in Command Prompt scripting, so let's do it via
:: python

:: This line is scary, but it basically assigns the output of the command inside (` and `)
:: to the variable specified after DO SET
:: The equivalent in bash is PYTHON_INSTALL_DIR=`python -c ...`
FOR /F "tokens=* USEBACKQ" %%i IN (`python -c "import os;print(os.path.relpath(os.environ['SP_DIR'],os.environ['LIBRARY_PREFIX']).replace('\\','/'))"`) DO SET PYTHON_INSTALL_DIR=%%i

colcon build ^
    --event-handlers console_cohesion+ ^
    --merge-install ^
    --install-base %LIBRARY_PREFIX% ^
    --cmake-args ^
    --compile-no-warning-as-error ^
     -G Ninja ^
     -DCMAKE_BUILD_TYPE=Release ^
     -DBUILD_TESTING=OFF ^
     -DPYTHON_INSTALL_DIR=%PYTHON_INSTALL_DIR% ^
     -DPYTHON_EXECUTABLE=%PYTHON%
if errorlevel 1 exit 1

:: Copy the [de]activate scripts to %PREFIX%\etc\conda\[de]activate.d.
:: This will allow them to be run on environment activation.
for %%F in (activate deactivate) DO (
    if not exist %PREFIX%\etc\conda\%%F.d mkdir %PREFIX%\etc\conda\%%F.d
    copy %RECIPE_DIR%\%%F.bat %PREFIX%\etc\conda\%%F.d\%PKG_NAME%_%%F.bat
)