#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Skript pro kontrolu nefunkčních interních odkazů na webu.
Spouští se z příkazového řádku s URL sitemapy jako argumentem.
Pokud nalezne nefunkční odkazy, vypíše je a skončí s chybovým kódem 1.
"""

import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import concurrent.futures
import time
import threading
import sys
import os

# --- Hlavní nastavení ---
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/5.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/5.36'
}
# Počet souběžných vláken
MAX_WORKERS = 10
# Timeout pro jednotlivé dotazy
LINK_TIMEOUT = 7

# --- Cache pro již zkontrolované odkazy ---
# Ukládá: { 'url': (status, message) }
link_cache = {}
cache_lock = threading.Lock()

# Globální proměnná pro základní doménu webu
BASE_DOMAIN = ""

def get_sitemap_urls(sitemap_url):
    """Načte URL sitemapy a vrátí seznam URL stránek."""
    urls = []
    print(f"ℹ️ Načítám sitemapu z: {sitemap_url}")
    try:
        response = requests.get(sitemap_url, headers=HEADERS)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'lxml-xml')
        for loc in soup.find_all('loc'):
            urls.append(loc.text)
        print(f"✅ Nalezeno {len(urls)} URL v sitemapě.")
        return urls
    except requests.exceptions.RequestException as e:
        print(f"❌ Chyba při načítání sitemapy: {e}", file=sys.stderr)
        return []

def check_link(url):
    """
    Zkontroluje stav jednoho odkazu pomocí metody GET (maskování).
    """
    status_code = 0
    message = "OK"
    
    if url.startswith(('mailto:', 'tel:', 'javascript:')) or url.startswith('#'):
        return (url, 0, "SKIPPED")
        
    try:
        response = requests.get(
            url, 
            headers=HEADERS, 
            timeout=LINK_TIMEOUT, 
            allow_redirects=True,
            stream=True 
        )
        status_code = response.status_code
        if status_code >= 400:
            message = "BROKEN"
    except requests.exceptions.Timeout:
        status_code = -1
        message = "ERROR (Timeout)"
    except requests.exceptions.ConnectionError:
        status_code = -2
        message = "ERROR (Connection)"
    except requests.exceptions.RequestException:
        status_code = -3
        message = "ERROR (Jiná chyba)"
    
    return (url, status_code, message)

def check_page_links(page_url):
    """
    Najde všechny INTERNÍ odkazy na stránce a vrátí seznam nefunkčních.
    """
    broken_links_on_page = []
    
    try:
        response = requests.get(page_url, headers=HEADERS, timeout=10)
        if response.status_code >= 400:
            print(f"  -> ⚠️ Varování: Samotná stránka '{page_url}' je nefunkční (Status: {response.status_code}), přeskočeno.")
            return []
            
        soup = BeautifulSoup(response.content, 'html.parser')
        links_on_page = set()
        
        for a_tag in soup.find_all('a', href=True):
            href = a_tag['href']
            absolute_url = urljoin(page_url, href)
            absolute_url = urlparse(absolute_url)._replace(fragment="").geturl()
            links_on_page.add(absolute_url)

        if not links_on_page:
            return []

        links_for_executor = []
        
        with cache_lock:
            for url in links_on_page:
                try:
                    parsed_link = urlparse(url)
                    hostname = parsed_link.hostname or ""
                    
                    # --- FILTROVÁNÍ: POUZE INTERNÍ ---
                    # Přeskočíme vše, co není http/https nebo není na naší doméně
                    if parsed_link.scheme not in ('http', 'https') or hostname != BASE_DOMAIN:
                        continue
                    # --- Konec filtru ---

                    if url in link_cache:
                        status, message = link_cache[url]
                        if message not in ("OK", "SKIPPED"):
                            broken_links_on_page.append((url, status, message))
                    else:
                        links_for_executor.append(url)
                
                except Exception as e:
                    print(f"  -> ! Chyba při parsování URL: {url} ({e})")
        
        if links_for_executor:
            with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                futures = [executor.submit(check_link, url) for url in links_for_executor]
                
                for future in concurrent.futures.as_completed(futures):
                    url, status, message = future.result()
                    
                    with cache_lock:
                        link_cache[url] = (status, message)
                        
                    if message not in ("OK", "SKIPPED"):
                        broken_links_on_page.append((url, status, message))

    except requests.exceptions.RequestException as e:
        print(f"  -> ❌ Chyba při načítání stránky {page_url}: {e}", file=sys.stderr)
    
    return broken_links_on_page

def main(sitemap_url):
    """Hlavní funkce skriptu."""
    global BASE_DOMAIN
    
    try:
        BASE_DOMAIN = urlparse(sitemap_url).hostname
        if not BASE_DOMAIN:
             raise ValueError("Nelze extrahovat doménu z URL sitemapy.")
        print(f"ℹ️ Kontrola interních odkazů pro doménu: {BASE_DOMAIN}")
            
    except ValueError as e:
        print(f"❌ Kritická chyba: {e}. Skript nemůže pokračovat.", file=sys.stderr)
        sys.exit(1)
        
    start_time = time.time()
    page_urls = get_sitemap_urls(sitemap_url)
    
    all_broken_links_set = set()
    
    if page_urls:
        for i, page_url in enumerate(page_urls):
            print(f"\n🔎 Kontroluji stránku ({i+1}/{len(page_urls)}): {page_url}")
            
            broken_links = check_page_links(page_url)
            
            if broken_links:
                print(f"  🚨 Nalezeny nefunkční odkazy:")
                for url, status, msg in broken_links:
                    print(f"     -> {url} (Status: {status}, Důvod: {msg})")
                    all_broken_links_set.add(url)
            else:
                print("  ✅ Všechny interní odkazy se zdají být v pořádku.")
                
        end_time = time.time()
        print("\n" + "="*40)
        print("--- 🏁 KONTROLA DOKONČENA (SOUHRN) ---")
        print(f"Celkem zkontrolováno stránek: {len(page_urls)}")
        print(f"Celkem unikátních interních odkazů zkontrolováno (v cache): {len(link_cache)}")
        print(f"Celkový čas: {end_time - start_time:.2f} sekund")
        
        print("\n" + "="*40)
        
        if all_broken_links_set:
            print(f"🚨🚨🚨 NALEZENY CHYBY 🚨🚨🚨")
            print(f"Celkem nalezeno unikátních nefunkčních interních odkazů: {len(all_broken_links_set)}")
            print("--- Seznam všech unikátních nefunkčních odkazů ---")
            for broken_url in sorted(list(all_broken_links_set)):
                print(f"-> {broken_url}")
            print("="*40)
            # Vracíme chybový kód, aby GitHub Action selhala
            sys.exit(1)
        else:
            print("🎉🎉🎉 VÝBORNĚ! 🎉🎉🎉")
            print("Žádné unikátní nefunkční interní odkazy nebyly nalezeny.")
            print("="*40)
            # Vracíme kód 0 (úspěch)
            sys.exit(0)
    else:
        print("Nebyla nalezena žádná URL v sitemapě. Kontrola končí.", file=sys.stderr)
        sys.exit(1) # Selhání, pokud se nenačte sitemapa

if __name__ == "__main__":
    # Přečteme URL sitemapy z prvního argumentu
    if len(sys.argv) < 2:
        print("Chyba: Musíte zadat URL sitemapy jako argument.", file=sys.stderr)
        print("Příklad: python check_links.py https://web.cz/sitemap.xml", file=sys.stderr)
        sys.exit(1)
    
    main(sitemap_url=sys.argv[1])
