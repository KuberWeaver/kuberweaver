"""Executable SDK contract tests; run with stdlib unittest or pytest."""
import json
import sys
import os
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class RegistryTests(unittest.TestCase):
    def test_registration_is_generic_unique_and_json_safe(self):
        from backend.extensions import ExtensionRegistry
        registry = ExtensionRegistry()
        provider = object()
        description = {"label": "Local provider", "nested": {"version": 1}}
        registry.register("custom-kind", "example", provider, description)
        description["nested"]["version"] = 99
        self.assertIs(registry.get("custom-kind", "example"), provider)
        self.assertEqual(registry.list(), [{"kind": "custom-kind", "name": "example", "label": "Local provider", "nested": {"version": 1}}])
        self.assertEqual(registry.list("unknown"), [])
        json.dumps(registry.list())
        with self.assertRaises(ValueError):
            registry.register("custom-kind", "example", object())
        with self.assertRaises(KeyError):
            registry.get("missing", "example")
        with self.assertRaises(ValueError):
            registry.register("diagnostics", "bad", provider, {"kind": "spoof"})
        with self.assertRaises((TypeError, ValueError)):
            registry.register("diagnostics", "secret-repr", provider, {"unsafe": provider})

    def test_hook_system_exit_is_reported_and_async_hooks_rejected(self):
        from backend.extensions import ExtensionRegistry
        registry = ExtensionRegistry()
        def stop(_payload):
            raise SystemExit(2)
        registry.add_hook("job_finished", stop)
        registry.add_hook("job_finished", lambda _payload: None)
        self.assertEqual([r["status"] for r in registry.emit("job_finished", {})], ["error", "ok"])
        async def unsupported(_payload):
            pass
        with self.assertRaises(TypeError):
            registry.add_hook("job_finished", unsupported)

    def test_hooks_copy_nested_payloads_and_isolate_failures(self):
        from backend.extensions import ExtensionRegistry
        registry = ExtensionRegistry()
        payload = {"approval": {"allowed": False}}
        seen = []

        def mutate(value):
            value["approval"]["allowed"] = True
            raise RuntimeError("isolated hook failure")

        registry.add_hook("job_finished", mutate)
        registry.add_hook("job_finished", lambda value: seen.append(value))
        reports = registry.emit("job_finished", payload)
        self.assertFalse(payload["approval"]["allowed"])
        self.assertEqual(seen, [payload])
        self.assertEqual([r["status"] for r in reports], ["error", "ok"])
        self.assertEqual(reports[0]["error_type"], "RuntimeError")
        json.dumps(reports)
        self.assertEqual(registry.emit("no_listeners", payload), [])
        with self.assertRaises(TypeError):
            registry.add_hook("job_finished", "not callable")


class PackageFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR"))
        self.base = Path(self.temp.name)
        self.plugins = self.base / "extensions"
        self.plugins.mkdir()
        self.addCleanup(self.temp.cleanup)

    def plugin(self, folder="demo", *, plugin_id=None, code="def register(ctx): pass\n", capabilities=None, entrypoint="module.py"):
        import yaml
        target = self.plugins / folder
        target.mkdir()
        manifest = {"id": plugin_id or folder, "version": "1.0.0", "entrypoint": entrypoint, "capabilities": capabilities or []}
        (target / "plugin.yaml").write_text(yaml.safe_dump(manifest))
        (target / "module.py").write_text(textwrap.dedent(code))
        return target



