import { it, expect } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { SchemaFields } from "./SchemaFields";
import type { Schema } from "./model";

it("affiche les libellés des enums sans remplacer les identifiants", () => {
  const schema = {
    properties: {
      storage: { type: "string", enum: ["store-17"], enumNames: ["Stockage principal"] },
      links: { type: "array", items: { type: "string", enum: ["net-42"], enumNames: ["Réseau atelier"] } },
    },
  } as Schema;
  const html = renderToStaticMarkup(
    <SchemaFields schema={schema} value={{ storage: "store-17", links: ["net-42"] }} onChange={() => {}} />,
  );
  expect(html).toContain('value="store-17"');
  expect(html).toContain("Stockage principal");
  expect(html).toContain("Réseau atelier");
  expect(html).toContain('checked=""');
});
it("génère les champs des plugins sans condition sur leur identifiant", () => {
  const html = renderToStaticMarkup(
    <SchemaFields
      schema={{
        properties: {
          flavor: { type: "string", enum: ["small", "large"] },
          port: { type: "number", minimum: 1 },
          enabled: { type: "boolean" },
          targets: {
            type: "array",
            items: { type: "string", enum: ["app", "branch-client"] },
          },
        },
      }}
      value={{ flavor: "small", port: 18090, enabled: true, targets: ["app"] }}
      onChange={() => {}}
      disabled={false}
    />,
  );
  expect(html).toContain("<select");
  expect(html).toContain('type="number"');
  expect(html).toContain('type="checkbox"');
  expect(html).toContain("branch-client");
  expect(html).toContain("18090");
});
it("rend tous les champs en lecture seule pour le viewer", () => {
  const html = renderToStaticMarkup(
    <SchemaFields
      schema={{ properties: { version: { type: "string" } } }}
      value={{ version: "1" }}
      onChange={() => {}}
      disabled
    />,
  );
  expect(html).toContain("disabled");
});
