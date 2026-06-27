import sys
with open("D:/AI选股Github/alphasift/py_test.txt", "w", encoding="utf-8") as f:
    f.write(f"Python version: {sys.version}\n")
    f.write(f"Python executable: {sys.executable}\n")
    
    # Test basic imports
    try:
        import pandas as pd
        f.write(f"pandas: {pd.__version__}\n")
    except ImportError as e:
        f.write(f"pandas: NOT INSTALLED - {e}\n")
    
    try:
        import yaml
        f.write(f"pyyaml: OK\n")
    except ImportError as e:
        f.write(f"pyyaml: NOT INSTALLED - {e}\n")
    
    try:
        import efinance as ef
        f.write(f"efinance: OK\n")
    except ImportError as e:
        f.write(f"efinance: NOT INSTALLED - {e}\n")
    
    try:
        import akshare as ak
        f.write(f"akshare: {ak.__version__}\n")
    except ImportError as e:
        f.write(f"akshare: NOT INSTALLED - {e}\n")
    
    try:
        import requests
        f.write(f"requests: {requests.__version__}\n")
    except ImportError as e:
        f.write(f"requests: NOT INSTALLED - {e}\n")
    
    f.write("DONE\n")
