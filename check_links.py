#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Skript pro kontrolu nefunkčních interních odkazů na webu.
Spouští se z příkazového řádku s URL sitemapy jako argumentem.

Pokud nalezne nefunkční odkazy, vypíše je, uloží do 'broken_links_report.md'
s kontextem (kde byl odkaz nalezen) a skončí s chybovým kódem 1.
"""

import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import concurrent.futures
import time
import threading
import sys
import collections

# --- Hlavní nastavení ---
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}
MAX_WORKERS = 10
LINK_TIMEOUT = 7

# --- Cache pro již zkontrolované odkazy ---
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
            print(f"  -> 🚨 Chyba: Samotná stránka '{page_url}' je nefunkční (Status: {response.status_code})")
            return [(page_url, response.status_code, "BROKEN (Page from sitemap)")]
            
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
                    
                    if parsed_link.scheme not in ('http', 'https') or hostname != BASE_DOMAIN:
                        continue

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
    
    # *** ZMĚNA ZDE ***
    # Místo sady (set) použijeme slovník (dictionary),
    # kde klíč je nefunkční URL a hodnota je sada (set) stránek, kde byl nalezen.
    # Použijeme defaultdict pro snadnější přidávání.
    all_broken_links_map = collections.defaultdict(set)
    
    if page_urls:
        for i, page_url in enumerate(page_urls):
            print(f"\n🔎 Kontroluji stránku ({i+1}/{len(page_urls)}): {page_url}")
            
            broken_links = check_page_links(page_url)
            
            if broken_links:
                print(f"  🚨 Nalezeny nefunkční odkazy:")
                for url, status, msg in broken_links:
                    print(f"     -> {url} (Status: {status}, Důvod: {msg})")
                    # *** ZMĚNA ZDE ***
                    # Uložíme si, že 'url' (nefunkční) byla nalezena na 'page_url' (aktuální stránka)
                    all_broken_links_map[url].add(page_url)
            else:
                print("  ✅ Všechny interní odkazy se zdají být v pořádku.")
                
        end_time = time.time()
        print("\n" + "="*40)
        print("--- 🏁 KONTROLA DOKONČENA (SOUHRN) ---")
        print(f"Celkem zkontrolováno stránek: {len(page_urls)}")
        print(f"Celkem unikátních interních odkazů zkontrolováno (v cache): {len(link_cache)}")
        print(f"Celkový čas: {end_time - start_time:.2f} sekund")
        
        print("\n" + "="*40)
        
        # *** ZMĚNA ZDE ***
        # Změnili jsme logiku reportování, aby používala novou mapu
        if all_broken_links_map:
            print(f"🚨🚨🚨 NALEZENY CHYBY 🚨🚨🚨")
            print(f"Celkem nalezeno unikátních nefunkčních interních odkazů: {len(all_broken_links_map)}")
            print("--- Seznam všech unikátních nefunkčních odkazů a jejich zdrojů ---")

            try:
                with open("broken_links_report.md", "w", encoding="utf-8") as f:
                    f.write(f"# 🚨 Nalezeny nefunkční odkazy ({len(all_broken_links_map)})\n\n")
                    f.write("Během automatické kontroly webu byly nalezeny následující nefunkční interní odkazy:\n\n")
                    
                    # Seřadíme podle nefunkčního odkazu
                    for broken_url, pages in sorted(all_broken_links_map.items()):
                        print(f"\n-> NEFUNKČNÍ ODKAZ: {broken_url}")
                        # Použijeme Markdown nadpis pro přehlednost v Issue
                        f.write(f"## ❌ `{broken_url}`\n\n") 
                        f.write("**Nalezeno na těchto stránkách:**\n")
                        print("   Nalezeno na:")
                        
                        for page in sorted(list(pages)):
                            print(f"   - {page}")
                            f.write(f"- {page}\n")
                        f.write("\n") # Přidá mezeru před dalším odkazem
                            
                print("\nℹ️ Report o chybách byl uložen do souboru broken_links_report.md")
            except Exception as e:
                print(f"Chyba při zápisu reportu do souboru: {e}", file=sys.stderr)
            
            print("="*40)
            sys.exit(1) # Vracíme chybový kód
        else:
            print("🎉🎉🎉 VÝBORNĚ! 🎉🎉🎉")
            print("Žádné unikátní nefunkční interní odkazy nebyly nalezeny.")
            print("="*40)
            sys.exit(0) # Vracíme kód 0 (úspěch)
    else:
        print("Nebyla nalezena žádná URL v sitemapě. Kontrola končí.", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Chyba: Musíte zadat URL sitemapy jako argument.", file=sys.stderr)
        print("Příklad: python check_links.py https://web.cz/sitemap.xml", file=sys.stderr)
        sys.exit(1)
    
    main(sitemap_url=sys.argv[1])
