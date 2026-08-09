# abotclaw Conda Environment - Dependency List
# Generated: 2026-05-20
# Environment path: /home/unitree/miniconda3/envs/abotclaw
# Python: 3.10.20

# =============================================================================
# SYSTEM DEPENDENCIES (Conda packages)
# =============================================================================

# Core
python=3.10.20
pip=26.0.1
setuptools=80.10.2
wheel=0.46.3

# Compilers & Libraries
_libgcc_mutex=0.1
_openmp_mutex=5.1
libgcc=15.2.0
libgcc-ng=15.2.0
libgomp=15.2.0
libstdcxx=15.2.0
libstdcxx-ng=15.2.0

# System Libraries
bzip2=1.0.8
ca-certificates=2025.12.2
ld_impl_linux-aarch64=2.44
libexpat=2.7.5
libffi=3.4.4
libnsl=2.0.0
libuuid=1.41.5
libzlib=1.3.1
ncurses=6.5
openssl=3.5.5
readline=8.3
sqlite=3.51.2
tk=8.6.15
xz=5.8.2
zlib=1.3.1

# X11 / Display
libxcb=1.17.0
xorg-libx11=1.8.12
xorg-libxau=1.0.12
xorg-libxdmcp=1.1.5
xorg-xorgproto=2024.1
pthread-stubs=0.3

# Timezone
tzdata=2026a

# Packaging
packaging=25.0

# =============================================================================
# PYTHON PACKAGES (pip)
# =============================================================================

# Hardware / Robotics Communication
minimalmodbus=2.1.1       # Modbus RTU/ASCII protocol for serial communication
pymodbus=3.5.1            # Full Modbus protocol implementation
pyserial=3.5              # Serial port communication
python-can=4.6.1           # CAN bus interface
python-can-candle=1.2.4   # CAN interface for Candle hardware
pycryptodome=3.23.0       # Cryptographic primitives

# Computer Vision
opencv-python=4.13.0.92   # OpenCV for image processing
pyrealsense2=2.57.7.10387 # Intel RealSense camera SDK

# Scientific Computing
numpy=2.2.6               # Numerical computing
contourpy=1.3.2            # Contour plotting library

# Visualization
matplotlib=3.10.8          # Plotting and visualization
pillow=12.1.1              # Image processing
cycler=0.12.1              # Cycling tools (matplotlib dependency)
fonttools=4.62.1           # Font tools (matplotlib dependency)
kiwisolver=1.5.0           # Graph layout solver (matplotlib dependency)
pyparsing=3.3.2            # Parsing library (matplotlib dependency)

# Math & Geometry
pyquaternion=0.9.9         # Quaternion mathematics

# Configuration
pyyaml=6.0.3               # YAML parsing

# Utilities
python-dateutil=2.9.0.post0 # Date/time utilities
six=1.17.0                 # Python 2/3 compatibility
tqdm=4.67.3                # Progress bars
typing-extensions=4.15.0   # Type hints backport
wrapt=1.17.3               # Wrapping primitives

# Candle API
candle-api==0.0.12         # Candle hardware API

# Process Management
pexpect=4.9.0              # Expect-like interface for subprocess control
ptyprocess=0.7.0           # Pseudo-terminal handling

# =============================================================================
# CATEGORY SUMMARY
# =============================================================================
#
# Category              | Packages
# ----------------------|--------------------------------------------------
# Hardware/Bus Comm     | minimalmodbus, pymodbus, pyserial, python-can,
#                       | python-can-candle, pycryptodome
# Computer Vision       | opencv-python, pyrealsense2
# Math/Science          | numpy, contourpy, pyquaternion
# Visualization         | matplotlib, pillow, cycler, fonttools,
#                       | kiwisolver, pyparsing
# Configuration/Utils   | pyyaml, python-dateutil, six, tqdm,
#                       | typing-extensions, wrapt, pexpect, ptyprocess
# Candle Specific       | candle-api
#
# =============================================================================
# NOTE: This file was generated from `conda env export -n abotclaw`.
# To reproduce this environment, run:
#   conda env create -f environment.yml --name abotclaw
# Or with pip requirements:
#   pip install -r requirements.txt
# =============================================================================
