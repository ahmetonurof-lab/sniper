/**
 * jCodeMunch — Risk Gutter Decorations
 *
 * Paints coloured dots (yellow / orange / red) in the editor gutter
 * at the line of each function/method that has a non-trivial risk score.
 * Green-risk symbols are intentionally invisible (no decoration).
 *
 * On hover, shows a detailed breakdown of why the symbol scored that way.
 */

import * as vscode from "vscode";
import * as cp from "child_process";
import type { DebounceEntry, SymbolRisk, RiskLevel } from "./types";

// ── SVG dot icons (inline data URIs) ─────────────────────────────────

const DOT_URI_YELLOW = vscode.Uri.parse(
  "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12'%3E%3Ccircle cx='6' cy='6' r='4' fill='%23eab308'/%3E%3C/svg%3E"
);
const DOT_URI_ORANGE = vscode.Uri.parse(
  "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12'%3E%3Ccircle cx='6' cy='6' r='4' fill='%23f97316'/%3E%3C/svg%3E"
);
const DOT_URI_RED = vscode.Uri.parse(
  "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12'%3E%3Ccircle cx='6' cy='6' r='4' fill='%23ef4444'/%3E%3C/svg%3E"
);

// ── Decoration Types ─────────────────────────────────────────────────

const decorationTypes: Record<RiskLevel, vscode.TextEditorDecorationType | null> = {
  green: null, // invisible — no decoration for safe symbols
  yellow: vscode.window.createTextEditorDecorationType({
    gutterIconPath: DOT_URI_YELLOW,
    gutterIconSize: "contain",
    overviewRulerColor: "#eab308",
    overviewRulerLane: vscode.OverviewRulerLane.Left,
  }),
  orange: vscode.window.createTextEditorDecorationType({
    gutterIconPath: DOT_URI_ORANGE,
    gutterIconSize: "contain",
    overviewRulerColor: "#f97316",
    overviewRulerLane: vscode.OverviewRulerLane.Left,
  }),
  red: vscode.window.createTextEditorDecorationType({
    gutterIconPath: DOT_URI_RED,
    gutterIconSize: "contain",
    overviewRulerColor: "#ef4444",
    overviewRulerLane: vscode.OverviewRulerLane.Left,
  }),
};

// ── Helpers ──────────────────────────────────────────────────────────

function runRiskSymbolsCommand(
  commandPath: string,
  filePath: string
): Promise<SymbolRisk[]> {
  return new Promise((resolve, reject) => {
    const args = ["risk-symbols", filePath, "--format", "json"];
    cp.execFile(commandPath, args, { timeout: 15_000 }, (err, stdout, stderr) => {
      if (err) {
        cp.execFile(
          process.platform === "win32" ? "npx.cmd" : "npx",
          [commandPath, ...args],
          { timeout: 15_000 },
          (err2, stdout2, stderr2) => {
            if (err2) {
              reject(new Error(stderr2 || err2.message));
            } else {
              try {
                const parsed = JSON.parse(stdout2);
                resolve(parsed.symbols ?? parsed ?? []);
              } catch {
                reject(new Error("Failed to parse risk-symbols output"));
              }
            }
          }
        );
      } else {
        try {
          const parsed = JSON.parse(stdout);
          resolve(parsed.symbols ?? parsed ?? []);
        } catch {
          reject(new Error("Failed to parse risk-symbols output"));
        }
      }
    });
  });
}

