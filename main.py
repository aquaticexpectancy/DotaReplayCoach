"""CLI: python main.py <match_id> --account <steam32-or-64> [--open]

Thin wrapper over pipeline.generate (the same code path the web app uses).
"""
from __future__ import annotations
import argparse
import os
import webbrowser
from dotenv import load_dotenv

import pipeline


def main():
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("match_id", type=int)
    ap.add_argument("--account", type=int, required=True,
                    help="your Steam ID (32-bit account id or 64-bit SteamID)")
    ap.add_argument("--open", action="store_true", help="open the report in a browser")
    ap.add_argument("--force-parse", action="store_true",
                    help="re-request the OpenDota parse and rebuild even if cached")
    ap.add_argument("--no-wait-parse", action="store_true",
                    help="don't wait for an OpenDota parse (faster, maybe partial)")
    ap.add_argument("--coach", action="store_true",
                    help="add AI coaching (needs ANTHROPIC_API_KEY)")
    args = ap.parse_args()

    try:
        res = pipeline.generate(
            args.match_id, args.account,
            wait_parse=not args.no_wait_parse,
            force=args.force_parse,
            coach=args.coach,
        )
    except pipeline.PipelineError as e:
        print(f"Error: {e}")
        return

    print(f"Report -> {res['path']}"
          + (" (partial data)" if res.get("partial") else ""))
    if args.open:
        webbrowser.open("file://" + os.path.abspath(res["path"]))


if __name__ == "__main__":
    main()
