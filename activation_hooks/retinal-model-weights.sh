# Run from Conda activate.d; uses standard-library Python and cached hashes.
# On Windows Git Bash, python.exe is at the environment root.
if [ -x "${CONDA_PREFIX}/python.exe" ]; then
  _RETINAL_PYTHON="${CONDA_PREFIX}/python.exe"
else
  _RETINAL_PYTHON="${CONDA_PREFIX}/bin/python"
fi
"${_RETINAL_PYTHON}" -m retinal_setup.download_model_weights || \
  echo '[retinal-vasculature] Model download incomplete. Check release availability and network access.'
unset _RETINAL_PYTHON
