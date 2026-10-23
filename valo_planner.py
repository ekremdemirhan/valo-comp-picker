#!/usr/bin/env python3
"""
valo_planner.py: best Valorant composition for a chosen lineup and map, from tracker.gg history + the pro meta.

  pip install curl_cffi                          # once (gets past Cloudflare)
  python valo_planner.py fetch                   # download match history, write data/squad_data.json
  python valo_planner.py comp --map haven --players "merhaba, bigbeko, sriderya, unsent, draven" --igl merhaba
  python valo_planner.py ui --open               # the picker page: map dropdown, player checkboxes, IGL dropdown

Files: squad.json (roster), meta.json (per-map pro data from VLR.gg), data/squad_data.json (our stats,
rebuilt by `fetch`). Names and maps are matched loosely: "merhaba", "kivo", "haven" all work.

How a comp is scored: each player's agent score on the map (recent-weighted win rate + impact vs. role
average, shrunk toward their overall form when samples are small), plus the pro meta: the role setup must be
one pros run on that map (>= 3% of games), pro-picked agents get a bonus, matching a top pro comp adds a
little. --meta-weight 0 ignores the meta. The IGL is steered off duelist (--igl-duelist-penalty).

If tracker.gg blocks `fetch` (HTTP 403), open this in your browser:
  https://api.tracker.gg/api/v2/valorant/standard/matches/riot/<Name%23TAG>?type=competitive
save it as cache/<Name_TAG>/matches_0.json, then run `fetch` again; it uses what's in the cache.
Profiles must be public on tracker.gg.
"""
import argparse
import itertools
import json
import math
import os
import random
import re
import sys
import time
from collections import defaultdict
from datetime import datetime
from urllib.parse import quote

API = "https://api.tracker.gg/api/v2/valorant/standard"

ROLES = {
    "Jett": "Duelist", "Phoenix": "Duelist", "Reyna": "Duelist", "Raze": "Duelist", "Yoru": "Duelist",
    "Neon": "Duelist", "Iso": "Duelist", "Waylay": "Duelist",
    "Sova": "Initiator", "Breach": "Initiator", "Skye": "Initiator", "KAY/O": "Initiator", "Fade": "Initiator",
    "Gekko": "Initiator", "Tejo": "Initiator",
    "Brimstone": "Controller", "Viper": "Controller", "Omen": "Controller", "Astra": "Controller",
    "Harbor": "Controller", "Clove": "Controller", "Miks": "Controller",
    "Sage": "Sentinel", "Cypher": "Sentinel", "Killjoy": "Sentinel", "Chamber": "Sentinel",
    "Deadlock": "Sentinel", "Vyse": "Sentinel", "Veto": "Sentinel",
}
# Agent icon ids on valorant-api.com (https://media.valorant-api.com/agents/<id>/displayicon.png)
AGENT_ICONS = {
    "Astra": "41fb69c1-4189-7b37-f117-bcaf1e96f1bf",
    "Breach": "5f8d3a7f-467b-97f3-062c-13acf203c006",
    "Brimstone": "9f0d8ba9-4140-b941-57d3-a7ad57c6b417",
    "Chamber": "22697a3d-45bf-8dd7-4fec-84a9e28c69d7",
    "Clove": "1dbf2edd-4729-0984-3115-daa5eed44993",
    "Cypher": "117ed9e3-49f3-6512-3ccf-0cada7e3823b",
    "Deadlock": "cc8b64c8-4b25-4ff9-6e7f-37b4da43d235",
    "Fade": "dade69b4-4f5a-8528-247b-219e5a1facd6",
    "Gekko": "e370fa57-4757-3604-3648-499e1f642d3f",
    "Harbor": "95b78ed7-4637-86d9-7e41-71ba8c293152",
    "Iso": "0e38b510-41a8-5780-5e8f-568b2a4f2d6c",
    "Jett": "add6443a-41bd-e414-f6ad-e58d267f4e95",
    "KAY/O": "601dbbe7-43ce-be57-2a40-4abd24953621",
    "Killjoy": "1e58de9c-4950-5125-93e9-a0aee9f98746",
    "Miks": "7c8a4701-4de6-9355-b254-e09bc2a34b72",
    "Neon": "bb2a4828-46eb-8cd1-e765-15848195d751",
    "Omen": "8e253930-4c05-31dd-1b6c-968525494517",
    "Phoenix": "eb93336a-449b-9c1b-0a54-a891f7921d69",
    "Raze": "f94c3b30-42be-e959-889c-5aa313dba261",
    "Reyna": "a3bfb853-43b2-7238-a4f1-ad90e9e46bcc",
    "Sage": "569fdd95-4d10-43ab-ca70-79becc718b46",
    "Skye": "6f2a04ca-43e0-be17-7f36-b3908627744d",
    "Sova": "320b2a48-4d9b-a075-30f1-1f93a9b638fa",
    "Tejo": "b444168c-4e35-8076-db47-ef9bf368f384",
    "Veto": "92eeef5d-43b5-1d4a-8d03-b3927a09034b",
    "Viper": "707eab51-4836-f488-046a-cda6bf494859",
    "Vyse": "efba5359-4016-a1e5-7626-b1ae76895940",
    "Waylay": "df1cb487-4902-002e-5c17-d28e83e78588",
    "Yoru": "7f94d92c-4234-0a36-9646-3a87eb8b5c89",
}
REQUIRED_ROLES = ["Duelist", "Initiator", "Controller", "Sentinel"]
ROLE_ORDER = {r: i for i, r in enumerate(REQUIRED_ROLES)}
MAPS = ["Abyss", "Ascent", "Bind", "Breeze", "Corrode", "Fracture", "Haven", "Icebox", "Lotus", "Pearl",
        "Split", "Summit", "Sunset"]


# ----------------------------------------------------------------------------- helpers
def safe_name(riot_id):
    return re.sub(r"[^A-Za-z0-9_-]+", "_", riot_id.strip())


def short(rid):
    return rid.split("#")[0]


def dig(obj, *keys, default=None):
    for k in keys:
        if isinstance(obj, dict) and k in obj:
            obj = obj[k]
        else:
            return default
    return obj


def sval(stats, *names):
    for n in names:
        v = stats.get(n)
        if isinstance(v, dict):
            v = v.get("value")
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
    return None


