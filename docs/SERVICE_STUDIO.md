# Service Studio

Service Studio is part of the existing portal. Sign in with your normal KuberWeaver account. No separate listener, API key, or database is required.

1. In Canvas, select **Add resource** to place a blank square. Select any square to open its resource panel. Choose an available adapter, configure its schema-defined inputs, and use **Runs after** to declare prerequisites. Previously saved blueprints and dependencies use the same graph format.
2. Validate the blueprint and, as an administrator, choose **Publish to Service Catalog**. This saves the blueprint and publishes a versioned offering. You can also publish a previously saved blueprint from Engineer Studio. Publication freezes its normalized graph, manifests, and source fingerprints. It is not a test deployment or an approval.
3. In Service Catalog, operators and administrators can request an offering. That creates an ordinary job, including preparation where required. An administrator must review and approve the job before it applies anything.
4. My Services shows requests made by the signed-in account, the corresponding job status, and the estimated monthly cost of the declared resources. The estimate is not metered consumption or a bill.

The panel reads available adapters, origins, options, and specifications from the manifest catalog. Browse external directories (Docker Hub, Amazon EC2 AMIs, OpenTofu Registry, Ansible Galaxy) or paste/open a JSON object of values for the selected adapter. The import accepts only declared fields; the server still validates all values. External directories do not install or fetch executable modules, images, or roles. An unregistered source requires a reviewed adapter and manifest. Some VM and workload adapters are restricted to single-resource jobs; mixed execution profiles are not enabled by this UI.

Artifact registration separately records references to OCI images, VM images, and Galaxy roles. These records are metadata only: they are not fetched, composed into blueprints, or executed. This release does not add marketplace discovery, guest health monitoring, service-level decommission or recommission, or customer tenant isolation. My Services filters its own list by requester; it does not change the visibility rules of the existing Jobs API.
