"""Pin delegated provider implementation files, not only extension entrypoints."""
import importlib.util
import sys

import pytest
from fastapi import HTTPException
from test_core_graph import api
from test_provider_bindings import install_extension
from kuberweaver.providers import execution_binding


@pytest.mark.parametrize('changed', ['class', 'dependency'])
def test_extension_provider_pins_imported_implementation_source(api, tmp_path, monkeypatch, changed):
    client, app = api
    source = tmp_path / 'outside_approved_engine.py'
    source.write_text('class Engine:\n    def execute(self, job, emit): return {"status": "succeeded"}\n    def rollback(self, job, emit): return {"status": "rolled_back"}\n')
    spec = importlib.util.spec_from_file_location('outside_approved_engine', source)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, 'outside_approved_engine', module)
    spec.loader.exec_module(module)
    helper = tmp_path / "execution_helper.py"
    helper.write_text("# reviewed helper source\n")
    module.Engine.source_dependencies = (helper,)
    install_extension(app, 'delegated-engine', '''
        from outside_approved_engine import Engine
        def register(ctx):
            ctx.register('execution', 'delegated-engine', Engine())
    ''', ['providers:execution'])
    assert client.post('/api/extensions/delegated-engine/enable', json={'trusted': True}).status_code == 200
    assert client.patch('/api/providers', json={'bindings': {'execution': 'delegated-engine'}}).status_code == 200
    pinned = execution_binding(app)
    assert pinned['provenance']
    changed_source = source if changed == 'class' else helper
    changed_source.write_text(changed_source.read_text() + '# changed executable source\n')
    with pytest.raises(HTTPException) as failure:
        execution_binding(app)
    assert failure.value.status_code == 503


def test_default_wrapper_pins_delegated_executor_declared_dependencies(api, tmp_path, monkeypatch):
    from test_core_jobs import RecordingExecutor, install_executor
    client, app = api
    helper = tmp_path / 'delegated_helper.py'
    helper.write_text('# reviewed delegated helper\n')
    monkeypatch.setattr(RecordingExecutor, 'source_dependencies', (helper,), raising=False)
    install_executor(app, RecordingExecutor())
    assert execution_binding(app)['provenance']
    helper.write_text('# changed delegated helper\n')
    with pytest.raises(HTTPException) as failure:
        execution_binding(app)
    assert failure.value.status_code == 503