def norm(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def load_squad(path):
    if not os.path.exists(path):
        sys.exit(f"{path} not found. Create it with your roster (see --help).")
    with open(path, encoding="utf-8") as f:
        sq = json.load(f)
    return sq


def resolve_player(name, roster):
    """Match a loosely typed name to a Riot ID from the roster; a full Name#TAG not in the roster is accepted."""
    n = norm(name.split("#")[0])
    exact = [r for r in roster if norm(r) == norm(name) or norm(short(r)) == n]
    if exact:
        return exact[0]
    hits = [r for r in roster if norm(short(r)).startswith(n)] or [r for r in roster if n in norm(short(r))]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        sys.exit(f'"{name}" matches several players: {", ".join(hits)}. Be more specific.')
    if "#" in name:
        return name.strip()
    sys.exit(f'"{name}" is not in squad.json. Use their full Riot ID (Name#TAG) or add them to the roster.')


def resolve_map(name):
    n = norm(name)
    hits = [m for m in MAPS if norm(m).startswith(n)]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        sys.exit(f'"{name}" matches several maps: {", ".join(hits)}')
    return name.strip().title()  # unknown/new map: use as typed


# ----------------------------------------------------------------------------- fetching
class FetchError(Exception):
    pass


def make_session():
    headers = {"Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9",
               "Origin": "https://tracker.gg", "Referer": "https://tracker.gg/"}
    try:
        from curl_cffi import requests as creq
        s, kind = creq.Session(impersonate="chrome"), "curl_cffi"
    except ImportError:
        import requests
        s, kind = requests.Session(), "requests"
        headers["User-Agent"] = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                 "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
    s.headers.update(headers)
    return s, kind


def get_json(session, url, retries=5):
    for attempt in range(retries):
        try:
            r = session.get(url, timeout=30)
        except Exception as e:
            if attempt == retries - 1:
                raise FetchError(f"network error: {e}")
            time.sleep(2 + attempt * 2)
            continue
        if r.status_code == 200:
            try:
                return r.json()
            except ValueError:
                raise FetchError("got HTML instead of JSON (Cloudflare challenge?)")
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep((2 ** attempt) * 3 + random.random() * 2)
            continue
        if r.status_code == 403:
            raise FetchError("HTTP 403 blocked by Cloudflare. `pip install curl_cffi`, or use the browser fallback in --help")
        if r.status_code in (404, 451):
            raise FetchError(f"HTTP {r.status_code}: profile private or not found on tracker.gg")
        raise FetchError(f"HTTP {r.status_code}")
    raise FetchError("rate limited too long, try again later or raise --delay")


def fetch_player(session, riot_id, pages, delay, cache_dir):
    d = os.path.join(cache_dir, safe_name(riot_id))
    os.makedirs(d, exist_ok=True)
    cursor, total = None, 0
    for p in range(pages):
        url = f"{API}/matches/riot/{quote(riot_id, safe='')}?type=competitive"
        if cursor is not None:
            url += f"&next={quote(str(cursor), safe='')}"
        data = get_json(session, url)
        with open(os.path.join(d, f"matches_{p}.json"), "w", encoding="utf-8") as f:
            json.dump(data, f)
        matches = dig(data, "data", "matches", default=[]) or []
        total += len(matches)
        cursor = dig(data, "data", "metadata", "next")
        if not matches or cursor in (None, "", -1):
            break
        time.sleep(delay + random.random())
    for f in os.listdir(d):  # drop stale pages beyond what we just fetched
        m = re.match(r"matches_(\d+)\.json$", f)
        if m and int(m.group(1)) > p:
            os.remove(os.path.join(d, f))
    with open(os.path.join(d, "fetched_at"), "w") as f:
        f.write(str(time.time()))
    return total


def fetch_many(players, args):
    """Returns the players that got fresh data."""
    key = os.environ.get("HENRIKDEV_API_KEY", "").strip()
    source = args.source if args.source != "auto" else ("henrik" if key else "tracker")
    ok = []
    if source == "henrik":
        if not key:
            sys.exit("Set HENRIKDEV_API_KEY (get a key at https://api.henrikdev.xyz/dashboard/).")
        print(f"Fetching {len(players)} player(s) from the HenrikDev API ({args.region})...")
        for rid in players:
            try:
                new, total = fetch_player_henrik(rid, key, args.region, args.history, args.delay)
                print(f"  {rid:<26} {new} matches fetched, {total} stored")
                ok.append(rid)
            except FetchError as e:
                print(f"  {rid:<26} FAILED: {e}")
            time.sleep(args.delay)
        return ok
    session, kind = make_session()
    print(f"Fetching {len(players)} player(s) via {kind}...")
    for rid in players:
        try:
            n = fetch_player(session, rid, args.pages, args.delay, args.cache)
            print(f"  {rid:<26} {n} matches")
            ok.append(rid)
        except FetchError as e:
            print(f"  {rid:<26} FAILED: {e}")
    return ok


# ----------------------------------------------------------------------------- HenrikDev API
# Free community API that, unlike tracker.gg, doesn't block GitHub's servers. Needs a key from
# https://api.henrikdev.xyz/dashboard/ in the HENRIKDEV_API_KEY environment variable / repo secret.
HENRIK = "https://api.henrikdev.xyz"


def henrik_get(path, key, retries=4):
    import urllib.error
    import urllib.request
    for attempt in range(retries):
        req = urllib.request.Request(HENRIK + path, headers={"Authorization": key, "Accept": "application/json",
                                                             "User-Agent": "valo-comp-picker"})
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500:
                wait = float(e.headers.get("Retry-After") or e.headers.get("X-RateLimit-Reset") or 5 * (attempt + 1))
                time.sleep(min(wait, 60) + 1)
                continue
            if e.code in (401, 403):
                raise FetchError(f"HenrikDev HTTP {e.code}: the API key is missing, wrong or expired")
            if e.code == 404:
                raise FetchError("HenrikDev HTTP 404: account not found (check the Riot ID and region)")
            raise FetchError(f"HenrikDev HTTP {e.code}")
        except urllib.error.URLError as e:
            if attempt == retries - 1:
                raise FetchError(f"network error: {e.reason}")
            time.sleep(3 * (attempt + 1))
    raise FetchError("HenrikDev kept rate-limiting; try again later")


def _iso_ts(v):
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def henrik_stored_record(m):
    """One entry of /v1/stored-matches -> our record (without player/weight)."""
    meta, st, teams = m.get("meta") or {}, m.get("stats") or {}, m.get("teams") or {}
    mine = str(st.get("team") or "").lower()
    if mine not in ("red", "blue"):
        return None
    my_r, their_r = teams.get(mine), teams.get("blue" if mine == "red" else "red")
    if not isinstance(my_r, int) or not isinstance(their_r, int):
        return None
    rounds = my_r + their_r
    # Stored matches only have round counts. A tie before overtime means a surrender or remake,
    # where the winner can't be known, and very short games say little: skip both.
    if rounds < 8 or (my_r == their_r and rounds < 24):
        return None
    agent, map_name = dig(st, "character", "name"), dig(meta, "map", "name")
    if not agent or not map_name:
        return None
    return {"id": meta.get("id"), "ts": _iso_ts(meta.get("started_at")), "agent": agent, "map": map_name,
            "won": 1.0 if my_r > their_r else 0.5 if my_r == their_r else 0.0,
            "acs": (st.get("score") or 0) / rounds, "adr": (dig(st, "damage", "made") or 0) / rounds,
            "kd": (st.get("kills") or 0) / max(st.get("deaths") or 0, 1)}


def henrik_v4_record(m, riot_id):
    """One entry of /v4/matches -> our record for this player."""
    name, tag = riot_id.lower().split("#", 1)
    me = next((p for p in m.get("players") or [] if str(p.get("name", "")).lower() == name
               and str(p.get("tag", "")).lower() == tag), None)
    if not me:
        return None
    team = next((t for t in m.get("teams") or [] if t.get("team_id") == me.get("team_id")), None)
    if not team:
        return None
    rw, rl = dig(team, "rounds", "won") or 0, dig(team, "rounds", "lost") or 0
    rounds = rw + rl
    st, md = me.get("stats") or {}, m.get("metadata") or {}
    agent, map_name = dig(me, "agent", "name"), dig(md, "map", "name")
    if rounds < 8 or not agent or not map_name:  # remakes / very short games say little
        return None
    return {"id": md.get("match_id"), "ts": _iso_ts(md.get("started_at")), "agent": agent, "map": map_name,
            "won": 1.0 if team.get("won") else 0.5 if rw == rl else 0.0,
            "acs": (st.get("score") or 0) / rounds, "adr": (dig(st, "damage", "dealt") or 0) / rounds,
            "kd": (st.get("kills") or 0) / max(st.get("deaths") or 0, 1)}


def fetch_player_henrik(riot_id, key, region, history_dir, delay):
    """Latest games (v4, which also makes HenrikDev store them) + the stored competitive history.
    Results accumulate in data/history/<player>.json (committed), so history keeps growing week after week."""
    name, tag = (quote(x, safe="") for x in riot_id.split("#", 1))
    found = {}
    v4 = henrik_get(f"/valorant/v4/matches/{region}/pc/{name}/{tag}?mode=competitive&size=10", key)
    for m in v4.get("data") or []:
        r = henrik_v4_record(m, riot_id)
        if r and r["id"]:
            found[r["id"]] = r
    time.sleep(delay)
    stored = henrik_get(f"/valorant/v1/stored-matches/{region}/{name}/{tag}?mode=competitive&size=200&page=1", key)
    for m in stored.get("data") or []:
        r = henrik_stored_record(m)
        if r and r["id"] and r["id"] not in found:
            found[r["id"]] = r
    os.makedirs(history_dir, exist_ok=True)
    path = os.path.join(history_dir, safe_name(riot_id) + ".json")
    old = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            old = {r["id"]: r for r in json.load(f)}
    old.update(found)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sorted(old.values(), key=lambda r: -(r["ts"] or 0)), f)
    return len(found), len(old)


# ----------------------------------------------------------------------------- parsing
def parse_match(m, riot_id):
    md = m.get("metadata") or {}
    segs = m.get("segments") or []
    seg = next((s for s in segs if str(dig(s, "attributes", "platformUserIdentifier") or "").lower() == riot_id.lower()),
               None) or next((s for s in segs if dig(s, "metadata", "agentName")), None)
    if seg is None:
        return None
    smd, st = seg.get("metadata") or {}, seg.get("stats") or {}
    agent, map_name = smd.get("agentName"), md.get("mapName")
    if not agent or not map_name or (md.get("modeName") and md["modeName"] != "Competitive"):
        return None
    kills, deaths = sval(st, "kills") or 0.0, sval(st, "deaths") or 0.0
    res = str(smd.get("result") or md.get("result") or "").lower()
    if res in ("victory", "win"):
        won = 1.0
    elif res in ("defeat", "loss"):
        won = 0.0
    elif res in ("tie", "draw"):
        won = 0.5
    elif isinstance(smd.get("hasWon"), bool):
        won = float(smd["hasWon"])
    else:
        return None
    ts = None
    try:
        ts = datetime.fromisoformat(str(md.get("timestamp")).replace("Z", "+00:00")).timestamp()
    except ValueError:
        pass
    return {"id": dig(m, "attributes", "id"), "ts": ts, "player": riot_id, "agent": agent,
            "role": ROLES.get(agent) or smd.get("agentRole") or "Flex", "map": map_name, "won": won,
            "acs": sval(st, "scorePerRound"), "adr": sval(st, "damagePerRound"), "kd": kills / max(deaths, 1.0)}


def load_records(players, cache_dir, half_life=90, history_dir=None):
    """Load every cached player (the whole squad gives better role baselines), deduped per player.
    Each game gets weight 0.5 ** (age_days / half_life); half_life 0 = all games equal."""
    records, now = [], time.time()
    for rid in players:
        d = os.path.join(cache_dir, safe_name(rid))
        seen = set()
        found = []
        hp = os.path.join(history_dir or "", safe_name(rid) + ".json")
        if history_dir and os.path.exists(hp):
            with open(hp, encoding="utf-8") as f:
                for h in json.load(f):
                    found.append({**h, "player": rid, "role": ROLES.get(h["agent"], "Flex")})
        for fn in sorted(f for f in (os.listdir(d) if os.path.isdir(d) else []) if re.match(r"matches_\d+\.json$", f)):
            with open(os.path.join(d, fn), encoding="utf-8") as f:
                data = json.load(f)
            for m in dig(data, "data", "matches", default=[]) or []:
                r = parse_match(m, rid)
                if r:
                    found.append(r)
        for r in found:  # HenrikDev and tracker.gg use the same Riot match ids, so this dedupes across sources
            if r["id"] not in seen:
                seen.add(r["id"])
                age = max(0.0, (now - r["ts"]) / 86400) if r["ts"] else 0.0
                r["w"] = 0.5 ** (age / half_life) if half_life else 1.0
                records.append(r)
    return records


# ----------------------------------------------------------------------------- model
class Stat:
    """n = raw games; wn, won, imp = recency-weighted sums."""
    __slots__ = ("n", "wn", "won", "imp")

    def __init__(self):
        self.n, self.wn, self.won, self.imp = 0, 0.0, 0.0, 0.0

    def add(self, r):
        w = r.get("w", 1.0)
        self.n += 1
        self.wn += w
        self.won += r["won"] * w
        self.imp += r["impact"] * w

    @property
    def wr(self):
        return self.won / self.wn if self.wn > 0.05 else None


def shrink(total, n, prior, k):
    return (total + k * prior) / (n + k)


