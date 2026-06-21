/**
 * jCodeMunch — Auto-reindex on file save
 *
 * Watches `workspace.onDidSaveTextDocument`, debounces per-file,
 * and spawns `jcodemunch-mcp index-file <path>` for modified files
 * that are not excluded by glob patterns.
 */

import * as vscode from "vscode";
import * as cp from "child_process";
import * as path from "path";
import type { DebounceEntry } from "./types";

// ── Helpers ──────────────────────────────────────────────────────────

function isExcluded(filePath: string, patterns: string[]): boolean {
  const workspaceFolders = vscode.workspace.workspaceFolders;
  if (!workspaceFolders) return false;

  for (const folder of workspaceFolders) {
    const relative = path.relative(folder.uri.fsPath, filePath);
    for (const pattern of patterns) {
      const normalized = pattern.replace(/\\/g, "/");
      const relNormalized = relative.replace(/\\/g, "/");

      if (normalized.startsWith("**/")) {
        const suffix = normalized.slice(3);
        if (relNormalized.includes(suffix)) return true;
      } else if (normalized.endsWith("/**")) {
        const prefix = normalized.slice(0, -3);
        if (relNormalized.startsWith(prefix)) return true;
      } else if (normalized.includes("*")) {
        const regex = new RegExp(
          "^" + normalized.replace(/\*\*/g, ".*").replace(/\*/g, "[^/]*") + "$"
        );
        if (regex.test(relNormalized)) return true;
      } else if (relNormalized === normalized) {
        return true;
      }
    }
  }
  return false;
}

export function runIndexCommand(
  commandPath: string,
  filePath: string
): Promise<string> {
  return new Promise((resolve, reject) => {
    const args = ["index-file", filePath];
    cp.execFile(commandPath, args, { timeout: 30_000 }, (err, stdout, stderr) => {
      if (err) {
        if ((err as NodeJS.ErrnoException).code === "ENOENT") {
          cp.execFile(
            process.platform === "win32" ? "npx.cmd" : "npx",
            [commandPath, ...args],
            { timeout: 30_000 },
            (err2, stdout2, stderr2) => {
              if (err2) {
                reject(new Error(stderr2 || err2.message));
              } else {
                resolve(stdout2);
              }
            }
          );
        } else {
          reject(new Error(stderr || err.message));
        }
      } else {
        resolve(stdout);
      }
    });
  });
}

// ── Main ─────────────────────────────────────────────────────────────

const debounceMap = new Map<string, DebounceEntry>();

export function activateIndexOnSave(context: vscode.ExtensionContext): void {
  const disposable = vscode.workspace.onDidSaveTextDocument((doc) => {
    const config = vscode.workspace.getConfiguration("jcodemunch");
    const enabled = config.get<boolean>("indexOnSave.enabled", true);
    if (!enabled) return;

    const filePath = doc.uri.fsPath;
    if (!filePath) return;

    const excludePatterns = config.get<string[]>("indexOnSave.exclude", []);
    if (isExcluded(filePath, excludePatterns)) return;

    const commandPath = config.get<string>("indexOnSave.command", "jcodemunch-mcp");
    const debounceMs = config.get<number>("indexOnSave.debounceMs", 500);

    const existing = debounceMap.get(filePath);
    if (existing) {
      clearTimeout(existing.timeout);
    }

    const timeout = setTimeout(async () => {
      const start = Date.now();
      try {
        const output = await runIndexCommand(commandPath, filePath);
        console.log(`[jCodeMunch] Indexed ${filePath} (${Date.now() - start}ms)`);
      } catch (err) {
        console.error(`[jCodeMunch] Index failed for ${filePath}:`, err);
        const msg = err instanceof Error ? err.message : String(err);
        vscode.window.showWarningMessage(
          `jCodeMunch: reindex failed for ${path.basename(filePath)} — ${msg}`,
          "Dismiss"
        );
      }
      debounceMap.delete(filePath);
    }, debounceMs);

    debounceMap.set(filePath, { timeout, lastSaved: Date.now() });
  });

  context.subscriptions.push(disposable);
}

export function deactivateIndexOnSave(): void {
  for (const [, entry] of debounceMap) {
    clearTimeout(entry.timeout);
  }
  debounceMap.clear();
}
