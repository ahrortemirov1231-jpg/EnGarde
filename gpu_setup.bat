@echo off
REM Engarde Analytics - switch PyTorch to the NVIDIA (CUDA) version so pose tracking runs on the GPU.
REM Run AFTER setup.bat. Run it again if you ever re-run setup.bat (setup installs the CPU version).
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\activate.bat (
  echo Run setup.bat first.
  pause
  exit /b 1
)
where nvidia-smi >nul 2>&1 || (
  echo nvidia-smi was not found, so the NVIDIA driver is missing or too old.
  echo Install or update it with the NVIDIA app ^(nvidia.com/en-us/software/nvidia-app/^), restart, and try again.
  pause
  exit /b 1
)
echo == Your GPU and driver ==
nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv
call .venv\Scripts\activate.bat
set "PYTHONUTF8=1"
set "PATH=%CD%\.venv\Scripts;%PATH%"
echo.
echo == Installing the CUDA version of PyTorch ^(about 3 GB, a few minutes^) ==
python -m pip uninstall -y torch torchvision >nul 2>&1
python -m pip install torch==2.14.1 torchvision --index-url https://download.pytorch.org/whl/cu130
if errorlevel 1 (
  echo CUDA 13.0 build failed; trying the CUDA 12.8 build...
  python -m pip install torch==2.14.1 torchvision --index-url https://download.pytorch.org/whl/cu128 || goto :fail
)
echo.
echo == Check ==
python -c "import torch,sys; ok=torch.cuda.is_available(); print('PyTorch', torch.__version__, '| CUDA available:', ok); print('GPU:', torch.cuda.get_device_name(0) if ok else 'none'); x=torch.randn(2048,2048,device='cuda') if ok else None; print('GPU test:', 'passed' if ok and float((x@x).sum())==float((x@x).sum()) else 'not run'); sys.exit(0 if ok else 1)" || goto :fail
echo.
echo Done. The pose step now uses the GPU automatically. Next: tim_data.bat
pause
exit /b 0
:fail
echo.
echo PyTorch cannot see the GPU. Most common fix: update the NVIDIA driver with the NVIDIA app, restart the PC,
echo and run gpu_setup.bat again. Send a screenshot of this window if it still fails.
pause
exit /b 1
