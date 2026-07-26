#!/usr/bin/env python3
"""Start Archive Organiser (local, private file organiser)."""

import ctk_fix
from gui import ArchiveOrganiserApp


def main() -> None:
    ctk_fix.apply()
    app = ArchiveOrganiserApp()
    app.mainloop()


if __name__ == "__main__":
    main()
