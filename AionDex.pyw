# Double-click launcher: runs the overlay without a console window.
import os
import sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.getcwd())

from run import main  # noqa: E402

main()