class LoadingTests(PackageFixture, unittest.TestCase):
    def test_manifest_path_and_schema_validation_is_fail_closed(self):
        from backend.extensions import ExtensionRegistry
        for index, entrypoint in enumerate(("../escape.py", "/module.py", "sub/module.py")):
            self.plugin(f"traversal-{index}", entrypoint=entrypoint)
        linked = self.plugin("linked")
        (linked / "module.py").unlink()
        outside = self.base / "outside.py"
        outside.write_text("raise RuntimeError('must never import')")
        (linked / "module.py").symlink_to(outside)
        self.plugin("invalid-capability", capabilities=["shell:execute"])
        self.plugin("bad-version")
        (self.plugins / "bad-version" / "plugin.yaml").write_text("id: bad-version\nversion: 1\nentrypoint: module.py\ncapabilities: []\n")
        self.plugin("good")
        reports = ExtensionRegistry().load_directory(self.plugins)
        self.assertEqual(len(reports), 7)
        self.assertEqual(sum(r["status"] == "error" for r in reports), 6)
        self.assertEqual(next(r for r in reports if r.get("id") == "good")["status"], "disabled")
        json.dumps(reports)

    def test_duplicate_ids_are_both_rejected(self):
        from backend.extensions import ExtensionRegistry
        self.plugin("one", plugin_id="same")
        self.plugin("two", plugin_id="same")
        reports = ExtensionRegistry().load_directory(self.plugins, {"same"})
        self.assertEqual([r["status"] for r in reports], ["error", "error"])
        self.assertTrue(all(r["error"] == "Duplicate extension id" for r in reports))

    def test_enabled_registration_is_atomic_and_valid_siblings_survive(self):
        from backend.extensions import ExtensionRegistry
        self.plugin("broken", capabilities=["providers:diagnostics", "hooks:job_finished"], code='''
            def register(ctx):
                ctx.register("diagnostics", "partial", lambda: None)
                ctx.add_hook("job_finished", lambda payload: None)
                raise RuntimeError("failure must roll back")
        ''')
        self.plugin("working", capabilities=["providers:diagnostics"], code='''
            def register(ctx):
                assert ctx.protocol_version == 1
                assert not hasattr(ctx, "get")
                ctx.register("diagnostics", "working", lambda: {"source": "actual"}, {"label": "Works"})
        ''')
        registry = ExtensionRegistry()
        reports = registry.load_directory(self.plugins, {"broken", "working"})
        self.assertEqual([r["status"] for r in reports], ["error", "loaded"])
        self.assertEqual([p["name"] for p in registry.list()], ["working"])
        self.assertEqual(registry.emit("job_finished", {}), [])
        self.assertEqual(registry.get("diagnostics", "working")(), {"source": "actual"})
        self.assertIn("providers:diagnostics", reports[1]["capabilities"])
        self.assertEqual(registry.load_directory(self.plugins, {"working"})[1]["status"], "error")

    def test_undeclared_capabilities_are_rejected(self):
        from backend.extensions import ExtensionRegistry
        cases = {
            "provider": 'ctx.register("infrastructure", "bad", object())',
            "hook": 'ctx.add_hook("job_finished", lambda payload: None)',
            "data": 'ctx.plugin_data_dir()',
        }
        for folder, action in cases.items():
            self.plugin(folder, capabilities=["providers:diagnostics"], code=f"def register(ctx):\n    {action}\n")
        registry = ExtensionRegistry()
        reports = registry.load_directory(self.plugins, set(cases))
        self.assertTrue(all(r["status"] == "error" and r["error_type"] == "PermissionError" for r in reports))
        self.assertEqual(registry.list(), [])

    def test_data_directory_is_contained_persistent_and_rejects_symlinks(self):
        from backend.extensions import ExtensionRegistry
        self.plugin(capabilities=["data:read-write", "providers:diagnostics"], code='''
            def register(ctx):
                state = ctx.plugin_data_dir()
                counter = state / "counter"
                counter.write_text(str(int(counter.read_text()) + 1) if counter.exists() else "1")
                ctx.register("diagnostics", "state", lambda: str(ctx.plugin_data_dir()))
        ''')
        for expected in (1, 2):
            registry = ExtensionRegistry()
            self.assertEqual(registry.load_directory(self.plugins, {"demo"})[0]["status"], "loaded")
            state = Path(registry.get("diagnostics", "state")())
            self.assertEqual(state, self.base / "plugin-data" / "demo")
            self.assertEqual((state / "counter").read_text(), str(expected))
        import shutil
        shutil.rmtree(state)
        outside = self.base / "outside"
        outside.mkdir()
        state.symlink_to(outside, target_is_directory=True)
        report = ExtensionRegistry().load_directory(self.plugins, {"demo"})[0]
        self.assertEqual(report["status"], "error")
        self.assertEqual(list(outside.iterdir()), [])

    def test_async_registration_and_import_exits_are_isolated(self):
        from backend.extensions import ExtensionRegistry
        self.plugin("async-plugin", code="async def register(ctx): pass\n")
        self.plugin("exits", code="raise SystemExit(99)\n")
        self.plugin("valid")
        registry = ExtensionRegistry()
        reports = registry.load_directory(self.plugins, {"async-plugin", "exits", "valid"})
        self.assertEqual([r["status"] for r in reports], ["error", "error", "loaded"])

    def test_context_identity_and_capabilities_cannot_be_reassigned(self):
        from backend.extensions import ExtensionRegistry
        self.plugin(capabilities=["providers:diagnostics"], code='''
            def register(ctx):
                for field, value in (("plugin_id", "other"), ("capabilities", frozenset({"providers:infrastructure"}))):
                    try:
                        setattr(ctx, field, value)
                    except AttributeError:
                        continue
                    raise AssertionError("SDK identity/declarations must be read-only")
                ctx.register("diagnostics", "later", lambda: ctx.register("diagnostics", "late", object()))
        ''')
        registry = ExtensionRegistry()
        self.assertEqual(registry.load_directory(self.plugins, {"demo"})[0]["status"], "loaded")
        with self.assertRaises(RuntimeError):
            registry.get("diagnostics", "later")()
        self.assertEqual([r["name"] for r in registry.list()], ["later"])

    def test_conflicting_provider_rolls_back_all_new_entries_and_hooks(self):
        from backend.extensions import ExtensionRegistry
        self.plugin(capabilities=["providers:diagnostics", "hooks:job_finished"], code='''
            def register(ctx):
                ctx.register("diagnostics", "unique", object())
                ctx.add_hook("job_finished", lambda payload: None)
                ctx.register("diagnostics", "builtin", object())
        ''')
        registry = ExtensionRegistry()
        original = object()
        registry.register("diagnostics", "builtin", original)
        self.assertEqual(registry.load_directory(self.plugins, {"demo"})[0]["status"], "error")
        self.assertIs(registry.get("diagnostics", "builtin"), original)
        self.assertEqual([r["name"] for r in registry.list()], ["builtin"])
        self.assertEqual(registry.emit("job_finished", {}), [])

    def test_disabled_extensions_are_never_imported(self):
        from backend.extensions import ExtensionRegistry
        sentinel = self.base / "executed"
        self.plugin(code=f"from pathlib import Path\nPath({str(sentinel)!r}).touch()\ndef register(ctx): pass\n")
        for enabled in (None, set()):
            registry = ExtensionRegistry()
            report = registry.load_directory(self.plugins, enabled)
            self.assertEqual(report[0]["status"], "disabled")
            self.assertEqual(registry.list(), [])
        self.assertFalse(sentinel.exists())


