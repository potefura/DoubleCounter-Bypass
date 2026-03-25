"""
Double Counter Verification Bypass
Fetches, validates, and uses HTTP proxies to bypass Discord's Double Counter bot.
"""

from __future__ import annotations

import os
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import colorama
import fake_headers
import requests
from colorama import Fore, Style

colorama.init(autoreset=True)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PROXY_FILE       = "proxies.txt"
VALIDATE_URL     = "https://www.google.com"
VALIDATE_TIMEOUT = 7    # seconds per proxy check
VALIDATE_THREADS = 250  # concurrent threads during validation
BYPASS_TIMEOUT   = 10   # seconds per bypass request

BROWSERS: list[str]       = ["chrome", "firefox", "opera"]
OS_MAP:   dict[str, str]  = {"win": "Windows", "mac": "macOS", "linux": "Linux"}

PROXY_SOURCES: list[dict[str, str]] = [
    {
        "name": "ProxyScrape",
        "url": "https://api.proxyscrape.com/v3/free-proxy-list/get?request=displayproxies&protocol=http&timeout=10000&country=all&simplified=true"
    },
    {
        "name": "TheSpeedX/PROXY-List",
        "url": "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
    },
    {
        "name": "monosans/proxy-list",
        "url": "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
    },
    {
        "name": "roosterkid/openproxylist",
        "url": "https://raw.githubusercontent.com/roosterkid/openproxylist/main/HTTPS_RAW.txt",
    },
]

CF_SIGNATURES: list[tuple[str, str]] = [
    ("cf-turnstile",             "CF Turnstile CAPTCHA"),
    ("cf-browser-verification",  "CF browser verification"),
    ("challenge-form",           "CF challenge form"),
    ("Just a moment",            "CF JS challenge (Just a moment...)"),
    ("Checking your browser",    "CF JS challenge (Checking your browser)"),
    ("Please Wait",              "CF waiting room"),
    ("DDoS protection",          "CF DDoS protection page"),
    ("cf_clearance",             "CF clearance cookie challenge"),
    ("Ray ID",                   "CF generic block (Ray ID present)"),
    ("Access denied",            "CF access denied"),
    ("captcha",                  "CF CAPTCHA"),
]

# ---------------------------------------------------------------------------
# Shared state (bypass worker threads)
# ---------------------------------------------------------------------------

attempt_count = 0
attempt_lock  = threading.Lock()
file_lock     = threading.Lock()

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def timestamp() -> str:
    return f"{Fore.LIGHTBLACK_EX}[{datetime.now().strftime('%H:%M:%S')}]{Style.RESET_ALL}"


def banner() -> None:
    print(f"""
{Fore.CYAN}╔════════════════════════════════════════╗
║   {Fore.WHITE}Double Counter Verification Bypass{Fore.CYAN}   ║
╚════════════════════════════════════════╝{Style.RESET_ALL}""")


def menu() -> str:
    print(f"\n{Fore.WHITE}  {Fore.CYAN}[1]{Fore.WHITE} Run Double Counter bypass")
    print(f"  {Fore.CYAN}[2]{Fore.WHITE} Check current proxies")
    print(f"  {Fore.CYAN}[3]{Fore.WHITE} Get fresh proxies from public providers")
    print(f"  {Fore.CYAN}[4]{Fore.WHITE} Exit\n")

    while True:
        choice = input(f"{Fore.WHITE}  Choice {Fore.CYAN}» {Style.RESET_ALL}").strip()
        if choice in ("1", "2", "3", "4"):
            return choice
        print(f"{timestamp()} {Fore.RED}Invalid choice — enter 1, 2, 3, or 4.")


# ---------------------------------------------------------------------------
# Proxy helpers
# ---------------------------------------------------------------------------

def _strip_scheme(raw: str) -> str:
    """Remove any protocol prefix from a proxy string and return bare ip:port."""
    for scheme in ("http://", "https://", "socks5://", "socks4://"):
        if raw.startswith(scheme):
            return raw[len(scheme):]
    return raw


def is_valid_proxy(raw: str) -> bool:
    """Return True if raw is a well-formed ip:port string."""
    raw = _strip_scheme(raw.strip())
    if not raw or raw.startswith("#"):
        return False
    try:
        host, port_str = raw.rsplit(":", 1)
    except ValueError:
        return False
    if not port_str.isdigit() or not (1 <= int(port_str) <= 65535):
        return False
    octets = host.split(".")
    return len(octets) == 4 and all(o.isdigit() and 0 <= int(o) <= 255 for o in octets)


