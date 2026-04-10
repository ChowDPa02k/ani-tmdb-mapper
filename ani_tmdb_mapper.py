#!/usr/bin/env python3
"""
ANi RSS → TMDB Season/Episode Mapper v3

Features:
1. Fetch ANi RSS feed, parse anime titles and episode numbers
2. Search TMDB for TV show matches (skip exact movie matches)
3. Cache ANi directory structure in SQLite (only refresh recent 4 quarters)
4. Load confirmed mappings from confirmed.json, skip already-mapped titles
5. Output unmapped items for LLM inference
6. Generate mapping.json as final output

Usage:
  python ani_tmdb_mapper.py
  python ani_tmdb_mapper.py --dry-run
  python ani_tmdb_mapper.py --refresh-cache   # Force refresh all ANi cache

Config: .env file with TMDB_API_KEY and optional HTTP_PROXY
"""

import re
import sys
import os
import json
import time
import sqlite3
import argparse
import xml.etree.ElementTree as ET
from urllib.request import urlopen, Request, ProxyHandler, build_opener, install_opener
from urllib.parse import quote
from urllib.error import HTTPError, URLError
from collections import defaultdict, OrderedDict
from datetime import datetime
from pathlib import Path

# ============================================================
# .env loader
# ============================================================
def load_dotenv():
    """Load .env file from script directory"""
    script_dir = Path(__file__).parent
    env_path = script_dir / ".env"
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip("'\"")
                if key and key not in os.environ:
                    os.environ[key] = value

load_dotenv()

# ============================================================
# Configuration
# ============================================================
SCRIPT_DIR = Path(__file__).parent
ANI_RSS_URL = "https://api.ani.rip/ani-download.xml"
ANI_OPEN_BASE = "https://openani.an-i.workers.dev"
TMDB_BASE = "https://api.themoviedb.org/3"
TMDB_LANG = "zh-TW"
RATE_LIMIT_DELAY = 0.35

ANI_OPEN_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "Content-Type": "text/plain",
    "Origin": "https://openani.an-i.workers.dev",
    "Referer": "https://openani.an-i.workers.dev/",
}

# Global opener (proxy support)
_opener = None

def setup_proxy(proxy_url):
    global _opener
    if proxy_url:
        proxy_handler = ProxyHandler({
            'http': proxy_url,
            'https': proxy_url,
        })
        _opener = build_opener(proxy_handler)
        install_opener(_opener)
        print(f"🌐 Proxy: {proxy_url}")
    else:
        _opener = None
        install_opener(build_opener())

def _urlopen(req, **kwargs):
    return urlopen(req, **kwargs)

# ANi title regex
ANI_TITLE_RE = re.compile(r'^\[ANi\]\s*(.+?)\s*-\s*(\d+(?:\.\d+)?)\s+\[1080P\]')

# Chinese number mapping
CN_NUM = {'一':1,'二':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10,
          '零':0,'壹':1,'貳':2,'參':3,'肆':4,'伍':5,'陸':6,'柒':7,'捌':8,'玖':9,'拾':10}

def cn_to_int(s):
    if s.isdigit(): return int(s)
    if s in CN_NUM: return CN_NUM[s]
    if '十' in s:
        s = s.replace('十','')
        return 10 + (CN_NUM.get(s, 0) if s else 0)
    return 1


