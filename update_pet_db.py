"""Build the local pet database from metabot.gg (same as the tray's "Actualizar base de datos").

    python update_pet_db.py            pets, icons, sources, monsters and spawn maps (cached, resumable)
    python update_pet_db.py --refresh  ignore the per-page cache and download everything again
"""
import argparse
import sys

from pet_overlay import dbupdate
from pet_overlay.paths import DATA_DIR


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    last = [None]

    def report(stage, done, total):
        if stage != last[0]:
            if last[0]:
                sys.stdout.write("\n")
            last[0] = stage
        sys.stdout.write(f"\r{stage}: {done}/{total}")
        sys.stdout.flush()

    summary = dbupdate.run(DATA_DIR, refresh=args.refresh, report=report)
    print(f"\nmap_db.json: {summary}")


if __name__ == "__main__":
    main()
