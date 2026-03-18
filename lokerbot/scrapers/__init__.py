from .dealls import scrape as dealls_scrape
from .glints import scrape as glints_scrape
from .kitalulus import scrape as kitalulus_scrape
from .karirhub import scrape as karirhub_scrape

DEFAULT_SOURCE = "dealls"
SCRAPERS = {
    DEFAULT_SOURCE: dealls_scrape,
    "glints": glints_scrape,
    "kitalulus": kitalulus_scrape,
    "karirhub": karirhub_scrape,
}
scrape = dealls_scrape

__all__ = [
    "DEFAULT_SOURCE",
    "SCRAPERS",
    "dealls_scrape",
    "glints_scrape",
    "kitalulus_scrape",
    "karirhub_scrape",
    "scrape",
]
