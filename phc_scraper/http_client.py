"""A single, throttled, retrying HTTP client shared by every request the
scraper makes (search POSTs, robots.txt, PDF downloads)."""

#used to generate pseudo-random numbers and make random choices.
import random
import time

#tool for sending HTTP requests
import requests

#handles network connections.By default, requests does not automatically retry failed connections, nor does it limit how fast it talks to a server. An HTTPAdapter acts as a bridge that lets you configure automatic retries, connection pooling, and timeouts for specific websites
from requests.adapters import HTTPAdapter

#While HTTPAdapter is the engine that executes retries, Retry is the control panel where you configure the settings. It allows you to handle flaky Wi-Fi, temporary server crashes, or API rate limits gracefully without crashing your script
from urllib3.util.retry import Retry

from . import config
from .logging_setup import logger
from .robots import RobotsGate


class ThrottledClient:
    """One requests.Session reused for the whole run (connection pooling,
    picks up any session cookie the server sets). urllib3's Retry handles
    transient HTTP/connection failures at the connect/header level; a
    manual throttle sits in front of every request so retries can't bypass
    our politeness floor. Never raises for "site is down" - callers get
    None back and decide what that means for their record."""

    def __init__(self):
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": config.USER_AGENT})
        if config.IDENTIFY_AS_BROWSER:
            self._session.headers.update(config.BROWSER_HEADERS)

        retry = Retry(
            total=config.MAX_RETRIES, backoff_factor=config.BACKOFF_FACTOR,
            status_forcelist=config.RETRYABLE_STATUS_CODES,
            allowed_methods=frozenset(["GET", "POST"]),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)

        self._last_request_ts = 0.0
        # Unloaded RobotsGate defaults to permissive (see
        # ASSUME_ALLOWED_IF_ROBOTS_UNREACHABLE), which is what lets the
        # very first request below -- fetching robots.txt itself -- go
        # through without a chicken-and-egg deadlock.
        self.robots = RobotsGate(http_client=self)
        self.robots.load()

    def _wait_for_slot(self):
        """Calculates and executes an anti-bot/throttling delay between requests.
        
        It determines the remaining cooldown time based on the website's requested
        crawl_delay and the timestamp of the last request. If the scraper is running 
        too fast, it forces the script to sleep. It also injects a randomized 'jitter' 
        slice to disrupt predictable request patterns, helping the bot mimic human 
        behavior and blend in to avoid WAF/firewall detection."""
        delay = self.robots.crawl_delay()
        #time.monotonic()-->internal clock timestamp representing "right now" in seconds.self._last_request_ts -->timestamp of exactly when the previous web request finished.
        remaining = delay - (time.monotonic() - self._last_request_ts)
        if remaining > 0:
            time.sleep(remaining + random.uniform(0, config.JITTER_SECONDS))

    def warm_up(self):
        """GET the search page once before any POST to it, so the server's
        PHPSESSID cookie is established first. A raw POST straight to
        `?action=search` with no prior session/Referer is exactly the kind
        of request a PHP session guard or WAF is likely to reject."""
        
        response = self.get(config.SEARCH_PAGE_URL)
        if response is None:
            logger.warning(
                "Could not warm up a session against %s -- search POSTs "
                "will be sent without an established PHPSESSID cookie, "
                "which may get rejected/reset.", config.SEARCH_PAGE_URL,
            )
            return False
        got_cookie = "PHPSESSID" in self._session.cookies.get_dict()
        logger.info("Warmed up session against %s (PHPSESSID cookie %s).",
                    config.SEARCH_PAGE_URL, "acquired" if got_cookie else "not present")
        return got_cookie
    
    def get(self, url, **kwargs):
        """Passes a standard GET request to the central throttled request handler."""
        return self._request("GET", url, **kwargs)

    def post(self, url, **kwargs):
        """Passes a POST request to the central handler after injecting a Referer header.
        
        The Referer header mimics a human coming directly from the search form page, 
        preventing the request from being flagged and blocked by a security WAF.
        """
        kwargs.setdefault("headers", {}).setdefault("Referer", config.SEARCH_PAGE_URL)
        return self._request("POST", url, **kwargs)

    def _request(self, method, url, **kwargs):
        #touches the internet, it asks the RobotsGate object (self.robots.can_fetch(url)) if your user agent is allowed to visit this URL.
        if not self.robots.can_fetch(url):
            logger.error("robots.txt disallows %s for our user agent. Skipping.", url)
            return None
        #forces the program to calculate how much time has passed since the last request and pauses the scraper (time.sleep) with randomized jitter if it is moving too fast.
        self._wait_for_slot()
        #If you didn't manually define a maximum wait time for the webpage to load, this line automatically injects a default timeout limit from config
        kwargs.setdefault("timeout", config.REQUEST_TIMEOUT)
        try:
            response = self._session.request(method, url, **kwargs)
            self._last_request_ts = time.monotonic()
            response.raise_for_status()
            return response
        except requests.exceptions.RequestException as exc:
            self._last_request_ts = time.monotonic()
            logger.error("%s %s failed after retries: %s", method, url, exc)
            return None

    def close(self):
        self._session.close()
