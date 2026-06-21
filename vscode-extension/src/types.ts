/**
 * jCodeMunch — Shared type definitions
 */

/** Risk level returned by jcodemunch-mcp risk-score for a symbol */
export type RiskLevel = "green" | "yellow" | "orange" | "red";

/** A single symbol with its risk score */
export interface SymbolRisk {
  name: string;
  kind: "function" | "method" | "class" | "module";
  line: number;
  column: number;
  risk: RiskLevel;
  riskScore: number;       // 0–1
  reasons: string[];       // what contributed to the score
}

/** Response shape from `jcodemunch-mcp risk-symbols <file>` */
export interface RiskSymbolsResponse {
  file: string;
  symbols: SymbolRisk[];
}

/** Debounce entry — tracks pending timeouts per file */
export interface DebounceEntry {
  timeout: NodeJS.Timeout;
  lastSaved: number;
}
