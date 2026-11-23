"""No infrastructure access: observe real events and report their provenance."""
import json
import os


class LabNotesDiagnostics:
    def __init__(self, record):
        self.record = record

    def read(self):
        findings = []
        if self.record.exists():
            evidence = json.loads(self.record.read_text(encoding="utf-8"))
            findings.append({
                "id": "last-job-event", "code": "LAB_NOTES_JOB_EVENT",
                "title": "Observed worker completion", "severity": "info",
                "message": f"Observed job {evidence['job_id']} with status {evidence['status']}.",
                "provenance": {"source": "job_finished", "record": str(self.record),
                               "job_id": evidence["job_id"], "time": evidence.get("time")},
            })
        return {"mode": "rules", "read_only": True, "findings": findings,
                "summary": {"total": len(findings), "critical": 0, "warning": 0},
                "explanation": "Local event observations only; not an infrastructure health check."}

    __call__ = read  # Compatibility for SDK-only callers; HTTP consumers use .read().


def register(ctx):
    record = ctx.plugin_data_dir() / "last-job.json"

    def job_finished(payload):
        job_id, status = payload.get("job_id"), payload.get("status")
        if not isinstance(job_id, str) or not isinstance(status, str):
            return  # No observation is better than fabricated job evidence.
        evidence = {"event": "job_finished", "job_id": job_id, "status": status,
                    "time": payload.get("time")}
        temporary = ctx.plugin_data_dir() / "last-job.new"
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(evidence, stream)
        os.replace(temporary, record)

    ctx.register("diagnostics", "lab-notes", LabNotesDiagnostics(record),
                 {"label": "Lab notes", "read_only": True, "source": "job_finished observations"})
    ctx.add_hook("job_finished", job_finished)
