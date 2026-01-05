# Configuration file for the Sphinx documentation builder.
#
# This file is based on the Canonical Sphinx Docs Starter Pack.

import sys
import os

# -- Project information -----------------------------------------------------
project = 'PackaStack'
copyright = '2025 Canonical Ltd.'
author = 'Canonical Ltd.'

# -- General configuration ---------------------------------------------------
extensions = [
    'myst_parser',
    'sphinx_copybutton',
]

myst_enable_extensions = [
    "colon_fence",
    "deflist",
    "html_admonition",
    "html_image",
    "replacements",
    "smartquotes",
    "substitution",
    "tasklist",
]

source_suffix = {
    '.rst': 'restructuredtext',
    '.md': 'markdown',
}

exclude_patterns = ['_build', 'Thumbs.db', '.DS_Store']

# -- Options for HTML output -------------------------------------------------
html_theme = 'canonical_sphinx_theme'
html_title = 'PackaStack Documentation'
html_static_path = ['_static']

def setup(app):
    app.add_css_file('canonical-overrides.css')