# ============================================================
# ANi Directory Cache (SQLite)
# ============================================================
class AniDirectoryCache:
    """
    Caches ANi's complete directory structure in SQLite.
    Only refreshes the most recent 4 quarter-folders; older ones are kept as-is.
    """

    SEASON_DIR_RE = re.compile(r'^\d{4}-\d{1,2}$')

    def __init__(self, db_path=None):
        if db_path is None:
            db_path = str(SCRIPT_DIR / "ani_directory_cache.db")
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_tables()

    def _init_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS directories (
                path TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)
        self.conn.commit()

    def get_root_dirs(self):
        """Get cached root dirs or fetch fresh"""
        row = self.conn.execute(
            "SELECT value FROM metadata WHERE key = 'root_dirs'"
        ).fetchone()
        if row:
            return json.loads(row["value"])
        return self._refresh_root_dirs()

    def _refresh_root_dirs(self):
        """Fetch root directory listing from ANi"""
        print("  📂 Fetching ANi root directory...")
        files = self._fetch_dir_listing("/")
        dirs = [
            f for f in files
            if f.get("mimeType") == "application/vnd.google-apps.folder"
            and self.SEASON_DIR_RE.match(f.get("name", ""))
        ]
        # Sort by name descending (newest first)
        dirs.sort(key=lambda x: x.get("name", ""), reverse=True)
        self.conn.execute(
            "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
            ("root_dirs", json.dumps(dirs, ensure_ascii=False))
        )
        self.conn.commit()
        return dirs

    def get_directory(self, path):
        """Get directory listing from cache or fetch"""
        row = self.conn.execute(
            "SELECT data FROM directories WHERE path = ?", (path,)
        ).fetchone()
        if row:
            return json.loads(row["data"])
        return self._fetch_and_cache(path)

    def _fetch_and_cache(self, path):
        """Fetch a directory and cache it"""
        files = self._fetch_dir_listing(path)
        self.conn.execute(
            "INSERT OR REPLACE INTO directories (path, data, updated_at) VALUES (?, ?, ?)",
            (path, json.dumps(files, ensure_ascii=False), datetime.now().isoformat())
        )
        self.conn.commit()
        return files

    def _fetch_dir_listing(self, path):
        """Raw HTTP fetch of a directory listing"""
        url = ANI_OPEN_BASE + path
        body = json.dumps({"password": "null"}).encode("utf-8")
        req = Request(url, data=body, headers=ANI_OPEN_HEADERS)
        try:
            with _urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            return result.get("files", [])
        except Exception as e:
            print(f"    ⚠️ Failed to list {path}: {e}")
            return []

    def refresh_recent(self, count=4):
        """
        Refresh only the most recent `count` quarter-folders.
        Older ones remain cached.
        Returns total number of directories refreshed.
        """
        root_dirs = self.get_root_dirs()
        to_refresh = root_dirs[:count]
        refreshed = 0

        for d in to_refresh:
            path = "/" + d["name"] + "/"
            old = self.conn.execute(
                "SELECT updated_at FROM directories WHERE path = ?", (path,)
            ).fetchone()

            print(f"  🔄 Refreshing {d['name']}...", end="", flush=True)
            self._fetch_and_cache(path)
            refreshed += 1

            # Also refresh subdirectories (anime folders inside quarter)
            sub_files = self.get_directory(path)
            sub_dirs = [
                f for f in sub_files
                if f.get("mimeType") == "application/vnd.google-apps.folder"
            ]
            for sd in sub_dirs:
                sub_path = path + quote(sd["name"]) + "/"
                self._fetch_and_cache(sub_path)
                time.sleep(0.3)  # Polite delay

            print(f" ({len(sub_dirs)} subdirs)")
            time.sleep(0.3)

        return refreshed

    def force_refresh_all(self):
        """Clear cache and rebuild from scratch"""
        self.conn.execute("DELETE FROM directories")
        self.conn.execute("DELETE FROM metadata")
        self.conn.commit()
        print("  🗑️ Cache cleared")
        self._refresh_root_dirs()
        return self.refresh_recent(count=999)

    def search_anime_in_cache(self, query):
        """
        Search for anime across all cached directories.
        Returns: [(folder_name, path)] tuples
        """
        root_dirs = self.get_root_dirs()
        query_lower = query.lower()
        results = []

        for d in root_dirs:
            path = "/" + d["name"] + "/"
            files = self.get_directory(path)
            has_folders = any(
                f.get("mimeType") == "application/vnd.google-apps.folder"
                for f in files
            )

            if has_folders:
                for f in files:
                    if f.get("mimeType") != "application/vnd.google-apps.folder":
                        continue
                    name = f.get("name", "")
                    name_lower = name.lower()
                    if query_lower in name_lower or self._fuzzy_match(query_lower, name_lower):
                        full_path = path + quote(name) + "/"
                        results.append((name, full_path))
            else:
                # Loose files (current season)
                matches = [
                    f for f in files
                    if f.get("mimeType") != "application/vnd.google-apps.folder"
                    and (query_lower in f.get("name", "").lower()
                         or self._fuzzy_match(query_lower, f.get("name", "").lower()))
                ]
                if matches:
                    m0 = re.match(r'\[ANi\]\s*(.+?)\s+-\s+\d', matches[0].get("name", ""))
                    clean_name = m0.group(1).strip() if m0 else query
                    results.append((clean_name, "__loose__:" + path, matches))

        # Deduplicate
        seen = set()
        unique = []
        for item in results:
            key = item[0] + "@" + (item[1] if len(item) == 2 else item[1])
            if key not in seen:
                seen.add(key)
                unique.append(item)
        return unique

    def get_episode_range_from_cache(self, query):
        """
        Get episode ranges for an anime from cache.
        Returns: {folder_name: (min_ep, max_ep, file_count)}
        """
        folders = self.search_anime_in_cache(query)
        if not folders:
            return None

        merged = {}
        for item in folders:
            name = item[0]
            if len(item) == 3:
                files = item[2]
            else:
                _, path = item
                files = self.get_directory(path)
                files = [f for f in files if f.get("mimeType") != "application/vnd.google-apps.folder"]

            ep_range = self._extract_episode_range(files)
            if ep_range:
                if name in merged:
                    old = merged[name]
                    merged[name] = (
                        min(old[0], ep_range[0]),
                        max(old[1], ep_range[1]),
                        old[2] + ep_range[2]
                    )
                else:
                    merged[name] = ep_range
        return merged

    @staticmethod
    def _fuzzy_match(query, target):
        clean_target = re.sub(r'\[.*?\]', '', target).strip()
        clean_query = re.sub(r'\s+', '', query)
        if len(clean_query) < 2:
            return clean_query in clean_target
        match_count = sum(1 for c in clean_query if c in clean_target)
        return match_count / len(clean_query) >= 0.6

    @staticmethod
    def _extract_episode_range(files):
        eps = set()
        for f in files:
            name = f.get("name", "")
            m = re.search(r'\s+-\s+(\d+(?:\.\d+)?)\s+\[', name)
            if m:
                try:
                    ep = float(m.group(1))
                    if ep <= 200:
                        eps.add(ep)
                except ValueError:
                    pass
                continue
            for val in re.findall(r'\[(\d+(?:\.\d+)?)\]', name):
                try:
                    ep = float(val)
                    if 0 < ep <= 200:
                        eps.add(ep)
                except ValueError:
                    pass
        if not eps:
            return None
        eps = sorted(eps)
        return (eps[0], eps[-1], len(eps))


