import { useState } from "react";
import type { FormEvent } from "react";
import { Archive, Boxes, RefreshCw, ShieldCheck } from "lucide-react";
import type { User } from "./model";
import { api } from "./api";
import { pageHeading } from "./navigation";
import {
  Badge,
  Empty,
  ErrorNotice,
  Notice,
  PageTitle,
  ResourceBoundary,
  errorMessage,
  useResource,
} from "./ui";
import {
  artifactKindLabel,
  artifactKinds,
  blueprintLabel,
  canPublishOfferings,
  canRequestOffering,
  offeringRequestMessage,
  offeringTitleError,
  validateArtifact,
} from "./studio";
import type { BlueprintRecord, Offering, StudioArtifact } from "./studio";

export function EngineeringStudio({
  user,
  onOpenDeployments,
}: {
  user: User;
  onOpenDeployments?: () => void;
}) {
  const artifacts = useResource<StudioArtifact[]>("/studio/artifacts");
  const offerings = useResource<Offering[]>("/studio/offerings");
  const blueprints = useResource<BlueprintRecord[]>("/blueprints");

  const [kind, setKind] = useState<string>(artifactKinds[0].id);
  const [reference, setReference] = useState("");
  const [pin, setPin] = useState("");
  const [artifactBusy, setArtifactBusy] = useState(false);
  const [artifactError, setArtifactError] = useState("");
  const [artifactMessage, setArtifactMessage] = useState("");

  const [title, setTitle] = useState("");
  const [blueprintId, setBlueprintId] = useState("");
  const [publishBusy, setPublishBusy] = useState(false);
  const [publishError, setPublishError] = useState("");
  const [publishMessage, setPublishMessage] = useState("");

  const [requesting, setRequesting] = useState("");
  const [requestError, setRequestError] = useState("");
  const [requestMessage, setRequestMessage] = useState("");

  const authoring = canPublishOfferings(user.role);

  async function register(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const result = validateArtifact({ kind, reference, pin });
    if (!result.ok) {
      setArtifactError(result.error);
      setArtifactMessage("");
      return;
    }
    setArtifactBusy(true);
    setArtifactError("");
    setArtifactMessage("");
    try {
      const created = await api<StudioArtifact>("/studio/artifacts", {
        method: "POST",
        body: JSON.stringify(result.value),
      });
      setArtifactMessage(
        `Artifact registered: ${artifactKindLabel(created?.kind ?? result.value.kind)} ${created?.reference ?? result.value.reference}. Metadata only — nothing is fetched, built or monitored.`,
      );
      setReference("");
      setPin("");
      artifacts.refresh();
    } catch (error) {
      setArtifactError(errorMessage(error));
    } finally {
      setArtifactBusy(false);
    }
  }

  async function publish(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const clean = title.trim();
    const titleError = offeringTitleError(clean);
    if (titleError) {
      setPublishError(titleError);
      setPublishMessage("");
      return;
    }
    if (!blueprintId) {
      setPublishError("Choose a blueprint for the offering.");
      setPublishMessage("");
      return;
    }
    setPublishBusy(true);
    setPublishError("");
    setPublishMessage("");
    try {
      const created = await api<Offering>("/studio/offerings", {
        method: "POST",
        body: JSON.stringify({ title: clean, blueprint_id: blueprintId }),
      });
      setPublishMessage(`Published "${created.title}" (v${created.version}).`);
      setTitle("");
      offerings.refresh();
    } catch (error) {
      setPublishError(errorMessage(error));
    } finally {
      setPublishBusy(false);
    }
  }

  async function request(offering: Offering) {
    setRequesting(offering.id);
    setRequestError("");
    setRequestMessage("");
    try {
      const result = await api<{ job_id: string }>(
        `/studio/offerings/${encodeURIComponent(offering.id)}/request`,
        { method: "POST", body: JSON.stringify({}) },
      );
      setRequestMessage(offeringRequestMessage(offering.title, result.job_id));
    } catch (error) {
      setRequestError(errorMessage(error));
    } finally {
      setRequesting("");
    }
  }

  return (
    <>
      <PageTitle
        {...pageHeading("studio")}
        actions={
          <button
            onClick={() => {
              artifacts.refresh();
              offerings.refresh();
              blueprints.refresh();
            }}
          >
            <RefreshCw size={15} />
            Refresh
          </button>
        }
      />
      <Notice>
        Artifacts are metadata only: a kind, a reference and an optional pin. They are
        independent from blueprints, and the portal never fetches, builds, scans or
        monitors them.
      </Notice>
      <div className="studio-grid">
        <section className="panel" aria-label="Artifact registry">
          <div className="panel-title">
            <h2>
              <Archive size={16} />
              Artifact registry
            </h2>
            <Badge>{artifacts.data?.length ?? 0}</Badge>
          </div>
          <form className="studio-form" onSubmit={register} aria-label="Register artifact">
            <div className="field">
              <label htmlFor="artifact-kind">Artifact kind</label>
              <select
                id="artifact-kind"
                value={kind}
                onChange={(event) => setKind(event.target.value)}
              >
                {artifactKinds.map((entry) => (
                  <option key={entry.id} value={entry.id}>
                    {entry.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor="artifact-reference">Reference</label>
              <input
                id="artifact-reference"
                value={reference}
                onChange={(event) => setReference(event.target.value)}
                placeholder={
                  artifactKinds.find((entry) => entry.id === kind)?.hint ?? ""
                }
              />
            </div>
            <div className="field">
              <label htmlFor="artifact-pin">Pin (optional)</label>
              <input
                id="artifact-pin"
                value={pin}
                onChange={(event) => setPin(event.target.value)}
                placeholder="Digest, tag or role version"
              />
              <small>
                Stored verbatim. The platform does not resolve, check or rotate pins.
              </small>
            </div>
            {artifactError && <ErrorNotice message={artifactError} />}
            <button className="primary" disabled={artifactBusy}>
              {artifactBusy ? "Registering…" : "Register artifact"}
            </button>
          </form>
          {artifactMessage && (
            <div className="notice success" role="status">
              <ShieldCheck size={18} />
              <span>{artifactMessage}</span>
            </div>
          )}
          <ResourceBoundary
            loading={artifacts.loading}
            error={artifacts.error}
            retry={artifacts.refresh}
            label="Loading artifacts"
          >
            {artifacts.data?.length ? (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Kind</th>
                      <th>Reference</th>
                      <th>Pin</th>
                    </tr>
                  </thead>
                  <tbody>
                    {artifacts.data.map((artifact) => (
                      <tr key={artifact.id}>
                        <td>
                          <Badge>{artifactKindLabel(artifact.kind)}</Badge>
                        </td>
                        <td className="mono">{artifact.reference}</td>
                        <td className="mono">{artifact.pin?.trim() || "Not pinned"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <Empty title="No artifacts registered" icon={<Archive size={26} />}>
                Registered artifacts are references only. Nothing is downloaded,
                published or monitored from this page.
              </Empty>
            )}
          </ResourceBoundary>
        </section>

        <section className="panel" aria-label="Service offerings">
          <div className="panel-title">
            <h2>
              <Boxes size={16} />
              Service offerings
            </h2>
            <Badge>{offerings.data?.length ?? 0}</Badge>
          </div>
          <p className="studio-hint">
            Offerings are what customers see in the service catalog. Requesting one
            creates a deployment job that stays pending approval.
          </p>
          {requestMessage && (
            <div className="notice success" role="status">
              <ShieldCheck size={18} />
              <span>{requestMessage}</span>
              {onOpenDeployments && (
                <button className="text-button" onClick={onOpenDeployments}>
                  Open deployments
                </button>
              )}
            </div>
          )}
          {requestError && <ErrorNotice message={requestError} />}
          <ResourceBoundary
            loading={offerings.loading}
            error={offerings.error}
            retry={offerings.refresh}
            label="Loading offerings"
          >
            {offerings.data?.length ? (
              <ul className="studio-offerings">
                {offerings.data.map((offering) => (
                  <li key={offering.id} className="studio-offering">
                    <div>
                      <strong>{offering.title}</strong>
                      <small>Offering v{offering.version}</small>
                    </div>
                    <button
                      aria-label={`Request ${offering.title}`}
                      disabled={
                        !canRequestOffering(user.role) || requesting === offering.id
                      }
                      onClick={() => request(offering)}
                    >
                      {requesting === offering.id ? "Requesting…" : "Request"}
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <Empty title="No offerings published" icon={<Boxes size={26} />}>
                Publish an offering from a saved blueprint to make it requestable.
              </Empty>
            )}
          </ResourceBoundary>

          <div className="studio-publish">
            <h3>Publish an offering</h3>
            {authoring ? (
              <form onSubmit={publish} aria-label="Publish offering">
                <div className="field">
                  <label htmlFor="offering-title">Offering title</label>
                  <input
                    id="offering-title"
                    value={title}
                    onChange={(event) => setTitle(event.target.value)}
                    placeholder="Managed database"
                  />
                </div>
                <div className="field">
                  <label htmlFor="offering-blueprint">Blueprint</label>
                  <select
                    id="offering-blueprint"
                    value={blueprintId}
                    onChange={(event) => setBlueprintId(event.target.value)}
                  >
                    <option value="">Choose a saved blueprint</option>
                    {(blueprints.data || []).map((blueprint) => (
                      <option key={blueprint.id} value={blueprint.id}>
                        {blueprintLabel(blueprint)}
                      </option>
                    ))}
                  </select>
                  <small>
                    {blueprints.data?.length
                      ? "The offering pins this blueprint as its published version."
                      : "No saved blueprint yet. Save one from the canvas first."}
                  </small>
                </div>
                {publishError && <ErrorNotice message={publishError} />}
                <button
                  className="primary"
                  disabled={publishBusy || !blueprints.data?.length}
                >
                  {publishBusy ? "Publishing…" : "Publish offering"}
                </button>
              </form>
            ) : (
              <p className="studio-hint">
                Publishing offerings requires an administrator. You can still request an
                existing offering.
              </p>
            )}
            {publishMessage && (
              <div className="notice success" role="status">
                <ShieldCheck size={18} />
                <span>{publishMessage}</span>
              </div>
            )}
          </div>
        </section>
      </div>
    </>
  );
}
