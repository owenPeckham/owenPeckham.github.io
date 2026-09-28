AUTHOR = 'Owen Peckham'
SITENAME = 'Owen Peckham'
SITEURL = ""

PATH = "content"

THEME = "../themes/owenpeckham"

TIMEZONE = 'GB'

DEFAULT_LANG = 'En'

# Feed generation is usually not desired when developing
FEED_ALL_ATOM = None
CATEGORY_FEED_ATOM = None
TRANSLATION_FEED_ATOM = None
AUTHOR_FEED_ATOM = None
AUTHOR_FEED_RSS = None

# 'extra' holds the two standalone interactive tools (self-contained HTML,
# not Pelican templates) -- copied through verbatim.
# 'images' and 'cv' are plain static assets referenced from page content.
ARTICLE_EXCLUDES = ['extra', 'images', 'cv']

STATIC_PATHS = ['extra', 'images', 'cv']
EXTRA_PATH_METADATA = {
    'extra/gd_setup.html': {'path': 'gd_setup.html'},
    'extra/pcls_demonstrator.html': {'path': 'pcls_demonstrator.html'},
}

# Project write-ups live under /projects/<slug>.html; everything else
# (About, Publications) sits at the site root, e.g. /publications.html.
PAGE_URL = '{slug}.html'
PAGE_SAVE_AS = '{slug}.html'

# Interactive tools shown in the "Interactive Tools" sidebar box. Both are
# given equal billing -- they're two tools from the same PCLS project.
TOOLS = (
    {"label": "PCLS Demonstrator", "url": "/pcls_demonstrator.html", "primary": True},
    {"label": "GD Setup Tool", "url": "/gd_setup.html", "primary": True},
)

# "Elsewhere" links in the sidebar + footer icon row.
ELSEWHERE = (
    {"label": "LinkedIn", "url": "https://www.linkedin.com/in/owen-peckham-26a038203"},
    {"label": "ORCID", "url": "https://orcid.org/0009-0000-6762-7594"},
    {"label": "Google Scholar", "url": "https://scholar.google.com/owen-peckham"},
    {"label": "DMF Lab", "url": "https://dmf-lab.co.uk/owen-peckham"},
)

FOOTER_LINKS = (
    {"label": "LinkedIn", "url": "https://www.linkedin.com/in/owen-peckham-26a038203", "icon": "linkedin"},
    {"label": "ORCID", "url": "https://orcid.org/0009-0000-6762-7594", "icon": "orcid"},
    {"label": "Google Scholar", "url": "https://scholar.google.com/owen-peckham", "icon": "scholar"},
    {"label": "DMF Lab", "url": "https://dmf-lab.co.uk/owen-peckham", "icon": "dmf"},
    {"label": "Email", "url": "mailto:owenpeckham@gmail.com", "icon": "mail"},
)

CV_LINKS = (
    {"label": "academic", "url": "/cv/academic_cv.pdf"},
    {"label": "industry", "url": "/cv/industry_cv.pdf"},
)

DEFAULT_PAGINATION = False

# Uncomment following line if you want document-relative URLs when developing
# RELATIVE_URLS = True