class Model:
    """Win rate and impact (ACS, K/D, ADR relative to the agent's role average), estimated per
    player+agent+map and pulled toward the player's agent form and map form when samples are small."""

    def __init__(self, records, min_agent_games=6, min_recent=1.5):
        self.min_agent_games, self.min_recent = min_agent_games, min_recent
        self._impact(records)
        self.G = Stat()
        self.P, self.PA, self.PM, self.PAM = (defaultdict(Stat) for _ in range(4))
        for r in records:
            self.G.add(r)
            self.P[r["player"]].add(r)
            self.PA[(r["player"], r["agent"])].add(r)
            self.PM[(r["player"], r["map"])].add(r)
            self.PAM[(r["player"], r["agent"], r["map"])].add(r)
        self.role = {r["agent"]: r["role"] for r in records}

    @staticmethod
    def _impact(records):
        def mean(rs, k):
            pairs = [(r[k], r.get("w", 1.0)) for r in rs if r[k] is not None]
            w = sum(p[1] for p in pairs)
            return sum(x * wt for x, wt in pairs) / w if w else None
        g = {k: mean(records, k) for k in ("acs", "kd", "adr")}
        base = {}
        for role in {r["role"] for r in records}:
            rr = [r for r in records if r["role"] == role]
            base[role] = {k: (mean(rr, k) if len(rr) >= 15 else None) or g[k] for k in g}
        for r in records:
            b, parts = base[r["role"]], []
            if r["acs"] is not None and b["acs"]:
                parts.append((0.5, r["acs"] / b["acs"]))
            if b["kd"]:
                parts.append((0.25, r["kd"] / b["kd"]))
            if r["adr"] is not None and b["adr"]:
                parts.append((0.25, r["adr"] / b["adr"]))
            w = sum(p[0] for p in parts)
            r["impact"] = sum(p[0] * p[1] for p in parts) / w if w else 1.0

    def estimate(self, player, agent, map_name):
        g_wr, g_imp = self.G.won / self.G.wn, self.G.imp / self.G.wn
        p = self.P[player]
        p_wr, p_imp = shrink(p.won, p.wn, g_wr, 20), shrink(p.imp, p.wn, g_imp, 20)
        pa = self.PA[(player, agent)]
        pa_wr, pa_imp = shrink(pa.won, pa.wn, p_wr, 12), shrink(pa.imp, pa.wn, p_imp, 12)
        pm = self.PM[(player, map_name)]
        pm_wr, pm_imp = shrink(pm.won, pm.wn, p_wr, 10), shrink(pm.imp, pm.wn, p_imp, 10)
        t_wr = min(max(pa_wr + (pm_wr - p_wr), 0.05), 0.95)
        t_imp = max(pa_imp + (pm_imp - p_imp), 0.3)
        pam = self.PAM[(player, agent, map_name)]
        wr, imp = shrink(pam.won, pam.wn, t_wr, 5), shrink(pam.imp, pam.wn, t_imp, 5)
        return {"agent": agent, "role": self.role.get(agent, "Flex"), "score": 100 * (0.5 * wr + 0.25 * imp),
                "n_map": pam.n, "wn_map": pam.wn, "n_agent": pa.n, "wr_map": pam.wr}

    def options(self, player, map_name):
        agents = [a for (p, a), s in self.PA.items()
                  if p == player and s.n >= self.min_agent_games and s.wn >= self.min_recent]
        if not agents:  # tiny or old history: fall back to their most recently played agents
            played = sorted(((s.wn, a) for (p, a), s in self.PA.items() if p == player), reverse=True)
            agents = [a for _, a in played[:3]]
        return sorted((self.estimate(player, a, map_name) for a in agents), key=lambda o: -o["score"])


def load_meta(path):
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {"maps": {}, "pool": []}


LETTER = {"Duelist": "D", "Initiator": "I", "Controller": "C", "Sentinel": "S"}
MIN_STRUCT_SHARE = 0.03


def pro_pick(meta, map_name, agent):
    mm = meta["maps"].get(map_name)
    return mm["ag"].get(agent, [0])[0] / mm["n"] if mm else None


def agent_meta(meta, map_name, agent):
    p = pro_pick(meta, map_name, agent)
    return 0.0 if p is None else 8 * math.sqrt(p) - 3


def struct_key(picks):
    c = {"D": 0, "I": 0, "C": 0, "S": 0}
    for o in picks:
        if o["role"] in LETTER:
            c[LETTER[o["role"]]] += 1
    return f"{c['D']}{c['I']}{c['C']}{c['S']}"


def struct_info(meta, map_name, key):
    mm = meta["maps"][map_name]
    top = max(v[0] for v in mm["st"].values()) / mm["n"]
    share = mm["st"].get(key, [0])[0] / mm["n"]
    bonus = max(-8.0, 4 * math.log(share / top)) if share > 0 else -10.0
    return {"share": share, "allowed": share >= MIN_STRUCT_SHARE, "bonus": bonus}


def closest_pro(meta, map_name, agents):
    mm = meta["maps"].get(map_name)
    if not mm:
        return None
    best = None
    for comp, n, w in mm["cp"]:
        cs = comp.split(",")
        ov = sum(a in agents for a in cs)
        if best is None or ov > best["overlap"] or (ov == best["overlap"] and n > best["n"]):
            best = {"comp": cs, "n": n, "w": w, "overlap": ov}
    return best


# Pro IGLs (VLR team captains, VCT 2026) play controller 42%, initiator 40%, sentinel 11%, duelist 7% of rounds.
ROLE_PREF = {"Controller": 0, "Initiator": 1, "Sentinel": 2, "Duelist": 3}


def suggest_igl(model, map_name, players, callers, igl_pen, meta, meta_weight=1.0, top=3, locks=None):
    """Try each candidate as IGL and keep the one whose best comp scores highest (ties: controller, then
    initiator). Candidates are the usual callers in the lineup, or everyone if none is playing."""
    pool = [p for p in players if p in callers] or list(players)
    best = None
    for p in pool:
        comps, mode, opts = compositions(model, map_name, players, p, igl_pen, meta, meta_weight, top=top, locks=locks)
        if not comps:
            continue
        role = next(o["role"] for q, o in comps[0]["picks"] if q == p)
        key = comps[0]["total"] - ROLE_PREF.get(role, 4) * 0.01
        if best is None or key > best["key"]:
            best = {"igl": p, "key": key, "comps": comps, "mode": mode, "opts": opts,
                    "from_callers": any(q in callers for q in players)}
    return best


def compositions(model, map_name, players, igl, igl_pen, meta, meta_weight=1.0, top=3, per_player=8, locks=None):
    """All valid agent assignments for exactly these players, best first.
    With pro-meta data for the map, the role structure must be one pros run there (>= 3% of games).
    locks: {player: agent} chosen by hand; everyone else is picked around them."""
    locks = locks or {}
    taken = {locks[p] for p in players if p in locks}
    opts = {}
    for p in players:
        if p in locks:
            opts[p] = [model.estimate(p, locks[p], map_name)]
            continue
        o = [x for x in model.options(p, map_name) if x["agent"] not in taken][:per_player]
        if not o:  # their usual agents are all locked by teammates: anything they've played
            played = sorted(((s.wn, a) for (q, a), s in model.PA.items() if q == p and a not in taken), reverse=True)[:4]
            o = sorted((model.estimate(p, a, map_name) for _, a in played), key=lambda x: -x["score"])
        opts[p] = o
    has_meta = map_name in meta["maps"] and meta_weight > 0 and len(players) == 5
    for mode in (["meta", "loose"] if has_meta else ["roles", "controller", "loose"]):
        found = []
        for combo in itertools.product(*(opts[p] for p in players)):
            agents = [o["agent"] for o in combo]
            if len(set(agents)) < len(agents):
                continue
            own = sum(o["score"] - (igl_pen.get(o["role"], 0) if p == igl else 0)
                      for p, o in zip(players, combo)) / len(players)
            bonus, st = 0.0, None
            if has_meta:
                st = struct_info(meta, map_name, struct_key(combo))
                if mode == "meta" and not st["allowed"]:
                    continue
                cp = closest_pro(meta, map_name, agents)
                cp_bonus = (1.5 if cp["overlap"] == 5 else 0.75 if cp["overlap"] == 4 else 0) if cp and cp["n"] >= 5 else 0
                bonus = meta_weight * (sum(agent_meta(meta, map_name, a) for a in agents) / 5 + st["bonus"] + cp_bonus)
            else:
                roles = {o["role"] for o in combo}
                if mode == "roles" and not all(r in roles for r in REQUIRED_ROLES):
                    continue
                if mode == "controller" and "Controller" not in roles:
                    continue
            found.append({"total": own + bonus, "own": own, "bonus": bonus, "st": st,
                          "picks": list(zip(players, combo))})
        if found:
            found.sort(key=lambda x: -x["total"])
            return found[:top], mode, opts
    return [], None, opts


def confidence(wn):
    return "solid" if wn >= 4 else "some data" if wn >= 1.5 else "thin recent data"


def fmt_pct(x):
    return "  -" if x is None else f"{x * 100:3.0f}%"


# ----------------------------------------------------------------------------- commands
def load_data(path):
    """The compact squad data file: recency-weighted aggregates per player/agent/map (see build_rows)."""
    if not os.path.exists(path):
        sys.exit(f"{path} not found. Run `python valo_planner.py fetch` first.")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def records_from_data(data):
    """Expand aggregate rows back into per-game records; the weighted sums come out exactly the same."""
    recs = []
    for p, a, m, g, n, wn, w, acs, kd, adr in data["rows"]:
        if wn <= 0:
            continue
        rec = {"id": None, "ts": None, "player": data["players"][p], "agent": a, "role": ROLES.get(a, "Flex"),
               "map": m, "won": w / wn, "acs": acs / wn, "kd": kd / wn, "adr": adr / wn, "w": wn / n, "squad": g}
        recs.extend(dict(rec) for _ in range(n))
    return recs


def cmd_fetch(args):
    roster = load_squad(args.squad)["players"]
    fresh = fetch_many(roster, args)
    write_data(args, roster)
    if len(fresh) < len(roster):
        sys.exit(2)  # partial refresh: data written, but tell the pipeline


def cmd_rebuild(args):
    """Recompute data/squad_data.json from data/history (and any cache) without fetching,
    e.g. after editing the history files or to re-apply the recency weights."""
    write_data(args, load_squad(args.squad)["players"])


