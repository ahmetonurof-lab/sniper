/**
 * jCodeMunch MCP — VS Code Extension Entry Point
 *
 * Features:
 * 1. **Auto-reindex on save** — debounced `jcodemunch-mcp index-file`
 * 2. **Risk Gutter** — coloured dots at risky function/method headers
 *
 * Fully compatible with Cline (`.clinerules/`) and Continue (`.continue/rules/`)
 * via shared jcodemunch-mcp tool configurations.
 */

import * as vscode from "vscode";
import { activateIndexOnSave, deactivateIndexOnSave, runIndexCommand } from "./indexOnSave";
import { activateRiskGutter, deactivateRiskGutter, refreshRiskGutter } from "./riskGutter";

export function activate(context: vscode.ExtensionContext): void {
  console.log("[jCodeMunch] Activating v0.2.0 ...");

  // ── 1. Auto-reindex on save ──
  activateIndexOnSave(context);

  // ── 2. Risk Gutter decorations ──
  activateRiskGutter(context);

  // ── 3. Manual command: reindex current file ──
  context.subscriptions.push(
    vscode.commands.registerCommand("jcodemunch.reindexFile", async () => {
      const editor = vscode.window.activeTextEditor;
      if (!editor) {
        vscode.window.showWarningMessage("jCodeMunch: No active editor.");
        return;
      }

      const config = vscode.workspace.getConfiguration("jcodemunch");
      const commandPath = config.get<string>("indexOnSave.command", "jcodemunch-mcp");
      const filePath = editor.document.uri.fsPath;

      try {
        await runIndexCommand(commandPath, filePath);
        vscode.window.showInformationMessage(`jCodeMunch: reindexed ${filePath}`);
      } catch (err) {
        vscode.window.showErrorMessage(
          `jCodeMunch: reindex failed — ${err instanceof Error ? err.message : String(err)}`
        );
      }
    })
  );

  // ── 4. Manual command: refresh risk gutter ──
  context.subscriptions.push(
    vscode.commands.registerCommand("jcodemunch.refreshRiskGutter", async () => {
      const editor = vscode.window.activeTextEditor;
      if (!editor) {
        vscode.window.showWarningMessage("jCodeMunch: No active editor.");
        return;
      }

      const config = vscode.workspace.getConfiguration("jcodemunch");
      const commandPath = config.get<string>("indexOnSave.command", "jcodemunch-mcp");

      try {
        await refreshRiskGutter(editor, commandPath);
        vscode.window.showInformationMessage("jCodeMunch: risk gutter refreshed");
      } catch (err) {
        vscode.window.showErrorMessage(
          `jCodeMunch: risk refresh failed — ${err instanceof Error ? err.message : String(err)}`
        );
      }
    })
  );

  console.log("[jCodeMunch] Activated.");
}

export function deactivate(): void {
  deactivateIndexOnSave();
  deactivateRiskGutter();
  console.log("[jCodeMunch] Deactivated.");
}
