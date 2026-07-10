# UX Rules

## User Mode vs Developer Mode

| Audience | Sees |
|----------|------|
| User | Progress, ETA, warnings (`/monitoring`) |
| Developer | Full pipeline, agents, LLM, plugins (`/dev/*`) |

## Principles

1. Users never see technical errors — show actionable warnings
2. Developer tools require `is_developer_session()`
3. Plugin permissions are visible and user-controllable
4. No automatic changes without developer approval

## Pages

- `/monitoring` — user progress dashboard
- `/dev/monitoring` — full system visibility
- `/plugins` — plugin management
- `/dev/panel` — developer panel
