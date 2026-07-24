import { readFile, writeFile } from "node:fs/promises"
import { fileURLToPath } from "node:url"

const generatedPath =
  process.argv[2] ?? fileURLToPath(new URL("../src/lib/api.generated.ts", import.meta.url))
const recursiveJsonValue = `        JsonValue: components["schemas"]["JsonScalar"] | components["schemas"]["JsonValue"][] | {
            [key: string]: components["schemas"]["JsonValue"];
        };`
const apiJsonValue = `export type ApiJsonValue =
    | string
    | number
    | boolean
    | null
    | ApiJsonValue[]
    | { [key: string]: ApiJsonValue };

export interface paths {`

const generated = await readFile(generatedPath, "utf8")
const jsonSafeGenerated = generated.includes(recursiveJsonValue)
  ? generated
      .replace("export interface paths {", apiJsonValue)
      .replace(recursiveJsonValue, "        JsonValue: ApiJsonValue;")
  : generated
const normalized = `// biome-ignore-all format: generated from the committed OpenAPI document
${jsonSafeGenerated}`
await writeFile(generatedPath, normalized, "utf8")
