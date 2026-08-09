#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONDA_ROOT="$HOME/miniconda3"

if [ -d "$CONDA_ROOT" ]; then
    source "$CONDA_ROOT/etc/profile.d/conda.sh"
    conda activate anygrasp
fi

python "$SCRIPT_DIR/demo.py" --checkpoint_path "$SCRIPT_DIR/log/checkpoint_tracking.tar" 
#--filter oneeuro