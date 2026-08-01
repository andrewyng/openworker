"""Freeze Playwright's driver without rewriting its nested Chromium app."""

from PyInstaller.utils.hooks import collect_data_files


datas = [
    entry
    for entry in collect_data_files("playwright")
    if not any(
        ".local-browsers" in str(value).replace("\\", "/")
        for value in entry[:2]
    )
]