# ============================================================
# TMDB Client
# ============================================================
class TMDBClient:
    def __init__(self, api_key):
        self.api_key = api_key
        self.cache = {}

    def _get(self, path, params=None):
        url = f"{TMDB_BASE}{path}?api_key={self.api_key}&language={TMDB_LANG}"
        if params:
            for k, v in params.items():
                url += f"&{k}={quote(str(v), safe='')}"
        req = Request(url, headers={"User-Agent": "ANI-TMDB-Mapper/3.0"})
        try:
            with _urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode('utf-8'))
        except HTTPError as e:
            if e.code == 429:
                print("  ⏳ TMDB rate-limited, waiting 2s...")
                time.sleep(2)
                return self._get(path, params)
            print(f"  [HTTP {e.code}] TMDB error: {path}")
            return {}
        except Exception as e:
            print(f"  [Error] {e}")
            return {}

    def search_tv(self, query):
        data = self._get("/search/tv", {"query": query})
        return data.get("results", [])

    def search_movie(self, query):
        """Search TMDB movies - used to skip movies"""
        data = self._get("/search/movie", {"query": query})
        return data.get("results", [])

    def get_tv_details(self, tv_id):
        return self._get(f"/tv/{tv_id}")

    def get_season_details(self, tv_id, season_num):
        return self._get(f"/tv/{tv_id}/season/{season_num}")

    def is_exact_movie_match(self, title):
        """
        Check if the title exactly matches a TMDB movie.
        Returns True if it's a movie (should be skipped).
        """
        query = self._clean_for_search(title)
        if not query:
            return False

        results = self.search_movie(query)
        q = query.lower().strip()

        for r in results:
            name = (r.get("title") or "").lower().strip()
            orig = (r.get("original_title") or "").lower().strip()
            if name == q or orig == q:
                print(f"    🎬 Exact movie match: {r.get('title')} — skipping")
                return True
        return False

    def search_and_match(self, ani_title, base_title):
        """
        Search TMDB and return best TV match.
        Returns {"tmdb_id", "name", "seasons": [...]} or None
        """
        cache_key = base_title
        if cache_key in self.cache:
            return self.cache[cache_key]

        query = self._clean_for_search(base_title)
        if not query:
            self.cache[cache_key] = None
            return None

        print(f"  🔍 Searching: '{query}'")
        results = self.search_tv(query)

        if not results:
            stripped = self._strip_season(query)
            if stripped and stripped != query:
                time.sleep(RATE_LIMIT_DELAY)
                print(f"    Retry: '{stripped}'")
                results = self.search_tv(stripped)

        if not results:
            print(f"    ❌ Not found")
            self.cache[cache_key] = None
            return None

        best = self._pick_best(ani_title, query, results)
        if not best:
            self.cache[cache_key] = None
            return None

        tv_id = best["id"]
        tmdb_name = best.get("name", "?")
        print(f"    ✅ Match: {tmdb_name} (ID:{tv_id})")

        time.sleep(RATE_LIMIT_DELAY)
        details = self.get_tv_details(tv_id)
        if not details:
            self.cache[cache_key] = None
            return None

        seasons = []
        for s in details.get("seasons", []):
            seasons.append({
                "sn": s["season_number"],
                "name": s.get("name", ""),
                "ep_count": s.get("episode_count", 0),
            })

        info = {
            "tmdb_id": tv_id,
            "name": tmdb_name,
            "original_name": details.get("original_name", ""),
            "first_air_date": details.get("first_air_date", ""),
            "total_seasons": details.get("number_of_seasons", 0),
            "total_episodes": details.get("number_of_episodes", 0),
            "seasons": seasons,
        }
        self.cache[cache_key] = info
        return info

    def _clean_for_search(self, title):
        t = re.sub(r'\[.*?\]', '', title).strip()
        t = re.sub(r'(中文配音|年齡限制版)', '', t).strip()
        t = re.sub(r'\s*(第[一二三四五六七八九十\d]+季|Season\s*\d+|\d+(?:nd|rd|th)?\s+Season)\s*$', '', t).strip()
        return t

    def _strip_season(self, title):
        t = title
        for pat in [r'\s*第[一二三四五六七八九十\d]+季\s*',
                     r'\s*Season\s*\d+\s*$',
                     r'\s*\d+(?:nd|rd|th)?\s+Season\s*$',
                     r'\s*第[一二三四五六七八九十]+章\s*$',
                     r'\s*參之章\s*$']:
            new = re.sub(pat, ' ', t).strip()
            if new and new != t:
                return new
        return None

    def _pick_best(self, ani_title, query, results):
        if not results:
            return None
        q = query.lower().strip()
        # Exact match
        for r in results:
            if (r.get("name") or "").lower().strip() == q:
                return r
            if (r.get("original_name") or "").lower().strip() == q:
                return r
        # Containment match
        for r in results:
            n = (r.get("name") or "").lower().strip()
            o = (r.get("original_name") or "").lower().strip()
            if q in n or n in q or q in o or o in q:
                return r
        # Most votes
        sorted_r = sorted(results, key=lambda x: x.get("vote_count", 0), reverse=True)
        return sorted_r[0]


