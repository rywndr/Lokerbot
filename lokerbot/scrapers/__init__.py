from .dealls import scrape as dealls_scrape
from .glints import scrape as glints_scrape

DEFAULT_SOURCE = "dealls"
SCRAPERS = {
    DEFAULT_SOURCE: dealls_scrape,
    "glints": glints_scrape,
}
scrape = dealls_scrape

__all__ = ["DEFAULT_SOURCE", "SCRAPERS", "dealls_scrape", "glints_scrape", "scrape"]