class LifecycleTests(PackageFixture, unittest.TestCase):
    def cli(self, *args, expected=0):
        import subprocess
        result = subprocess.run([sys.executable, str(ROOT / "scripts" / "plugins.py"), "--data-dir", str(self.base / "data"), *args], text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, expected, result.stderr + result.stdout)
        return json.loads(result.stdout)

    def test_local_install_is_disabled_until_explicit_trust_and_enable(self):
        from backend.extensions import read_enabled
        source = self.plugin()
        self.cli("install", str(source))
        config = self.base / "data" / "extensions.json"
        self.assertEqual(read_enabled(config), set())
        listed = self.cli("list")
        self.assertEqual(listed[0]["status"], "disabled")
        self.cli("enable", "demo", expected=2)
        self.assertEqual(read_enabled(config), set())
        self.cli("enable", "demo", "--trust")
        self.assertEqual(read_enabled(config), {"demo"})
        self.assertEqual(self.cli("list")[0]["status"], "enabled")
        self.cli("disable", "demo")
        self.assertEqual(read_enabled(config), set())
        self.assertEqual(config.stat().st_mode & 0o777, 0o600)

    def test_doctor_is_static_unless_trusted_execution_is_requested(self):
        sentinel = self.base / "sentinel"
        source = self.plugin(code=f"from pathlib import Path\nPath({str(sentinel)!r}).touch()\ndef register(ctx): pass\n")
        report = self.cli("doctor", str(source))
        self.assertEqual(report["status"], "validated")
        self.assertFalse(sentinel.exists())
        self.cli("doctor", str(source), "--execute", expected=2)
        self.assertFalse(sentinel.exists())
        report = self.cli("doctor", str(source), "--execute", "--trust")
        self.assertEqual(report["status"], "loaded")
        self.assertTrue(report["isolated_process"])
        self.assertTrue(sentinel.exists())  # Process isolation is NOT sandboxing.

    def test_doctor_reports_malformed_yaml_without_traceback(self):
        source = self.plugin()
        (source / "plugin.yaml").write_text("id: [unterminated\n")
        result = self.cli("doctor", str(source), expected=2)
        self.assertEqual(result["status"], "error")

    def test_duplicate_yaml_fields_and_symlinked_packages_are_rejected(self):
        from backend.extensions import ExtensionRegistry
        source = self.plugin()
        with (source / "plugin.yaml").open("a") as stream:
            stream.write("id: shadow-id\n")
        (self.plugins / "linked-package").symlink_to(source, target_is_directory=True)
        reports = ExtensionRegistry().load_directory(self.plugins)
        self.assertEqual([r["status"] for r in reports], ["error", "error"])

    def test_config_fails_closed_when_malformed(self):
        from backend.extensions import read_enabled
        config = self.base / "extensions.json"
        self.assertEqual(read_enabled(config), set())
        for value in ('not json', '{"enabled":"demo"}', '{"schema_version":1,"enabled":["../escape"]}'):
            config.write_text(value)
            with self.assertRaises(ValueError):
                read_enabled(config)

    def test_example_diagnostic_appears_only_when_enabled(self):
        from backend.extensions import ExtensionRegistry
        import shutil
        shutil.copytree(ROOT / "extensions" / "lab-notes", self.plugins / "lab-notes")
        registry = ExtensionRegistry()
        report = registry.load_directory(self.plugins, {"lab-notes"})
        self.assertEqual(report[0]["status"], "loaded")
        provider = registry.get("diagnostics", "lab-notes")
        self.assertEqual(provider()["findings"], [])
        self.assertEqual(registry.emit("job_finished", {"job_id": "actual-job-123", "status": "succeeded"})[0]["status"], "ok")
        diagnostic = provider()
        self.assertTrue(diagnostic["read_only"])
        self.assertEqual(diagnostic["findings"][0]["provenance"]["job_id"], "actual-job-123")
        self.assertEqual(ExtensionRegistry().load_directory(self.plugins)[0]["status"], "disabled")
        disabled = ExtensionRegistry()
        disabled.load_directory(self.plugins, set())
        self.assertEqual(disabled.list("diagnostics"), [])


if __name__ == "__main__":
    unittest.main()