# ============================================================
# Confirmed Mappings Manager
# ============================================================
class ConfirmedMappingManager:
    """
    Manages confirmed.json — a record of titles that have been
    verified by human/LLM review and should be skipped in future runs.
    
    Design: Keys in 'mappings' are exact ANi parsed titles (the part
    between [ANi] and the episode number). Matching is EXACT only —
    no substring/fuzzy matching. This prevents S1 confirmed entries
    from matching future S2 titles (e.g., "非人學生與厭世教師" will
    NOT match "非人學生與厭世教師 第二季").
    """

    def __init__(self, path=None):
        if path is None:
            path = str(SCRIPT_DIR / "confirmed.json")
        self.path = path
        self.data = self._load()

    def _load(self):
        if os.path.exists(self.path):
            with open(self.path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {"mappings": {}}

    def save(self):
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
        print(f"  💾 Confirmed mappings saved: {self.path}")

    def is_confirmed(self, full_title):
        """Exact match only. No substring/fuzzy matching."""
        mappings = self.data.get("mappings", {})
        return full_title in mappings and not full_title.startswith("_")

    def get_mapping_for(self, full_title):
        """Get mapping by exact title match. Returns dict or None."""
        if full_title.startswith("_"):
            return None
        return self.data.get("mappings", {}).get(full_title)

    def add_mapping(self, full_title, mapping_info):
        """Add a confirmed mapping. mapping_info: {tmdb_season, episode_offset?, _note?}"""
        self.data.setdefault("mappings", {})[full_title] = mapping_info
        self.save()


# ============================================================
# ANi RSS Parsing
# ============================================================
def fetch_ani_titles():
    print(f"📡 Fetching ANi RSS: {ANI_RSS_URL}")
    req = Request(ANI_RSS_URL, headers={"User-Agent": "ANI-TMDB-Mapper/3.0"})
    with _urlopen(req, timeout=30) as resp:
        xml_data = resp.read().decode('utf-8')
    root = ET.fromstring(xml_data)
    titles = []
    for item in root.findall('.//item'):
        el = item.find('title')
        if el is not None and el.text:
            titles.append(el.text.strip())
    print(f"  ✅ {len(titles)} titles")
    return titles


def parse_ani_title(raw):
    m = ANI_TITLE_RE.match(raw)
    if not m:
        return None
    full = m.group(1).strip()
    episode = m.group(2)
    clean = re.sub(r'\[.*?\]', '', full).strip()
    return {"raw": raw, "full_title": clean, "episode": episode}


def detect_season_number(title):
    m = re.search(r'第([一二三四五六七八九十\d]+)季', title)
    if m: return cn_to_int(m.group(1))
    m = re.search(r'Season\s+(\d+)', title)
    if m: return int(m.group(1))
    m = re.search(r'(\d+)\s*(?:nd|rd|th)?\s+Season', title, re.IGNORECASE)
    if m: return int(m.group(1))
    if re.search(r'參之章', title): return 3
    if re.search(r'貳', title): return 2
    m = re.search(r'\s+(\d+)\s*$', title)
    if m:
        num = int(m.group(1))
        if 2 <= num <= 20:
            return num
    return 1


def extract_base_and_keyword(title):
    season_keyword = None
    subtitle = ""
    base_title = title

    patterns = [
        (r'(第[一二三四五六七八九十\d]+季)', 'cn'),
        (r'(Season\s+\d+)', 'en'),
        (r'(\d+(?:nd|rd|th)?\s+Season)', 'en2'),
        (r'(參之章)', 'special'),
        (r'(貳)', 'special'),
    ]

    for pat, ptype in patterns:
        m = re.search(pat, title)
        if m:
            season_keyword = m.group(1)
            remaining = title[:m.start()].strip()
            after = title[m.end():].strip()
            if remaining:
                base_title = remaining
                subtitle = after
            else:
                base_title = after or title
            break

    if not season_keyword:
        m = re.search(r'\s+(\d+)\s*$', title)
        if m and int(m.group(1)) <= 20:
            num = int(m.group(1))
            if num > 1:
                base_title = title[:m.start()].strip()
                season_keyword = m.group(1)
                return base_title, season_keyword, subtitle

    return base_title, season_keyword, subtitle


def group_by_anime(titles):
    parsed = []
    for t in titles:
        p = parse_ani_title(t)
        if p:
            parsed.append(p)

    full_groups = defaultdict(list)
    for p in parsed:
        full_groups[p["full_title"]].append(p["episode"])

    base_groups = defaultdict(dict)
    for full_title, episodes in full_groups.items():
        base, keyword, subtitle = extract_base_and_keyword(full_title)
        season = detect_season_number(full_title)
        base_groups[base][full_title] = {
            "episodes": sorted(set(episodes)),
            "season": season,
            "keyword": keyword,
            "subtitle": subtitle,
        }
    return dict(base_groups)


# ============================================================
# LLM Context Generation
# ============================================================
def generate_llm_context(base_groups, tmdb_client, ani_cache, confirmed_mgr):
    """
    Collect all data for unmapped titles only.
    Returns list of context items for LLM inference.
    """
    context_items = []

    for base_title, variants in sorted(base_groups.items()):
        # Skip if all variants are already confirmed
        all_confirmed = all(
            confirmed_mgr.is_confirmed(vt) for vt in variants
        )
        if all_confirmed:
            print(f"  ⏩ {base_title}: already confirmed, skipping")
            continue

        item = {
            "base_title": base_title,
            "ani_variants": [],
            "ani_history": None,
            "tmdb": None,
        }

        for vt, info in variants.items():
            if confirmed_mgr.is_confirmed(vt):
                continue
            item["ani_variants"].append({
                "title": vt,
                "episodes": info["episodes"],
                "ani_season": info["season"],
                "keyword": info["keyword"],
                "subtitle": info["subtitle"],
            })

        if not item["ani_variants"]:
            continue

        # Check if it's a movie (exact match)
        time.sleep(RATE_LIMIT_DELAY)
        if tmdb_client.is_exact_movie_match(base_title):
            print(f"  🎬 {base_title}: exact movie match, skipping")
            # Add to confirmed as movie skip
            confirmed_mgr.data.setdefault("_skipped_movies", []).append(base_title)
            confirmed_mgr.save()
            continue

        # TMDB TV search
        time.sleep(RATE_LIMIT_DELAY)
        first_variant = next(iter(variants.keys()))
        tmdb = tmdb_client.search_and_match(first_variant, base_title)
        if tmdb:
            item["tmdb"] = {
                "name": tmdb["name"],
                "tmdb_id": tmdb["tmdb_id"],
                "total_seasons": tmdb["total_seasons"],
                "seasons": [
                    {"sn": s["sn"], "name": s["name"], "ep_count": s["ep_count"]}
                    for s in tmdb["seasons"]
                ],
            }

            # ANi history for multi-season anime
            has_multi = any(info["season"] > 1 for info in variants.values())
            if has_multi and ani_cache:
                search_q = re.sub(r'\s*第[一二三四五六七八九十\d]+季\s*$', '', base_title).strip()
                search_q = re.sub(r'\s+\d+\s*$', '', search_q).strip()
                alt_q = search_q[:6] if len(search_q) > 6 else search_q

                ep_ranges = ani_cache.get_episode_range_from_cache(search_q)
                if not ep_ranges:
                    ep_ranges = ani_cache.get_episode_range_from_cache(alt_q)

                if ep_ranges:
                    item["ani_history"] = {}
                    for name, (mn, mx, cnt) in ep_ranges.items():
                        item["ani_history"][name] = {
                            "min_ep": mn, "max_ep": mx, "file_count": cnt
                        }

        context_items.append(item)
        print(f"  📊 {base_title}: variants={len(item['ani_variants'])}, "
              f"tmdb={'✅' if item['tmdb'] else '❌'}, "
              f"history={'✅' if item['ani_history'] else '-'}")

    return context_items


def format_llm_prompt(context_items):
    """Format context data as LLM-ready prompt"""
    parts = [
        "# ANi → TMDB Season/Episode Mapping Task",
        "",
        "You are an anime season/episode mapping expert. Given the data below,",
        "determine how ANi file titles should map to TMDB seasons and episodes.",
        "",
        "## Mapping Rules",
        "",
        "1. **custom_season_mapping**: When ANi's \"season\" differs from TMDB's",
        "   - e.g. ANi \"Season 4\" but TMDB only has 1 season → map to S01",
        "   - Include episode_offset: ANi_ep + offset = TMDB_ep",
        "",
        "2. **season_episode_adjustment**: When ANi uses continuous numbering",
        "   - Offset is negative: ANi_ep + offset = TMDB_ep",
        "   - e.g. ANi ep25 (season 2) + (-24) = TMDB S02E01",
        "",
        "3. **X.5 episodes**: Try mapping to S00 (Specials) first",
        "   - e.g. ANi \"Season 2 - 12.5\" → TMDB S00E01",
        "",
        "4. **Cumulative offset**: When TMDB has only 1 season",
        "   - e.g. Re:Zero S1(25ep) + S2(25ep) + S3(16ep) = 66",
        "   - ANi \"Season 4 - 01\" → TMDB S01E67",
        "",
        "5. **Missing intermediate seasons**: Normal — ANi may not have",
        "   licensed all seasons. Only map what exists in the data.",
        "",
        "## Notes",
        "- TMDB data may lag behind ANi (ANi updates faster)",
        "- If TMDB season has ep_count=0 or very few, data may be outdated",
        "- ANi history episode ranges are from actual files, most reliable",
        "",
        "Output the mapping as JSON.",
        "",
    ]

    for i, item in enumerate(context_items, 1):
        parts.append(f"---")
        parts.append(f"## [{i}] {item['base_title']}")
        parts.append("")

        parts.append("### ANi RSS Current:")
        for v in item["ani_variants"]:
            eps_str = ", ".join(str(e) for e in v["episodes"]) if v["episodes"] else "?"
            parts.append(
                f'- "{v["title"]}" → ANi season S{v["ani_season"]}, '
                f'episodes: [{eps_str}]'
            )
        parts.append("")

        if item["ani_history"]:
            parts.append("### ANi History (actual episode ranges from files):")
            for name, data in sorted(item["ani_history"].items()):
                parts.append(
                    f'- "{name}": ep{data["min_ep"]:g}-{data["max_ep"]:g} '
                    f'({data["file_count"]} files)'
                )
            parts.append("")

        if item["tmdb"]:
            parts.append("### TMDB Info:")
            parts.append(f'- Name: {item["tmdb"]["name"]} (ID: {item["tmdb"]["tmdb_id"]})')
            parts.append(f'- Total seasons: {item["tmdb"]["total_seasons"]}')
            for s in item["tmdb"]["seasons"]:
                parts.append(f'  - S{s["sn"]:02d}: "{s["name"]}" ({s["ep_count"]}ep)')
            parts.append("")
        else:
            parts.append("### TMDB: ❌ No match")
            parts.append("")

    parts.append("---")
    parts.append("")
    parts.append("## Output Format (JSON)")
    parts.append("")
    parts.append("Each key is the EXACT ANi parsed title (full title between [ANi] and episode number).")
    parts.append("Matching is exact-only — S1 entries will NOT match S2 titles.")
    parts.append("")
    parts.append("```json")
    parts.append('{')
    parts.append('  "EXACT_ANi_TITLE": {')
    parts.append('    "tmdb_season": <TMDB season number>,')
    parts.append('    "episode_offset": <offset or omit if 0, ANi_ep + offset = TMDB_ep>,')
    parts.append('    "_note": "explanation"')
    parts.append('  },')
    parts.append('  "EXACT_ANi_TITLE Season 2": {')
    parts.append('    "tmdb_season": <TMDB season number>,')
    parts.append('    "episode_offset": <offset if continuous numbering>')
    parts.append('  }')
    parts.append('}')
    parts.append('```')

    return "\n".join(parts)


# ============================================================
# Mapping.json Output
# ============================================================
def generate_mapping_json(confirmed_mgr, output_path=None):
    """
    Generate the final mapping.json from confirmed mappings.
    Strips internal fields (_note, _*) for clean output.
    """
    if output_path is None:
        output_path = str(SCRIPT_DIR / "mapping.json")

    clean_mappings = {}
    for title, info in confirmed_mgr.data.get("mappings", {}).items():
        if title.startswith("_"):
            continue
        clean = {k: v for k, v in info.items() if not k.startswith("_")}
        clean_mappings[title] = clean

    result = {
        "_metadata": {
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "tmdb_language": TMDB_LANG,
            "version": "4.0",
            "description": "Keys are exact ANi parsed titles. Exact match only — no substring matching.",
        },
        "mappings": clean_mappings,
    }

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n✅ mapping.json written: {output_path}")
    return result


# ============================================================
# KubeSpider Format Output
# ============================================================
def generate_kubespider_json(confirmed_mgr, output_path=None):
    """
    Generate mappings_kubespider.json from confirmed mappings.

    KubeSpider format:
    {
      "custom_season_mapping": {
        "suffix_or_title": tmdb_season,          // simple
        "full title": { "season": N, "reserve_keywords": "base" }  // complex
      },
      "season_episode_adjustment": {
        "base_title": { "season_num": offset }
      }
    }

    Rules:
    - Only emit entries where mapping is non-trivial (S1 default skipped)
    - custom_season_mapping keys must be UNIQUE suffixes to avoid collisions
      - Unique suffix: "參之章", "Divinez 第五季「幻真星戰篇」" etc.
      - Generic suffix: "第四季", "第二季" — these collide across anime
      - For generic suffixes, use full title as key (complex format)
    - For numeric suffixes ("2"), always use complex format with reserve_keywords
    - season_episode_adjustment keys are base_title, values are {season: offset}
    """
    if output_path is None:
        output_path = str(SCRIPT_DIR / "mappings_kubespider.json")

    # First pass: collect all suffixes to detect collisions
    suffix_map = {}  # suffix -> list of (full_title, tmdb_season)
    title_infos = []  # (full_title, info, base, keyword, ani_season)

    for full_title, info in confirmed_mgr.data.get("mappings", {}).items():
        if full_title.startswith("_"):
            continue
        tmdb_season = info.get("tmdb_season", 1)
        offset = info.get("episode_offset", 0)
        base_title, keyword, subtitle = extract_base_and_keyword(full_title)
        ani_season = detect_season_number(full_title)

        needs_custom = ani_season != tmdb_season
        # Numeric suffix always needs custom mapping (KubeSpider can't detect "Title 2" = S02)
        if keyword and keyword.isdigit() and int(keyword) >= 2:
            needs_custom = True
        # Non-standard suffix (參之章, etc.) always needs mapping
        if keyword and not re.match(r'^(第[一二三四五六七八九十\d]+季|Season\s+\d+|\d+)$', keyword):
            needs_custom = True
        needs_adjustment = offset != 0

        if not needs_custom and not needs_adjustment:
            continue

        title_infos.append((full_title, info, base_title, keyword, ani_season, needs_custom, needs_adjustment))

        if keyword and needs_custom:
            suffix = keyword.strip()
            suffix_map.setdefault(suffix, []).append((full_title, tmdb_season))

    # Detect generic (collision-prone) suffixes
    generic_suffixes = {
        s for s, entries in suffix_map.items()
        if len(entries) > 1  # same suffix used by multiple anime
    }
    # Also treat standard season keywords as potentially generic
    generic_patterns = re.compile(
        r'^(第[一二三四五六七八九十\d]+季|Season\s*\d+|\d+(?:nd|rd|th)?\s*Season|第二季|第三季|第四季|第五季)$'
    )

    custom = {}
    adjustments = {}

    for full_title, info, base_title, keyword, ani_season, needs_custom, needs_adjustment in title_infos:
        tmdb_season = info.get("tmdb_season", 1)
        offset = info.get("episode_offset", 0)

        # custom_season_mapping
        if needs_custom and keyword:
            suffix = keyword.strip()
            # Numeric suffix (like "2") → always complex
            if suffix.isdigit() and int(suffix) >= 2:
                custom[full_title] = {
                    "season": tmdb_season,
                    "reserve_keywords": base_title,
                }
            # Generic suffix (collision) → use full title as complex key
            elif suffix in generic_suffixes or generic_patterns.match(suffix):
                custom[full_title] = {
                    "season": tmdb_season,
                    "reserve_keywords": base_title,
                }
            else:
                # Unique suffix → simple mapping
                custom[suffix] = tmdb_season

        # season_episode_adjustment
        if needs_adjustment:
            if base_title not in adjustments:
                adjustments[base_title] = {}
            adjustments[base_title][str(ani_season)] = offset

    result = {}
    if custom:
        result["custom_season_mapping"] = custom
    if adjustments:
        result["season_episode_adjustment"] = adjustments

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"✅ mappings_kubespider.json written: {output_path}")
    return result


