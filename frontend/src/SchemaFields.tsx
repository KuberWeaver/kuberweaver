import type { Schema } from "./model";
export function SchemaFields({
  schema,
  value,
  onChange,
  disabled = false,
  prefix = "config",
}: {
  schema: Schema;
  value: Record<string, unknown>;
  onChange: (value: Record<string, unknown>) => void;
  disabled?: boolean;
  prefix?: string;
}) {
  return (
    <div className="schema-fields">
      {Object.entries(schema.properties || {}).map(([key, field]) => {
        const id = `${prefix}-${key}`,
          current =
            value[key] ??
            field.default ??
            (field.type === "boolean"
              ? false
              : field.type === "array"
                ? []
                : "");
        const update = (v: unknown) => onChange({ ...value, [key]: v });
        const required = schema.required?.includes(key);
        return (
          <div className="field" key={key}>
            <label htmlFor={id}>
              {field.title || key}
              {required && <span className="required"> *</span>}
            </label>
            {field.enum ? (
              <select
                id={id}
                disabled={disabled}
                value={String(current)}
                onChange={(e) =>
                  update(
                    field.enum!.find((v) => String(v) === e.target.value) ??
                      e.target.value,
                  )
                }
              >
                {!required && <option value="">Sélectionner</option>}
                {field.enum.map((option) => (
                  <option key={String(option)} value={String(option)}>
                    {String(option)}
                  </option>
                ))}
              </select>
            ) : field.type === "boolean" ? (
              <label className="toggle">
                <input
                  id={id}
                  type="checkbox"
                  disabled={disabled}
                  checked={Boolean(current)}
                  onChange={(e) => update(e.target.checked)}
                />
                <span>{current ? "Activé" : "Désactivé"}</span>
              </label>
            ) : field.type === "array" && field.items?.enum ? (
              <div className="checks">
                {field.items.enum.map((option) => (
                  <label key={String(option)}>
                    <input
                      type="checkbox"
                      disabled={disabled}
                      checked={
                        Array.isArray(current) && current.includes(option)
                      }
                      onChange={(e) =>
                        update(
                          e.target.checked
                            ? [
                                ...(Array.isArray(current) ? current : []),
                                option,
                              ]
                            : (Array.isArray(current) ? current : []).filter(
                                (v) => v !== option,
                              ),
                        )
                      }
                    />
                    {String(option)}
                  </label>
                ))}
              </div>
            ) : field.type === "array" ? (
              <input
                id={id}
                disabled={disabled}
                value={
                  Array.isArray(current) ? current.join(", ") : String(current)
                }
                placeholder="app, branch-client"
                onChange={(e) =>
                  update(
                    e.target.value
                      .split(",")
                      .map((v) => v.trim())
                      .filter(Boolean),
                  )
                }
              />
            ) : field.type === "object" ? (
              <SchemaFields
                schema={field}
                value={(current || {}) as Record<string, unknown>}
                onChange={update}
                disabled={disabled}
                prefix={id}
              />
            ) : (
              <input
                id={id}
                type={
                  field.type === "number" || field.type === "integer"
                    ? "number"
                    : "text"
                }
                required={required}
                disabled={disabled}
                min={field.minimum}
                max={field.maximum}
                step={field.type === "integer" ? 1 : "any"}
                value={String(current)}
                onChange={(e) =>
                  update(
                    field.type === "number" || field.type === "integer"
                      ? e.target.value === ""
                        ? ""
                        : Number(e.target.value)
                      : e.target.value,
                  )
                }
              />
            )}
            {field.description && (
              <small id={`${id}-help`}>{field.description}</small>
            )}
          </div>
        );
      })}
    </div>
  );
}
