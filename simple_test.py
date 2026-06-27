import os
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "simple_test.txt")
with open(out, "w") as f:
    f.write("Python is working\n")
    f.write(f"Version: {__import__('sys').version}\n")
    f.write(f"CWD: {os.getcwd()}\n")