# ============================================================
# Main
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="ANi → TMDB Season/Episode Mapper v3")
    parser.add_argument("--output", "-o", default=str(SCRIPT_DIR / "mapping.json"))
    parser.add_argument("--dry-run", action="store_true", help="Parse only, no TMDB queries")
    parser.add_argument("--refresh-cache", action="store_true", help="Force refresh ANi directory cache")
    parser.add_argument("--no-cache-refresh", action="store_true", help="Skip cache refresh, use existing")
    parser.add_argument("--confirmed", default=str(SCRIPT_DIR / "confirmed.json"), help="Path to confirmed.json")
    parser.add_argument("--llm", action="store_true", help="Generate LLM context for unmapped items")
    args = parser.parse_args()

    # Config from env
    api_key = os.environ.get("TMDB_API_KEY", "")
    proxy = os.environ.get("HTTP_PROXY") or os.environ.get("HTTPS_PROXY") or ""
    setup_proxy(proxy if proxy else None)

    if not api_key and not args.dry_run:
        print("❌ TMDB_API_KEY required! Set it in .env or environment")
        sys.exit(1)

    # Initialize components
    confirmed_mgr = ConfirmedMappingManager(args.confirmed)
    ani_cache = AniDirectoryCache()
    tmdb_client = TMDBClient(api_key) if api_key else None

    # 1. Fetch RSS
    titles = fetch_ani_titles()

    # 2. Parse & group
    base_groups = group_by_anime(titles)
    print(f"\n📊 Parsed {len(base_groups)} anime titles:")
    for base, variants in sorted(base_groups.items()):
        for vt, info in variants.items():
            s = info['season']
            eps = info['episodes']
            confirmed = "✓" if confirmed_mgr.is_confirmed(vt) else " "
            print(f"  [{confirmed}] {vt}  (S{s}, ep{eps})")

    if args.dry_run:
        out = args.output.replace('.json', '_dry.json')
        serializable = {}
        for base, variants in base_groups.items():
            serializable[base] = {}
            for vt, info in variants.items():
                serializable[base][vt] = {
                    "episodes": info["episodes"],
                    "season": info["season"],
                    "confirmed": confirmed_mgr.is_confirmed(vt),
                }
        with open(out, 'w', encoding='utf-8') as f:
            json.dump(serializable, f, ensure_ascii=False, indent=2)
        print(f"\n✅ Dry-run result: {out}")
        return

    # 3. Refresh ANi directory cache
    if not args.no_cache_refresh:
        print(f"\n📂 ANi directory cache refresh...")
        if args.refresh_cache:
            ani_cache.force_refresh_all()
        else:
            refreshed = ani_cache.refresh_recent(count=4)
            print(f"  ✅ Refreshed {refreshed} recent directories")

    # 4. Collect context for unmapped items
    print(f"\n🧠 Collecting context for unmapped titles...")
    context_items = generate_llm_context(
        base_groups, tmdb_client, ani_cache, confirmed_mgr
    )

    if not context_items:
        print("\n🎉 All titles are already mapped! No new items to process.")
        generate_mapping_json(confirmed_mgr, output_path=args.output)
        generate_kubespider_json(confirmed_mgr, output_path=str(SCRIPT_DIR / "mappings_kubespider.json"))
        return

    print(f"\n📋 {len(context_items)} unmapped items found")

    # 5. Generate LLM prompt
    prompt = format_llm_prompt(context_items)
    prompt_path = args.output.replace('.json', '_prompt.md')
    with open(prompt_path, 'w', encoding='utf-8') as f:
        f.write(prompt)
    print(f"  📝 LLM Prompt: {prompt_path}")

    # Also output context JSON
    ctx_path = args.output.replace('.json', '_context.json')
    with open(ctx_path, 'w', encoding='utf-8') as f:
        json.dump(context_items, f, ensure_ascii=False, indent=2, default=str)
    print(f"  📄 Context data: {ctx_path}")

    # 6. Generate mapping.json and kubespider.json with current confirmed data
    generate_mapping_json(confirmed_mgr, output_path=args.output)
    generate_kubespider_json(confirmed_mgr, output_path=str(SCRIPT_DIR / "mappings_kubespider.json"))

    print(f"\n💡 Next steps:")
    print(f"   1. Review {prompt_path}")
    print(f"   2. Submit to LLM for mapping inference")
    print(f"   3. Paste LLM output into confirmed.json")
    print(f"   4. Re-run to generate updated mapping.json")


if __name__ == "__main__":
    main()