def write_data(args, roster):
    records = load_records(roster, args.cache, args.half_life, args.history)
    old_players = load_data(args.data)["players"] if os.path.exists(args.data) else []
    counts = {p: sum(1 for r in records if r["player"] == p) for p in roster}
    thin = [p for p in roster if 0 < counts[p] < args.min_history and p in old_players]
    for p in thin:  # not enough history yet: keep last numbers until it builds up
        print(f"  {p}: only {counts[p]} games so far (< {args.min_history}), keeping the previous numbers for now")
    records = [r for r in records if r["player"] not in thin]
    have = {r["player"] for r in records}
    if not have:
        sys.exit(f"No fresh data for anyone, so {args.data} was left as it is.")
    rows = build_rows(records, roster)
    # keep last week's numbers for anyone whose fetch failed and who has no cache here
    if os.path.exists(args.data):
        old = load_data(args.data)
        for p in roster:
            if p not in have and p in old["players"]:
                oi, ni = old["players"].index(p), roster.index(p)
                kept = [[ni, *r[1:]] for r in old["rows"] if r[0] == oi]
                rows += kept
                if kept:
                    print(f"  {p}: no fresh data, kept the previous numbers")
    if not rows:
        sys.exit("No data at all. Are the tracker.gg profiles public, and is tracker.gg reachable?")
    os.makedirs(os.path.dirname(os.path.abspath(args.data)), exist_ok=True)
    with open(args.data, "w", encoding="utf-8") as f:
        json.dump({"asOf": time.strftime("%d %b %Y"), "players": roster, "rows": rows}, f, separators=(",", ":"))
    print(f"Wrote {args.data} ({len(rows)} rows)")


def comp_report(args):
    """Returns (lines, ok) for the requested lineup; lines are Markdown when args.markdown."""
    md = args.markdown
    data = load_data(args.data)
    roster = data["players"]
    players = [resolve_player(n, roster) for n in args.players.split(",") if n.strip()]
    if len(set(players)) != len(players):
        return ["The same player is listed twice."], False
    if not 2 <= len(players) <= 5:
        return [f"Pick between 2 and 5 players (got {len(players)})."], False
    map_name = resolve_map(args.map)
    model = Model(records_from_data(data), args.min_agent_games)
    missing = [p for p in players if not model.P[p].n]
    if missing:
        return [f"No match data for: {', '.join(missing)}. Check their tracker.gg profile is public."], False
    meta = load_meta(args.meta)
    locks = {}
    for item in (args.lock or "").split(","):
        if not item.strip():
            continue
        if "=" not in item:
            return [f'Use name=Agent for hand-picked agents, e.g. "sriderya=Jett" (got "{item.strip()}").'], False
        who, agent = (x.strip() for x in item.split("=", 1))
        p = resolve_player(who, roster)
        if p not in players:
            return [f"{short(p)} has a hand-picked agent but isn't one of the selected players."], False
        a = next((x for x in ROLES if norm(x) == norm(agent)), None)
        if not a:
            return [f"{agent!r} isn't a Valorant agent."], False
        if a in locks.values():
            return [f"{a} is hand-picked for two players."], False
        locks[p] = a
    igl_pen = {"Duelist": args.igl_duelist_penalty, "Sentinel": args.igl_sentinel_penalty}
    choice = (args.igl or "auto").strip().lower()
    suggested = None
    if choice == "auto":
        callers = set(load_squad(args.squad).get("callers", []))
        suggested = suggest_igl(model, map_name, players, callers, igl_pen, meta, args.meta_weight, top=args.alts + 1,
                                locks=locks)
        if not suggested:
            return ["Couldn't build a composition (not enough agent data)."], False
        igl, comps, mode, opts = suggested["igl"], suggested["comps"], suggested["mode"], suggested["opts"]
    else:
        igl = None if choice in ("", "none", "not set") else resolve_player(args.igl, roster)
        if igl and igl not in players:
            return [f"The IGL ({short(igl)}) isn't one of the selected players. Tick them too, or pick another IGL."], False
        comps, mode, opts = compositions(model, map_name, players, igl, igl_pen if igl else {},
                                         meta, args.meta_weight, top=args.alts + 1, locks=locks)
    if not comps:
        return ["Couldn't build a composition (not enough agent data)."], False
    igl_line = "not set"
    if igl:
        agent = next(o["agent"] for q, o in comps[0]["picks"] if q == igl)
        igl_line = f"{short(igl)} on {agent}"
        if suggested:
            igl_line += " (suggested" + ("" if suggested["from_callers"] else "; none of the usual callers is playing") + ")"

    out = []
    if md:
        out += [f"## {map_name}: {', '.join(short(p) for p in players)}", "",
                f"**IGL:** {igl_line}", ""]
    else:
        out += [f"\n{map_name.upper()}  ·  {', '.join(short(p) for p in players)}",
                f"IGL: {igl_line}"]
    if len(players) < 5:
        out.append(f"Note: {len(players)} players, so this is a partial comp and the pro-meta checks are skipped.")
    elif map_name not in meta["maps"]:
        out.append(f"Note: no pro-meta data for {map_name}; using the basic one-of-each-role rule.")
    elif mode == "loose":
        out.append(f"Note: these agent pools can't make a role setup pros use on {map_name}; showing the closest possible.")

    for i, c in enumerate(comps):
        title = "Best composition" if i == 0 else f"Alternative {i}"
        setup = f"; pros run this role setup in {c['st']['share'] * 100:.0f}% of games here" if c["st"] else ""
        picks = sorted(c["picks"], key=lambda x: ROLE_ORDER.get(x[1]["role"], 9))
        if md:
            out += ["", f"### {title}", f"Team score {c['total']:.1f} (own stats {c['own']:.1f}, meta {c['bonus']:+.1f}){setup}", ""]
            if i == 0:
                out += ["| Role | Agent | Player | Score | Games here | Recent WR here | Pro pick here |",
                        "|---|---|---|---|---|---|---|"]
                for p, o in picks:
                    pp = pro_pick(meta, map_name, o["agent"])
                    out.append(f"| {o['role']} | **{o['agent']}**{' (hand-picked)' if p in locks else ''} | {short(p)}{' (IGL)' if p == igl else ''} | "
                               f"{o['score']:.1f} | {o['n_map']} ({confidence(o['wn_map'])}) | {fmt_pct(o['wr_map']).strip()} | "
                               f"{'-' if pp is None else f'{pp * 100:.0f}%'} |")
            else:
                out.append(", ".join(f"**{o['agent']}** {short(p)}" for p, o in picks))
        else:
            out.append(f"\n{title}  (team score {c['total']:.1f}: own stats {c['own']:.1f}, meta {c['bonus']:+.1f}{setup})")
            for p, o in picks:
                pp = pro_pick(meta, map_name, o["agent"])
                pro = f", pro pick {pp * 100:.0f}%" if pp is not None else ""
                out.append(f"  {o['role']:<10} {o['agent']:<10} {short(p):<18} score {o['score']:5.1f}  "
                           f"{o['n_map']:>2} games here ({confidence(o['wn_map'])}){pro}{'  [IGL]' if p == igl else ''}"
                           f"{'  [hand-picked]' if p in locks else ''}")
        cp = closest_pro(meta, map_name, [o["agent"] for _, o in c["picks"]])
        if cp and i == 0 and len(players) == 5:
            line = f"Closest pro comp: {', '.join(cp['comp'])} ({cp['overlap']}/5 same, pros {cp['w']}-{cp['n'] - cp['w']})"
            out += ["", line] if md else ["  " + line]

    opts = {p: model.options(p, map_name) for p in players}
    if md:
        out += ["", "### Each player's best agents on this map", "", "| Player | 1st | 2nd | 3rd | 4th |", "|---|---|---|---|---|"]
        for p in players:
            cells = [f"{o['agent']} {o['score']:.0f}" for o in opts[p][:4]] + [""] * 4
            out.append(f"| {short(p)} | " + " | ".join(cells[:4]) + " |")
        out += ["", f"_Our data as of {data['asOf']}. Pro meta: {meta.get('asOf', 'n/a')}._"]
    else:
        out.append("\nEach player's best agents on this map:")
        for p in players:
            out.append(f"  {short(p):<18} " + ", ".join(f"{o['agent']} {o['score']:.0f}" for o in opts[p][:4]))
        out.append(f"\n(our data as of {data['asOf']})")
    return out, True


def cmd_comp(args):
    lines, ok = comp_report(args)
    text = "\n".join(lines)
    print(text)
    if args.summary:  # e.g. $GITHUB_STEP_SUMMARY
        with open(args.summary, "a", encoding="utf-8") as f:
            f.write(text + "\n")
    if not ok:
        sys.exit(1)


# ----------------------------------------------------------------------------- interactive page
def build_rows(records, players):
    """Aggregate per player/agent/map plus how many squad members were in the match (1, 3+ or 5)."""
    from collections import Counter
    cnt = Counter(r["id"] for r in records if r["id"])
    idx = {p: i for i, p in enumerate(players)}
    agg = {}
    for r in records:
        if r["player"] not in idx:
            continue
        c = cnt.get(r["id"], 1)
        k = (idx[r["player"]], r["agent"], r["map"], 5 if c >= 5 else 3 if c >= 3 else 1)
        w = r.get("w", 1.0)
        a = agg.setdefault(k, [0, 0.0, 0.0, 0.0, 0.0, 0.0])
        a[0] += 1
        a[1] += w
        a[2] += r["won"] * w
        a[3] += (r["acs"] or 0.0) * w
        a[4] += r["kd"] * w
        a[5] += (r["adr"] or 0.0) * w
    return [[*k, a[0], round(a[1], 3), round(a[2], 3), round(a[3], 1), round(a[4], 3), round(a[5], 1)]
            for k, a in agg.items() if a[1] > 0.001]


def render_ui(players, rows, as_of, meta, callers=()):
    data = {"players": players, "rows": rows, "asOf": as_of, "roles": ROLES, "meta": meta, "icons": AGENT_ICONS, "callers": list(callers)}
    page = UI_TEMPLATE.replace("__DATA__", json.dumps(data, separators=(",", ":")))
    logo = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "logo.png")
    if os.path.exists(logo):  # embedded, so the page stays a single file
        import base64
        with open(logo, "rb") as f:
            uri = "data:image/png;base64," + base64.b64encode(f.read()).decode()
        page = page.replace("<!--LOGO-->", f'<img class="logo" src="{uri}" alt="Squad logo">')
        page = page.replace("<!--FAVICON-->", f'<link rel="icon" href="{uri}">')
    return page


