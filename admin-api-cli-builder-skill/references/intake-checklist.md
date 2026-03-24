# Intake Checklist

Use this when the user has not yet provided enough detail.

## Environment

- `base_url`
- `org_uuid`
- `team_uuid`
- `region_uuid`

## Admin auth

- admin login endpoint
- whether password must be encrypted
- how token/session is obtained
- whether cookies are required
- whether HAR is available

## Business APIs

- endpoint URL
- method
- headers
- request payload
- sample success response
- sample failure response

## Batch behavior

- `--count` or file input
- random data rules if needed
- retries/polling
- output fields to keep

## Output expectations

- CLI commands needed
- default output filenames
- whether fixed files or timestamped files are desired
