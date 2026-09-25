import requests

PORTS = [1080, 7890, 8080, 3128]
PROTOCOLS = ["socks5", "http"]
TARGET = "https://api.telegram.org"


def check_port(port: int) -> bool:
    """Пытается достучаться до api.telegram.org через прокси на указанном порту."""
    for proto in PROTOCOLS:
        proxy_url = f"{proto}://127.0.0.1:{port}"
        proxies = {"http": proxy_url, "https": proxy_url}
        try:
            resp = requests.get(TARGET, proxies=proxies, timeout=5)
            if resp.status_code < 500:
                return True
        except Exception:
            continue
    return False


def main() -> None:
    for port in PORTS:
        if check_port(port):
            print(f"Порт {port} работает!")
        else:
            print("Не работает")


if __name__ == "__main__":
    main()