def load_proxies(filename: str) -> list[dict]:
    """
    Read proxies from file, skip malformed entries, and rewrite the file
    without them. Returns a list of proxy dicts ready for requests.
    """
    valid:   list[dict] = []
    skipped: int        = 0

    with open(filename, "r") as fh:
        for line in fh:
            raw = line.strip()
            if not raw:
                continue
            if is_valid_proxy(raw):
                valid.append({"https": f"http://{raw}"})
            else:
                skipped += 1

    if skipped:
        label = "proxy" if skipped == 1 else "proxies"
        print(
            f"{timestamp()} {Fore.YELLOW}Removed {Fore.CYAN}{skipped}{Fore.YELLOW} "
            f"invalid {label} from {filename} — expected format: ip:port"
        )
        with open(filename, "w") as fh:
            for entry in valid:
                fh.write(entry["https"].removeprefix("http://") + "\n")

    return valid


def write_proxies(filename: str, proxies: list[str]) -> None:
    with open(filename, "w") as fh:
        fh.write("\n".join(proxies) + "\n")


def split_evenly(lst: list, n: int) -> list[list]:
    avg = len(lst) / n
    return [lst[int(i * avg):int((i + 1) * avg)] for i in range(n)]

# ---------------------------------------------------------------------------
# Proxy validation
# ---------------------------------------------------------------------------

def _probe_proxy(proxy: str) -> str | None:
    """Return the proxy string if it can reach VALIDATE_URL, else None."""
    try:
        response = requests.get(
            VALIDATE_URL,
            proxies={"http": f"http://{proxy}", "https": f"http://{proxy}"},
            timeout=VALIDATE_TIMEOUT,
            allow_redirects=True,
        )
        if response.status_code < 500:
            return proxy
    except Exception:
        pass
    return None


def validate_proxies(proxies: list[str]) -> list[str]:
    """
    Test every proxy in parallel. Returns only the live ones.
    Prints a live progress bar to stdout.
    """
    total   = len(proxies)
    live:   list[str] = []
    checked = 0
    lock    = threading.Lock()
    start   = time.time()

    print(
        f"{timestamp()} {Fore.WHITE}Validating {Fore.CYAN}{total:,}{Fore.WHITE} proxies "
        f"against {Fore.CYAN}{VALIDATE_URL}{Fore.WHITE} "
        f"({Fore.CYAN}{VALIDATE_THREADS}{Fore.WHITE} threads, "
        f"{VALIDATE_TIMEOUT}s timeout)...\n"
    )

    with ThreadPoolExecutor(max_workers=VALIDATE_THREADS) as pool:
        futures = {pool.submit(_probe_proxy, p): p for p in proxies}

        for future in as_completed(futures):
            result = future.result()
            with lock:
                checked += 1
                if result:
                    live.append(result)

                pct     = checked / total * 100
                filled  = int(pct / 5)
                bar     = "█" * filled + "░" * (20 - filled)
                elapsed = time.time() - start
                speed   = checked / elapsed if elapsed > 0 else 0

                print(
                    f"\r  {Fore.CYAN}{bar}{Style.RESET_ALL} "
                    f"{Fore.WHITE}{pct:5.1f}%  "
                    f"checked {Fore.CYAN}{checked:,}{Fore.WHITE}/{total:,}  "
                    f"valid {Fore.GREEN}{len(live):,}{Style.RESET_ALL}  "
                    f"{Fore.LIGHTBLACK_EX}{speed:.0f} p/s",
                    end="",
                    flush=True,
                )

    elapsed = time.time() - start
    print(
        f"\n\n{timestamp()} {Fore.GREEN}✓ Validation complete — "
        f"{Fore.CYAN}{len(live):,}{Fore.GREEN} live out of {total:,} "
        f"{Fore.LIGHTBLACK_EX}({elapsed:.1f}s)\n"
    )
    return live

# ---------------------------------------------------------------------------
# Proxy fetching
# ---------------------------------------------------------------------------

