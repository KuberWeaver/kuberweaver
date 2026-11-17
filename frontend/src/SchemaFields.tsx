import type { ReactNode } from "react";
import type { Schema } from "./model";
export function SchemaFields({
  schema,
  value,
  onChange,
  disabled = false,
  prefix = "config",
  fieldAction,
}: {
  schema: Schema;
  value: Record<string, unknown>;
  onChange: (value: Record<string, unknown>) => void;
  disabled?: boolean;
  prefix?: string;
  fieldAction?: (field: Schema, update: (value: unknown) => void) => ReactNode;
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
        const selectedItems = Array.isArray(current)
          ? current.filter((item) => !field.items?.["x-options-source"] || field.items.enum?.includes(item))
          : [];
        const update = (v: unknown) => onChange({ ...value, [key]: v });
        const required = schema.required?.includes(key);
        const discovered = field["x-options-source"] !== undefined;
        return (
          <div className="field" key={key}>
            <label htmlFor={id}>
              {field.title || key}
              {required && <span className="required"> *</span>}
            </label>
            {field.enum ? (
              <select
                id={id}
                required={required}
                disabled={disabled}
                value={discovered && !field.enum.some((option) => String(option) === String(current)) ? "" : String(current)}
                onChange={(e) =>
                  update(
                    field.enum!.find((v) => String(v) === e.target.value) ??
                      e.target.value,
                  )
                }
              >
                {(!required || discovered) && <option value="">Select</option>}
                {field.enum.map((option, index) => (
                  <option key={String(option)} value={String(option)}>
                    {field.enumNames?.[index] ?? String(option)}
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
                <span>{current ? "Enabled" : "Disabled"}</span>
              </label>
            ) : field.type === "array" && field.items?.enum ? (
              <div className="checks">
                {field.items.enum.map((option, index) => (
                  <label key={String(option)}>
                    <input
                      type="checkbox"
                      disabled={disabled || (
                        selectedItems.includes(option)
                          ? selectedItems.length <= (field.minItems ?? 0)
                          : field.maxItems !== undefined && selectedItems.length >= field.maxItems
                      )}
                      checked={selectedItems.includes(option)}
                      onChange={(e) =>
                        update(
                          e.target.checked
                            ? [
                                ...selectedItems,
                                option,
                              ]
                            : selectedItems.filter(
                                (v) => v !== option,
                              ),
                        )
                      }
                    />
                    {field.items?.enumNames?.[index] ?? String(option)}
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
                fieldAction={fieldAction}
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
                pattern={field.pattern}
                ref={(input) => {
                  if (!input || !field.pattern) return;
                  try {
                    input.setCustomValidity(!input.value || new RegExp(`^(?:${field.pattern})$`, "u").test(input.value)
                      ? "" : "The value does not match the required format.");
                  } catch {
                    input.setCustomValidity("Validation format unavailable.");
                  }
                }}
                maxLength={field.maxLength}
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
            {fieldAction?.(field, update)}
            {field.description && (
              <small id={`${id}-help`}>{field.description}</small>
            )}
          </div>
        );
      })}
    </div>
  );
}
