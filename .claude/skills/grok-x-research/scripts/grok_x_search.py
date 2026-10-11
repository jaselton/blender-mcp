#!/usr/bin/env python3
"""Ask Grok (xAI) a research question using its live X search tool.

Standard library only. Reads the API key from XAI_API_KEY.

Usage:
    grok_x_search.py "What are people saying about Blender 5 geometry nodes?"
    grok_x_search.py --handles BlenderDev,blender --from 2026-10-01 "release news"
    grok_x_search.py --web "Topic that benefits from web results too"
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

API_URL = "https://api.x.ai/v1/responses"
DEFAULT_MODEL = "grok-4.7"


def build_request(args):
    x_search = {"type": "x_search"}
    if args.handles:
        x_search["allowed_x_handles"] = _split(args.handles)[:10]
    elif args.exclude_handles:
        x_search["excluded_x_handles"] = _split(args.exclude_handles)[:10]
    if args.from_date:
        x_search["from_date"] = args.from_date
    if args.to_date:
        x_search["to_date"] = args.to_date

    tools = [x_search]
    if args.web:
        tools.append({"type": "web_search"})

    return {
        "model": args.model,
        "input": [
            {
                "role": "system",
                "content": (
                    "You are a research assistant with live access to X. Search X "
                    "for the question, then answer concisely with specifics: who "
                    "said what, when, and links to the posts. Say clearly when you "
                    "found nothing relevant rather than guessing."
                ),
            },
            {"role": "user", "content": args.question},
        ],
        "tools": tools,
    }


def _split(value):
    return [h.strip().lstrip("@") for h in value.split(",") if h.strip()]


def extract(response):
    """Pull the answer text and cited URLs out of a Responses API payload."""
    texts, urls = [], []
    for item in response.get("output", []):
        if item.get("type") != "message":
            continue
        for part in item.get("content", []):
            if part.get("type") == "output_text":
                texts.append(part.get("text", ""))
                for ann in part.get("annotations", []) or []:
                    if ann.get("url"):
                        urls.append(ann["url"])
    for cite in response.get("citations", []) or []:
        urls.append(cite if isinstance(cite, str) else cite.get("url", ""))
    seen = set()
    urls = [u for u in urls if u and not (u in seen or seen.add(u))]
    return "\n".join(texts).strip(), urls


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("question")
    parser.add_argument("--handles", help="Comma-separated X handles to restrict to (max 10)")
    parser.add_argument("--exclude-handles", help="Comma-separated X handles to exclude (max 10)")
    parser.add_argument("--from", dest="from_date", help="Start date, YYYY-MM-DD")
    parser.add_argument("--to", dest="to_date", help="End date, YYYY-MM-DD")
    parser.add_argument("--web", action="store_true", help="Also allow web search")
    parser.add_argument("--model", default=os.environ.get("GROK_MODEL", DEFAULT_MODEL))
    parser.add_argument("--timeout", type=int, default=300, help="Seconds to wait")
    parser.add_argument("--raw", action="store_true", help="Print the raw JSON response")
    args = parser.parse_args()

    key = os.environ.get("XAI_API_KEY")
    if not key:
        sys.exit("XAI_API_KEY is not set. Add it to the environment's secrets.")

    req = urllib.request.Request(
        API_URL,
        data=json.dumps(build_request(args)).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=args.timeout) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as e:
        sys.exit(f"xAI API error {e.code}: {e.read().decode(errors='replace')[:2000]}")
    except urllib.error.URLError as e:
        sys.exit(f"Could not reach xAI API: {e.reason}")

    if args.raw:
        print(json.dumps(payload, indent=2))
        return

    text, urls = extract(payload)
    print(text or "(Grok returned no text; rerun with --raw to inspect the response.)")
    if urls:
        print("\nSources:")
        for u in urls:
            print(f"- {u}")


if __name__ == "__main__":
    main()