def fetch_proxies() -> None:
    """Pull proxies from all configured public sources, validate, and save."""
    print(f"""
{Fore.YELLOW}  ⚠  WARNING — Public Proxy Risks
{Fore.LIGHTBLACK_EX}  ─────────────────────────────────────────────────────────────────
{Fore.WHITE}  These proxies are sourced from public lists and are used by many
{Fore.WHITE}  people simultaneously. Be aware of the following risks:

{Fore.RED}  • High detection rate   {Fore.WHITE}— Public proxies are well-known to Cloudflare
{Fore.WHITE}    and Double Counter. Expect a higher chance of being flagged as a
{Fore.WHITE}    proxy user compared to private or residential proxies.

{Fore.RED}  • Alt account risk      {Fore.WHITE}— Because these IPs are shared, another user
{Fore.WHITE}    may have already triggered a ban or flag on the same IP, which
{Fore.WHITE}    could result in your alt account being detected (RR02).

{Fore.RED}  • No privacy guarantee  {Fore.WHITE}— Traffic routed through unknown public
{Fore.WHITE}    proxies may be logged or intercepted by the proxy operator.
{Fore.LIGHTBLACK_EX}  ─────────────────────────────────────────────────────────────────{Style.RESET_ALL}
""")

    confirm = input(f"  {Fore.WHITE}Continue anyway? {Fore.CYAN}[y/n]{Fore.WHITE} » {Style.RESET_ALL}").strip().lower()
    if confirm != "y":
        print(f"{timestamp()} {Fore.CYAN}Cancelled.\n")
        return

    print(f"\n{timestamp()} {Fore.CYAN}Starting proxy collection from {len(PROXY_SOURCES)} providers...\n")

    # 1 — clear existing file
    print(f"{timestamp()} {Fore.YELLOW}[1/5] {Fore.WHITE}Clearing {PROXY_FILE}...")
    write_proxies(PROXY_FILE, [])
    time.sleep(0.3)
    print(f"{timestamp()} {Fore.GREEN}      ✓ Cleared.\n")

    # 2 — fetch raw proxies from every source
    print(f"{timestamp()} {Fore.YELLOW}[2/5] {Fore.WHITE}Fetching from providers...\n")
    collected: set[str] = set()

    for source in PROXY_SOURCES:
        name = source["name"]
        url  = source["url"]
        url_display = url[:72] + ("..." if len(url) > 72 else "")
        print(f"{timestamp()}   {Fore.CYAN}↳ {Fore.WHITE}{name}  {Fore.LIGHTBLACK_EX}{url_display}")
        try:
            response = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            response.raise_for_status()
            before = len(collected)
            for line in response.text.splitlines():
                cleaned = _strip_scheme(line.strip())
                if is_valid_proxy(cleaned):
                    collected.add(cleaned)
            added = len(collected) - before
            print(
                f"{timestamp()}     {Fore.GREEN}✓ {Fore.CYAN}{added:,}{Fore.WHITE} new proxies  "
                f"{Fore.LIGHTBLACK_EX}(total: {len(collected):,})\n"
            )
        except requests.RequestException as exc:
            print(f"{timestamp()}     {Fore.RED}✗ Failed — {type(exc).__name__}: {exc}\n")

    if not collected:
        print(f"{timestamp()} {Fore.RED}No proxies collected. Check your connection and try again.")
        return

    # 3 — deduplicate
    raw_list = sorted(collected)
    print(f"{timestamp()} {Fore.YELLOW}[3/5] {Fore.WHITE}Deduplicated — {Fore.CYAN}{len(raw_list):,}{Fore.WHITE} unique proxies.\n")

    # 4 — validate
    print(f"{timestamp()} {Fore.YELLOW}[4/5] {Fore.WHITE}Running connectivity check...")
    live = validate_proxies(raw_list)

    if not live:
        print(f"{timestamp()} {Fore.RED}No live proxies after validation. Try again later.")
        return

    # 5 — save
    print(f"{timestamp()} {Fore.YELLOW}[5/5] {Fore.WHITE}Saving {Fore.CYAN}{len(live):,}{Fore.WHITE} live proxies to {PROXY_FILE}...")
    write_proxies(PROXY_FILE, live)
    print(
        f"{timestamp()} {Fore.GREEN}      ✓ Done — "
        f"{Fore.CYAN}{len(live):,}{Fore.GREEN} saved, "
        f"{Fore.CYAN}{len(raw_list) - len(live):,}{Fore.GREEN} discarded.\n"
    )

