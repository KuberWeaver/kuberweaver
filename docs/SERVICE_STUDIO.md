# Service Studio

Service Studio is part of the existing portal. Sign in with your normal KuberWeaver account. No separate listener, API key, or database is required.

1. Create and save an admitted graph in Canvas. This is the blueprint used for execution.
2. In Engineer Studio, an administrator can publish that blueprint as a versioned offering. Publication freezes its normalized graph, manifests, and source fingerprints. It is not a test deployment or an approval.
3. In Service Catalog, operators and administrators can request an offering. That creates an ordinary job, including preparation where required. An administrator must review and approve the job before it applies anything.
4. My Services shows requests made by the signed-in account, the corresponding job status, and the estimated monthly cost of the declared resources. The estimate is not metered consumption or a bill.

Artifact registration records references to OCI images, VM images, and Galaxy roles. These records are metadata only: they are not fetched, composed into blueprints, or executed. This release does not add marketplace discovery, guest health monitoring, service-level decommission or recommission, or customer tenant isolation. My Services filters its own list by requester; it does not change the visibility rules of the existing Jobs API.
