# jCodeMunch VS Code Extension — CLAUDE.md

## Build & Run
```bash
npm install          # dependencies
npm run compile      # tsc build → out/
npm run watch        # watch mode
npm run package      # vsce package → .vsix
```

## Debug
- VS Code: `F5` → Extension Development Host
- Extension output channel: `jCodeMunch` (Ctrl+Shift+U → drop-down)
- Settings prefix: `jcodemunch.*`

## Architecture
| File | Purpose |
|------|---------|
| `src/extension.ts` | Entry point + manual commands |
| `src/indexOnSave.ts` | Auto-reindex on save (debounced) |
| `src/riskGutter.ts` | Risk gutter decorations (coloured dots) |
| `src/types.ts` | Shared TypeScript types |

## Key Behaviours
- **Debounce**: Same file saving rapidly → single reindex call
- **Exclude globs**: `node_modules`, `.git`, `dist`, `build`, `.venv`, `__pycache__`, `*.min.*`
- **npx fallback**: If `jcodemunch-mcp` not on PATH, tries `npx jcodemunch-mcp`
- **Gutter colours**: Green (invisible) → Yellow 🟡 → Orange 🟠 → Red 🔴

## Commands (Command Palette)
- `jcodemunch.reindexFile` — reindex active file
- `jcodemunch.refreshRiskGutter` — refresh risk decorations
