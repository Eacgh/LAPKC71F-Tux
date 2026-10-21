# Vendored fallback: pycairo

This directory contains the `cairo` Python bindings extracted from the Arch
Linux `python-cairo` package (1.29.2, Python 3.14, x86_64).  NUC Center uses it
only when the system `python-cairo` package is not installed, so the GUI can
draw its gauges and charts before the installer has run.

`install.sh` installs the proper system package; the vendored copy is a
convenience fallback for this machine.  pycairo is licensed under LGPL-2.1 /
MPL-1.1; see the Arch package for details.
