"""Point every machine-specific path used by app.py at a throwaway sandbox.

app.py computes its Douzy, VideOCR, Edge and VietSub Studio paths from
environment variables when it is imported. Import this module before
``import app`` so a test run never reads or writes the developer's real data.
"""

import atexit
import os
import shutil
import tempfile

SANDBOX_DIR = tempfile.mkdtemp(prefix="vietsub-tests-")
APPDATA_DIR = os.path.join(SANDBOX_DIR, "AppData", "Roaming")
LOCALAPPDATA_DIR = os.path.join(SANDBOX_DIR, "AppData", "Local")
CONFIG_DIR = os.path.join(SANDBOX_DIR, "VietSub Studio")
DOWNLOAD_DIR = os.path.join(SANDBOX_DIR, "downloads")

for _directory in (APPDATA_DIR, LOCALAPPDATA_DIR):
    os.makedirs(_directory, exist_ok=True)

os.environ["APPDATA"] = APPDATA_DIR
os.environ["LOCALAPPDATA"] = LOCALAPPDATA_DIR
os.environ["VIETSUB_STUDIO_CONFIG_DIR"] = CONFIG_DIR
os.environ["VIETSUB_STUDIO_DOWNLOAD_DIR"] = DOWNLOAD_DIR

atexit.register(shutil.rmtree, SANDBOX_DIR, ignore_errors=True)
