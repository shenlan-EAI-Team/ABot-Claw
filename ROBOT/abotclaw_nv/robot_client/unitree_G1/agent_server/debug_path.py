#!/usr/bin/env python3
import sys
print("sys.path:")
for i, p in enumerate(sys.path):
    print(f"  {i}: {p}")
