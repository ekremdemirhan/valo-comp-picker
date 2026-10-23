# Valorant comp picker

## 👉 [Open the Comp Picker](https://ekremdemirhan.github.io/valo-comp-picker/)

`https://ekremdemirhan.github.io/valo-comp-picker/`

On a phone, if this link opens the GitHub app instead of the page, copy the address above into your browser (Chrome or Safari).

Picks the best agent composition for the five of us on a given map, from everyone's recent competitive
games on tracker.gg plus what pros run on that map (VCT 2026 data from VLR.gg).

## For the squad: how to use it

**The page (easiest).** Open the Pages link (Settings → Pages shows it). Pick the map, tick this week's
five, choose the IGL. The comp updates instantly. Works on phones, and nobody needs a GitHub account.

**The pipeline.** Actions tab → **Pick a comp** → **Run workflow**. Choose the map, tick five players,
pick the IGL and how much to follow the pro meta, then **Run workflow**. After ~20 seconds, open the
run; the comp is on its summary page. Running it needs write access to this repo (ask to be added
as a collaborator).

**Bored of an agent?** Each player's card on the page has a dropdown: pick another agent and the rest of
the team is re-picked around it (Undo or "Back to recommended" reverts). In the pipeline, use the
"Hand-picked agents" field, e.g. `Sriderya=Jett, unsent181=Killjoy`.

The data refreshes every Monday morning by itself.

## How a comp is scored

**Player score** (one player, one agent, one map) = 50 × estimated win rate + 25 × impact.
Impact compares ACS (half), K/D and ADR (a quarter each) with everyone in the squad on the same role; 1.0 is
average. So an average player on an agent scores **50**, and 1 point = 2 percentage points of win rate.
Recent games count more (weight halves every 90 days), and small samples are pulled toward the player's form on
that agent and that map, so a short lucky run doesn't dominate. An agent needs 6+ games to be suggested.

**Team score** = average of the five player scores + pro meta − IGL role penalty.

- Pro meta (VCT 2026, × the "Follow the pro meta" setting: Off 0, Normal 1, Strong 2): agent fit −3…+5 per agent
  (averaged), role setup 0 for the most common pro setup down to −8 (setups under 3% of pro games aren't allowed),
  +1.5 / +0.75 for matching all five / four agents of a top pro comp.
- IGL role: −4 on the IGL's score on duelist, −1.5 on sentinel (pro IGLs play controller 42% and initiator 40% of
  rounds, sentinel 11%, duelist 7%).

**Reading differences:** under 1 point ≈ the same, 1–3 a small edge, 3+ a clear difference. On our data the best and
second-best comp are usually ~0.4 apart; the best beats a typical valid comp for the same five by ~5.7. It ranks
comps; it isn't a win probability.

## Plays for each map

Under the cards there's a **Plays** list: 5 attack plays and 5 defense setups for every map in the current pool
(Abyss, Bind, Corrode, Haven, Lotus, Summit, Sunset). They don't depend on agents: each play has a short **call word**
(e.g. `A SPLIT`, `C GO`) and a small map showing where to **reveal**, **smoke** and **flash**, which way the team goes in
(and how many each way), and where to trap or watch the flank. On defense it shows the 5 positions (with the role that
usually plays each) and where to reveal, trap, flash and smoke. Who throws what is up to you. No lineups needed.

The plays live in `plays.json`; edit callouts or add your own there. If you add one that needs a lineup, set
`"lineup": true` and its name gets "(lineup)" on the page.

## Setup (once)

1. Make this a **public** repo (Pages and Actions are free for public repos on a free account).
2. **Settings → Pages → Build and deployment → Source: GitHub Actions.**
3. **Settings → Actions → General → Workflow permissions: Read and write**, so the Monday refresh
   can commit new data.
4. Actions tab → **Refresh data and publish page** → **Run workflow** once to publish the page.
5. Add friends who should run the pipeline: **Settings → Collaborators**.

## Changing things

- **Add or remove a player:** edit `squad.json` (and `callers` there if they plan rounds), and add/remove their checkbox and line in
  `.github/workflows/comp.yml`. Then run **Refresh data and publish page**.
- **New pro meta** (after a big patch or event): `meta.json` holds role setups, agent pick rates and
  top comps per map. Ask Claude to refresh it from VLR.gg, or edit by hand.

## Weekly data refresh: HenrikDev API key (one-time, free)

tracker.gg blocks GitHub's servers, so the Monday refresh uses the free HenrikDev Valorant API instead:

1. Go to https://api.henrikdev.xyz/dashboard/, sign in, open **API Keys** and generate a key
   (a basic key is enough for 9 players once a week).
2. In this repo: **Settings → Secrets and variables → Actions → New repository secret**.
   Name: `HENRIKDEV_API_KEY`, value: the key.
3. Actions → **Refresh data and publish page** → **Run workflow** to test it. The run summary says if
   anything failed.

Match history builds up in `data/history/` every week (it was seeded with each player's last 160
tracker.gg games, so picks don't start from scratch). `python valo_planner.py rebuild` recomputes the
stats from that folder without fetching. Until a player has 40 games there, their previous
numbers are kept. Everyone is looked up in the `eu` region; change `--region` in the workflow if that's wrong.

## If the Monday refresh can't reach tracker.gg

tracker.gg sits behind Cloudflare, which sometimes blocks GitHub's servers. The page still publishes
with the last good data, and the run summary says so. To refresh from your own PC:

```
pip install -r requirements.txt
python valo_planner.py fetch
git add data/ && git commit -m "Refresh squad data" && git push
```

Profiles must be public on tracker.gg.

## Running it locally

```
python valo_planner.py comp --map haven --players "merhaba, bigbeko, sriderya, unsent, draven" --igl merhaba
python valo_planner.py ui --open
```

Names and maps can be typed loosely ("kivo", "unsent", "hav").
