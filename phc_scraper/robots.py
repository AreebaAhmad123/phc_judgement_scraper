"""robots.txt compliance, isolated from the HTTP client so it's easy to
test/mock independently."""
from urllib.parse import urljoin
from urllib.robotparser import RobotFileParser

from . import config
from .logging_setup import logger


class RobotsGate:
    """Checked once per run (robots.txt doesn't change mid-run) and cached
    for the rest of the process.

    Fetched through the same `requests`-based HTTP client used for
    everything else, rather than RobotFileParser.read() (which opens its
    own bare urllib connection under the hood and can behave differently
    against sites that are picky about TLS/headers).

    A missing robots.txt (404, or unreachable after retries) is treated as
    "no crawling restrictions" per ASSUME_ALLOWED_IF_ROBOTS_UNREACHABLE -
    this is the standard interpretation, since plenty of sites simply don't
    publish one. If a robots.txt IS found and it disallows a path, that
    disallow is always honoured regardless of this setting.
    """

    def __init__(self, base_url=config.BASE_URL, http_client=None):
        self.robots_url = urljoin(base_url, "/robots.txt")
        self._parser = RobotFileParser()
        # status check for wheather robots.txt is downloaded/parsed or not
        self._loaded_ok = False
        self._client = http_client  # wired in by ThrottledClient after construction

    #checks if the internet/network engine (self._client) was successfully attached.
    def load(self):
        if self._client is None:
            self._loaded_ok = False
            return
        response = self._client.get(self.robots_url)
        #checks if the server returned absolutely nothing (which happens if the website has no robots.txt file and returns a 404 Not Found error, or if the server timed out/dropped the connection).
        if response is None:
            logger.info(
                "No robots.txt reachable at %s (404 or connection failure "
                "after retries) -- treating as no crawling restrictions "
                "(ASSUME_ALLOWED_IF_ROBOTS_UNREACHABLE=%s).",
                self.robots_url, config.ASSUME_ALLOWED_IF_ROBOTS_UNREACHABLE,
            )
            self._loaded_ok = False
            return
        try:
            #raw downloaded text file and chops it up line-by-line.
            self._parser.parse(response.text.splitlines())
            self._loaded_ok = True
            logger.info("Loaded robots.txt from %s", self.robots_url)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Fetched %s but couldn't parse it (%s); treating "
                           "as no crawling restrictions.", self.robots_url, exc)
            self._loaded_ok = False


   #acts as the decision-maker for the scraper. Right before the scraper tries to download data from a specific page or case URL, it calls this function to ask: "Am I legally/ethically allowed to visit this specific URL?
    def can_fetch(self, url):
        if not self._loaded_ok:
            return config.ASSUME_ALLOWED_IF_ROBOTS_UNREACHABLE
        return self._parser.can_fetch(config.USER_AGENT, url)

    #This method is responsible for determining how long the scraper should wait (pause) between making consecutive web requests to the court's website. Its primary goal is to ensure the scraper runs politely and doesn't overload or crash the website's server.
    def crawl_delay(self):
        floor = config.MIN_REQUEST_DELAY_SECONDS
        if not self._loaded_ok:
            return floor
        try:
            site_delay = self._parser.crawl_delay(config.USER_AGENT)
        except Exception:  # noqa: BLE001
            site_delay = None
        # find and selects the max delay between site delay and your configuration floor
        return max(floor, float(site_delay)) if site_delay else floor
