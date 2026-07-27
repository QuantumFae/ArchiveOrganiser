# Archive Organiser

A **private, local** desktop app to help you clean up personal files spread across external HDDs, USBs, and SD cards.

- Finds **exact duplicates** (same file contents)
- Suggests a **tidy folder layout** for mixed files (photos, videos, documents, etc.)
- Uses **quarantine** instead of permanent delete
- Does **not** upload your files anywhere

**GitHub (account: QuantumFae):** https://github.com/QuantumFae/ArchiveOrganiser  
**Latest release:** https://github.com/QuantumFae/ArchiveOrganiser/releases/tag/v1.0.0

---

## Requirements

- Linux (also works on other desktops with Python + Tk)
- Python 3.10+
- External drives plugged in and visible in your file manager

---

## Install (one-time)

Open a terminal in this project folder and run:

```bash
cd /home/p/ArchiveOrganiser
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## Run the app

```bash
cd /home/p/ArchiveOrganiser
source .venv/bin/activate
python3 main.py
```

---

## How to use (recommended order)

### 1. Start small
Use a **test folder** with a few copied files first. Learn the buttons before scanning your whole archive.

### 2. Sources tab
1. Click **Add folder / drive** — a file-manager style window opens:
   - Left: Places (Home, Desktop, …) and mounted drives
   - Right: folders in the current location
   - Double-click a folder to open it; use **Up** / **Home** / path bar to navigate
   - Click **Select this folder** when you are inside the right place
2. Optionally tick:
   - **Include junk / system folders** (Trash, System Volume Information, dot-folders, …)
   - **Scan inside .zip archives** (off by default for huge drives; listings are capped)
3. On Linux, external drives often appear under Places as **Drive: …** (from `/media`, `/mnt`, `/run/media`).
4. Click **Scan now** and wait for the status bar to finish. Large libraries use an **SQLite index** so RAM stays under control.

### 3. Overview tab
- Check counts by category and total size.
- Optionally click **Save report…** to keep a text summary.
- Choose duplicate modes: **Exact**, **Similar photos**, **Similar docs**.
- Click **Find duplicates**.

### 4. Duplicates tab
- Left: **duplicate groups** (Exact / Photo≈ / Doc≈). Huge result sets load in pages (**Load more**).
- Click a group → **side-by-side cards** on the right for each copy.
- Every file gets a **best-effort preview** (image/page/frame/waveform/text) or a **type card + binary sample**.
- Prefer **Quarantine selected**. Permanent delete needs confirmations.

Quarantined files go to:

`ArchiveOrganiser_Quarantine/<date_time>/`

Each session includes a `manifest.json` listing original paths. Restore by moving files back manually.

### 5. Organise tab
1. Choose a **destination** folder (ideally empty / a tidy drive, outside your sources).
2. Pick a **pre-defined folder layout** or **Custom structure** and edit the tree/rules box:
   - Example rule: `Photos = MyArchive/Photos/{year}/{month}`
   - Placeholders: `{year}` `{month}` `{ext}` `{category}` `{name}`
3. Drag the **sashes** to resize options, preview, and plan panes.
4. Keep **Dry run only** ticked → **Preview plan** → **Browse dry-run…** (file-manager view of the plan).
5. When ready, untick Dry run and click **Apply organise**.

---

## Safety tips

| Do | Don’t |
|----|--------|
| Preview / dry run first | Permanently delete until you trust the results |
| Prefer **Copy** when building an archive | Point organise destination at a source drive by accident without checking the plan |
| Quarantine duplicates | Unplug a drive mid-scan / mid-copy |
| Keep originals until you verify copies | Assume same filename = same file (this app checks content) |
| Re-scan after big changes | Nest folders endlessly — keep trees shallow |

---

## Privacy

Everything runs on your computer. Scanning and hashing are local. There is no cloud account and no upload step for organising.

---

## First-draft limits (expected)

- “Remove selected line” removes the **last** source in the list (simple first version).
- Similar-but-not-identical photos (different resolution/edit) are **not** detected yet — only exact content matches.
- Very large libraries can take a long time to fingerprint; leave the window open.
- After quarantine or organise moves, run a **new scan** before trusting the overview again.

When you test, note what you want changed (wording, buttons, layout rules, etc.) and we can adjust.