/** Build hover message from a SymbolRisk */
function buildHover(symbol: SymbolRisk): vscode.MarkdownString {
  const md = new vscode.MarkdownString();
  md.isTrusted = true;
  md.supportHtml = true;

  const emoji: Record<RiskLevel, string> = {
    green: "🟢",
    yellow: "🟡",
    orange: "🟠",
    red: "🔴",
  };

  md.appendMarkdown(`**${emoji[symbol.risk]} \`${symbol.name}\`**  \n`);
  md.appendMarkdown(`Risk: **${symbol.riskScore.toFixed(2)}** / 1.00  \n`);
  md.appendMarkdown(`Kind: \`${symbol.kind}\`  \n`);

  if (symbol.reasons.length > 0) {
    md.appendMarkdown(`\n---\n**Why?**  \n`);
    for (const reason of symbol.reasons) {
      md.appendMarkdown(`- ${reason}  \n`);
    }
  }

  return md;
}

// ── Main ─────────────────────────────────────────────────────────────

const debounceMap = new Map<string, DebounceEntry>();

export async function refreshRiskGutter(
  editor: vscode.TextEditor | undefined,
  commandPath: string
): Promise<void> {
  if (!editor) return;

  const filePath = editor.document.uri.fsPath;
  if (!filePath) return;

  try {
    const symbols = await runRiskSymbolsCommand(commandPath, filePath);

    for (const deco of Object.values(decorationTypes)) {
      if (deco) editor.setDecorations(deco, []);
    }

    const yellowLines: vscode.DecorationOptions[] = [];
    const orangeLines: vscode.DecorationOptions[] = [];
    const redLines: vscode.DecorationOptions[] = [];

    for (const sym of symbols) {
      if (sym.risk === "green") continue;

      const lineNumber = Math.max(0, sym.line - 1);
      if (lineNumber >= editor.document.lineCount) continue;

      const range = new vscode.Range(lineNumber, 0, lineNumber, 0);
      const hover = buildHover(sym);
      const opts: vscode.DecorationOptions = { range, hoverMessage: hover };

      switch (sym.risk) {
        case "yellow":
          yellowLines.push(opts);
          break;
        case "orange":
          orangeLines.push(opts);
          break;
        case "red":
          redLines.push(opts);
          break;
      }
    }

    if (decorationTypes.yellow) editor.setDecorations(decorationTypes.yellow, yellowLines);
    if (decorationTypes.orange) editor.setDecorations(decorationTypes.orange, orangeLines);
    if (decorationTypes.red) editor.setDecorations(decorationTypes.red, redLines);
  } catch (err) {
    console.error(`[jCodeMunch] riskGutter failed for ${filePath}:`, err);
  }
}

export function activateRiskGutter(context: vscode.ExtensionContext): void {
  const config = vscode.workspace.getConfiguration("jcodemunch");
  const enabled = config.get<boolean>("riskGutter.enabled", true);
  if (!enabled) return;

  const commandPath = config.get<string>("indexOnSave.command", "jcodemunch-mcp");
  const debounceMs = config.get<number>("riskGutter.debounceMs", 600);

  context.subscriptions.push(
    vscode.window.onDidChangeActiveTextEditor((editor) => {
      if (editor) {
        refreshRiskGutter(editor, commandPath);
      }
    })
  );

  context.subscriptions.push(
    vscode.workspace.onDidSaveTextDocument((doc) => {
      const editor = vscode.window.activeTextEditor;
      if (!editor || editor.document.uri.fsPath !== doc.uri.fsPath) return;

      const existing = debounceMap.get(doc.uri.fsPath);
      if (existing) clearTimeout(existing.timeout);

      const timeout = setTimeout(() => {
        refreshRiskGutter(editor, commandPath);
        debounceMap.delete(doc.uri.fsPath);
      }, debounceMs);

      debounceMap.set(doc.uri.fsPath, { timeout, lastSaved: Date.now() });
    })
  );

  if (vscode.window.activeTextEditor) {
    refreshRiskGutter(vscode.window.activeTextEditor, commandPath);
  }
}

export function deactivateRiskGutter(): void {
  for (const [, entry] of debounceMap) {
    clearTimeout(entry.timeout);
  }
  debounceMap.clear();

  for (const deco of Object.values(decorationTypes)) {
    if (deco) deco.dispose();
  }
}
