from time import sleep
import logging
import re
from requests.sessions import Session
import js2py
from copy import deepcopy

try:
    from urlparse import urlparse
except ImportError:
    from urllib.parse import urlparse

DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 6.1; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/49.0.2623.110 Safari/537.36"


class CloudflareScraper(Session):
    def __init__(self, *args, **kwargs):
        super(CloudflareScraper, self).__init__(*args, **kwargs)

        if "requests" in self.headers["User-Agent"]:
            # Spoof Firefox on Linux if no custom User-Agent has been set
            self.headers["User-Agent"] = DEFAULT_USER_AGENT

    def request(self, method, url, *args, **kwargs):
        resp = super(CloudflareScraper, self).request(method, url, *args, **kwargs)

        # Check if Cloudflare anti-bot is on
        if resp.status_code == 503 and resp.headers.get("Server") == "cloudflare-nginx":
            return self.solve_cf_challenge(resp, **kwargs)

        # Otherwise, no Cloudflare anti-bot detected
        return resp

    def solve_cf_challenge(self, resp, **original_kwargs):
        sleep(5)  # Cloudflare requires a delay before solving the challenge

        body = resp.text
        parsed_url = urlparse(resp.url)
        domain = urlparse(resp.url).netloc
        submit_url = "%s://%s/cdn-cgi/l/chk_jschl" % (parsed_url.scheme, domain)

        cloudflare_kwargs = deepcopy(original_kwargs)
        params = cloudflare_kwargs.setdefault("params", {})
        headers = cloudflare_kwargs.setdefault("headers", {})
        headers["Referer"] = resp.url

        try:
            params["jschl_vc"] = re.search(r'name="jschl_vc" value="(\w+)"', body).group(1)
            params["pass"] = re.search(r'name="pass" value="(.+?)"', body).group(1)

            # Extract the arithmetic operation
            js = self.extract_js(body)

        except Exception:
            # Something is wrong with the page.
            # This may indicate Cloudflare has changed their anti-bot
            # technique. If you see this and are running the latest version,
            # please open a GitHub issue so I can update the code accordingly.
            logging.error("[!] Unable to parse Cloudflare anti-bots page. "
                          "Try upgrading cloudflare-scrape, or submit a bug report "
                          "if you are running the latest version. Please read "
                          "https://github.com/Anorov/cloudflare-scrape#updates "
                          "before submitting a bug report.")
            raise

        # Safely evaluate the Javascript expression
        params["jschl_answer"] = str(int(js2py.eval_js(js)) + len(domain))

        # Requests transforms any request into a GET after a redirect,
        # so the redirect has to be handled manually here to allow for
        # performing other types of requests even as the first request.
        method = resp.request.method
        cloudflare_kwargs['allow_redirects'] = False
        redirect = self.request(method, submit_url, **cloudflare_kwargs)
        return self.request(method, redirect.headers['Location'], **original_kwargs)

    def extract_js(self, body):
        js = re.search(r"setTimeout\(function\(\){\s+(var "
                        "s,t,o,p,b,r,e,a,k,i,n,g,f.+?\r?\n[\s\S]+?a\.value =.+?)\r?\n", body).group(1)
        js = re.sub(r"a\.value = (parseInt\(.+?\)).+", r"\1", js)
        js = re.sub(r"\s{3,}[a-z](?: = |\.).+", "", js)

        # Strip characters that could be used to exit the string context
        # These characters are not currently used in Cloudflare's arithmetic snippet
        js = re.sub(r"[\n\\']", "", js)

        return js

    @classmethod
    def create_scraper(cls, sess=None, **kwargs):
        """
        Convenience function for creating a ready-to-go requests.Session (subclass) object.
        """

        scraper = cls()

        if sess:
            attrs = ["auth", "cert", "cookies", "headers", "hooks", "params", "proxies", "data"]
            for attr in attrs:
                val = getattr(sess, attr, None)
                if val:
                    setattr(scraper, attr, val)

        return scraper


    ## Functions for integrating cloudflare-scrape with other applications and scripts

    @classmethod
    def get_tokens(cls, url, user_agent=None, **kwargs):
        scraper = cls.create_scraper()
        if user_agent:
            scraper.headers["User-Agent"] = user_agent

        try:
            resp = scraper.get(url)
            resp.raise_for_status()
        except Exception as e:
            logging.error("'%s' returned an error. Could not collect tokens." % url)
            raise

        domain = urlparse(resp.url).netloc
        cookie_domain = None

        for d in scraper.cookies.list_domains():
            if d.startswith(".") and d in ("." + domain):
                cookie_domain = d
                break
        else:
            raise ValueError("Unable to find Cloudflare cookies. Does the site actually have Cloudflare IUAM (\"I'm Under Attack Mode\") enabled?")

        return ({
                    "__cfduid": scraper.cookies.get("__cfduid", "", domain=cookie_domain),
                    "cf_clearance": scraper.cookies.get("cf_clearance", "", domain=cookie_domain)
                },
                scraper.headers["User-Agent"]
               )

    @classmethod
    def get_cookie_string(cls, url, user_agent=None, **kwargs):
        """
        Convenience function for building a Cookie HTTP header value.
        """
        tokens, user_agent = cls.get_tokens(url, user_agent=user_agent)
        return "; ".join("=".join(pair) for pair in tokens.items()), user_agent

create_scraper = CloudflareScraper.create_scraper
get_tokens = CloudflareScraper.get_tokens
get_cookie_string = CloudflareScraper.get_cookie_string
