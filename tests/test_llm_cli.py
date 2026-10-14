import json
from types import SimpleNamespace

from pdf2tex.llm_cli import command_for, prompt_with_images, save_response


def test_provider_commands_and_structured_response(tmp_path):
    schema = tmp_path/'schema.json'
    schema.write_text(json.dumps({'type': 'object', 'properties': {'ok': {'type': 'boolean'}}}))
    result = tmp_path/'result.json'
    image = tmp_path/'crop.png'
    image.write_bytes(b'image fixture')

    codex = command_for('codex', 'codex', schema, result, [image])
    assert codex[-1] == '-'
    assert codex[codex.index('--image')+1] == str(image)

    claude = command_for('claude', 'claude', schema, result, [image], tmp_path)
    assert '--json-schema' in claude and '--add-dir' in claude
    assert '--image' not in claude
    assert str(image) in prompt_with_images('claude', 'Review the crop.', [image])
    assert prompt_with_images('codex', 'Review the crop.', [image]) == 'Review the crop.'
    assert save_response('claude', json.dumps({'structured_output': {'ok': True}}), result)
    assert json.loads(result.read_text()) == {'ok': True}


def test_claude_layout_adapter_uses_structured_output(tmp_path, monkeypatch):
    import fitz
    from pdf2tex.preflight import assess_layout

    pdf = tmp_path/'paper.pdf'
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((50, 70), 'A sample academic paragraph. ' * 10)
        doc.save(pdf)
    monkeypatch.setattr('pdf2tex.preflight.shutil.which', lambda name: '/fake/claude')
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        response = {'family': 'single_column', 'confidence': .95,
                    'single_column_score': 95, 'two_column_score': 5,
                    'text_source': 'native', 'pages': [{'page': 1,
                    'body_columns': 1, 'confidence': .95,
                    'full_width_elements': [], 'notes': ''}],
                    'risks': [], 'summary': 'single column'}
        return SimpleNamespace(returncode=0, stdout=json.dumps({'structured_output': response}))

    monkeypatch.setattr('pdf2tex.preflight.subprocess.run', fake_run)
    plan = assess_layout(pdf, reviewer='claude', provider='claude', cache_root=tmp_path/'cache')
    assert plan['reviewer'] == 'claude' and plan['family'] == 'single_column'
    assert len(calls) == 1 and calls[0][0] == 'claude'
    assert assess_layout(pdf, reviewer='claude', provider='claude', cache_root=tmp_path/'cache')['cache_hit']
    assert len(calls) == 1


def test_claude_formula_review_adapts_stdout_and_reuses_cache(tmp_path, monkeypatch):
    from PIL import Image
    from pdf2tex.formula_review import review_formulas

    Image.new('RGB', (50, 20), 'white').save(tmp_path/'crop.png')
    formulas = [{'id': 'f1', 'page': 1, 'kind': 'inline', 'crop': 'crop.png',
                 'ocr_latex': 'x'}]
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        kwargs['stdout'].write(json.dumps({'structured_output': {'formulas': [
            {'id': 'f1', 'status': 'confirmed', 'latex': 'x', 'notes': ''}]}}))
        kwargs['stdout'].flush()
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr('pdf2tex.formula_review.run_review', fake_run)
    values, stats = review_formulas(formulas, tmp_path, provider='claude')
    assert values['f1']['latex'] == 'x' and stats['new_calls'] == 1
    assert calls[0][0] == 'claude' and '--json-schema' in calls[0]
    _, stats = review_formulas(formulas, tmp_path, provider='claude')
    assert stats['cached_batches'] == 1 and len(calls) == 1
