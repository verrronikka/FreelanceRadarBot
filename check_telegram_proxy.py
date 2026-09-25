import requests

PROXIES = [
    ("http", "http://127.0.0.1:10809"),
    ("socks5", "socks5://127.0.0.1:10808"),
]

TARGET = "https://api.telegram.org"


def check_proxy(protocol: str, proxy_url: str) -> bool:
    """Пытается достучаться до api.telegram.org через указанный прокси."""
    proxies = {"http": proxy_url, "https": proxy_url}
    try:
        resp = requests.get(TARGET, proxies=proxies, timeout=5)
        return resp.status_code < 500
    except Exception:
        return False


def main() -> None:
    for protocol, proxy_url in PROXIES:
        port = proxy_url.split(":")[-1]
        if check_proxy(protocol, proxy_url):
            print(f"Протокол {protocol}, порт {port} работает")
        else:
            print(f"Протокол {protocol}, порт {port} не работает")


if __name__ == "__main__":
    main()
