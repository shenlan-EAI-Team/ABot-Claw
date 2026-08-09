# Test script that exactly mimics what the server does
source ~/miniconda3/etc/profile.d/conda.sh && conda activate g1_agent

# Verify what the shell sees
python3 -c "
import sys
print('sys.path in activated env:')
for i, p in enumerate(sys.path):
    print(f'  {i}: {p}')
"