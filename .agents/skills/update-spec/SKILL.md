---
name: update-spec
description: Update project documentation (README.md and docs/system_specification.md) after implementing a new feature or making significant changes to the codebase. Use when the user wants to update specs, docs, or documentation to reflect code changes.
---

# Update Specification

Update `README.md` and `docs/system_specification.md` to reflect a newly implemented feature or significant codebase change.

**Feature/change to document:** $ARGUMENTS

## Process

### Phase 1: Understand the change

1. Read git diff or recent commits to understand what changed:
   - New files created
   - Modified files
   - New API endpoints
   - New database tables/models
   - New UI features
   - Changed behavior

2. Read the current documentation to understand existing structure:
   - `README.md` — English-facing project overview
   - `docs/system_specification.md` — Chinese (Traditional) detailed spec

### Phase 2: Update README.md

Update the following sections as needed (preserve existing content structure):

| Section | When to update |
|---------|---------------|
| Top description | If core capability changed |
| Architecture diagram | If new infrastructure component (DB, queue, etc.) |
| Features list | If new user-facing feature added |
| Quick Start | If setup steps changed |
| Adding a New ... | If workflow for adding things changed |
| Configuration | If new env vars added |
| Project Structure | If new files/directories added |

**README.md conventions:**
- Language: English
- Keep concise — link to spec for details
- Code examples use `bash` or `json` fenced blocks
- Architecture diagrams use ASCII art

### Phase 3: Update docs/system_specification.md

This is a comprehensive Chinese (Traditional) specification. Update these sections as needed:

| Section | Content |
|---------|---------|
| Header table | Bump version (patch for fixes, minor for features), update date to today |
| 1.2 Design goals | If a core design goal changed or was added |
| 2.2 Layer descriptions | If a new architectural layer was introduced |
| 3.1 Core dependencies | If new packages added |
| 3.3 Infrastructure | If new infrastructure components |
| 4.x Module specs | Add new subsection or update existing for changed modules |
| 5.x API endpoints | Add/update endpoint tables with method, path, auth, description |
| 6.1.x DB tables | Add new table schema if new ORM model created |
| 6.2 ER Diagram | Update ASCII ER diagram if new tables |
| 7.x Auth & RBAC | If permission model changed |
| 9.x SQL Definition System | If SQL definition workflow changed |
| 10.x Admin UI | If Admin UI pages/features changed |
| 12.x Deployment | If Docker/deployment changed |
| 13.x Security | If new security measures |
| 14 Config params | If new environment variables |
| 15 Project structure | If new files/directories added |

**system_specification.md conventions:**
- Language: Traditional Chinese (繁體中文)
- Version format: `X.Y.Z` (major.minor.patch)
- Date format: `YYYY-MM-DD`
- Tables use markdown pipe syntax
- API examples include full request/response JSON
- ASCII diagrams for architecture and ER
- Each section separated by `---` horizontal rule

### Phase 4: Consistency check

After editing, verify:

1. No stale references to old behavior (e.g., "YAML-driven" after migrating to DB)
2. Version number is consistent across files
3. New files mentioned in project structure sections
4. New env vars listed in configuration sections
5. API endpoint tables match actual router code

## Versioning guide

| Change type | Version bump | Example |
|-------------|-------------|---------|
| Bug fix, docs-only | Patch (x.y.Z) | 1.2.0 -> 1.2.1 |
| New feature, new endpoint | Minor (x.Y.0) | 1.2.0 -> 1.3.0 |
| Breaking API change | Major (X.0.0) | 1.2.0 -> 2.0.0 |

## Output

After completing all updates, provide a summary listing:
- Files modified
- Sections updated in each file
- Version change (old -> new)
