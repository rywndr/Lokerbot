from .dealls import scrape as dealls_scrape

DEFAULT_SOURCE = "dealls"
SCRAPERS = {DEFAULT_SOURCE: dealls_scrape}
scrape = dealls_scrape

__all__ = ["DEFAULT_SOURCE", "SCRAPERS", "dealls_scrape", "scrape"]