def cmd_ui(args):
    data = load_data(args.data)
    callers = load_squad(args.squad).get("callers", []) if os.path.exists(args.squad) else []
    page = render_ui(data["players"], data["rows"], data["asOf"], load_meta(args.meta), callers)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write('<!doctype html><html lang="en"><head><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"></head><body>'
                + page + "</body></html>")
    path = os.path.abspath(args.out)
    print(f"Wrote {path}")
    if args.open:
        import webbrowser
        webbrowser.open("file://" + path)


UI_TEMPLATE = r"""<title>Squad Comp Picker</title>
<!--FAVICON-->
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@600;700&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@500&display=swap">
<style>
/* layout: control rail (map + roster) on the left, live composition on the right; stacks on phones */
:root{--bg:#eef0f3;--fg:#14171d;--mut:#5c6370;--card:#ffffff;--line:#d9dde4;--soft:#f6f7f9;
--accent:#e23346;--on-accent:#ffffff;--duel:#e23346;--init:#1d8659;--ctrl:#5548d9;--sent:#a87d00;
--display:"Barlow Condensed","Arial Narrow",sans-serif;--body:"IBM Plex Sans",system-ui,sans-serif;--mono:"IBM Plex Mono",ui-monospace,monospace}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#0d1016;--fg:#e8eaef;--mut:#959bab;--card:#161a22;--line:#29303b;--soft:#1c212b;
--accent:#ff5163;--on-accent:#140508;--duel:#ff5163;--init:#3cc489;--ctrl:#8d83ff;--sent:#e8b931;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#0d1016;--fg:#e8eaef;--mut:#959bab;--card:#161a22;--line:#29303b;--soft:#1c212b;
--accent:#ff5163;--on-accent:#140508;--duel:#ff5163;--init:#3cc489;--ctrl:#8d83ff;--sent:#e8b931;color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 var(--body)}
.wrap{max-width:1120px;margin:0 auto;padding-block:28px 56px;padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px))}
.top{display:flex;gap:16px;align-items:center;margin-bottom:22px}
@media (max-width:520px){.top{align-items:flex-start}}
.topt{min-width:0}.top .lead{margin-bottom:0}
.logo{width:76px;height:auto;flex:none;border-radius:12px}
@media (max-width:520px){.logo{width:56px}}
h1{margin:0;font:700 36px/1 var(--display);letter-spacing:.02em;text-transform:uppercase}
.lead{margin:6px 0 22px;color:var(--mut)}
.grid{display:grid;grid-template-columns:300px minmax(0,1fr);gap:20px;align-items:start}
@media (max-width:820px){.grid{grid-template-columns:minmax(0,1fr)}}
.panel{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:16px;display:flex;flex-direction:column;gap:14px}
.lbl{font:500 11px var(--mono);text-transform:uppercase;letter-spacing:.08em;color:var(--mut);display:flex;justify-content:space-between;align-items:center}
select{width:100%;padding:9px 10px;border:1px solid var(--line);border-radius:6px;background:var(--soft);color:var(--fg);font:600 18px var(--display);letter-spacing:.04em;text-transform:uppercase}
select.sm{font:500 15px var(--body);text-transform:none;letter-spacing:0}
select:disabled{opacity:.5}
select:focus-visible,input:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.plist{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:4px}
.plist label{display:flex;align-items:center;gap:10px;padding:7px 8px;border-radius:6px;cursor:pointer;border:1px solid transparent}
.plist label:hover{background:var(--soft)}
.plist input{width:18px;height:18px;accent-color:var(--accent);flex:none}
.plist input:checked+.nm{font-weight:600}
.nm{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.tagline{color:var(--mut);font-weight:400}
.procomp{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px 12px;font-size:14px;display:flex;flex-wrap:wrap;gap:4px 12px;align-items:baseline}
.procomp .k{font:500 11px var(--mono);text-transform:uppercase;letter-spacing:.06em;color:var(--mut)}
.procomp .match{font:500 12px var(--mono);color:var(--mut);margin-left:auto}
.struct{font:500 12px var(--mono);color:var(--mut)}
.pro{display:block;font:500 11px var(--mono);color:var(--mut);margin-top:2px}
.chip{font:500 10px var(--mono);letter-spacing:.06em;text-transform:uppercase;padding:2px 6px;border-radius:4px;border:1px solid var(--line);color:var(--mut);white-space:nowrap}
.count{font:600 12px var(--mono);color:var(--fg)}
.count.bad{color:var(--duel)}
.quick{display:flex;gap:8px}
.quick button{flex:1;padding:6px;border:1px solid var(--line);border-radius:6px;background:var(--soft);color:var(--fg);font:500 12px var(--body);cursor:pointer}
.quick button:hover{border-color:var(--mut)}
.opts{display:flex;flex-direction:column;gap:8px;border-top:1px solid var(--line);padding-top:12px}
.chk{display:flex;gap:10px;align-items:flex-start;font-size:13px;cursor:pointer}
.chk input{accent-color:var(--accent);margin-top:3px}
.results{display:flex;flex-direction:column;gap:18px;min-width:0}
.head{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 16px}
.head h2{margin:0;font:700 30px/1 var(--display);text-transform:uppercase;letter-spacing:.03em}
.igl{font-size:14px}.sugg{font:500 11px var(--mono);text-transform:uppercase;letter-spacing:.05em;color:var(--mut);margin-left:4px}.igl b{color:var(--accent)}
.msg{background:var(--card);border:1px dashed var(--line);border-radius:8px;padding:28px 20px;color:var(--mut);text-align:center}
.warn{color:var(--duel);font-size:13px;margin:0}
.cards{display:flex;flex-wrap:wrap;justify-content:center;gap:12px}
.results{container-type:inline-size}
.cards .card{flex:0 1 calc((100% - 24px) / 3)}
@container (max-width:680px){.cards .card{flex-basis:calc((100% - 12px) / 2)}}
@container (max-width:420px){.cards .card{flex-basis:100%}}
@supports not (container-type:inline-size){@media (max-width:640px){.cards .card{flex-basis:100%}}}
.agent{overflow-wrap:anywhere}
.ch{display:flex;gap:12px;align-items:center;min-width:0}
.ai{width:60px;height:60px;flex:none;border-radius:8px;background:var(--soft);object-fit:cover}
.ai.sm{width:22px;height:22px;border-radius:4px;vertical-align:-6px;margin-right:6px;background:none}
.kv{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:4px;margin-top:10px;padding:8px 0;border-block:1px solid var(--line)}
.kv div{display:flex;flex-direction:column;align-items:center;min-width:0}
.kv b{font:600 16px var(--body);font-variant-numeric:tabular-nums}
.kv span{font:500 10px var(--mono);text-transform:uppercase;letter-spacing:.06em;color:var(--mut)}
.kvh{font:500 10px var(--mono);text-transform:uppercase;letter-spacing:.06em;color:var(--mut);margin-top:10px}
.here{font-size:12px;color:var(--mut);margin-top:6px;font-variant-numeric:tabular-nums}
.foot{display:flex;justify-content:space-between;align-items:center;margin-top:8px;gap:8px}
.foot .sc{font:500 12px var(--mono);color:var(--mut)}
.swap{margin-top:10px;padding-top:10px;border-top:1px dashed var(--line);display:flex;flex-direction:column;gap:4px}
.swap label{font:500 10px var(--mono);text-transform:uppercase;letter-spacing:.06em;color:var(--mut)}
.swaprow{display:flex;gap:6px}
.swapsel{flex:1;min-width:0;padding:6px 8px;border:1px solid var(--line);border-radius:6px;background:var(--soft);color:var(--fg);font:500 13px var(--body);text-transform:none;letter-spacing:0}
.unlock,.changes button{padding:5px 10px;border:1px solid var(--line);border-radius:6px;background:var(--soft);color:var(--fg);font:500 12px var(--body);cursor:pointer}
.unlock:hover,.changes button:hover{border-color:var(--mut)}
.changes{display:flex;flex-wrap:wrap;gap:8px 12px;align-items:center;justify-content:space-between;background:var(--card);border:1px solid var(--accent);border-radius:8px;padding:10px 12px;font-size:13px}
.card{background:var(--card);border:1px solid var(--line);border-top:4px solid var(--mut);border-radius:8px;padding:12px;display:flex;flex-direction:column;min-width:0}
.card.Duelist{border-top-color:var(--duel)}.card.Initiator{border-top-color:var(--init)}.card.Controller{border-top-color:var(--ctrl)}.card.Sentinel{border-top-color:var(--sent)}
.role{font:500 11px var(--mono);text-transform:uppercase;letter-spacing:.08em}
.Duelist .role{color:var(--duel)}.Initiator .role{color:var(--init)}.Controller .role{color:var(--ctrl)}.Sentinel .role{color:var(--sent)}
.agent{font:700 28px/1.05 var(--display);text-transform:uppercase;letter-spacing:.02em}
.who{font-weight:600;display:flex;gap:6px;align-items:center;flex-wrap:wrap}
.iglb{font:600 10px var(--mono);letter-spacing:.06em;background:var(--accent);color:var(--on-accent);padding:1px 6px;border-radius:3px}
.meta{font-size:12px;color:var(--mut);font-variant-numeric:tabular-nums;margin-top:4px}
.pill{display:inline-block;font:500 10px var(--mono);text-transform:uppercase;letter-spacing:.05em;padding:1px 6px;border-radius:999px;border:1px solid currentColor}
.pill.solid{color:var(--init)}.pill.some{color:var(--sent)}.pill.thin{color:var(--duel)}
h3{margin:0 0 8px;font:600 18px var(--display);letter-spacing:.06em;text-transform:uppercase}
.alts{display:flex;flex-direction:column;gap:6px}
.alt{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px 12px;display:flex;flex-wrap:wrap;gap:4px 14px;font-size:14px}
.alt .sc{font:500 12px var(--mono);color:var(--mut);margin-left:auto}
.alt span b{font-weight:600}
.scroll{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:8px}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
th,td{padding:8px 12px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap;vertical-align:top}
tr:last-child td{border-bottom:0}
th{font:500 11px var(--mono);color:var(--mut);text-transform:uppercase;letter-spacing:.06em}
td .s{color:var(--mut);font:500 11px var(--mono)}
td.pn{font-weight:600}
.note{color:var(--mut);font-size:13px;max-width:72ch;margin:26px 0 0}
@media (prefers-reduced-motion:no-preference){.cards .card{animation:in .25s ease both}}
@keyframes in{from{transform:translateY(4px)}to{transform:none}}
</style>

<main class="wrap">
  <header class="top"><!--LOGO--><div class="topt"><h1>Comp Picker</h1><p class="lead" id="lead"></p></div></header>
  <div class="grid">
    <section class="panel" aria-label="Lineup">
      <div>
        <label class="lbl" for="map">Map</label>
        <select id="map"></select>
      </div>
      <div>
        <div class="lbl"><span>Players this week</span><span class="count" id="count"></span></div>
        <ul class="plist" id="players"></ul>
      </div>
      <div class="quick"><button type="button" id="clear">Clear</button></div>
      <div>
        <label class="lbl" for="igl">IGL for this map</label>
        <select id="igl" class="sm"></select>
      </div>
      <div>
        <label class="lbl" for="metaW">Follow the pro meta</label>
        <select id="metaW" class="sm">
          <option value="0">Off: only our own stats</option>
          <option value="1">Normal</option>
          <option value="2">Strong</option>
        </select>
      </div>
      <div class="opts">
        <label class="chk"><input type="checkbox" id="iglDuel" checked><span>Keep the IGL on controller or initiator where possible</span></label>
        <label class="chk"><input type="checkbox" id="groupOnly"><span>Only count games where 3+ of us played together</span></label>
      </div>
    </section>
    <section class="results" id="results" aria-live="polite"></section>
  </div>
  <p class="note"><b>How it picks.</b> Each player gets a score per agent on the chosen map from our own games: half
  estimated win rate, half impact (ACS, K/D and ADR compared with others on the same role). Recent games count more
  (a game's weight halves every 90 days) and small samples are pulled toward the player's overall form, so a 3–0 streak
  doesn't dominate. An agent needs 6+ games, some of them recent, to be considered. The win rate, ACS and K/D on
  each card are also weighted toward recent games.<br><br>
  <b>Pro meta.</b> The team's role structure must be one that pros actually run on this map (at least 3% of games), and rarer
  structures cost points. Double sentinel is normal on Sunset but almost never seen on Haven or Lotus. Agents pros pick
  on the map get a bonus, and matching a top pro comp adds a little more. <span id="metaSrc"></span><br><br>
  <b>Picking by hand.</b> Bored of the same agent? Use the dropdown on a player's card. That agent is locked in for this
  map and the other players' agents are re-picked around it (the role-setup rule still applies). Agents someone hasn't
  played are scored from their overall form, so they look average rather than bad. Undo or "Back to recommended"
  removes the change.<br><br>
  <b>IGL.</b> Pro IGLs mostly play controller (42% of rounds) or initiator (40%), rarely sentinel (11%) and almost never
  duelist (7%). So an IGL on duelist costs 4 points and on sentinel 1.5. <i>Auto</i> tries each usual caller in the lineup
  (everyone, if none is playing) and suggests the one whose best comp scores highest.</p>
</main>

<script>
const DATA = __DATA__;
const META = DATA.meta || {maps: {}, pool: []};
const ROLES = DATA.roles, LETTER = {Duelist: "D", Initiator: "I", Controller: "C", Sentinel: "S"};
const ORDER = {Duelist: 0, Initiator: 1, Controller: 2, Sentinel: 3};
const REQ = ["Duelist", "Initiator", "Controller", "Sentinel"];
const MIN_AGENT_GAMES = 6, MIN_RECENT = 1.5, MIN_STRUCT_SHARE = 0.03;
// Pro IGLs (VLR team captains, 2026) play controller 42%, initiator 40%, sentinel 11%, duelist 7% of rounds.
const IGL_PENALTY = {Duelist: 4, Sentinel: 1.5};
const CALLERS = new Set((DATA.callers || []).map(id => DATA.players.indexOf(id)).filter(i => i >= 0));
const short = id => id.split("#")[0];
const ICONS = DATA.icons || {};
const icon = (a, cls = "ai") => ICONS[a] ? `<img class="${cls}" src="https://media.valorant-api.com/agents/${ICONS[a]}/displayicon.png" alt="" loading="lazy" onerror="this.remove()">` : "";
const tagOf = id => id.split("#")[1] || "";
const esc = s => String(s).replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
const roleOf = a => ROLES[a] || "Flex";
const shrink = (t, n, prior, k) => (t + k * prior) / (n + k);

// ---------- our own players
const models = {};
function model(groupOnly) {
  if (models[groupOnly]) return models[groupOnly];
  // rows: [player, agent, map, squad-in-match, games, weighted games, weighted wins, weighted ACS, weighted K/D, weighted ADR]
  const rows = DATA.rows.filter(r => !groupOnly || r[3] >= 3);
  const tot = {n: 0, w: 0, acs: 0, kd: 0, adr: 0}, byRole = {};
  for (const [p, a, m, g, n, wn, w, acs, kd, adr] of rows)
    for (const t of [tot, byRole[roleOf(a)] ||= {n: 0, w: 0, acs: 0, kd: 0, adr: 0}]) { t.n += n; t.w += wn; t.acs += acs; t.kd += kd; t.adr += adr; }
  const gb = {acs: tot.acs / tot.w, kd: tot.kd / tot.w, adr: tot.adr / tot.w}, base = {};
  for (const [k, t] of Object.entries(byRole))
    base[k] = t.n >= 15 && t.w > 0 ? {acs: t.acs / t.w || gb.acs, kd: t.kd / t.w || gb.kd, adr: t.adr / t.w || gb.adr} : gb;
  const S = new Map(), agents = {};
  const add = (k, n, wn, w, imp, acs, kd) => {
    const s = S.get(k) || {n: 0, wn: 0, won: 0, imp: 0, acs: 0, kd: 0};
    s.n += n; s.wn += wn; s.won += w; s.imp += imp; s.acs += acs; s.kd += kd; S.set(k, s);
  };
  for (const [p, a, m, g, n, wn, w, acs, kd, adr] of rows) {
    const b = base[roleOf(a)], imp = 0.5 * acs / b.acs + 0.25 * kd / b.kd + 0.25 * adr / b.adr;
    for (const k of ["G", `P|${p}`, `PA|${p}|${a}`, `PM|${p}|${m}`, `PAM|${p}|${a}|${m}`]) add(k, n, wn, w, imp, acs, kd);
    const ag = (agents[p] ||= {})[a] ||= {n: 0, wn: 0};
    ag.n += n; ag.wn += wn;
  }
  return models[groupOnly] = {S, agents};
}

function estimate(M, p, a, m) {
  const z = {n: 0, wn: 0, won: 0, imp: 0}, G = M.S.get("G"), get = k => M.S.get(k) || z;
  const P = get(`P|${p}`), PA = get(`PA|${p}|${a}`), PM = get(`PM|${p}|${m}`), X = get(`PAM|${p}|${a}|${m}`);
  const pwr = shrink(P.won, P.wn, G.won / G.wn, 20), pimp = shrink(P.imp, P.wn, G.imp / G.wn, 20);
  const pawr = shrink(PA.won, PA.wn, pwr, 12), paimp = shrink(PA.imp, PA.wn, pimp, 12);
  const pmwr = shrink(PM.won, PM.wn, pwr, 10), pmimp = shrink(PM.imp, PM.wn, pimp, 10);
  const twr = Math.min(Math.max(pawr + (pmwr - pwr), 0.05), 0.95), timp = Math.max(paimp + (pmimp - pimp), 0.3);
  const wr = shrink(X.won, X.wn, twr, 5), imp = shrink(X.imp, X.wn, timp, 5);
  return {agent: a, role: roleOf(a), score: 100 * (0.5 * wr + 0.25 * imp), nMap: X.n, wnMap: X.wn, nAgent: PA.n,
          wrMap: X.wn > 0.05 ? X.won / X.wn : null, pro: proPick(m, a)};
}

function options(M, p, m) {
  const pool = M.agents[p] || {};
  let agents = Object.keys(pool).filter(a => pool[a].n >= MIN_AGENT_GAMES && pool[a].wn >= MIN_RECENT);
  if (!agents.length) agents = Object.keys(pool).sort((x, y) => pool[y].wn - pool[x].wn).slice(0, 3);
  return agents.map(a => estimate(M, p, a, m)).sort((x, y) => y.score - x.score);
}

// ---------- pro meta
const proPick = (m, a) => { const mm = META.maps[m]; return mm ? (mm.ag[a] || [0])[0] / mm.n : null; };
const agentMeta = (m, a) => { const p = proPick(m, a); return p == null ? 0 : 8 * Math.sqrt(p) - 3; };
const structKey = picks => { const c = {D: 0, I: 0, C: 0, S: 0}; picks.forEach(o => { const l = LETTER[o.role]; if (l) c[l]++; }); return `${c.D}${c.I}${c.C}${c.S}`; };
const structText = k => [["duelist", k[0]], ["initiator", k[1]], ["controller", k[2]], ["sentinel", k[3]]]
  .filter(([, n]) => +n).map(([r, n]) => `${n} ${r}${+n > 1 ? "s" : ""}`).join(" · ");
function structInfo(m, key) {
  const mm = META.maps[m]; if (!mm) return null;
  const top = Math.max(...Object.values(mm.st).map(v => v[0]));
  const share = (mm.st[key] || [0])[0] / mm.n;
  return {share, allowed: share >= MIN_STRUCT_SHARE, bonus: share > 0 ? Math.max(-8, 4 * Math.log(share / (top / mm.n))) : -10};
}
function closestPro(m, agents) {
  const mm = META.maps[m]; if (!mm) return null;
  let best = null;
  for (const [c, n, w] of mm.cp) {
    const set = c.split(","), overlap = set.filter(a => agents.includes(a)).length;
    if (!best || overlap > best.overlap || (overlap === best.overlap && n > best.n)) best = {comp: set, n, w, overlap};
  }
  return best;
}

// locks: {player index: agent} chosen by hand; the rest of the team is rebuilt around them
function compositions(M, m, players, igl, pen, metaW, locks = {}) {
  const taken = new Set(players.filter(p => locks[p]).map(p => locks[p]));
  const opts = players.map(p => {
    if (locks[p]) return [estimate(M, p, locks[p], m)];
    let o = options(M, p, m).filter(x => !taken.has(x.agent)).slice(0, 8);
    if (!o.length) {  // their usual agents are all locked by teammates: fall back to anything they've played
      const pool = M.agents[p] || {};
      o = Object.keys(pool).filter(a => !taken.has(a)).sort((x, y) => pool[y].wn - pool[x].wn).slice(0, 4)
        .map(a => estimate(M, p, a, m)).sort((x, y) => y.score - x.score);
    }
    return o;
  });
  const hasMeta = !!META.maps[m] && metaW > 0;
  for (const mode of hasMeta ? ["meta", "loose"] : ["roles", "controller", "loose"]) {
    const found = [], pick = [], used = new Set();
    (function rec(i, total) {
      if (i === players.length) {
        let bonus = 0, st = null;
        if (hasMeta && players.length === 5) {
          st = structInfo(m, structKey(pick));
          if (mode === "meta" && !st.allowed) return;
          const cp = closestPro(m, pick.map(o => o.agent));
          bonus = metaW * (pick.reduce((s, o) => s + agentMeta(m, o.agent), 0) / 5 + st.bonus +
                           (cp && cp.n >= 5 ? (cp.overlap === 5 ? 1.5 : cp.overlap === 4 ? 0.75 : 0) : 0));
        } else {
          const roles = new Set(pick.map(o => o.role));
          if (mode === "roles" && !REQ.every(r => roles.has(r))) return;
          if (mode === "controller" && !roles.has("Controller")) return;
        }
        found.push({total: total / players.length + bonus, own: total / players.length, bonus, st, picks: pick.map((o, j) => ({p: players[j], o}))});
        return;
      }
      for (const o of opts[i]) {
        if (used.has(o.agent)) continue;
        used.add(o.agent); pick.push(o);
        rec(i + 1, total + o.score - (players[i] === igl ? (pen[o.role] || 0) : 0));
        pick.pop(); used.delete(o.agent);
      }
    })(0, 0);
    if (found.length) { found.sort((a, b) => b.total - a.total); return {comps: found.slice(0, 3), mode, opts}; }
  }
  return {comps: [], mode: null, opts};
}

const conf = w => w >= 4 ? ["solid", "solid data"] : w >= 1.5 ? ["some", "some data"] : ["thin", "thin recent data"];
const pct = x => x == null ? "–" : Math.round(x * 100) + "%";

// ---------- UI
const $ = id => document.getElementById(id);
const dataMaps = new Set(DATA.rows.map(r => r[2]));
const poolMaps = META.pool.filter(m => dataMaps.has(m)).sort();
const otherMaps = [...dataMaps].filter(m => !poolMaps.includes(m)).sort();
const allMaps = [...poolMaps, ...otherMaps];
let state = {map: allMaps.includes("Haven") ? "Haven" : allMaps[0], sel: [], iglDuel: true, groupOnly: false, iglByMap: {}, metaW: 1, locks: {}};
try { const s = JSON.parse(localStorage.getItem("compPicker2") || "null"); if (s && allMaps.includes(s.map)) state = {...state, ...s, iglByMap: s.iglByMap || {}, locks: s.locks || {}}; } catch (e) {}
const AGENTS = Object.keys(ROLES).sort();
const locksFor = players => { const L = state.locks[state.map] || {}, out = {}; for (const p of players) if (L[p]) out[p] = L[p]; return out; };
if (!state.sel.length) state.sel = [0, 1, 3, 6, 8].filter(i => i < DATA.players.length);
const save = () => { try { localStorage.setItem("compPicker2", JSON.stringify(state)); } catch (e) {} };

$("lead").textContent = `Pick the map, this week's five and who calls. Our data: each player's recent competitive games on tracker.gg (as of ${DATA.asOf}), weighted toward recent games.`;
if (META.source) $("metaSrc").textContent = `Source: ${META.source}`;
const opt = m => `<option value="${esc(m)}">${esc(m)}</option>`;
$("map").innerHTML = (poolMaps.length ? `<optgroup label="Current competitive pool">${poolMaps.map(opt).join("")}</optgroup>` : "") +
  (otherMaps.length ? `<optgroup label="Out of rotation">${otherMaps.map(opt).join("")}</optgroup>` : "");
$("players").innerHTML = DATA.players.map((id, i) =>
  `<li><label><input type="checkbox" id="p${i}" value="${i}"><span class="nm">${esc(short(id))} <span class="tagline">#${esc(tagOf(id))}</span></span></label></li>`).join("");

function sync() {
  $("map").value = state.map;
  DATA.players.forEach((_, i) => $("p" + i).checked = state.sel.includes(i));
  $("iglDuel").checked = state.iglDuel; $("groupOnly").checked = state.groupOnly; $("metaW").value = String(state.metaW);
}

// Dropdown on each card: the player's own agents first (best on this map first), then everything else.
function agentPicker(M, p, current, players, locks) {
  const takenBy = {}; for (const q of players) if (q !== p && locks[q]) takenBy[locks[q]] = q;
  const pool = M.agents[p] || {};
  const mine = Object.keys(pool).map(a => estimate(M, p, a, state.map)).sort((x, y) => y.score - x.score);
  const mineSet = new Set(mine.map(x => x.agent));
  const optn = (a, label) => `<option value="${esc(a)}"${a === current ? " selected" : ""}${takenBy[a] != null ? " disabled" : ""}>${esc(label)}${takenBy[a] != null ? ` (${esc(short(DATA.players[takenBy[a]]))})` : ""}</option>`;
  const locked = locks[p] != null;
  return `<div class="swap"><label for="sw${p}">${locked ? "Picked by hand" : "Play something else"}</label>
    <div class="swaprow"><select id="sw${p}" class="swapsel" data-p="${p}">
      <optgroup label="${esc(short(DATA.players[p]))}'s agents">${mine.map(x => optn(x.agent, `${x.agent} · ${x.nAgent} game${x.nAgent === 1 ? "" : "s"}`)).join("")}</optgroup>
      <optgroup label="Other agents">${AGENTS.filter(a => !mineSet.has(a)).map(a => optn(a, `${a} · new`)).join("")}</optgroup>
    </select>${locked ? `<button type="button" class="unlock" data-p="${p}" aria-label="Undo ${esc(short(DATA.players[p]))}'s pick" title="Undo">Undo</button>` : ""}</div></div>`;
}

// Try each candidate as IGL; keep the one whose best comp scores highest
// (ties: the IGL on controller, then initiator). Usual callers first, everyone if none is playing.
const ROLE_PREF = {Controller: 0, Initiator: 1, Sentinel: 2, Duelist: 3};
function suggestIgl(M, m, players, pen, metaW, locks = {}) {
  const callers = players.filter(p => CALLERS.has(p)), pool = callers.length ? callers : players;
  let best = null;
  for (const p of pool) {
    const r = compositions(M, m, players, p, pen, metaW, locks);
    if (!r.comps.length) continue;
    const role = r.comps[0].picks.find(x => x.p === p).o.role;
    const key = r.comps[0].total - (ROLE_PREF[role] ?? 4) * 0.01;
    if (!best || key > best.key) best = {igl: p, key, role, r, fromCallers: callers.length > 0};
  }
  return best;
}

function renderIglSelect(players) {
  const sel = $("igl");
  if (!players.length) { sel.innerHTML = `<option>Tick players first</option>`; sel.disabled = true; return {mode: "none"}; }
  sel.disabled = false;
  const saved = state.iglByMap[state.map];
  const mode = saved === "none" ? "none" : players.includes(saved) ? "manual" : "auto";
  sel.innerHTML = `<option value="auto">Auto: suggest the best caller</option><option value="none">Not set</option>` +
    players.map(p => `<option value="${p}">${esc(short(DATA.players[p]))}${CALLERS.has(p) ? " (usual caller)" : ""}</option>`).join("");
  sel.value = mode === "manual" ? String(saved) : mode;
  return {mode, igl: mode === "manual" ? saved : null};
}

function render() {
  const n = state.sel.length, out = $("results");
  const picked = DATA.players.map((_, i) => i).filter(i => state.sel.includes(i));
  const iglSel = renderIglSelect(picked);
  $("count").textContent = `${n} / 5`;
  $("count").className = "count" + (n === 5 ? "" : " bad");
  if (n < 2) { out.innerHTML = `<div class="msg">Tick at least two players (five for a full team) to see a composition for ${esc(state.map)}.</div>`; return; }
  if (n > 5) { out.innerHTML = `<div class="msg">${n} players ticked. Untick ${n - 5} so there are five.</div>`; return; }
  const M = model(state.groupOnly), players = picked;
  const noData = players.filter(p => !(M.S.get(`P|${p}`) || {}).n);
  if (noData.length) { out.innerHTML = `<div class="msg">No games for ${noData.map(p => esc(short(DATA.players[p]))).join(", ")} with this filter. Turn off “Only count games where 3+ of us played together”.</div>`; return; }
  const pen = state.iglDuel ? IGL_PENALTY : {};
  const locks = locksFor(players), nLocks = Object.keys(locks).length;
  const solve = L => {
    if (iglSel.mode === "auto") {
      const s = suggestIgl(M, state.map, players, pen, state.metaW, L);
      return {sugg: s, igl: s ? s.igl : null, result: s ? s.r : compositions(M, state.map, players, null, {}, state.metaW, L)};
    }
    const g = iglSel.igl ?? null;
    return {sugg: null, igl: g, result: compositions(M, state.map, players, g, iglSel.mode === "manual" ? pen : {}, state.metaW, L)};
  };
  let {sugg, igl, result} = solve(locks);
  const recommended = nLocks ? solve({}).result.comps[0] : null;
  const {comps, mode} = result;
  const opts = players.map(p => options(M, p, state.map));
  if (!comps.length) { out.innerHTML = `<div class="msg">Not enough agent data to build a composition.</div>`; return; }
  const byRole = picks => [...picks].sort((a, b) => (ORDER[a.o.role] ?? 9) - (ORDER[b.o.role] ?? 9));
  const best = comps[0];
  const iglRole = igl != null ? (best.picks.find(x => x.p === igl) || {o: {}}).o : null;
  const iglLine = igl == null ? `IGL: <span class="warn">not set</span>`
    : `IGL: <b>${esc(short(DATA.players[igl]))}</b>${iglRole && iglRole.agent ? ` on ${esc(iglRole.agent)}` : ""}` +
      (sugg ? ` <span class="sugg">suggested${sugg.fromCallers ? "" : ", none of the usual callers is playing"}</span>` : "");
  let html = `<div class="head"><h2>${esc(state.map)}</h2><span class="igl">${iglLine}</span></div>`;
  if (n < 5) html += `<p class="warn">${n} players ticked. Showing a ${n}-player partial composition; pro-meta checks need all five.</p>`;
  if (mode === "loose" && META.maps[state.map] && state.metaW > 0 && n === 5)
    html += `<p class="warn">These players' agent pools can't make a role structure pros use on ${esc(state.map)}, so this is the closest possible.</p>`;
  else if (mode === "loose" || mode === "controller") html += `<p class="warn">These agent pools can't cover all four roles, so the role rule was relaxed.</p>`;
  if (nLocks && recommended) {
    const d = best.total - recommended.total;
    html += `<div class="changes"><span>${nLocks} hand-picked agent${nLocks > 1 ? "s" : ""}; the rest of the team was rebuilt around ${nLocks > 1 ? "them" : "it"}.
      ${Math.abs(d) < 0.05 ? "Same team score as the recommended comp." : `Team score ${d > 0 ? "+" : ""}${d.toFixed(1)} vs. the recommended comp.`}</span>
      <button type="button" id="resetLocks">Back to recommended</button></div>`;
  }
  if (best.st) html += `<div class="struct">${structText(structKey(best.picks.map(x => x.o)))} · pros run this on ${pct(best.st.share)} of ${esc(state.map)} games</div>`;
  html += `<div class="cards">` + byRole(best.picks).map(({p, o}) => {
    const [cls, txt] = conf(o.wnMap);
    const A = M.S.get(`PA|${p}|${o.agent}`) || {n: 0, wn: 0, won: 0, acs: 0, kd: 0};
    const f = (x, d) => A.wn > 0.05 ? (x / A.wn).toFixed(d) : "–";
    return `<div class="card ${esc(o.role)}">
      <div class="ch">${icon(o.agent)}<div style="min-width:0"><div class="role">${esc(o.role)}</div><div class="agent">${esc(o.agent)}</div>
        <div class="who">${esc(short(DATA.players[p]))}${p === igl ? '<span class="iglb">IGL</span>' : ""}</div></div></div>
      <div class="kvh">${esc(short(DATA.players[p]))} on ${esc(o.agent)}, all maps</div>
      <div class="kv"><div><b>${A.n}</b><span>Games</span></div><div><b>${A.wn > 0.05 ? pct(A.won / A.wn) : "–"}</b><span>Win rate</span></div>
        <div><b>${f(A.acs, 0)}</b><span>ACS</span></div><div><b>${f(A.kd, 2)}</b><span>K/D</span></div></div>
      <div class="here">On ${esc(state.map)}: ${o.nMap} game${o.nMap === 1 ? "" : "s"} · WR ${pct(o.wrMap)}${o.pro != null ? ` · pros pick it ${pct(o.pro)}` : ""}</div>
      <div class="foot"><span class="pill ${cls}">${txt}</span><span class="sc">score ${o.score.toFixed(1)}</span></div>
      ${agentPicker(M, p, o.agent, players, locks)}</div>`;
  }).join("") + `</div>`;
  const cp = closestPro(state.map, best.picks.map(x => x.o.agent));
  if (cp && n === 5) html += `<div class="procomp"><span class="k">Closest pro comp</span><span>${cp.comp.map(esc).join(" · ")}</span><span class="match">${cp.overlap}/5 same · pros ${cp.w}–${cp.n - cp.w}</span></div>`;
  if (comps.length > 1) {
    html += `<div><h3>Alternatives</h3><div class="alts">` + comps.slice(1).map(c =>
      `<div class="alt">${byRole(c.picks).map(({p, o}) => `<span>${icon(o.agent, "ai sm")}<b>${esc(o.agent)}</b> ${esc(short(DATA.players[p]))}</span>`).join("")}<span class="sc">${c.total.toFixed(1)}</span></div>`
    ).join("") + `</div></div>`;
  }
  html += `<div><h3>Everyone's best agents on ${esc(state.map)}</h3><div class="scroll"><table><thead><tr><th>Player</th><th>1st</th><th>2nd</th><th>3rd</th><th>4th</th></tr></thead><tbody>` +
    players.map((p, i) => `<tr><td class="pn">${esc(short(DATA.players[p]))}</td>` +
      [0, 1, 2, 3].map(k => { const o = opts[i][k]; return o ? `<td>${icon(o.agent, "ai sm")}${esc(o.agent)} <span class="s">${o.score.toFixed(0)} · ${o.nMap}g${o.pro != null ? ` · pro ${pct(o.pro)}` : ""}</span></td>` : "<td></td>"; }).join("") + `</tr>`
    ).join("") + `</tbody></table></div></div>`;
  out.innerHTML = html;
}

$("map").addEventListener("change", e => { state.map = e.target.value; save(); render(); });
$("players").addEventListener("change", e => {
  const i = +e.target.value;
  state.sel = e.target.checked ? [...new Set([...state.sel, i])] : state.sel.filter(x => x !== i);
  save(); render();
});
$("igl").addEventListener("change", e => { const v = e.target.value; if (v === "auto") delete state.iglByMap[state.map]; else state.iglByMap[state.map] = v === "none" ? "none" : +v; save(); render(); });
$("metaW").addEventListener("change", e => { state.metaW = +e.target.value; save(); render(); });
$("iglDuel").addEventListener("change", e => { state.iglDuel = e.target.checked; save(); render(); });
$("groupOnly").addEventListener("change", e => { state.groupOnly = e.target.checked; save(); render(); });
$("clear").addEventListener("click", () => { state.sel = []; sync(); save(); render(); });
$("results").addEventListener("change", e => {
  if (!e.target.classList.contains("swapsel")) return;
  const L = state.locks[state.map] ||= {};
  L[+e.target.dataset.p] = e.target.value; save(); render();
});
$("results").addEventListener("click", e => {
  const b = e.target.closest("button"); if (!b) return;
  if (b.id === "resetLocks") { delete state.locks[state.map]; save(); render(); }
  else if (b.classList.contains("unlock")) { const L = state.locks[state.map] || {}; delete L[+b.dataset.p]; save(); render(); }
});
sync(); render();
</script>
"""


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--squad", default=os.path.join(here, "squad.json"), help="roster file (default squad.json)")
        p.add_argument("--data", default=os.path.join(here, "data", "squad_data.json"),
                       help="compact stats file written by fetch (default data/squad_data.json)")
        p.add_argument("--meta", default=os.path.join(here, "meta.json"), help="pro-meta file (default meta.json)")

    p = sub.add_parser("fetch", help="download tracker.gg match history and rebuild the data file")
    common(p)
    p.add_argument("--cache", default=os.path.join(here, "cache"), help="raw download cache (default ./cache)")
    p.add_argument("--source", choices=["auto", "henrik", "tracker"], default="auto",
                   help="auto (default): HenrikDev API if HENRIKDEV_API_KEY is set, else tracker.gg")
    p.add_argument("--region", default="eu", help="HenrikDev region: eu, na, ap, kr, latam, br (default eu)")
    p.add_argument("--history", default=os.path.join(here, "data", "history"),
                   help="where HenrikDev match history accumulates (default data/history, committed)")
    p.add_argument("--min-history", type=int, default=40,
                   help="keep a player's previous numbers until they have this many games (default 40)")
    p.add_argument("--pages", type=int, default=8, help="tracker.gg match-history pages per player, 20 games each (default 8)")
    p.add_argument("--delay", type=float, default=2.0, help="seconds between requests (default 2)")
    p.add_argument("--half-life", type=float, default=90, help="days until a game counts half (default 90, 0 = no weighting)")
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("rebuild", help="recompute the stats file from data/history without fetching")
    common(p)
    p.add_argument("--cache", default=os.path.join(here, "cache"), help="raw download cache (default ./cache)")
    p.add_argument("--history", default=os.path.join(here, "data", "history"), help="match history folder")
    p.add_argument("--min-history", type=int, default=40, help="keep previous numbers below this many games")
    p.add_argument("--half-life", type=float, default=90, help="days until a game counts half (default 90)")
    p.set_defaults(func=cmd_rebuild)

    p = sub.add_parser("comp", help="best composition for the chosen players on a map")
    common(p)
    p.add_argument("--map", required=True, help="map name, e.g. haven")
    p.add_argument("--players", required=True, help='comma-separated names, e.g. "merhaba, bigbeko, kivo, unsent, pasha"')
    p.add_argument("--igl", default="auto",
                   help='who calls: a player name, "auto" (default: suggest the best caller) or "none"')
    p.add_argument("--meta-weight", type=float, default=1.0, help="how much the pro meta counts: 0 off, 1 normal, 2 strong")
    p.add_argument("--lock", help='hand-picked agents, e.g. "sriderya=Jett, unsent=Killjoy"; the rest is picked around them')
    p.add_argument("--alts", type=int, default=2, help="alternative compositions to show (default 2)")
    p.add_argument("--igl-duelist-penalty", type=float, default=4.0, help="score penalty for the IGL on duelist (default 4)")
    p.add_argument("--igl-sentinel-penalty", type=float, default=1.5, help="score penalty for the IGL on sentinel (default 1.5)")
    p.add_argument("--min-agent-games", type=int, default=6, help="min games on an agent to consider it (default 6)")
    p.add_argument("--markdown", action="store_true", help="print Markdown (used by the GitHub pipeline)")
    p.add_argument("--summary", help="also append the output to this file (the pipeline passes $GITHUB_STEP_SUMMARY)")
    p.set_defaults(func=cmd_comp)

    p = sub.add_parser("ui", help="write the interactive picker page")
    common(p)
    p.add_argument("--out", default="valo_ui.html", help="output file (default valo_ui.html)")
    p.add_argument("--open", action="store_true", help="open the page in your browser afterwards")
    p.set_defaults(func=cmd_ui)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
