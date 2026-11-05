@echo off
REM Run once per Conda activation; verified cached files are reused.
REM A failed download should not make the environment unusable.
"%CONDA_PREFIX%\python.exe" -m retinal_setup.download_model_weights
if errorlevel 1 echo [retinal-vasculature] Model download incomplete. Check release availability and network access.
exit /b 0