# ---------------------------------------------------------------------------
# Check current proxies
# ---------------------------------------------------------------------------

def check_proxies() -> None:
    """Validate the proxies already in PROXY_FILE and remove the dead ones."""
    print(f"\n{timestamp()} {Fore.CYAN}Loading proxies from {PROXY_FILE}...")

    if not os.path.exists(PROXY_FILE):
        print(f"{timestamp()} {Fore.RED}  {PROXY_FILE} not found.")
        return

    with open(PROXY_FILE, "r") as fh:
        lines = [ln.strip() for ln in fh if ln.strip()]

    if not lines:
        print(f"{timestamp()} ⚠️  {Fore.YELLOW}  {PROXY_FILE} is empty — nothing to check.")
        return

    label = "proxy" if len(lines) == 1 else "proxies"
    print(f"{timestamp()} {Fore.WHITE}  Found {Fore.CYAN}{len(lines):,}{Fore.WHITE} {label}.\n")

    live = validate_proxies(lines)

    if not live:
        print(
            f"{timestamp()} {Fore.RED}  No live proxies found. "
            f"Fetch fresh ones with option {Fore.CYAN}[1]{Fore.RED}."
        )
        return

    write_proxies(PROXY_FILE, live)
    print(
        f"{timestamp()} {Fore.GREEN}  ✓ Done — "
        f"{Fore.CYAN}{len(live):,}{Fore.GREEN} kept, "
        f"{Fore.CYAN}{len(lines) - len(live):,}{Fore.GREEN} removed.\n"
    )

# ---------------------------------------------------------------------------
# Bypass worker
# ---------------------------------------------------------------------------

def _decode_body(response: requests.Response) -> str | None:
    """
    Safely decode the response body to a string.
    Returns None if the content appears to be binary/compressed data
    that could not be meaningfully decoded.
    """
    for encoding in ("utf-8", "latin-1"):
        try:
            text = response.content.decode(encoding)
            sample = text[:200]
            non_printable = sum(1 for c in sample if ord(c) < 32 and c not in "\n\r\t")
            if non_printable / max(len(sample), 1) > 0.10:
                return None
            return text
        except (UnicodeDecodeError, ValueError):
            continue
    return None


def _classify_response(response: requests.Response) -> tuple[str, str]:
    """
    Inspect a response that did not match any known DC outcome.
    Returns (label, detail) — neither includes the HTTP status code,
    since the caller appends that separately.
    """
    body = _decode_body(response)

    if body is None:
        content_type     = response.headers.get("Content-Type", "unknown")
        content_encoding = response.headers.get("Content-Encoding", "none")
        detail = f"Content-Type: {content_type} | Content-Encoding: {content_encoding} | {len(response.content)} bytes"
        return "Binary / compressed body — proxy may be mangling the response", detail

    body_lower = body.lower()
    snippet    = body.strip()[:120].replace("\n", " ").replace("\r", "")

    for marker, label in CF_SIGNATURES:
        if marker.lower() in body_lower:
            return label, snippet

    return "Unknown response", snippet

def _build_request_headers() -> tuple[dict, str, str, str]:
    """Return (headers, browser_name, os_name, user_agent) with randomised identity."""
    browser = random.choice(BROWSERS)
    os_key  = random.choice(list(OS_MAP.keys()))
    headers = fake_headers.Headers(browser=browser, os=os_key, headers=True).generate()
    ua      = headers.get("User-Agent", "Unknown")
    return headers, browser.capitalize(), OS_MAP[os_key], ua


def _log_attempt(icon: str, color: str, message: str, browser: str, os_name: str, ua: str, proxy: str) -> None:
    short_ua = ua if len(ua) <= 55 else ua[:52] + "..."
    print(
        f"{timestamp()} {icon}  {color}{message}{Style.RESET_ALL}"
        f" | {Fore.LIGHTBLACK_EX}{proxy}{Style.RESET_ALL}"
        f" | {Fore.YELLOW}{os_name}{Style.RESET_ALL}"
        f" | {Fore.CYAN}{browser}{Style.RESET_ALL}"
        f" | {Fore.LIGHTBLUE_EX}{short_ua}"
    )


