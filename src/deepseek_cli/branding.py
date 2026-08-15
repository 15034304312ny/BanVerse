"""User-facing BanVerse product identity.

Legacy organization and package identifiers intentionally remain unchanged in
the platform entry points so existing desktop and Android data stays available
after the display-name migration.
"""

from ._version import __version__ as PRODUCT_VERSION

PRODUCT_NAME = "伴界 BanVerse"
PRODUCT_SHORT_NAME = "伴界"
PRODUCT_NAME_EN = "BanVerse"
USER_AGENT = f"BanVerse/{PRODUCT_VERSION}"
