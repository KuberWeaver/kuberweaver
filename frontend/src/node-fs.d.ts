/* Vitest runs on Node, but the portal carries no Node type dependency for its
   production bundle. Only the theme contract test reads files from disk. */
declare module "node:fs" {
  export function readFileSync(path: string | URL, encoding: "utf8"): string;
}