def _remove_proxy_from_file(proxy_str: str) -> None:
    with file_lock:
        with open(PROXY_FILE, "r") as fh:
            lines = fh.readlines()
        with open(PROXY_FILE, "w") as fh:
            for line in lines:
                if line.strip() != proxy_str:
                    fh.write(line)


def _bypass_worker(url: str, chunk: list[dict]) -> None:
    global attempt_count

    for proxy_info in chunk:
        proxy      = {"https": proxy_info["https"]}
        proxy_addr = proxy_info["https"].removeprefix("http://")
        headers, browser, os_name, ua = _build_request_headers()

        with attempt_lock:
            attempt_count += 1

        try:
            response = requests.get(url, headers=headers, proxies=proxy, timeout=BYPASS_TIMEOUT)

            if response.status_code == 200 and "Success!" in response.text:
                _log_attempt("✅", Fore.GREEN, "Bypass successful", browser, os_name, ua, proxy_addr)
                _remove_proxy_from_file(proxy_addr)
                os._exit(0)

            elif "Expired link" in response.text:
                _log_attempt("⚠️", Fore.YELLOW, "Link expired — get a fresh URL", browser, os_name, ua, proxy_addr)
                _remove_proxy_from_file(proxy_addr)
                os._exit(0)

            elif "RR02" in response.text:
                _log_attempt("🚫", Fore.YELLOW, "Alt account flagged by DC", browser, os_name, ua, proxy_addr)
                _remove_proxy_from_file(proxy_addr)
                os._exit(0)

            elif "RV01" in response.text:
                _log_attempt("🔒", Fore.YELLOW, "Proxy flagged by DC", browser, os_name, ua, proxy_addr)

            else:
                label, detail = _classify_response(response)
                _log_attempt("☁️", Fore.MAGENTA, f"{label} [HTTP {response.status_code}]", browser, os_name, ua, proxy_addr)
                print(
                    f"          {Fore.LIGHTBLACK_EX}└─ "
                    f"{Fore.WHITE}{detail}{Style.RESET_ALL}"
                )

        except requests.exceptions.ProxyError:
            _log_attempt("❌", Fore.RED, "Proxy refused connection", browser, os_name, ua, proxy_addr)
        except requests.exceptions.ConnectTimeout:
            _log_attempt("⏱️", Fore.RED, "Proxy timed out", browser, os_name, ua, proxy_addr)
        except requests.exceptions.ConnectionError:
            _log_attempt("❌", Fore.RED, "Connection error", browser, os_name, ua, proxy_addr)
        except requests.exceptions.RequestException as exc:
            _log_attempt("❌", Fore.RED, type(exc).__name__, browser, os_name, ua, proxy_addr)

        _remove_proxy_from_file(proxy_addr)

# ---------------------------------------------------------------------------
# Run bypass
# ---------------------------------------------------------------------------

def run_bypass() -> None:
    proxies = load_proxies(PROXY_FILE)

    if not proxies:
        print(
            f"{timestamp()} ⚠️  {Fore.YELLOW}No valid proxies found — "
            f"use option {Fore.CYAN}[1]{Fore.YELLOW} to fetch some."
        )
        return

    label = "proxy" if len(proxies) == 1 else "proxies"
    print(f"{timestamp()} {Fore.WHITE}Loaded {Fore.CYAN}{len(proxies):,}{Fore.WHITE} valid {label}\n")

    url         = input(f"{Fore.WHITE}  DC verify URL  {Fore.CYAN}» {Style.RESET_ALL}")
    num_threads = int(input(f"{Fore.WHITE}  Threads        {Fore.CYAN}» {Style.RESET_ALL}"))

    chunks = split_evenly(proxies, num_threads)
    print(f"\n{timestamp()} {Fore.WHITE}Spawning {Fore.CYAN}{num_threads}{Fore.WHITE} threads...\n")

    threads = [threading.Thread(target=_bypass_worker, args=(url, chunk)) for chunk in chunks]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    print(f"\n{timestamp()} {Fore.WHITE}Done. Total attempts: {Fore.CYAN}{attempt_count}")

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    banner()

    while True:
        choice = menu()

        if choice == "1":
            run_bypass()
            break

        elif choice == "2":
            check_proxies()

        elif choice == "3":
            fetch_proxies()

        elif choice == "4":
            print(f"{timestamp()} {Fore.CYAN}Bye.\n")
            break
