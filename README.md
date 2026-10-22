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

The data refreshes every Monday morning by itself.

## How a comp is scored

- **Our own games:** each player's score on an agent on that map is half estimated win rate, half
  impact (ACS, K/D and ADR compared with others on the same role). A game's weight halves every
  90 days. Small samples are pulled toward the player's overall form. An agent needs 6+ games, some
  of them recent, to count.
- **Pro meta:** the team's role setup must be one pros run on that map in at least 3% of games
  (so double sentinel is fine on Sunset but not on Haven), pro-picked agents get a bonus, and
  matching a top pro comp adds a little. "Follow the pro meta: Off" turns this off.
- **IGL:** the IGL is kept off duelist (a 4-point penalty) unless their duelist is clearly better.

## Setup (once)

1. Make this a **public** repo (Pages and Actions are free for public repos on a free account).
2. **Settings → Pages → Build and deployment → Source: GitHub Actions.**
3. **Settings → Actions → General → Workflow permissions: Read and write**, so the Monday refresh
   can commit new data.
4. Actions tab → **Refresh data and publish page** → **Run workflow** once to publish the page.
5. Add friends who should run the pipeline: **Settings → Collaborators**.

## Changing things

- **Add or remove a player:** edit `squad.json`, and add/remove their checkbox and line in
  `.github/workflows/comp.yml`. Then run **Refresh data and publish page**.
- **New pro meta** (after a big patch or event): `meta.json` holds role setups, agent pick rates and
  top comps per map. Ask Claude to refresh it from VLR.gg, or edit by hand.

## If the Monday refresh can't reach tracker.gg

tracker.gg sits behind Cloudflare, which sometimes blocks GitHub's servers. The page still publishes
with the last good data, and the run summary says so. To refresh from your own PC:

```
pip install -r requirements.txt
python valo_planner.py fetch
git add data/squad_data.json && git commit -m "Refresh squad data" && git push
```

Profiles must be public on tracker.gg.

## Running it locally

```
python valo_planner.py comp --map haven --players "merhaba, bigbeko, sriderya, unsent, draven" --igl merhaba
python valo_planner.py ui --open
```

Names and maps can be typed loosely ("kivo", "unsent", "hav").
