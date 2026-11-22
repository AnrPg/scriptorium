"""Domain core: pure functions and value types. No file, network or subprocess access lives here.

Dependency rule (ports & adapters): core <- pipeline/application <- adapters. Nothing in core imports
from outside core except the standard library and small pure libraries (rapidfuzz).
"""
