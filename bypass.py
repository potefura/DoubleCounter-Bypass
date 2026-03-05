import requests
import threading
import os
import random
import fake_headers
import colorama
from datetime import datetime
from colorama import Fore, Style

colorama.init(autoreset=True)

def ts():
    return f"{Fore.LIGHTBLACK_EX}[{datetime.now().strftime('%H:%M:%S')}]{Style.RESET_ALL}"

def read_proxy_list(filename):
    valid, skipped = [], 0
    with open(filename, "r") as f:
        for line in f:
            raw = line.strip()
            if not raw:
                continue
            try:
                host, port_str = raw.rsplit(":", 1)
            except ValueError:
                skipped += 1
                continue

            if not port_str.isdigit() or not (1 <= int(port_str) <= 65535):
                skipped += 1
                continue

            octets = host.split(".")
            if len(octets) != 4 or not all(o.isdigit() and 0 <= int(o) <= 255 for o in octets):
                skipped += 1
                continue

            valid.append({"https": "http://" + raw})

    if skipped:
        print(f"{ts()} {Fore.YELLOW}Removed {Fore.CYAN}{skipped}{Fore.YELLOW} invalid {'proxy' if skipped == 1 else 'proxies'} from {filename} — expected format: ip:port")
        with open(filename, "w") as f:
            for entry in valid:
                f.write(entry["https"].split("//")[1] + "\n")

    return valid

def split_list(lst, num_chunks):
    avg = len(lst) / float(num_chunks)
    return [lst[int(i * avg):int((i + 1) * avg)] for i in range(num_chunks)]

BROWSERS = ["chrome", "firefox", "opera"]
OS_MAP = {"win": "Windows", "mac": "macOS", "linux": "Linux"}

attempt_count = 0
attempt_lock = threading.Lock()

def banner():
    print(f"""
{Fore.CYAN}╔════════════════════════════════════════╗
║   {Fore.WHITE}Double Counter Verification Bypass{Fore.CYAN}   ║
╚════════════════════════════════════════╝{Style.RESET_ALL}""")

def build_headers():
    browser = random.choice(BROWSERS)
    os_key = random.choice(list(OS_MAP.keys()))
    headers = fake_headers.Headers(browser=browser, os=os_key, headers=True).generate()
    ua = headers.get("User-Agent", "Unknown")
    return headers, browser.capitalize(), OS_MAP[os_key], ua

def log(icon, color, msg, browser, os_name, ua, proxy):
    short_ua = ua if len(ua) <= 55 else ua[:52] + "..."
    print(
        f"{ts()} {icon}  {color}{msg}{Style.RESET_ALL}"
        f" | {Fore.LIGHTBLACK_EX}{proxy}{Style.RESET_ALL}"
        f" | {Fore.YELLOW}{os_name}{Style.RESET_ALL}"
        f" | {Fore.CYAN}{browser}{Style.RESET_ALL}"
        f" | {Fore.LIGHTBLUE_EX}{short_ua}"
    )

def remove_proxy(proxy_str):
    threadLock.acquire()
    try:
        with open("proxies.txt", "r") as f:
            lines = f.readlines()
        with open("proxies.txt", "w") as f:
            for line in lines:
                if line.strip("\n") != proxy_str:
                    f.write(line)
    finally:
        threadLock.release()

def double_counter_request(url, proxies_chunk):
    global attempt_count

    for proxy_info in proxies_chunk:
        proxy = {"https": proxy_info["https"]}
        usingProxy = proxy_info["https"].split("//")[1]

        headers, browser, os_name, ua = build_headers()

        with attempt_lock:
            attempt_count += 1

        try:
            r = requests.get(url, headers=headers, proxies=proxy, timeout=10)

            if r.status_code == 200 and "Success!" in r.text:
                log("✅", Fore.GREEN, "Bypass successful", browser, os_name, ua, usingProxy)
                remove_proxy(usingProxy)
                os._exit(0)

            elif "Expired link" in r.text:
                log("⚠️", Fore.YELLOW, "Link expired — get a fresh URL", browser, os_name, ua, usingProxy)
                remove_proxy(usingProxy)
                os._exit(0)

            elif "RR02" in r.text:
                log("🚫", Fore.YELLOW, "Alt account flagged by DC", browser, os_name, ua, usingProxy)
                remove_proxy(usingProxy)
                os._exit(0)

            elif "RV01" in r.text:
                log("🔒", Fore.YELLOW, "Proxy flagged by DC", browser, os_name, ua, usingProxy)

            else:
                log("☁️", Fore.MAGENTA, f"Cloudflare block [HTTP {r.status_code}]", browser, os_name, ua, usingProxy)

        except requests.exceptions.ProxyError:
            log("❌", Fore.RED, "Proxy refused connection", browser, os_name, ua, usingProxy)
        except requests.exceptions.ConnectTimeout:
            log("⏱️", Fore.RED, "Proxy timed out", browser, os_name, ua, usingProxy)
        except requests.exceptions.ConnectionError:
            log("❌", Fore.RED, "Connection error", browser, os_name, ua, usingProxy)
        except requests.exceptions.RequestException as e:
            log("❌", Fore.RED, type(e).__name__, browser, os_name, ua, usingProxy)

        remove_proxy(usingProxy)

if __name__ == "__main__":
    banner()

    proxies_list = read_proxy_list("proxies.txt")

    if not proxies_list:
        print(f"{ts()} ⚠️  {Fore.YELLOW}No valid proxies found — add proxies to proxies.txt and try again.")
        exit(1)

    print(f"{ts()} {Fore.WHITE}Loaded {Fore.CYAN}{len(proxies_list)}{Fore.WHITE} valid {'proxy' if len(proxies_list) == 1 else 'proxies'}\n")

    url = input(f"{Fore.WHITE}  DC verify URL  {Fore.CYAN}» {Style.RESET_ALL}")
    num_threads = int(input(f"{Fore.WHITE}  Threads        {Fore.CYAN}» {Style.RESET_ALL}"))

    proxies_chunks = split_list(proxies_list, num_threads)
    print(f"\n{ts()} {Fore.WHITE}Spawning {Fore.CYAN}{num_threads}{Fore.WHITE} threads — starting now...\n")

    threadLock = threading.Lock()
    threads = [threading.Thread(target=double_counter_request, args=(url, chunk)) for chunk in proxies_chunks]

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    print(f"\n{ts()} {Fore.WHITE}Done. Total attempts: {Fore.CYAN}{attempt_count}")
