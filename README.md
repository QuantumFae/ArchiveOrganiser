# Archive Organiser

A **private, local** desktop app to help you clean up personal files spread across external HDDs, USBs, and SD cards.

- Finds **exact duplicates** (same file contents)
- Suggests a **tidy folder layout** for mixed files (photos, videos, documents, etc.)
- Uses **quarantine** instead of permanent delete
- Does **not** upload your files anywhere

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
1. Click **Add folder / drive** for each messy location (HDD, USB, SD mount, or folder).
2. On Linux, mounts are often under `/media/YOURNAME/` or `/mnt/`.
3. Click **Scan now** and wait for the status bar to finish.

### 3. Overview tab
- Check counts by category and total size.
- Optionally click **Save report…** to keep a text summary.
- Click **Find duplicates**.

### 4. Duplicates tab
- Left: **every duplicate group** in the full found list.
- Click a group → **side-by-side cards** on the right for each copy.
- Each card shows:
  - **Content preview** (images and many text files; other types show info only)
  - **File information** (path, size, dates, category, KEEP vs copy)
  - A **checkbox** to select that file
- Extras are pre-selected; the oldest KEEP file is not.
- **Quarantine selected** — safe remove (recommended)
- **Permanently delete selected** — erases from disk after **two** confirmations
- **Quarantine all extras (every group)** — clears extras across the whole list at once

Quarantined files go to:

`ArchiveOrganiser_Quarantine/<date_time>/`

Each session includes a `manifest.json` listing original paths. Restore by moving files back manually.

### 5. Organise tab
1. Choose a **destination** folder (ideally empty / a tidy drive).
2. Keep **Dry run only** ticked and click **Preview plan**.
3. Read the planned moves.
4. When ready, untick Dry run and click **Apply moves**.

Suggested layout:

```text
Destination/
  Photos/YYYY/MM/
  Videos/YYYY/MM/
  Audio/YYYY/MM/
  Documents/pdf/  (and other extensions)
  Archives/
  Other/
```

---

## Safety tips

| Do | Don’t |
|----|--------|
| Preview / dry run first | Permanently delete until you trust the results |
| Quarantine duplicates | Point organise destination at a source drive by accident without checking the plan |
| Keep originals until you verify | Unplug a drive mid-scan / mid-move |
| Re-scan after big changes | Assume same filename = same file (this app checks content) |

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
