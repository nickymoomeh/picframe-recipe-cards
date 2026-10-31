import argparse
from dataclasses import replace
from datetime import UTC, datetime
import json
import logging
from pathlib import Path
import sys

from .config import load
from .model import Recipe
from .render import render
from . import service
from .storage import lock


def main(argv=None):
    parser = argparse.ArgumentParser(description="Offline-capable Good Food recipe cards")
    parser.add_argument("--config", help="TOML configuration (before command)")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("validate-config", "refresh", "generate", "queue", "tick", "status", "list", "preview"):
        sub = commands.add_parser(name)
        sub.add_argument("--dry-run", action="store_true", help="No network or state changes")
        if name in ("generate", "preview"):
            sub.add_argument("--output", default="recipe-preview.png", help="PNG outside live output directory")
        if name == "preview":
            sub.add_argument("--fixture", required=True, help="Local demonstration JSON manifest")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        cfg = load(args.config)
        now = datetime.now(UTC)
        # Dry runs never create state, acquire a lock, make HTTP calls or render files.
        if args.dry_run:
            result = service.tick(cfg, now, True) if args.command == "tick" else service.queue(cfg, now, True) if args.command == "queue" else {"dry_run": True, "command": args.command, "source_mode": cfg.source_mode}
        elif args.command == "validate-config":
            result = {"valid": True, "source_mode": cfg.source_mode}
        elif args.command == "status":
            result = service.status(cfg, now)
        elif args.command == "list":
            result = [{k: r.asdict()[k] for k in ("title", "url", "category")} for r in service.cached(cfg)["recipes"]]
        elif args.command in ("generate", "preview"):
            output = Path(args.output).expanduser().resolve()
            if output.suffix.lower() != ".png" or output.is_relative_to(cfg.output) or output.name.startswith("recipe-queued-"):
                raise ValueError("Preview must be a .png outside the live output directory")
            if args.command == "preview":
                fixture = Path(args.fixture).resolve()
                raw = json.loads(fixture.read_text())["recipes"][0]
                recipe = Recipe.parse(raw)
                if not recipe.demonstration:
                    raise ValueError("preview fixtures must set demonstration=true")
                recipe = replace(recipe, image=str((fixture.parent / recipe.image).resolve()))
            else:
                recipes = service.cached(cfg)["recipes"]
                if not recipes:
                    raise ValueError("No usable cached recipes; run refresh with approved local images first")
                recipe = recipes[0]
            render(cfg, recipe, output)
            result = {"path": str(output), "queued": False}
        else:
            with lock(cfg):
                if args.command == "refresh":
                    ok = service.refresh(cfg, now)
                    result = {"refreshed": ok}
                    print(json.dumps(result, indent=2))
                    return 0 if ok else 1
                if args.command == "queue":
                    result = service.queue(cfg, now)
                    print(json.dumps(result, indent=2))
                    return 0 if result["queued"] else 1
                result = service.tick(cfg, now)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        logging.error("%s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
