import os

AUTHOR = 'Owen Peckham'
SITENAME = 'Owen Peckham'
SITEURL = ""

PATH = "content"

THEME = "../themes/bootstrap"

TIMEZONE = 'GB'

DEFAULT_LANG = 'En'

# Feed generation is usually not desired when developing
FEED_ALL_ATOM = None
CATEGORY_FEED_ATOM = None
TRANSLATION_FEED_ATOM = None
AUTHOR_FEED_ATOM = None
AUTHOR_FEED_RSS = None

ARTICLE_EXCLUDES = ['extra']

STATIC_PATHS = ['extra']
EXTRA_PATH_METADATA = {
    'extra/gd_setup.html': {'path': 'gd_setup.html'},
}

# Blogroll
LINKS = (
    ("Pelican", "https://getpelican.com/"),
    ("Python.org", "https://www.python.org/"),
    ("Jinja2", "https://palletsprojects.com/p/jinja/"),
    ("GD Setup Tool", "/gd-setup.html"),
)

# Social widget
SOCIAL = (
    ("LinkedIn", "https://www.linkedin.com/in/owen-peckham-26a038203"),
)

DEFAULT_PAGINATION = False

# Uncomment following line if you want document-relative URLs when developing
# RELATIVE_URLS = True