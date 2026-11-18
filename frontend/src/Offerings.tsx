import { useState } from "react";
import { Boxes, PackageCheck, ShieldCheck } from "lucide-react";
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
  canRequestOffering,
  offeringRequestMessage,
  serviceEstimate,
  serviceStatusLabel,
} from "./studio";
import type { Offering, Service } from "./studio";

const statusTone = (status: string) =>
  status === "pending_approval"
    ? "warning"
    : status === "succeeded"
      ? "success"
      : ["failed", "rejected", "rollback_failed"].includes(status)
        ? "danger"
        : ["queued", "planning", "running", "approving"].includes(status)
          ? "info"
          : "neutral";

export function ServiceOfferings({
  user,
  onOpenServices,
  onOpenCatalogue,
}: {
  user: User;
  onOpenServices?: () => void;
  onOpenCatalogue?: () => void;
}) {
  const offerings = useResource<Offering[]>("/studio/offerings");
  const [requesting, setRequesting] = useState("");
  const [requestError, setRequestError] = useState("");
  const [requested, setRequested] = useState<{ title: string; jobId: string } | null>(null);
  const allowed = canRequestOffering(user.role);

  async function request(offering: Offering) {
    setRequesting(offering.id);
    setRequestError("");
    setRequested(null);
    try {
      const result = await api<{ job_id: string }>(
        `/studio/offerings/${encodeURIComponent(offering.id)}/request`,
        { method: "POST", body: JSON.stringify({}) },
      );
      setRequested({ title: offering.title, jobId: result.job_id });
    } catch (error) {
      setRequestError(errorMessage(error));
    } finally {
      setRequesting("");
    }
  }

  return (
    <>
      <PageTitle
        {...pageHeading("offerings")}
        actions={
          <button onClick={offerings.refresh}>
            <Boxes size={15} />
            Refresh
          </button>
        }
      />
      <Notice>
        Offerings are ready-made services. A request creates a deployment job that stays
        pending approval — nothing is deployed until an operator approves it.
      </Notice>
      {!allowed && (
        <Notice>
          Requesting a service requires an operator or administrator. You can still view
          every published offering.
        </Notice>
      )}
      {requested && (
        <div className="notice success" role="status">
          <ShieldCheck size={18} />
          <span>{offeringRequestMessage(requested.title, requested.jobId)}</span>
          {onOpenServices && (
            <button className="text-button" onClick={onOpenServices}>
              Open my services
            </button>
          )}
        </div>
      )}
      {requestError && <ErrorNotice message={requestError} />}
      <ResourceBoundary
        loading={offerings.loading}
        error={offerings.error}
        retry={offerings.refresh}
        label="Loading service offerings"
      >
        {offerings.data?.length ? (
          <div className="catalogue-grid">
            {offerings.data.map((offering) => (
              <article className="catalogue-card offering-card" key={offering.id}>
                <span className="eyebrow">SERVICE OFFERING</span>
                <h2>
                  {offering.title}
                  <small>v{offering.version}</small>
                </h2>
                <p>
                  Published from a platform blueprint. Review the deployment plan after
                  approval in Deployments.
                </p>
                <div className="catalogue-actions">
                  <button
                    className="primary"
                    aria-label={`Request ${offering.title}`}
                    disabled={!allowed || requesting === offering.id}
                    onClick={() => request(offering)}
                  >
                    {requesting === offering.id ? "Requesting…" : "Request"}
                  </button>
                </div>
              </article>
            ))}
          </div>
        ) : (
          <Empty title="No offerings published" icon={<Boxes size={27} />}>
            An engineer has not published a service offering yet.
          </Empty>
        )}
        <p className="studio-hint">
          Looking for raw building blocks? The extension catalog lists the extensions
          engineers compose into blueprints.
          {onOpenCatalogue && (
            <button className="text-button" onClick={onOpenCatalogue}>
              Open extension catalog
            </button>
          )}
        </p>
      </ResourceBoundary>
    </>
  );
}

export function MyServices({
  onOpenDeployments,
  onOpenCatalogue,
}: {
  onOpenDeployments?: () => void;
  onOpenCatalogue?: () => void;
}) {
  const services = useResource<Service[]>("/studio/services");

  function openDeployment(service: Service) {
    sessionStorage.setItem("kuberweaver.job", service.job_id);
    onOpenDeployments?.();
  }

  return (
    <>
      <PageTitle
        {...pageHeading("services")}
        actions={
          <button onClick={services.refresh}>
            <PackageCheck size={15} />
            Refresh
          </button>
        }
      />
      <Notice>
        Monthly estimates describe declared resources. They are not bills, and this
        workspace does not meter usage.
      </Notice>
      <ResourceBoundary
        loading={services.loading}
        error={services.error}
        retry={services.refresh}
        label="Loading my services"
      >
        {services.data?.length ? (
          <section className="panel" aria-label="Requested services">
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Service</th>
                    <th>Status</th>
                    <th>Monthly estimate</th>
                    <th>Deployment</th>
                  </tr>
                </thead>
                <tbody>
                  {services.data.map((service) => (
                    <tr key={service.job_id}>
                      <td>
                        <strong>{service.title}</strong>
                      </td>
                      <td>
                        <Badge tone={statusTone(service.status)}>
                          {serviceStatusLabel(service.status)}
                        </Badge>
                        {service.status === "pending_approval" && (
                          <small className="pending-note">
                            Pending approval — not deployed
                          </small>
                        )}
                      </td>
                      <td>{serviceEstimate(service.monthly_estimate)}</td>
                      <td>
                        <button
                          className="text-button"
                          aria-label={`Open deployment ${service.job_id}`}
                          onClick={() => openDeployment(service)}
                        >
                          Open
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        ) : (
          <Empty
            title="No services yet"
            icon={<PackageCheck size={27} />}
            action={
              onOpenCatalogue && (
                <button onClick={onOpenCatalogue}>Browse service catalog</button>
              )
            }
          >
            Request an offering from the service catalog; it appears here once the
            request exists, even while it waits for approval.
          </Empty>
        )}
      </ResourceBoundary>
    </>
  );
}
