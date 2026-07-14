"""Shared slowapi Limiter instance.

Kept in its own module (rather than defined in main.py) so route
modules can import it without importing main.py itself, which would
create a circular import (main.py imports the route modules to
register them).
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
