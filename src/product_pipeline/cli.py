import argparse
import json
import sys
from pathlib import Path

import yaml

from .contracts import Contract, ExtractionResult, Recipe, SourceSpec, TargetSchema, TaxonomySpec
from .engine import extract, replay
from .errors import PipelineError


def read_document(path):
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="product-pipeline")
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate", help="validate a recipe without fetching a source")
    validate.add_argument("recipe")
    schema = commands.add_parser("schema", help="print a public JSON Schema")
    schema.add_argument("contract", choices=["recipe", "source", "target", "taxonomy"])
    execute = commands.add_parser("extract", help="extract an offline canonical artifact")
    execute.add_argument("recipe")
    execute.add_argument("snapshot")
    execute.add_argument("--source-url", default="")
    execute.add_argument("--workspace", default="default")
    execute.add_argument("--output")
    repeat = commands.add_parser("replay", help="verify an offline result")
    repeat.add_argument("recipe")
    repeat.add_argument("snapshot")
    repeat.add_argument("expected")
    repeat.add_argument("--source-url", default="")
    repeat.add_argument("--workspace", default="default")
    commands.add_parser("init-db", help="initialize an alpha development database")
    commands.add_parser("migrate", help="upgrade the database with versioned migrations")
    admin = commands.add_parser(
        "admin-bootstrap", help="create or recover a local administrator token"
    )
    admin.add_argument("--recover", action="store_true")
    serve = commands.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    worker = commands.add_parser("worker")
    worker.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "schema":
            models: dict[str, type[Contract]] = {
                "recipe": Recipe,
                "source": SourceSpec,
                "target": TargetSchema,
                "taxonomy": TaxonomySpec,
            }
            model = models[args.contract]
            print(json.dumps(model.model_json_schema(), indent=2))
        elif args.command in {"validate", "extract", "replay"}:
            recipe = Recipe.model_validate(read_document(args.recipe))
            if args.command == "validate":
                print("Recipe is valid.")
                return 0
            snapshot = Path(args.snapshot).read_bytes()
            if args.command == "replay":
                expected = ExtractionResult.model_validate(read_document(args.expected))
                result = replay(
                    snapshot,
                    recipe,
                    expected,
                    workspace_id=args.workspace,
                    source_url=args.source_url,
                )
            else:
                result = extract(
                    snapshot, recipe, workspace_id=args.workspace, source_url=args.source_url
                )
            output = result.model_dump_json(indent=2)
            if getattr(args, "output", None):
                Path(args.output).write_text(output, encoding="utf-8")
            else:
                print(output)
            return 1 if result.quarantined else 0
        else:
            from .server.db import Database
            from .server.settings import Settings

            settings = Settings.from_env()
            database = Database(settings.database_url)
            if args.command == "init-db":
                database.initialize()
                print("Development database initialized. Production upgrades must use migrations.")
            elif args.command == "migrate":
                from alembic import command
                from alembic.config import Config

                config = Config()
                config.set_main_option(
                    "script_location", str(Path(__file__).parent / "server" / "migrations")
                )
                config.attributes["database_url"] = settings.database_url
                command.upgrade(config, "head")
            elif args.command == "admin-bootstrap":
                if not sys.stdin.isatty():
                    raise PipelineError(
                        "TTY_REQUIRED", "bootstrap requires a local interactive terminal"
                    )
                from .server.auth import issue_token

                with database.sessions.begin() as session:
                    token = issue_token(session, settings.workspace, "bootstrap", args.recover)
                print("Store this administrator token securely; it is shown once:\n" + token)
            elif args.command == "serve":
                import uvicorn

                from .server.api import create_app

                uvicorn.run(create_app(settings, database), host=args.host, port=args.port)
            elif args.command == "worker":
                from .server.worker import Worker

                instance = Worker(database, settings.store(database))
                instance.once() if args.once else instance.run_forever()
        return 0
    except (PipelineError, ValueError, OSError) as exc:
        print(
            json.dumps(exc.problem() if isinstance(exc, PipelineError) else {"error": str(exc)}),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
