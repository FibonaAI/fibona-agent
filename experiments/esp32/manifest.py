# Firmware build manifest: stock MicroPython modules plus fibona, frozen in.
# include() and module() are provided by MicroPython's manifest tool.
include("$(PORT_DIR)/boards/manifest.py")  # noqa: F821
module("fibona.py")  # noqa: F821
