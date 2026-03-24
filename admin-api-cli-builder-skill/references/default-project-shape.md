# Default Project Shape

Prefer this layout unless the user asks for something else:

```text
project/
├── .env
├── README.md
├── tool.py
└── outputs/
    ├── run-result.csv
    ├── invite-result.csv
    └── activate-result.csv
```

## Conventions

- Python by default
- `.env` in project root
- one CLI entry with subcommands
- fixed output filenames under `outputs/`
- auth logic isolated in one client class
- batch/polling logic separated from auth

## Command pattern

```bash
python3 tool.py run --count 10
python3 tool.py invite --count 20
python3 tool.py activate --input outputs/invite-result.csv
```
