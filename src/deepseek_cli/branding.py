"""User-facing BanVerse product identity.

Legacy organization and package identifiers intentionally remain unchanged in
the platform entry points so existing desktop and Android data stays available
after the display-name migration.
"""

PRODUCT_NAME = "伴界 BanVerse"
PRODUCT_SHORT_NAME = "伴界"
PRODUCT_NAME_EN = "BanVerse"
# 版本号必须与 pyproject.toml 的 [project].version 及
# packaging/android/build_android.sh 的 APP_VERSION 保持一致；
# 由 packaging/check_version_consistency.py 强制校验。
PRODUCT_VERSION = "0.1.12"
USER_AGENT = f"BanVerse/{PRODUCT_VERSION}"
