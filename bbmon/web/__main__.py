"""Allows the web app to run standalone as ``python -m bbmon.web``."""

import sys

from bbmon.web.app import main

if __name__ == "__main__":
    sys.exit(main())
