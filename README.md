# Pokémonpris

Compares prices on Pokémon booster packs, booster boxes and Elite Trainer Boxes from 11 shops that sell to
Norway, and shows the 5 most valuable cards in each set. The website rebuilds itself twice a day on GitHub.

## Put it online (one time)

1. **Create a repository** on github.com: click **+** → **New repository**, name it `pokemonpris`,
   choose **Public**, and click **Create repository**.
2. **Upload the files**: on the new repository page click **uploading an existing file**, then drag in
   everything from this folder: `pokepris.py`, `README.md`, `.gitignore` and the `.github` folder.
   (If the `.github` folder doesn't show in File Explorer, turn on *View → Show → Hidden items*.)
   Click **Commit changes**.
3. **Turn on GitHub Pages**: **Settings** → **Pages** → under *Build and deployment*, set **Source** to
   **GitHub Actions**.
4. **Build the site the first time**: **Actions** tab → **Update Pokémonpris** → **Run workflow**.
   After 2-3 minutes the site is live at `https://<your-username>.github.io/pokemonpris/`.

After that it updates by itself every day at about 07:00 and 17:00.

## Connect pokemonpris.no

1. Buy the domain `pokemonpris.no` from a Norwegian registrar (for example Domeneshop or One.com).
2. In the registrar's DNS settings, add these records for `pokemonpris.no`:

   | Type | Name | Value |
   |------|------|-------|
   | A    | @    | 185.199.108.153 |
   | A    | @    | 185.199.109.153 |
   | A    | @    | 185.199.110.153 |
   | A    | @    | 185.199.111.153 |
   | CNAME | www | `<your-username>.github.io` |

3. On GitHub: **Settings** → **Pages** → **Custom domain**: type `pokemonpris.no` and click **Save**.
   When the check turns green, tick **Enforce HTTPS**. DNS changes can take up to a day.

## Run it on your own PC

```
python pokepris.py            # opens the price list in your browser
python pokepris.py --site     # the website version
```
