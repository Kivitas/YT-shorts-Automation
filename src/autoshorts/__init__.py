"""AutoShorts: CPU-optimized YouTube Shorts clipping & automation pipeline."""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _package_version

try:
    __version__ = _package_version("autoshorts")
except PackageNotFoundError:  # source tree without an installed distribution
    __version__ = "1.5.0"
