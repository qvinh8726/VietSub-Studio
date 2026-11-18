import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

// typescript-eslint needs the TypeScript 6 API, so package.json installs
// @typescript/typescript6 under the "typescript" name; `npm run typecheck`
// still type-checks with TypeScript 7 (@typescript/native).
const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
    // Vendored, unmodified third-party script (see public/sakuga-vfx/README.md).
    "public/sakuga-vfx/**",
  ]),
]);

export default eslintConfig;
